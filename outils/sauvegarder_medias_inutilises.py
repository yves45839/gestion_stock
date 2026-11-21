"""Back up unreferenced media to the PC. No remote writes or deletions."""
import argparse
import base64
import hashlib
import inspect
import json
from pathlib import Path
import re
import shlex
import subprocess
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError
import uuid
import zlib

SITE = 'https://samr.pythonanywhere.com'
BEGIN = 'LR_INUTILISES_DEBUT'
END = 'LR_INUTILISES_FIN'


def server_inventory(root=None, used=None):
    import hashlib
    from pathlib import Path
    if root is None and used is None:
        from django.apps import apps
        from django.conf import settings
        from django.db.models import FileField
        from inventory.models import Version
        root = settings.MEDIA_ROOT
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
    if root is None or used is None:
        raise ValueError('Racine et références requises ensemble.')
    root = Path(root).resolve()
    rows = []
    for path in sorted((root / 'products').rglob('*')):
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            continue
        key = path.relative_to(root).as_posix()
        if key in used:
            continue
        before = path.stat()
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1048576), b''):
                digest.update(block)
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('Fichier modifié pendant inventaire : ' + key)
        rows.append([key, before.st_size, digest.hexdigest()])
    return {'schema': 1, 'site': 'https://samr.pythonanywhere.com', 'files': rows}


def validate_path(key):
    parts = key.split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    if (len(parts) < 3 or parts[0] != 'products' or any(
            p in ('', '.', '..') or re.search(r'[<>:"\\|?*\x00-\x1f]', p) or
            p.endswith((' ', '.')) or p.split('.')[0].upper() in reserved for p in parts)):
        raise ValueError('Chemin local dangereux : ' + key)
    return Path(*parts)


def parse_inventory(text):
    match = re.search(r'^' + BEGIN + r'\s*\n([A-Za-z0-9+/=\s]+)\n' + END + r'\s*$', text, re.MULTILINE)
    if not match:
        raise ValueError('Copier le bloc complet du résultat serveur, entre LR_INUTILISES_DEBUT et LR_INUTILISES_FIN.')
    packed = base64.b64decode(re.sub(r'\s+', '', match.group(1)), validate=True)
    inventory = json.loads(zlib.decompress(packed))
    if inventory.get('schema') != 1 or inventory.get('site') != SITE or not isinstance(inventory.get('files'), list):
        raise ValueError('Inventaire incompatible.')
    seen = set()
    for key, size, digest in inventory['files']:
        validate_path(key)
        if key.casefold() in seen or not isinstance(size, int) or not 0 <= size <= 256 * 1048576 or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Inventaire invalide ou collision de noms Windows.')
        seen.add(key.casefold())
    return inventory


