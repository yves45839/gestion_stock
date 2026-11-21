"""Prepare guarded cleanup commands only after checking the PC backups again."""
import argparse
import base64
import hashlib
import inspect
import json
from pathlib import Path
import re
import shlex
import subprocess
import zlib


def server_cleanup(root, rows, apply=False, reference_reader=None):
    import hashlib
    from pathlib import Path
    import re
    import stat

    def references():
        if reference_reader is not None:
            return set(reference_reader())
        from django.apps import apps
        from django.db.models import FileField
        from inventory.models import Version
        used = set()

        def protect(value):
            if isinstance(value, str):
                used.add(value)
            elif isinstance(value, dict):
                for item in value.values():
                    protect(item)
            elif isinstance(value, list):
                for item in value:
                    protect(item)

        for model in apps.get_models():
            for field in model._meta.fields:
                if isinstance(field, FileField):
                    for name in model._base_manager.values_list(field.name, flat=True).iterator():
                        if name:
                            used.add(str(name))
        for snapshot in Version.objects.values_list('snapshot', flat=True).iterator():
            protect(snapshot)
        return used

    root = Path(root).resolve()
    used = references()
    checked = []
    seen = set()
    skipped = {'referenced': 0, 'missing': 0, 'changed': 0}
    for key, expected_size, expected_hash in rows:
        parts = key.split('/')
        if (key in seen or len(parts) < 3 or parts[0] != 'products' or
                any(p in ('', '.', '..') or '\\' in p for p in parts) or
                not re.fullmatch('[0-9a-f]{64}', expected_hash) or expected_size < 0):
            raise ValueError('Liste de nettoyage invalide.')
        seen.add(key)
        if key in used:
            skipped['referenced'] += 1
            continue
        path = root.joinpath(*parts)
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('Lien symbolique refusé : ' + key)
        if not path.exists():
            skipped['missing'] += 1
            continue
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Fichier non régulier : ' + key)
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1048576), b''):
                h.update(block)
        after = path.stat()
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if (identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or
                before.st_size != expected_size or h.hexdigest() != expected_hash):
            skipped['changed'] += 1
            continue
        checked.append((key, path, identity, before))
    # Refresh both live and historical references after the content checks.
    used.update(references())
    allowed = []
    for item in checked:
        if item[0] in used:
            skipped['referenced'] += 1
        else:
            allowed.append(item)
    inodes = {}
    for key, path, identity, info in allowed:
        group = inodes.setdefault((info.st_dev, info.st_ino), [info, 0])
        group[1] += 1
    estimated = sum(info.st_blocks * 512 for info, count in inodes.values() if info.st_nlink == count)
    print('Fichiers sauvegardés, conformes et sans référence :', len(allowed))
    print('Ignorés (référencés / absents / modifiés) :', skipped['referenced'], skipped['missing'], skipped['changed'])
    print('Blocs récupérables estimés : %.2f Mo' % (estimated / 1048576))
    freed = 0
    deleted = 0
    if apply:
        for key, path, identity, info in allowed:
            if path.is_symlink() or path.resolve() != path:
                raise ValueError('Chemin changé pendant le nettoyage ; arrêt.')
            current = path.stat()
            if identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
                raise ValueError('Fichier changé pendant le nettoyage ; arrêt : ' + key)
            last_link = current.st_nlink == 1
            path.unlink()
            deleted += 1
            if last_link:
                freed += current.st_blocks * 512
        print('Fichiers retirés :', deleted)
        print('Blocs libérés : %.2f Mo' % (freed / 1048576))
        print('Base de données et fichiers référencés inchangés. Conserver les sauvegardes PC.')
    else:
        print('SIMULATION : aucun fichier serveur modifié.')
    return {'deleted': deleted, 'freed': freed, 'estimated': estimated, 'skipped': skipped}


