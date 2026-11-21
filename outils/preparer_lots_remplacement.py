"""Build LOCAL media replacement batches. Never writes to PythonAnywhere."""
import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import uuid
import zipfile

SITE = 'https://samr.pythonanywhere.com'
LIMIT = 1024 * 1024  # Strictly smaller than one MiB.
BATCH_LIMIT = 20 * LIMIT


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(LIMIT), b''):
            h.update(block)
    return h.hexdigest()


def relative_path(key, kind):
    parts = key.split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    folder = {'pdf': 'datasheets', 'image': 'images'}.get(kind)
    if (len(parts) < 3 or parts[:2] != ['products', folder] or
            any(p in ('', '.', '..') or re.search(r'[<>:"\\|?*\x00-\x1f]', p) or
                p.endswith((' ', '.')) or p.split('.')[0].upper() in reserved for p in parts)):
        raise ValueError('Chemin refusé : ' + key)
    return Path(*parts)


def verified_file(root, relative, expected_hash, expected_bytes):
    if not re.fullmatch('[0-9a-f]{64}', expected_hash):
        raise ValueError('Empreinte invalide.')
    file = root / relative
    if (file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(root.resolve()) or
            file.stat().st_size != expected_bytes or digest(file) != expected_hash):
        raise ValueError('Fichier local absent ou modifié : ' + str(file))
    return file


def build(selection_path, downloads):
    selection = json.loads(selection_path.read_text(encoding='utf-8-sig'))
    if selection.get('site') != SITE or selection.get('complete') is not True:
        raise ValueError('Sélection incomplète ou provenant d’un autre site.')
    records = []
    excluded = {'non_selectionnes': 0, 'au_moins_1_mo': 0}
    for key, row in selection['files'].items():
        if row.get('status') != 'selectionne':
            excluded['non_selectionnes'] += 1
            continue
        if row['path'] != key:
            raise ValueError('Chemin incohérent dans la sélection.')
        kind = row['kind']
        relative = relative_path(key, kind)
        if row['output_bytes'] >= LIMIT:
            excluded['au_moins_1_mo'] += 1
            continue
        if not 0 < row['output_bytes'] < row['original_bytes']:
            raise ValueError('Taille invalide ou absence de gain : ' + key)
        folder = downloads / ('LR-PDF' if kind == 'pdf' else 'LR-IMAGES')
        # Do not trust absolute PC paths supplied by a report.
        verified_file(folder / 'originaux', relative, row['original_sha256'], row['original_bytes'])
        source = verified_file(folder / ('compresses' if kind == 'pdf' else 'optimisees'), relative,
                               row['output_sha256'], row['output_bytes'])
        records.append({'path': key, 'kind': kind, 'original_sha256': row['original_sha256'],
                        'original_bytes': row['original_bytes'], 'output_sha256': row['output_sha256'],
                        'output_bytes': row['output_bytes'], '_source': source})
    if not records:
        raise ValueError('Aucun fichier admissible.')
    # Identical originals use one verified compressed version, keeping every filename.
    groups = defaultdict(list)
    for row in records:
        groups[(row['kind'], row['original_sha256'])].append(row)
    output = downloads / ('LR-REMPLACEMENT-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    output.mkdir(exist_ok=False)
    manifest = {'schema': 1, 'site': SITE, 'complete': False, 'server_modified': False,
                'selection_checked_at': selection.get('checked_at'), 'strict_max_bytes': LIMIT,
                'requires_server_recheck': True, 'excluded': excluded, 'batches': []}
    archive = None
    archive_bytes = 0
    seen_payloads = set()
    try:
        for group in groups.values():
            best = min(group, key=lambda r: (r['output_bytes'], r['path']))
            payload_hash = best['output_sha256']
            # Grouping keeps related filenames in the same batch.
            if archive is None or archive_bytes + best['output_bytes'] > BATCH_LIMIT:
                if archive:
                    archive.close()
                name = f'lot-{len(manifest["batches"])+1:03d}.zip'
                archive = zipfile.ZipFile(output / name, 'x', compression=zipfile.ZIP_STORED)
                batch = {'archive': name, 'files': []}
                manifest['batches'].append(batch)
                archive_bytes = 0
                seen_payloads = set()
            if payload_hash not in seen_payloads:
                payload = best['_source'].read_bytes()
                if len(payload) != best['output_bytes'] or hashlib.sha256(payload).hexdigest() != payload_hash:
                    raise ValueError('Version compressée modifiée pendant la préparation.')
                archive.writestr('blobs/' + payload_hash, payload)
                archive_bytes += len(payload)
                seen_payloads.add(payload_hash)
            for row in group:
                batch['files'].append({k: v for k, v in row.items() if k not in ('_source', 'output_bytes', 'output_sha256')})
                batch['files'][-1].update(output_sha256=payload_hash, output_bytes=best['output_bytes'])
        if archive:
            archive.close()
            archive = None
        for batch in manifest['batches']:
            file = output / batch['archive']
            batch.update(archive_sha256=digest(file), archive_bytes=file.stat().st_size)
        manifest['complete'] = True
        (output / 'NE_PAS_ENVOYER_ENCORE.txt').write_text(
            'Lots préparés localement : aucun fichier serveur remplacé.\n'
            'Ne pas extraire les blobs directement dans media. Ils nécessitent un installateur.\n'
            'La taille inférieure à 1 Mo ne contourne pas le quota total PythonAnywhere.\n'
            'Avant toute installation : vérifier à nouveau existence et SHA256 sur le serveur ;\n'
            'ignorer les fichiers supprimés ou modifiés ; préserver les liens physiques.\n'
            'Conserver les originaux locaux et le manifeste.\n', encoding='utf-8')
    finally:
        if archive:
            archive.close()
        (output / 'manifest-remplacement.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return output, manifest


def main():
    parser = argparse.ArgumentParser(description='Prépare des lots LOCAUX de fichiers compressés strictement inférieurs à 1 Mo.')
    parser.add_argument('--selection', type=Path)
    parser.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads')
    args = parser.parse_args()
    if args.selection is None:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        chosen = filedialog.askopenfilename(title='Choisir selection-remplacement.json', filetypes=[('Rapport JSON', '*.json')])
        root.destroy()
        if not chosen:
            return 0
        args.selection = Path(chosen)
    output, manifest = build(args.selection, args.downloads)
    print('Dossier LOCAL :', output)
    print('Fichiers retenus :', sum(len(b['files']) for b in manifest['batches']))
    print('Exclus car version compressée >= 1 Mo :', manifest['excluded']['au_moins_1_mo'])
    print('Lots :', len(manifest['batches']))
    print('Volume à transférer : %.1f Mo' % (sum(b['archive_bytes'] for b in manifest['batches']) / LIMIT))
    print('Manifeste à transmettre :', output / 'manifest-remplacement.json')
    print('AUCUN transfert ni remplacement effectué. Le quota total reste à résoudre avant le téléversement.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print('ARRÊT :', error)
        raise SystemExit(1)
