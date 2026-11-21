"""Prepare an offline, journaled media installation for the PythonAnywhere console."""
import argparse
import base64
import hashlib
import inspect
import json
from pathlib import Path
import re
import shlex
import subprocess
import zipfile
import zlib


def server_operation(spec, mode, home='/home/samr', media='/home/samr/gestion_stock/media'):
    import hashlib
    import io
    import json
    import math
    import os
    from pathlib import Path
    import stat
    import subprocess
    import uuid
    import zipfile

    home, media = Path(home).resolve(), Path(media).resolve()
    rows, batches = spec['rows'], spec['batches']
    plan_id = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    journal = home / '.lr-remplacement' / plan_id[:16] / 'etat.json'
    paths = {r[0] for r in rows}
    if len(paths) != len(rows):
        raise ValueError('Chemins dupliqués.')

    def target(key):
        parts = key.split('/')
        if len(parts) < 3 or parts[:2] not in (['products', 'images'], ['products', 'datasheets']) or any(p in ('', '.', '..') or '\\' in p for p in parts):
            raise ValueError('Chemin interdit.')
        path = media.joinpath(*parts)
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('Lien symbolique refusé : ' + key)
        return path

    def identity(path):
        info = path.stat()
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

    def matches(path, digest, size):
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            return False
        before = identity(path)
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1048576), b''):
                h.update(block)
        return before == identity(path) and h.hexdigest() == digest

    def sync_dir(path):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def save(state):
        journal.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if journal.parent.resolve() != journal.parent or journal.is_symlink():
            raise ValueError('Journal redirigé.')
        temp = journal.with_name('etat-' + uuid.uuid4().hex + '.tmp')
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(state, stream, ensure_ascii=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, journal)
            sync_dir(journal.parent)
        finally:
            temp.unlink(missing_ok=True)

    def usage():
        result = subprocess.run(['du', '-s', '-B1', str(home)], capture_output=True, text=True, check=True)
        return int(result.stdout.split()[0])

    if journal.is_symlink() or journal.parent.resolve() != journal.parent:
        raise ValueError('Journal redirigé.')
    state = json.loads(journal.read_text()) if journal.exists() else None
    if state is not None:
        if (state.get('plan') != plan_id or not set(state.get('released', [])).issubset(paths) or
                not set(state.get('intent', [])).issubset(paths) or not set(state.get('installed', [])).issubset(paths)):
            raise ValueError('Journal incompatible.')

    if mode in ('controle', 'liberer', 'reprendre'):
        if state and state.get('release_complete'):
            print('Libération déjà terminée. Aucun original supplémentaire retiré.')
            return state
        if state and mode == 'liberer':
            raise ValueError('Libération interrompue : utiliser la commande reprendre.')
        if mode == 'reprendre' and state is None:
            raise ValueError('Aucun journal : ne pas relancer. Conserver les sauvegardes et transmettre le résultat pour récupération.')
        allowed_missing = set(state['released'] + state['intent']) if state else set()
        present, absent = {}, set()
        inodes = {}
        allocation = 0
        for row in rows:
            key, old_hash, old_size, new_hash, new_size, batch_index = row
            path = target(key)
            if not path.exists():
                if key not in allowed_missing:
                    raise ValueError('Fichier désormais absent, à exclure avant toute opération : ' + key)
                absent.add(key)
                continue
            if not matches(path, old_hash, old_size):
                raise ValueError('Original modifié, aucun nouveau retrait : ' + key)
            info = path.stat()
            present[key] = (path, identity(path))
            entry = inodes.setdefault((info.st_dev, info.st_ino), [info, 0])
            entry[1] += 1
            allocation = math.gcd(allocation, info.st_blocks * 512)
        allocation = max(4096, allocation)
        freed = sum(info.st_blocks * 512 for info, count in inodes.values() if info.st_nlink == count)
        payloads = {r[3]: r[4] for r in rows}
        rounded = lambda size: ((size + allocation - 1) // allocation) * allocation
        new_blocks = sum(rounded(size) for size in payloads.values())
        used = usage()
        staging = sum(rounded(b[2]) for b in batches)
        peak = staging
        uploaded = 0
        # Sequential browser uploads may temporarily hold both the received
        # upload and its destination. Include that possible extra disk copy.
        for batch in batches:
            peak = max(peak, uploaded + 2 * rounded(batch[2]))
            uploaded += rounded(batch[2])
        installed_hashes = set()
        for index, batch in enumerate(batches):
            staging -= rounded(batch[2])
            for row in rows:
                if row[5] == index and row[3] not in installed_hashes:
                    installed_hashes.add(row[3])
                    staging += rounded(row[4])
            peak = max(peak, staging)
        print('Plan :', plan_id[:16])
        print('Originaux conformes encore présents :', len(present))
        print('Blocs récupérables : %.2f Mo' % (freed / 1048576))
        print('Occupation finale estimée : %.2f Mo' % ((used - freed + new_blocks) / 1048576))
        print('Pic estimé pendant installation, hors réserve : %.2f Mo' % ((used - freed + peak) / 1048576))
        if (used - freed + new_blocks + 8 * 1048576 > 512 * 1048576 or
                used - freed + peak + 2 * 1048576 > 512 * 1048576):
            raise ValueError('Marge de transfert insuffisante : aucun nouveau retrait.')
        if mode == 'controle':
            print('CONTROLE : aucun fichier modifié.')
            return None
        sync_dir(home)
        sync_dir(media)
        print('Début maintenance : les médias concernés seront absents jusqu’à installation.', flush=True)
        if state is None:
            state = {'plan': plan_id, 'released': [], 'intent': [], 'installed': [], 'completed_batches': [], 'release_complete': False}
        else:
            state['released'] = sorted(absent)
            state['intent'] = []
            save(state)
        # Above quota a journal cannot be written. First remove only fully
        # verified, PC-backed originals until there is room for a durable log.
        durable = journal.exists()
        if not durable and used < 492 * 1048576:
            save(state)
            durable = True
        reclaimed = 0
        for index, batch in enumerate(batches):
            group = [r for r in rows if r[5] == index and r[0] not in absent]
            if durable:
                state['intent'] = [r[0] for r in group]
                save(state)
            for row in group:
                key = row[0]
                path, expected_identity = present[key]
                if path.is_symlink() or path.resolve() != path or identity(path) != expected_identity:
                    raise ValueError('Original changé pendant libération ; arrêt : ' + key)
                info = path.stat()
                path.unlink()
                if info.st_nlink == 1:
                    reclaimed += info.st_blocks * 512
                state['released'].append(key)
                if not durable:
                    print('AMORCAGE RETIRE :', key, flush=True)
                    if used - reclaimed < 492 * 1048576:
                        state['intent'] = [r[0] for r in group if r[0] not in state['released']]
                        save(state)
                        durable = True
            if durable:
                state['intent'] = []
                save(state)
        state['release_complete'] = True
        state['intent'] = []
        save(state)
        print('Libération terminée :', len(state['released']), 'originaux. Journal :', journal)
        print('Occupation actuelle : %.2f Mo' % (usage() / 1048576))
        print('Les ZIP peuvent maintenant être envoyés UN PAR UN, dans l’ordre, dans', home)
        return state

    if mode != 'installer' or not state or not state.get('release_complete'):
        raise ValueError('La libération doit être terminée et journalisée avant installation.')
    installed = set(state['installed'])
    released = set(state['released'])
    if released != paths:
        raise ValueError('Liste des retraits incomplète.')

    def archive_bytes(index):
        name, expected_hash, size = batches[index]
        path = home / name
        if path.is_symlink() or path.resolve() != path or not path.is_file():
            raise ValueError('Téléverser ou retéléverser ce lot depuis le PC : ' + name)
        before = identity(path)
        data = path.read_bytes()
        if before != identity(path) or len(data) != size or hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError('Lot modifié ou incomplet : ' + name)
        wanted = {r[3]: r[4] for r in rows if r[5] == index}
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != {'blobs/' + h for h in wanted}:
                raise ValueError('Contenu de lot inattendu.')
            for digest, expected_size in wanted.items():
                info = archive.getinfo('blobs/' + digest)
                if not 0 < info.file_size == expected_size < 1048576:
                    raise ValueError('Taille de contenu invalide.')
                content = archive.read(info)
                if hashlib.sha256(content).hexdigest() != digest:
                    raise ValueError('Empreinte de contenu invalide.')
        return path, data

    # Check every required archive and destination before consuming any ZIP.
    pending = [i for i in range(len(batches)) if i not in state['completed_batches']]
    for index in pending:
        archive_bytes(index)
    for row in rows:
        if row[0] not in installed:
            path = target(row[0])
            if path.exists() and not matches(path, row[3], row[4]):
                raise ValueError('Destination réapparue ou modifiée : ' + row[0])
    for index in pending:
        archive_path, data = archive_bytes(index)
        state['installing_batch'] = index
        save(state)
        # Hold the validated ZIP in memory and remove its disk copy before
        # writing payloads, avoiding a second full copy under the quota.
        archive_path.unlink()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            groups = {}
            for row in rows:
                if row[5] == index and row[0] not in installed:
                    groups.setdefault(row[3], []).append(row)
            for digest, group in groups.items():
                content = archive.read('blobs/' + digest)
                temp = None
                try:
                    # Reuse a validated existing payload after interruption, or
                    # across batches. Never write through a hardlinked inode.
                    source = None
                    for candidate in rows:
                        if candidate[3] == digest:
                            candidate_path = target(candidate[0])
                            if candidate_path.exists() and matches(candidate_path, digest, group[0][4]):
                                source = candidate_path
                                break
                    if source is None:
                        first = target(group[0][0])
                        first.parent.mkdir(parents=True, exist_ok=True)
                        temp = first.parent / ('.lr-install-' + uuid.uuid4().hex)
                        with temp.open('xb') as stream:
                            stream.write(content)
                            stream.flush()
                            os.fsync(stream.fileno())
                        source = temp
                    for row in group:
                        path = target(row[0])
                        path.parent.mkdir(parents=True, exist_ok=True)
                        if path.exists():
                            if not matches(path, digest, row[4]):
                                raise ValueError('Destination modifiée pendant installation : ' + row[0])
                        else:
                            os.link(source, path)  # No overwrite and no truncation of existing hardlinks.
                    if temp is not None:
                        temp.unlink()
                        temp = None
                    for parent in {target(r[0]).parent for r in group}:
                        sync_dir(parent)
                    installed.update(r[0] for r in group)
                    state['installed'] = sorted(installed)
                    save(state)
                finally:
                    if temp is not None:
                        temp.unlink(missing_ok=True)
        state['completed_batches'].append(index)
        state.pop('installing_batch', None)
        save(state)
        print('Installé :', batches[index][0], '; fichiers suivis :', len(installed), flush=True)
    bad = [r[0] for r in rows if not matches(target(r[0]), r[3], r[4])]
    print('Vérification finale :', len(rows) - len(bad), 'fichiers conformes ; absents ou modifiés :', len(bad))
    for key in bad:
        print('À vérifier sans restauration automatique :', key)
    state['complete'] = not bad
    save(state)
    print('Installation terminée :', len(installed), 'fichiers suivis.')
    print('Occupation actuelle : %.2f Mo' % (usage() / 1048576))
    print('Sauvegardes PC conservées ; base de données inchangée.')
    return state


def prepare(manifest_path, downloads):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if (manifest.get('schema') != 2 or manifest.get('complete') is not True or
            manifest.get('site') != 'https://samr.pythonanywhere.com' or
            manifest.get('large_file_policy') != 'remove_backed_up_server_original'):
        raise ValueError('Manifeste incompatible.')
    spec = {'rows': [], 'batches': []}
    seen = set()
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    for index, batch in enumerate(manifest['batches']):
        if not re.fullmatch(r'lot-\d{3}\.zip', batch['archive']):
            raise ValueError('Nom de lot interdit.')
        path = manifest_path.parent / batch['archive']
        raw = path.read_bytes()
        if len(raw) != batch['archive_bytes'] or hashlib.sha256(raw).hexdigest() != batch['archive_sha256']:
            raise ValueError('Lot local absent ou modifié : ' + path.name)
        spec['batches'].append([batch['archive'], batch['archive_sha256'], batch['archive_bytes']])
        with zipfile.ZipFile(path) as archive:
            wanted = {}
            for row in batch['files']:
                key = row['path']
                parts = key.split('/')
                if (key.casefold() in seen or len(parts) < 3 or row.get('kind') not in ('pdf', 'image') or
                        parts[:2] != ['products', 'datasheets' if row['kind'] == 'pdf' else 'images'] or
                        any(p in ('', '.', '..') or re.search(r'[<>:"\\|?*\x00-\x1f]', p) or
                            p.endswith((' ', '.')) or p.split('.')[0].upper() in reserved for p in parts)):
                    raise ValueError('Chemin invalide ou dupliqué.')
                seen.add(key.casefold())
                if not 0 < row['output_bytes'] < min(1048576, row['original_bytes']):
                    raise ValueError('Seuil de taille invalide.')
                if any(not re.fullmatch('[0-9a-f]{64}', row[field]) for field in ('original_sha256', 'output_sha256')):
                    raise ValueError('Empreinte invalide.')
                folder = downloads / ('LR-PDF' if row['kind'] == 'pdf' else 'LR-IMAGES') / 'originaux'
                original = folder.joinpath(*parts)
                if original.is_symlink() or not original.resolve().is_relative_to(folder.resolve()) or not original.is_file() or original.stat().st_size != row['original_bytes']:
                    raise ValueError('Sauvegarde originale absente ou modifiée : ' + key)
                h = hashlib.sha256()
                with original.open('rb') as stream:
                    for block in iter(lambda: stream.read(1048576), b''):
                        h.update(block)
                if h.hexdigest() != row['original_sha256']:
                    raise ValueError('Sauvegarde originale modifiée : ' + key)
                if row['output_sha256'] in wanted and wanted[row['output_sha256']] != row['output_bytes']:
                    raise ValueError('Tailles incohérentes pour un contenu compressé.')
                wanted[row['output_sha256']] = row['output_bytes']
                spec['rows'].append([key, row['original_sha256'], row['original_bytes'], row['output_sha256'], row['output_bytes'], index])
            if len(archive.namelist()) != len(wanted) or set(archive.namelist()) != {'blobs/' + h for h in wanted}:
                raise ValueError('Contenu du lot inattendu.')
            for digest, size in wanted.items():
                if archive.getinfo('blobs/' + digest).file_size != size or hashlib.sha256(archive.read('blobs/' + digest)).hexdigest() != digest:
                    raise ValueError('Contenu compressé invalide.')
    if not spec['rows']:
        raise ValueError('Aucun fichier à installer.')
    packed = base64.b64encode(zlib.compress(json.dumps(spec, separators=(',', ':')).encode(), 9)).decode()
    literals = '\n'.join(repr(packed[i:i+900]) for i in range(0, len(packed), 900))
    output = manifest_path.parent / 'installation'
    output.mkdir(exist_ok=True)
    for mode in ('controle', 'liberer', 'reprendre', 'installer'):
        source = inspect.getsource(server_operation) + '\nimport base64,json,zlib\nserver_operation(json.loads(zlib.decompress(base64.b64decode(\n' + literals + '\n))),' + repr(mode) + ')\n'
        if len(source.encode()) > 120000:
            raise ValueError('Commande trop grande.')
        command = "printf '%s\\n' " + shlex.quote(source) + ' | PYTHONDONTWRITEBYTECODE=1 /home/samr/gestion_stock/venv/bin/python -\n'
        (output / ('commande-' + mode + '.txt')).write_text(command, encoding='utf-8')
    return output


def main():
    parser = argparse.ArgumentParser(description='Prépare une installation journalisée des lots vérifiés. Aucun accès serveur depuis le PC.')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads')
    parser.add_argument('--copier', choices=['controle', 'liberer', 'reprendre', 'installer'], default='controle')
    args = parser.parse_args()
    if args.manifest is None:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        chosen = filedialog.askopenfilename(title='Choisir manifest-remplacement.json avec les neuf ZIP', filetypes=[('Manifeste', '*.json')])
        root.destroy()
        if not chosen:
            return 0
        args.manifest = Path(chosen)
    output = prepare(args.manifest, args.downloads)
    file = output / ('commande-' + args.copier + '.txt')
    packed = base64.b64encode(file.read_bytes()).decode('ascii')
    subprocess.run(['powershell', '-NoProfile', '-Command', '[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd())) | Set-Clipboard'], input=packed, text=True, check=True)
    print('Sauvegardes originales et ZIP revérifiés. Commandes dans :', output)
    print('Commande copiée :', args.copier)
    print('Le PC ne se connecte pas au serveur. Coller la commande dans Bash PythonAnywhere.')
    print('Maintenance nécessaire : images et PDF temporairement absents entre libération et installation.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print('ARRÊT :', error)
        raise SystemExit(1)
