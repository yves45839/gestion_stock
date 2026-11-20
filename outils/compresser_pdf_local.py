"""Back up and compress product PDFs on a local computer. Never uploads/deletes server files."""
import argparse
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid


PREFIX = '/media/products/datasheets/'


class SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        old, new = urlsplit(request.full_url), urlsplit(urljoin(request.full_url, new_url))
        if new.hostname != old.hostname or new.port != old.port or new.scheme != old.scheme:
            raise ValueError('Redirection hors du site ou changement de protocole refusé.')
        return super().redirect_request(request, fp, code, message, headers, new_url)


def open_url(request, timeout):
    return build_opener(SameHostRedirect()).open(request, timeout=timeout)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def media_path(url, site):
    """Only download PDFs from this site's media; reject unsafe Windows paths."""
    base = urlsplit(site)
    parsed = urlsplit(urljoin(site, url))
    if parsed.hostname != base.hostname or parsed.port != base.port:
        raise ValueError('Le PDF pointe vers un autre serveur : téléchargement refusé.')
    if parsed.username or parsed.password or parsed.scheme not in ('http', 'https'):
        raise ValueError('Adresse de PDF non prise en charge.')
    decoded = unquote(parsed.path)
    if not decoded.startswith(PREFIX) or not decoded.lower().endswith('.pdf'):
        raise ValueError('Le PDF doit se trouver dans media/products/datasheets.')
    relative = decoded[len('/media/'):]
    parts = relative.split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    for part in parts:
        if part in ('', '.', '..') or re.search(r'[<>:"\\|?*\x00-\x1f]', part) or part.endswith((' ', '.')) or part.split('.')[0].upper() in reserved:
            raise ValueError('Nom de fichier incompatible avec Windows : ' + relative)
    # Keep the authenticated site's scheme, and never follow an API's external URL.
    clean_url = urlunsplit((base.scheme, base.netloc, quote(decoded, safe='/'), parsed.query, ''))
    return PurePosixPath(relative), clean_url


def fetch_catalog(site):
    request = Request(site.rstrip('/') + '/api/products/', headers={'User-Agent': 'LR-Stockage/1.0'})
    with open_url(request, timeout=120) as response:
        if urlsplit(response.url).hostname != urlsplit(site).hostname:
            raise ValueError('Le catalogue redirige vers un autre serveur.')
        data = json.load(response)
    rows = data.get('results') if isinstance(data, dict) else None
    if not isinstance(rows, list) or data.get('count', len(rows)) != len(rows):
        raise ValueError('Le catalogue ne fournit pas la liste complète attendue. Aucun fichier serveur modifié.')
    files = {}
    folded = {}
    for row in rows:
        url = row.get('datasheet_pdf_url')
        if not url:
            continue
        path, url = media_path(url, site)
        key = str(path)
        if key.casefold() in folded and folded[key.casefold()] != key:
            raise ValueError('Deux noms ne diffèrent que par la casse : ' + key)
        folded[key.casefold()] = key
        files[key] = url
    return files