class SameSite(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(urljoin(req.full_url, newurl))
        if (parsed.scheme, parsed.netloc) != ('https', urlsplit(SITE).netloc):
            raise ValueError('Redirection hors du site refusée.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def backup(row, folder):
    key, size, expected = row
    relative = validate_path(key)
    target = folder / 'originaux' / relative
    if target.is_symlink() or not target.resolve().is_relative_to(folder.resolve()):
        raise ValueError('Chemin de sauvegarde redirigé.')
    if target.exists():
        if not target.is_file() or target.stat().st_size != size or file_hash(target) != expected:
            raise ValueError('Sauvegarde existante différente ; aucun écrasement.')
        return 'sauvegarde_verifiee'
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.part-' + uuid.uuid4().hex)
    try:
        request = Request(SITE + '/media/' + quote(key, safe='/'), headers={'User-Agent': 'LR-Sauvegarde/1.0', 'Cache-Control': 'no-cache', 'Accept-Encoding': 'identity'})
        try:
            with build_opener(SameSite()).open(request, timeout=120) as source, temporary.open('xb') as output:
                total = 0
                h = hashlib.sha256()
                for block in iter(lambda: source.read(1048576), b''):
                    total += len(block)
                    if total > size:
                        raise ValueError('Contenu serveur plus grand que prévu.')
                    h.update(block)
                    output.write(block)
        except HTTPError as error:
            if error.code in (404, 410):
                return 'deja_absent'
            raise
        if total != size or h.hexdigest() != expected:
            raise ValueError('Empreinte serveur différente ; sauvegarde refusée.')
        if target.exists():
            raise ValueError('Destination apparue pendant le téléchargement ; aucun écrasement.')
        temporary.replace(target)
        return 'sauvegarde_verifiee'
    finally:
        temporary.unlink(missing_ok=True)


def clipboard_set(text):
    packed = base64.b64encode(text.encode('utf-8')).decode('ascii')
    subprocess.run(['powershell', '-NoProfile', '-Command', '[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd())) | Set-Clipboard'], input=packed, text=True, check=True)


def clipboard_get():
    result = subprocess.run(['powershell', '-NoProfile', '-Command', '[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((Get-Clipboard -Raw)))'], capture_output=True, text=True, check=True)
    return base64.b64decode(result.stdout.strip(), validate=True).decode('utf-8')


def main():
    parser = argparse.ArgumentParser(description='Sauvegarde locale des médias sans référence actuelle ou historique. Aucun retrait serveur.')
    parser.add_argument('--sauvegarder', action='store_true')
    parser.add_argument('--inventaire', type=Path, help='Résultat serveur enregistré dans un fichier texte ; sinon lecture du presse-papiers.')
    parser.add_argument('--output', type=Path, default=Path.home() / 'Downloads' / 'LR-INUTILISES')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if not args.sauvegarder:
        source = inspect.getsource(server_inventory) + '\nimport base64,json,zlib\ndata=server_inventory()\nprint("LR_INUTILISES_DEBUT")\nencoded=base64.b64encode(zlib.compress(json.dumps(data,separators=(",",":")).encode(),9)).decode()\nfor index in range(0,len(encoded),900): print(encoded[index:index+900])\nprint("LR_INUTILISES_FIN")\n'
        command = "cd /home/samr/gestion_stock && printf '%s\\n' " + shlex.quote(source) + ' | PYTHONDONTWRITEBYTECODE=1 ./venv/bin/python manage.py shell\n'
        file = args.output / 'commande-inventaire-inutilises.txt'
        file.write_text(command, encoding='utf-8')
        clipboard_set(command)
        print('Commande copiée. Coller dans Bash PythonAnywhere. Aucun fichier serveur modifié.')
        print('Puis copier le résultat COMPLET entre LR_INUTILISES_DEBUT et LR_INUTILISES_FIN, marqueurs compris.')
        return 0
    inventory = parse_inventory(args.inventaire.read_text(encoding='utf-8-sig') if args.inventaire else clipboard_get())
    identity = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    folder = args.output / identity[:12]
    folder.mkdir(exist_ok=True)
    report_path = folder / 'rapport-inutilises.json'
    report = {'schema': 1, 'site': SITE, 'inventory_sha256': identity, 'complete': False, 'files': {}}
    def save():
        temp = folder / 'rapport.tmp'
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(report_path)
    errors = 0
    save()
    for index, row in enumerate(inventory['files'], 1):
        key, size, digest = row
        try:
            status = backup(row, folder)
        except (OSError, ValueError) as error:
            status = 'ERREUR : ' + str(error)
            errors += 1
        report['files'][key] = {'path': key, 'original_bytes': size, 'original_sha256': digest, 'status': status}
        print(f'[{index}/{len(inventory["files"])}] {status} : {key}', flush=True)
        if index % 20 == 0:
            save()
    report['complete'] = True
    save()
    print('Sauvegardés et vérifiés :', sum(r['status'] == 'sauvegarde_verifiee' for r in report['files'].values()))
    print('Erreurs :', errors)
    print('Rapport à transmettre :', report_path)
    print('AUCUN fichier serveur supprimé ou modifié.')
    return 1 if errors else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zlib.error) as error:
        print('ARRÊT :', error)
        raise SystemExit(1)
