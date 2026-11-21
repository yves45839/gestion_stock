"""Build LOCAL media replacement batches. Never writes to PythonAnywhere."""
import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import inspect
import json
from pathlib import Path
import re
import shlex
import uuid
import zipfile

SITE = 'https://samr.pythonanywhere.com'
LIMIT = 1024 * 1024  # Strictly smaller than one MiB.
BATCH_LIMIT = 20 * LIMIT


def retire_server(root, rows, apply=False):
    """Standalone server routine: preflight the entire backed-up removal list."""
    import hashlib
    from pathlib import Path
    import stat

    root = Path(root).resolve()
    checked = []
    for row in rows:
        if row.get('output_bytes', 0) < 1024 * 1024:
            raise ValueError('Retrait interdit : version locale inférieure à 1 Mo.')
        relative = Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.parts[:2] not in (
                ('products', 'datasheets'), ('products', 'images')):
            raise ValueError('Chemin interdit.')
        path = root / relative
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('Lien symbolique refusé : ' + str(path))
        if not path.exists():
            print('Déjà absent, conservé absent :', relative)
            continue
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Fichier non régulier : ' + str(path))
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        after = path.stat()
        identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if (identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or
                info.st_size != row['original_bytes'] or digest.hexdigest() != row['original_sha256']):
            raise ValueError('Contenu serveur modifié ; aucun retrait effectué : ' + str(path))
        checked.append((path, row, identity, info))
    # One invalid entry prevents the entire removal phase, before any unlink.
    estimated = 0
    inodes = {}
    for path, row, identity, info in checked:
        item = inodes.setdefault((info.st_dev, info.st_ino), [info, 0])
        item[1] += 1
    for info, count in inodes.values():
        if count == info.st_nlink:
            estimated += info.st_blocks * 512
    print('Retraits sauvegardés :', len(checked), '; blocs récupérables estimés : %.2f Mo' % (estimated / 1024**2))
    freed = 0
    for path, row, identity, info in checked:
        print(('RETRAIT :' if apply else 'SIMULATION :'), path.relative_to(root))
        if apply:
            current = path.stat()
            if identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
                raise ValueError('Fichier changé pendant le traitement ; arrêt.')
            # Last-link accounting avoids double-counting hardlinked PDFs.
            last_link = current.st_nlink == 1
            path.unlink()
            if last_link:
                freed += current.st_blocks * 512
    print(('Blocs libérés : %.2f Mo' % (freed / 1024**2)) if apply else 'Aucun fichier serveur modifié.')
    return freed if apply else estimated


def removal_command(rows, apply=False):
    source = inspect.getsource(retire_server) + '\nretire_server(' + repr('/home/samr/gestion_stock/media') + ', ' + repr(rows) + ', apply=' + repr(apply) + ')\n'
    return "printf '%s\\n' " + shlex.quote(source) + ' | PYTHONDONTWRITEBYTECODE=1 /home/samr/gestion_stock/venv/bin/python -\n'


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
    removals = []
    kept = 0
    raw_reports = {}
    excluded = {'non_selectionnes': 0, 'au_moins_1_mo': 0}
    for key, row in selection['files'].items():
        # Old audits did not check unchanged files. Recover their backup metadata,
        # but never infer existence on the server: the removal preflight checks it.
        if row.get('status') == 'sans_version_compressee':
            kind = row['kind']
            relative = relative_path(key, kind)
            folder = downloads / ('LR-PDF' if kind == 'pdf' else 'LR-IMAGES')
            filename = 'rapport.json' if kind == 'pdf' else 'rapport-images.json'
            if kind not in raw_reports:
                raw_reports[kind] = json.loads((folder / filename).read_text(encoding='utf-8-sig'))
                if raw_reports[kind].get('site', '').rstrip('/') != SITE:
                    raise ValueError('Rapport local d’un autre site.')
            entry = raw_reports[kind]['files'].get(key, {})
            field = 'compressed' if kind == 'pdf' else 'optimized'
            if entry.get('original_sha256') and not entry.get(field + '_sha256') and not entry.get('status', '').startswith('ERREUR'):
                original = verified_file(folder / 'originaux', relative, entry['original_sha256'], entry['original_bytes'])
                if original.stat().st_size >= LIMIT:
                    removals.append({'path': key, 'kind': kind, 'original_bytes': entry['original_bytes'],
                                     'original_sha256': entry['original_sha256'], 'output_bytes': entry['original_bytes'],
                                     'output_sha256': entry['original_sha256'], 'variant': 'original_sans_gain',
                                     'server_existence_unverified': True})
                    excluded['au_moins_1_mo'] += 1
                else:
                    kept += 1
                continue
        if row.get('status') != 'selectionne':
            excluded['non_selectionnes'] += 1
            continue
        if row['path'] != key:
            raise ValueError('Chemin incohérent dans la sélection.')
        kind = row['kind']
        relative = relative_path(key, kind)
        if not 0 < row['output_bytes'] < row['original_bytes']:
            raise ValueError('Taille invalide ou absence de gain : ' + key)
        folder = downloads / ('LR-PDF' if kind == 'pdf' else 'LR-IMAGES')
        # Do not trust absolute PC paths supplied by a report.
        verified_file(folder / 'originaux', relative, row['original_sha256'], row['original_bytes'])
        source = verified_file(folder / ('compresses' if kind == 'pdf' else 'optimisees'), relative,
                               row['output_sha256'], row['output_bytes'])
        if row['output_bytes'] >= LIMIT:
            removals.append({k: row[k] for k in ('path', 'kind', 'original_bytes', 'original_sha256', 'output_bytes', 'output_sha256')})
            excluded['au_moins_1_mo'] += 1
            continue
        records.append({'path': key, 'kind': kind, 'original_sha256': row['original_sha256'],
                        'original_bytes': row['original_bytes'], 'output_sha256': row['output_sha256'],
                        'output_bytes': row['output_bytes'], '_source': source})
    if not records and not removals:
        raise ValueError('Aucun fichier admissible.')
    # Identical originals use one verified compressed version, keeping every filename.
    groups = defaultdict(list)
    for row in records:
        groups[(row['kind'], row['original_sha256'])].append(row)
    output = downloads / ('LR-REMPLACEMENT-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    output.mkdir(exist_ok=False)
    manifest = {'schema': 2, 'site': SITE, 'complete': False, 'server_modified': False,
                'selection_checked_at': selection.get('checked_at'), 'strict_max_bytes': LIMIT,
                'requires_server_recheck': True, 'excluded': excluded, 'batches': [],
                'large_file_policy': 'remove_backed_up_server_original', 'removals': removals,
                'unchanged_small_kept': kept}
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
        for apply in (False, True):
            name = 'commande-retraits-' + ('appliquer' if apply else 'simulation') + '.txt'
            (output / name).write_text(removal_command(removals, apply), encoding='utf-8')
        manifest['complete'] = True
        (output / 'NE_PAS_ENVOYER_ENCORE.txt').write_text(
            'Lots préparés localement : aucun fichier serveur remplacé.\n'
            'Ne pas extraire les blobs directement dans media. Ils nécessitent un installateur.\n'
            'Les versions locales >= 1 Mo restent sur le PC ; leurs originaux serveur figurent dans removals.\n'
            'Les retraits nécessitent les commandes dédiées, avec vérification serveur.\n'
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
    print('Originaux serveur à retirer après vérification :', len(manifest['removals']))
    print('Fichiers locaux sans gain < 1 Mo à laisser en place si présents :', manifest['unchanged_small_kept'])
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