def download(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.part-' + uuid.uuid4().hex)
    try:
        request = Request(url, headers={'User-Agent': 'LR-Stockage/1.0'})
        with open_url(request, timeout=180) as source, temporary.open('xb') as output:
            if urlsplit(source.url).hostname != urlsplit(url).hostname:
                raise ValueError('Redirection inattendue du PDF.')
            shutil.copyfileobj(source, output, length=1024 * 1024)
        with temporary.open('rb') as stream:
            if b'%PDF-' not in stream.read(1024):
                raise ValueError('Le téléchargement ne contient pas un PDF (connexion ou erreur serveur possible).')
        if target.exists():
            raise ValueError('Une sauvegarde existe déjà ; elle ne sera pas écrasée.')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def ghostscript():
    found = shutil.which('gswin64c') or shutil.which('gs')
    if found:
        return found
    root = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'gs'
    found = list(root.glob('*/bin/gswin64c.exe'))
    if not found:
        raise ValueError('Ghostscript introuvable. Installez la version Windows 64 bits déjà utilisée pour votre essai.')
    return str(max(found, key=lambda p: tuple(int(n) for n in re.findall(r'\d+', p.parent.parent.name))))


def run_gs(executable, arguments):
    result = subprocess.run([executable, '-dSAFER', '-dBATCH', '-dNOPAUSE', *arguments],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
    output = result.stdout + result.stderr
    if result.returncode or re.search(r'\b(?:Error:|ERROR:|Unrecoverable error|ioerror)', output):
        raise ValueError('Ghostscript a signalé une erreur ; original conservé.')
    pages = re.search(r'Processing pages\s+(\d+)\s+through\s+(\d+)', output)
    if not pages:
        raise ValueError('Nombre de pages non vérifiable ; original conservé.')
    return int(pages.group(2)) - int(pages.group(1)) + 1


def compress(executable, source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / ('travail-' + uuid.uuid4().hex + '.pdf')
    try:
        original_pages = run_gs(executable, ['-sDEVICE=pdfwrite', '-dPDFSETTINGS=/ebook', '-sOutputFile=' + str(temporary), '-f', str(source)])
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise ValueError('PDF de sortie vide ; original conservé.')
        compressed_pages = run_gs(executable, ['-sDEVICE=nullpage', '-f', str(temporary)])
        if original_pages != compressed_pages:
            raise ValueError('Le nombre de pages a changé ; original conservé.')
        if temporary.stat().st_size >= source.stat().st_size:
            return None
        if destination.exists():
            raise ValueError('Une version compressée existe déjà ; aucun écrasement.')
        temporary.replace(destination)
        return original_pages
    finally:
        temporary.unlink(missing_ok=True)


def save_report(folder, report):
    temporary = folder / ('rapport-' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(folder / 'rapport.json')
    with (folder / 'rapport.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream, delimiter=';')
        writer.writerow(['Fichier', 'Original Mo', 'Compressé Mo', 'Gain Mo', 'Pages', 'État'])
        for row in report['files'].values():
            old = row.get('original_bytes', 0)
            new = row.get('compressed_bytes', old)
            writer.writerow([row['path'], round(old / 1024**2, 2), round(new / 1024**2, 2), round((old-new)/1024**2, 2), row.get('pages', ''), row.get('status', '')])


def main():
    parser = argparse.ArgumentParser(description='Sauvegarde et compression LOCALES. Aucun envoi ni suppression sur le serveur.')
    parser.add_argument('--site', default='https://samr.pythonanywhere.com')
    parser.add_argument('--output', type=Path, help='Dossier local ; réutiliser le même dossier pour reprendre un traitement interrompu.')
    parser.add_argument('--limit', type=int, default=0, help='Limiter le premier essai à N PDF ; 0 = tous les PDF actuels du catalogue.')
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('--limit doit être positif ou nul.')
    if urlsplit(args.site).scheme != 'https':
        parser.error('Utilisez une adresse https:// pour le serveur.')
    executable = ghostscript()
    folder = (args.output or Path.home() / 'Downloads' / ('LR-PDF-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    report_path = folder / 'rapport.json'
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {'site': args.site, 'setting': 'ebook', 'files': {}}
    if report.get('site') != args.site or report.get('setting') != 'ebook':
        raise ValueError('Ce dossier contient un rapport d’un autre site ou réglage.')
    print('Dossier LOCAL :', folder, flush=True)
    print('Lecture du catalogue ; aucun fichier serveur ne sera modifié.', flush=True)
    catalog = fetch_catalog(args.site)
    if not catalog:
        raise ValueError('Aucun PDF accessible trouvé dans le catalogue ; aucun traitement effectué.')
    jobs = list(catalog.items())[:args.limit or None]
    print(f'{len(jobs)} PDF uniques à traiter, un à la fois.', flush=True)
    errors = 0
    for index, (key, url) in enumerate(jobs, 1):
        source = folder / 'originaux' / Path(key)
        destination = folder / 'compresses' / Path(key)
        entry = report['files'].get(key, {'path': key, 'url': url})
        try:
            print(f'[{index}/{len(jobs)}] {key}', flush=True)
            if source.exists():
                if not entry.get('original_sha256') or sha256(source) != entry['original_sha256']:
                    raise ValueError('Sauvegarde existante non vérifiée ou modifiée ; aucun écrasement.')
            else:
                download(url, source)
                entry.update(original_sha256=sha256(source), original_bytes=source.stat().st_size)
                report['files'][key] = entry
                save_report(folder, report)
            if destination.exists():
                if not entry.get('compressed_sha256') or sha256(destination) != entry['compressed_sha256']:
                    raise ValueError('Version compressée existante non vérifiée ou modifiée.')
            else:
                pages = compress(executable, source, destination)
                if pages:
                    entry.update(compressed_sha256=sha256(destination), compressed_bytes=destination.stat().st_size, pages=pages, status='compressé et vérifié')
                else:
                    entry.update(compressed_bytes=source.stat().st_size, status='pas de gain — conserver original')
            print('  ' + entry['status'], flush=True)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            errors += 1
            entry['status'] = 'ERREUR : ' + str(error)
            print('  ' + entry['status'], flush=True)
        report['files'][key] = entry
        save_report(folder, report)
        time.sleep(0.1)
    originals = sum(row.get('original_bytes', 0) for row in report['files'].values())
    gain = sum(max(0, row.get('original_bytes', 0)-row.get('compressed_bytes', row.get('original_bytes', 0)))
               for row in report['files'].values() if not row.get('status', '').startswith('ERREUR'))
    print(f'\nOriginaux sauvegardés : {originals / 1024**2:.1f} Mo. Gain POTENTIEL : {gain / 1024**2:.1f} Mo.')
    print(f'Erreurs pendant cet essai : {errors}. Rapport : {folder / "rapport.csv"}')
    print('AUCUN fichier serveur remplacé ou supprimé. Gardez le dossier originaux et le rapport.')
    return 1 if errors else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Arrêt : ' + str(error), file=sys.stderr)
        sys.exit(1)
