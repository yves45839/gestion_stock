"""Back up and optimise product images on a local computer. No server writes."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import time
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid


PREFIX = '/media/products/images/'


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
    """Only download images from this site; reject unsafe Windows paths."""
    base = urlsplit(site)
    parsed = urlsplit(urljoin(site, url))
    if parsed.hostname != base.hostname or parsed.port != base.port:
        raise ValueError('L’image pointe vers un autre serveur : téléchargement refusé.')
    if parsed.username or parsed.password or parsed.scheme not in ('http', 'https'):
        raise ValueError('Adresse d’image non prise en charge.')
    decoded = unquote(parsed.path)
    if not decoded.startswith(PREFIX):
        raise ValueError('L’image doit se trouver dans media/products/images.')
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
        for field in ('image_url', 'pending_image_url'):
            url = row.get(field)
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
                raise ValueError('Redirection inattendue de l’image.')
            shutil.copyfileobj(source, output, length=1024 * 1024)
        if target.suffix.lower() == '.svg':
            import xml.etree.ElementTree as ET
            try:
                tag = ET.parse(temporary).getroot().tag
                if tag.split('}')[-1] != 'svg':
                    raise ValueError('SVG invalide.')
            except ET.ParseError as error:
                raise ValueError('SVG invalide ; téléchargement non conservé.') from error
        else:
            from PIL import Image
            try:
                with Image.open(temporary) as check:
                    check.verify()
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                raise ValueError('Le téléchargement ne contient pas une image valide.') from error
        if target.exists():
            raise ValueError('Une sauvegarde existe déjà ; elle ne sera pas écrasée.')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)



def optimise(source, destination, max_side=1600):
    from PIL import Image, ImageOps
    if source.suffix.lower() == '.svg':
        return {'status': 'SVG conservé sans modification'}
    temporary = destination.parent / ('travail-' + uuid.uuid4().hex + '.image')
    try:
        with Image.open(source) as opened:
            if getattr(opened, 'n_frames', 1) != 1:
                return {'status': 'image animée conservée sans modification'}
            image_format = opened.format
            if image_format not in ('JPEG', 'PNG', 'WEBP') or opened.mode not in ('RGB', 'RGBA', 'L', 'LA', 'P'):
                return {'status': 'format ou couleurs conservés sans modification'}
            image = ImageOps.exif_transpose(opened)
            original_size = image.size
            icc = opened.info.get('icc_profile')
            if image.mode == 'P':
                image = image.convert('RGBA' if 'transparency' in opened.info else 'RGB')
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            expected_size = image.size
            options = {}
            if icc:
                options['icc_profile'] = icc
            if image_format == 'JPEG':
                options.update(quality=85, optimize=True, progressive=True)
            elif image_format == 'PNG':
                options.update(optimize=True, compress_level=9)
            else:
                options.update(quality=85, method=6)
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(temporary, format=image_format, **options)
        with Image.open(temporary) as check:
            check.load()
            if check.format != image_format or check.size != expected_size:
                raise ValueError('Format ou dimensions de sortie invalides ; original conservé.')
        info = {'source_dimensions': list(original_size), 'output_dimensions': list(expected_size)}
        if temporary.stat().st_size >= source.stat().st_size:
            return {**info, 'status': 'pas de gain — conserver original'}
        if destination.exists():
            raise ValueError('Une version optimisée existe déjà ; aucun écrasement.')
        temporary.replace(destination)
        return {**info, 'status': 'optimisée et vérifiée'}
    finally:
        temporary.unlink(missing_ok=True)


def save_report(folder, report):
    temporary = folder / ('rapport-' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(folder / 'rapport-images.json')
    with (folder / 'rapport-images.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream, delimiter=';')
        writer.writerow(['Fichier', 'Original Mo', 'Optimisé Mo', 'Gain Mo', 'Dimensions source', 'Dimensions optimisées', 'État'])
        for row in report['files'].values():
            old = row.get('original_bytes', 0)
            new = row.get('optimized_bytes', old)
            writer.writerow([row['path'], round(old/1024**2, 2), round(new/1024**2, 2), round((old-new)/1024**2, 2),
                             row.get('source_dimensions', ''), row.get('output_dimensions', ''), row.get('status', '')])


def main():
    parser = argparse.ArgumentParser(description='Sauvegarde et optimisation LOCALES des images. Aucun remplacement sur le serveur.')
    parser.add_argument('--site', default='https://samr.pythonanywhere.com')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--max-side', type=int, default=1600)
    parser.add_argument('--limit', type=int, default=0, help='0 = toutes les images actuelles et en attente du catalogue.')
    args = parser.parse_args()
    if urlsplit(args.site).scheme != 'https':
        parser.error('Utilisez une adresse https://.')
    if args.limit < 0 or not 800 <= args.max_side <= 4000:
        parser.error('Limite positive ou nulle ; dimension entre 800 et 4000 pixels.')
    try:
        from PIL import Image
    except ImportError:
        raise ValueError('Installez Pillow sur le PC : py -3.12 -m pip install Pillow')
    folder = (args.output or Path.home()/'Downloads'/'LR-IMAGES').resolve()
    folder.mkdir(parents=True, exist_ok=True)
    report_path = folder/'rapport-images.json'
    settings = {'max_side': args.max_side, 'quality': 85, 'preserve_format': True}
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {'site': args.site, 'settings': settings, 'files': {}}
    if report.get('site') != args.site or report.get('settings') != settings:
        raise ValueError('Ce dossier contient un rapport d’un autre site ou réglage ; choisir un nouveau dossier.')
    print('Dossier LOCAL :', folder, flush=True)
    print('Lecture du catalogue ; aucun fichier serveur ne sera modifié.', flush=True)
    jobs = list(fetch_catalog(args.site).items())[:args.limit or None]
    if not jobs:
        raise ValueError('Aucune image trouvée dans le catalogue.')
    print(f'{len(jobs)} images uniques à traiter.', flush=True)
    errors = 0
    for index, (key, url) in enumerate(jobs, 1):
        source = folder/'originaux'/Path(key)
        destination = folder/'optimisees'/Path(key)
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
                if not entry.get('optimized_sha256') or sha256(destination) != entry['optimized_sha256']:
                    raise ValueError('Image optimisée existante non vérifiée ou modifiée.')
            else:
                entry.update(optimise(source, destination, args.max_side))
                if destination.exists():
                    entry.update(optimized_sha256=sha256(destination), optimized_bytes=destination.stat().st_size)
                else:
                    entry['optimized_bytes'] = source.stat().st_size
            print('  '+entry['status'], flush=True)
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            errors += 1
            entry['status'] = 'ERREUR : '+str(error)
            print('  '+entry['status'], flush=True)
        report['files'][key] = entry
        save_report(folder, report)
        time.sleep(0.1)
    originals = sum(row.get('original_bytes', 0) for row in report['files'].values())
    gain = sum(max(0, row.get('original_bytes', 0)-row.get('optimized_bytes', row.get('original_bytes', 0)))
               for row in report['files'].values() if not row.get('status', '').startswith('ERREUR'))
    print(f'\nOriginaux sauvegardés : {originals/1024**2:.1f} Mo. Gain POTENTIEL : {gain/1024**2:.1f} Mo.')
    print(f'Erreurs pendant cet essai : {errors}. Rapport : {folder/"rapport-images.csv"}')
    print('AUCUN fichier serveur remplacé ou supprimé. Gardez originaux et rapports.')
    return 1 if errors else 0


if __name__=='__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print('Arrêt : '+str(error), file=sys.stderr)
        sys.exit(1)
