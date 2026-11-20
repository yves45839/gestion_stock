"""Compare existing local backups with today's public catalogue; GET requests only."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

SITE = 'https://samr.pythonanywhere.com'


class SameSite(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(urljoin(req.full_url, newurl))
        if (target.scheme, target.netloc) != (urlsplit(SITE).scheme, urlsplit(SITE).netloc):
            raise ValueError('Redirection hors du site refusée.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url):
    return build_opener(SameSite()).open(Request(url, headers={
        'User-Agent': 'LR-Verification/1.0', 'Cache-Control': 'no-cache',
        'Accept-Encoding': 'identity'}), timeout=120)


def safe_path(key):
    parts = key.split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    if (len(parts) < 3 or parts[:2] not in (['products', 'images'], ['products', 'datasheets'])
            or any(p in ('', '.', '..') or re.search(r'[<>:"\\|?*\x00-\x1f]', p)
                   or p.endswith((' ', '.')) or p.split('.')[0].upper() in reserved for p in parts)):
        raise ValueError('Chemin refusé : ' + key)
    return Path(*parts)


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def catalogue():
    with open_url(SITE + '/api/products/') as stream:
        data = json.load(stream)
    rows = data.get('results')
    if not isinstance(rows, list) or data.get('count', len(rows)) != len(rows):
        raise ValueError('Catalogue incomplet : vérification interrompue.')
    files = set()
    for row in rows:
        for field, prefix in (('datasheet_pdf_url', 'products/datasheets/'),
                              ('image_url', 'products/images/'), ('pending_image_url', 'products/images/')):
            if not row.get(field):
                continue
            parsed = urlsplit(urljoin(SITE, row[field]))
            if (parsed.scheme, parsed.netloc) != ('https', urlsplit(SITE).netloc):
                raise ValueError('Lien de catalogue hors du site.')
            if not parsed.path.startswith('/media/'):
                raise ValueError('Lien hors de media.')
            key = unquote(parsed.path[len('/media/'):])
            safe_path(key)
            if not key.startswith(prefix):
                raise ValueError('Champ du catalogue et dossier incohérents.')
            files.add(key)
    return files


def check_entry(folder, kind, key, entry):
    relative = safe_path(key)
    result = {'path': key, 'kind': kind}
    field, subfolder = ('compressed', 'compresses') if kind == 'pdf' else ('optimized', 'optimisees')
    original = folder / 'originaux' / relative
    output = folder / subfolder / relative
    if not entry.get('original_sha256') or not entry.get(field + '_sha256'):
        return {**result, 'status': 'sans_version_compressee'}
    if (not original.is_file() or original.is_symlink() or sha256(original) != entry['original_sha256']):
        raise ValueError('Sauvegarde locale absente ou modifiée.')
    if (not output.is_file() or output.is_symlink() or sha256(output) != entry[field + '_sha256']):
        raise ValueError('Version compressée locale absente ou modifiée.')
    old, new = original.stat().st_size, output.stat().st_size
    if new <= 0 or new >= old:
        return {**result, 'status': 'sans_gain'}
    digest = hashlib.sha256()
    size = 0
    try:
        with open_url(SITE + '/media/' + quote(key, safe='/')) as response:
            for block in iter(lambda: response.read(1024 * 1024), b''):
                size += len(block)
                if size > old:
                    return {**result, 'status': 'serveur_modifie'}
                digest.update(block)
    except HTTPError as error:
        if error.code in (404, 410):
            return {**result, 'status': 'absent_serveur_ne_pas_retablir'}
        raise
    if digest.hexdigest() != entry['original_sha256'] or size != old:
        return {**result, 'status': 'serveur_modifie'}
    return {**result, 'status': 'selectionne', 'original_bytes': old, 'output_bytes': new,
            'original_sha256': entry['original_sha256'], 'output_sha256': entry[field + '_sha256'],
            'backup_local': str(original.resolve()), 'output_local': str(output.resolve())}


def save(path, report):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description='Actualise la sélection locale. AUCUNE écriture sur PythonAnywhere.')
    parser.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads')
    args = parser.parse_args()
    jobs = []
    for kind, name, filename in [('pdf', 'LR-PDF', 'rapport.json'), ('image', 'LR-IMAGES', 'rapport-images.json')]:
        folder = args.downloads / name
        report = json.loads((folder / filename).read_text(encoding='utf-8-sig'))
        if report.get('site', '').rstrip('/') != SITE:
            raise ValueError('Rapport d’un autre site : ' + str(folder))
        for key, entry in report['files'].items():
            safe_path(key)
            jobs.append((folder, kind, key, entry))
    current = catalogue()
    output = args.downloads / ('LR-SELECTION-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    output.mkdir(exist_ok=False)
    report_path = output / 'selection-remplacement.json'
    report = {'site': SITE, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'complete': False, 'new_catalogue_paths': sorted(current - {j[2] for j in jobs}), 'files': {}}
    print('Lecture seulement du serveur. Sauvegardes et anciens rapports conservés.', flush=True)
    for index, (folder, kind, key, entry) in enumerate(jobs, 1):
        try:
            row = (check_entry(folder, kind, key, entry) if key in current else
                   {'path': key, 'kind': kind, 'status': 'retire_du_catalogue'})
        except (OSError, ValueError) as error:
            row = {'path': key, 'kind': kind, 'status': 'erreur', 'detail': str(error)}
        report['files'][key] = row
        print(f'[{index}/{len(jobs)}] {row["status"]} : {key}', flush=True)
        if index % 20 == 0:
            save(report_path, report)
    report['complete'] = True
    save(report_path, report)
    print('\nBilan :', dict(Counter(r['status'] for r in report['files'].values())))
    print('Nouveaux chemins du catalogue, non traités :', len(report['new_catalogue_paths']))
    print('Rapport à transmettre :', report_path)
    print('AUCUN fichier serveur remplacé ou supprimé. Aucun original local effacé.')
    return 1 if any(r['status'] == 'erreur' for r in report['files'].values()) else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print('ARRÊT :', error)
        raise SystemExit(1)