def validate_path(key):
    parts = key.split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    if (len(parts) < 3 or parts[0] != 'products' or any(
            p in ('', '.', '..') or re.search(r'[<>:"\\|?*\x00-\x1f]', p) or
            p.endswith((' ', '.')) or p.split('.')[0].upper() in reserved for p in parts)):
        raise ValueError('Chemin dangereux : ' + key)
    return Path(*parts)


def prepare(report_path, apply=False):
    report = json.loads(report_path.read_text(encoding='utf-8-sig'))
    if (report.get('schema') != 1 or report.get('site') != 'https://samr.pythonanywhere.com' or
            report.get('complete') is not True):
        raise ValueError('Rapport incomplet ou incompatible.')
    rows = []
    folded = set()
    for key, row in report['files'].items():
        if row.get('status') != 'sauvegarde_verifiee':
            raise ValueError('Rapport contenant un fichier non sauvegardé : ' + key)
        if row.get('path') != key or key.casefold() in folded:
            raise ValueError('Chemin incohérent ou dupliqué.')
        folded.add(key.casefold())
        relative = validate_path(key)
        root = report_path.parent / 'originaux'
        path = root / relative
        expected = row['original_sha256']
        if not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('Empreinte invalide.')
        if (path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or
                not path.is_file() or path.stat().st_size != row['original_bytes']):
            raise ValueError('Sauvegarde locale manquante ou modifiée : ' + key)
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1048576), b''):
                h.update(block)
        if h.hexdigest() != expected:
            raise ValueError('Sauvegarde locale modifiée : ' + key)
        rows.append([key, row['original_bytes'], expected])
    inventory = {'schema': 1, 'site': report['site'], 'files': rows}
    signature = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    if signature != report['inventory_sha256']:
        raise ValueError('Le rapport ne correspond plus à son inventaire.')
    packed = base64.b64encode(zlib.compress(json.dumps(rows, separators=(',', ':')).encode(), 9)).decode()
    literals = '\n'.join(repr(packed[i:i+900]) for i in range(0, len(packed), 900))
    source = inspect.getsource(server_cleanup) + '\nimport base64,json,zlib\nfrom django.conf import settings\nserver_cleanup(settings.MEDIA_ROOT,json.loads(zlib.decompress(base64.b64decode(\n' + literals + '\n))),apply=' + repr(apply) + ')\n'
    if len(source.encode()) > 120000:
        raise ValueError('Commande trop grande ; ne pas effectuer le nettoyage.')
    command = "cd /home/samr/gestion_stock && printf '%s\\n' " + shlex.quote(source) + ' | PYTHONDONTWRITEBYTECODE=1 ./venv/bin/python manage.py shell\n'
    output = report_path.parent / ('commande-nettoyage-' + ('appliquer' if apply else 'simulation') + '.txt')
    output.write_text(command, encoding='utf-8')
    return output


def main():
    parser = argparse.ArgumentParser(description='Prépare le nettoyage des médias sauvegardés, avec vérification des références et de l’historique.')
    parser.add_argument('--rapport', type=Path)
    parser.add_argument('--appliquer', action='store_true', help='Préparer la commande de retrait ; sinon simulation seulement.')
    args = parser.parse_args()
    if args.rapport is None:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        chosen = filedialog.askopenfilename(title='Choisir rapport-inutilises.json dans le dossier contenant originaux', filetypes=[('Rapport JSON', '*.json')])
        root.destroy()
        if not chosen:
            return 0
        args.rapport = Path(chosen)
    file = prepare(args.rapport, args.appliquer)
    packed = base64.b64encode(file.read_bytes()).decode('ascii')
    subprocess.run(['powershell', '-NoProfile', '-Command', '[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd())) | Set-Clipboard'], input=packed, text=True, check=True)
    print('Sauvegardes PC revérifiées. Commande copiée :', file)
    print('Mode :', 'RETRAIT des fichiers sauvegardés encore inutilisés' if args.appliquer else 'SIMULATION')
    print('Coller dans Bash PythonAnywhere. Aucun changement serveur depuis ce programme PC.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('ARRÊT :', error)
        raise SystemExit(1)
