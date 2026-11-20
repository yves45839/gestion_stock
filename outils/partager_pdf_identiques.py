"""Deduplicate verified PDFs with atomic hard links; no database writes.

Use only with an independently backed-up manifest fingerprint. Different file
names remain available. Future content changes must replace files atomically,
not open a shared file for in-place writing.
"""
from collections import defaultdict
import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path
import shlex
import stat
import uuid


def fingerprint(path):
    if path.is_symlink():
        raise RuntimeError('Lien symbolique refusé : ' + str(path))
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError('Fichier ordinaire attendu : ' + str(path))
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise RuntimeError('Fichier modifié pendant le contrôle : ' + str(path))
    return digest.hexdigest()


def manifest_signature(pairs):
    digest = hashlib.sha256()
    for name, checksum in sorted(pairs):
        digest.update(name.encode('utf-8') + b'\0' + checksum.encode('ascii') + b'\n')
    return digest.hexdigest()


def share_pdfs(root, names, expected, apply=False):
    root = Path(root).resolve()
    groups = defaultdict(list)
    pairs = []
    for name in sorted(set(names)):
        path = root / name
        if not name.startswith('products/datasheets/') or not path.resolve().is_relative_to(root) or path.suffix.lower() != '.pdf':
            raise RuntimeError('Chemin inattendu : ' + name)
        checksum = fingerprint(path)
        pairs.append((name, checksum))
        groups[checksum].append(path)
    if manifest_signature(pairs) != expected:
        raise RuntimeError('Les fichiers ne correspondent plus à la sauvegarde PC. Aucun remplacement effectué.')
    print(f'Sauvegarde confirmée : {len(pairs)} chemins, {len(groups)} contenus uniques.', flush=True)
    replacements = 0
    freed = 0
    skipped = 0
    remaining_links = {}
    for checksum, paths in groups.items():
        source = paths[0]
        for target in paths[1:]:
            if os.path.samefile(source, target):
                continue
            a, b = source.stat(), target.stat()
            if (a.st_dev, stat.S_IMODE(a.st_mode), a.st_uid, a.st_gid) != (b.st_dev, stat.S_IMODE(b.st_mode), b.st_uid, b.st_gid):
                skipped += 1
                continue
            if fingerprint(source) != checksum or fingerprint(target) != checksum:
                raise RuntimeError('Un PDF a changé. Arrêt ; chemins déjà traités conservés.')
            if apply:
                temporary = target.parent / ('.lien-pdf-' + uuid.uuid4().hex)
                created = False
                try:
                    os.link(source, temporary, follow_symlinks=False)
                    created = True
                    if fingerprint(target) != checksum:
                        raise RuntimeError('PDF modifié juste avant remplacement : ' + str(target))
                    # Refresh link count: replacing the last name of this inode frees its blocks.
                    b = target.stat()
                    os.replace(temporary, target)
                    if not os.path.samefile(source, target):
                        raise RuntimeError('Vérification du lien impossible : ' + str(target))
                finally:
                    if created and temporary.exists():
                        temporary.unlink()
            replacements += 1
            key = (b.st_dev, b.st_ino)
            remaining_links[key] = remaining_links.get(key, b.st_nlink) - 1
            if (apply and b.st_nlink == 1) or (not apply and remaining_links[key] == 0):
                freed += getattr(b, 'st_blocks', 0) * 512
    print(('Effectué' if apply else 'Simulation') + f' : {replacements} chemins partagés ; {skipped} ignorés (permissions ou système de fichiers différents).')
    print(('Blocs libérés' if apply else 'Gain estimé') + f' : {freed / 1024**2:.1f} Mo. Aucun nom de PDF ni enregistrement de base supprimé.')
    return replacements, freed, skipped


def prepare_command(report_path, apply=False):
    """Generate plain, reviewable Bash text on the PC; never connects to a server."""
    report_path = Path(report_path)
    report = json.loads(report_path.read_text(encoding='utf-8-sig'))
    if report.get('site', '').rstrip('/') != 'https://samr.pythonanywhere.com':
        raise ValueError('Rapport du site samr.pythonanywhere.com attendu.')
    entries = report.get('files')
    if not isinstance(entries, dict) or not entries:
        raise ValueError('Rapport vide ou invalide.')
    pairs = []
    for name, entry in entries.items():
        value = entry.get('original_sha256', '')
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Empreinte de sauvegarde invalide.')
        pairs.append((name, value))
    expected = manifest_signature(pairs)
    core = '\n\n'.join(inspect.getsource(function) for function in (fingerprint, manifest_signature, share_pdfs))
    program = (
        "from collections import defaultdict\nimport hashlib, os, stat, uuid\nfrom pathlib import Path\n"
        "from django.conf import settings\nfrom inventory.models import Product\n\n"
        + core + '\n\n'
        + "noms = [str(n) for n in Product.objects.values_list('datasheet_pdf', flat=True) if n]\n"
        + f"share_pdfs(settings.MEDIA_ROOT, noms, {expected!r}, apply={apply!r})\n"
    )
    # Bash may put a long here-document in /tmp, which is also over quota.
    # Its built-in printf feeds a pipe without creating a temporary file.
    command = ("cd /home/samr/gestion_stock &&\n"
               "printf '%s\\n' " + shlex.quote(program)
               + " | PYTHONDONTWRITEBYTECODE=1 ./venv/bin/python manage.py shell\n")
    destination = report_path.parent / ('commande-liens-appliquer.txt' if apply else 'commande-liens-simulation.txt')
    destination.write_text(command, encoding='utf-8', newline='\n')
    print('Commande préparée sur le PC :', destination)
    print('Mode : ' + ('APPLICATION (mêmes contenus et noms, stockage partagé)' if apply else 'SIMULATION — aucun fichier serveur modifié'))
    print('Aucune connexion ni exécution sur PythonAnywhere depuis cet outil.')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Préparer sur le PC une commande de partage des PDF identiques pour PythonAnywhere.')
    parser.add_argument('--rapport', type=Path, default=Path.home() / 'Downloads' / 'LR-PDF' / 'rapport.json')
    parser.add_argument('--appliquer', action='store_true', help='Préparer une commande d’application ; le défaut est une simulation.')
    arguments = parser.parse_args()
    try:
        prepare_command(arguments.rapport, apply=arguments.appliquer)
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, str(error) + '\n')
