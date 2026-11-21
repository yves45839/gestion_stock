"""Prepare a read-only server storage check from a verified local manifest."""
import argparse
import base64
import hashlib
import inspect
import json
from pathlib import Path
import re
import shlex
import zlib


def server_check(rows, root='/home/samr/gestion_stock/media', home='/home/samr'):
    import hashlib
    from pathlib import Path
    import os
    import stat
    import subprocess

    root = Path(root).resolve()
    seen = set()
    inode_counts = {}
    payloads = {}
    missing = []
    changed = []
    valid = 0
    for row in rows:
        relative, old_hash, old_size, new_hash, new_size = row
        parts = relative.split('/')
        if (relative in seen or len(parts) < 3 or parts[:2] not in (
                ['products', 'datasheets'], ['products', 'images']) or
                any(p in ('', '.', '..') or '\\' in p for p in parts) or not 0 < new_size < 1048576):
            raise ValueError('Liste de fichiers invalide.')
        seen.add(relative)
        path = root.joinpath(*parts)
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('Lien symbolique refusé : ' + relative)
        if not path.exists():
            missing.append(relative)
            continue
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Fichier non régulier : ' + relative)
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1048576), b''):
                h.update(block)
        after = path.stat()
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or
                h.hexdigest() != old_hash or before.st_size != old_size):
            changed.append(relative)
            continue
        valid += 1
        item = inode_counts.setdefault((before.st_dev, before.st_ino), [before, 0])
        item[1] += 1
        if new_hash in payloads and payloads[new_hash] != new_size:
            raise ValueError('Taille incohérente pour un contenu compressé.')
        payloads[new_hash] = new_size
    freed = sum(info.st_blocks * 512 for info, count in inode_counts.values() if info.st_nlink == count)
    fs_block = os.statvfs(root).f_frsize or 4096
    new_blocks = sum(((size + fs_block - 1) // fs_block) * fs_block for size in payloads.values())
    # du counts hardlinks once across the entire home and includes dotfiles.
    result = subprocess.run(['du', '-s', '-B1', str(home)], capture_output=True, text=True, check=True)
    used = int(result.stdout.split()[0])
    projected = used - freed + new_blocks
    quota = 512 * 1048576
    print('Fichiers présents et conformes :', valid)
    print('Déjà absents à ne pas réintroduire :', len(missing))
    for path in missing:
        print('ABSENT :', path)
    print('Fichiers modifiés :', len(changed))
    for path in changed:
        print('MODIFIÉ :', path)
    print('Occupation actuelle exacte : %.2f Mo' % (used / 1048576))
    print('Blocs libérables des originaux conformes : %.2f Mo' % (freed / 1048576))
    print('Blocs des versions compressées partagées : %.2f Mo' % (new_blocks / 1048576))
    print('Occupation finale estimée hors journaux et staging : %.2f Mo' % (projected / 1048576))
    print('Marge estimée sur 512 Mo : %.2f Mo' % ((quota - projected) / 1048576))
    if missing or changed:
        print('Sélection à actualiser avant installation.')
    elif projected > quota - 8 * 1048576:
        print('Marge inférieure à 8 Mo : installation à ne pas lancer en l’état.')
    else:
        print('Marge estimée suffisante ; la capacité de staging reste à organiser par lots.')
    print('AUCUN fichier supprimé, transféré ou modifié.')
    return {'valid': valid, 'missing': missing, 'changed': changed, 'used': used,
            'freed': freed, 'new_blocks': new_blocks, 'projected': projected}


def prepare(manifest_path):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if (manifest.get('schema') != 2 or manifest.get('complete') is not True or
            manifest.get('site') != 'https://samr.pythonanywhere.com' or
            manifest.get('large_file_policy') != 'remove_backed_up_server_original'):
        raise ValueError('Manifeste incomplet ou incompatible.')
    rows = []
    seen = set()
    for batch in manifest['batches']:
        archive = manifest_path.parent / batch['archive']
        if not re.fullmatch(r'lot-\d{3}\.zip', batch['archive']):
            raise ValueError('Nom de lot invalide.')
        digest = hashlib.sha256()
        with archive.open('rb') as stream:
            for block in iter(lambda: stream.read(1048576), b''):
                digest.update(block)
        if digest.hexdigest() != batch['archive_sha256'] or archive.stat().st_size != batch['archive_bytes']:
            raise ValueError('Lot local absent ou modifié : ' + archive.name)
        for row in batch['files']:
            path = row['path']
            parts = path.split('/')
            if (path in seen or len(parts) < 3 or parts[:2] not in (
                    ['products', 'datasheets'], ['products', 'images']) or
                    any(p in ('', '.', '..') or '\\' in p for p in parts)):
                raise ValueError('Chemin invalide ou dupliqué.')
            if not (0 < row['output_bytes'] < 1048576 and row['output_bytes'] < row['original_bytes']):
                raise ValueError('Seuil de taille invalide.')
            if any(not re.fullmatch('[0-9a-f]{64}', row[k]) for k in ('original_sha256', 'output_sha256')):
                raise ValueError('Empreinte invalide.')
            seen.add(path)
            rows.append([path, row['original_sha256'], row['original_bytes'], row['output_sha256'], row['output_bytes']])
    if not rows:
        raise ValueError('Aucun remplacement.')
    data = base64.b64encode(zlib.compress(json.dumps(rows, separators=(',', ':')).encode(), 9)).decode()
    literals = '\n'.join(repr(data[index:index + 900]) for index in range(0, len(data), 900))
    source = inspect.getsource(server_check) + '\nimport base64,json,zlib\nserver_check(json.loads(zlib.decompress(base64.b64decode(\n' + literals + '\n))))\n'
    if len(source.encode()) > 120000:
        raise ValueError('Commande trop grande ; utiliser des lots de contrôle plus petits.')
    command = "printf '%s\\n' " + shlex.quote(source) + ' | PYTHONDONTWRITEBYTECODE=1 /home/samr/gestion_stock/venv/bin/python -\n'
    output = manifest_path.parent / 'commande-controle-espace.txt'
    output.write_text(command, encoding='utf-8')
    return output


def main():
    parser = argparse.ArgumentParser(description='Prépare un contrôle serveur sans aucune modification.')
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    if args.manifest is None:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        chosen = filedialog.askopenfilename(title='Choisir manifest-remplacement.json dans LR-REMPLACEMENT', filetypes=[('Manifeste JSON', '*.json')])
        root.destroy()
        if not chosen:
            return 0
        args.manifest = Path(chosen)
    output = prepare(args.manifest)
    print('Commande préparée :', output)
    if __import__('os').name == 'nt':
        import subprocess
        result = subprocess.run(['powershell', '-NoProfile', '-Command',
                                 '[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd())) | Set-Clipboard'],
                                input=base64.b64encode(output.read_bytes()).decode('ascii'), text=True, capture_output=True)
        if result.returncode == 0:
            print('Commande copiée : collez-la dans Bash PythonAnywhere et transmettez le résultat.')
        else:
            print('Copie automatique impossible. Copiez le contenu du fichier texte dans Bash PythonAnywhere.')
    print('Le contrôle ne modifie aucun fichier serveur.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print('ARRÊT :', error)
        raise SystemExit(1)
