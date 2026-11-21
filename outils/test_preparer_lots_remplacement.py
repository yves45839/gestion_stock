import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import preparer_lots_remplacement as tool


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = {'site': tool.SITE, 'complete': True, 'files': {}}
        self.selection = self.root / 'selection.json'

    def add(self, path='products/datasheets/a.pdf', original=b'original bytes', compressed=b'PDF', kind='pdf'):
        folder = self.root / ('LR-PDF' if kind == 'pdf' else 'LR-IMAGES')
        for subfolder, content in [('originaux', original), ('compresses' if kind == 'pdf' else 'optimisees', compressed)]:
            target = folder / subfolder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        row = {'path': path, 'kind': kind, 'status': 'selectionne', 'original_bytes': len(original),
               'original_sha256': hashlib.sha256(original).hexdigest(), 'output_bytes': len(compressed),
               'output_sha256': hashlib.sha256(compressed).hexdigest()}
        self.report['files'][path] = row
        return row

    def run_build(self):
        self.selection.write_text(json.dumps(self.report))
        return tool.build(self.selection, self.root)

    def test_package_contains_verified_payload_and_expected_server_hash(self):
        row = self.add()
        folder, manifest = self.run_build()
        self.assertTrue(manifest['complete'])
        batch = manifest['batches'][0]
        self.assertEqual(batch['files'][0]['original_sha256'], row['original_sha256'])
        with zipfile.ZipFile(folder / batch['archive']) as z:
            self.assertEqual(z.read('blobs/' + row['output_sha256']), b'PDF')
        self.assertEqual(batch['archive_sha256'], tool.digest(folder / batch['archive']))
        self.assertFalse(manifest['server_modified'])

    def test_deleted_and_at_least_one_mib_never_packaged(self):
        self.add()
        self.report['files']['deleted'] = {'status': 'absent_serveur_ne_pas_retablir'}
        for size in (tool.LIMIT, tool.LIMIT + 1):
            key = f'products/images/big{size}.png'
            self.add(key, original=b'A' * (size + 1), compressed=b'B' * size, kind='image')
        _, manifest = self.run_build()
        self.assertEqual(sum(len(b['files']) for b in manifest['batches']), 1)
        self.assertEqual(manifest['excluded'], {'non_selectionnes': 1, 'au_moins_1_mo': 2})
        self.assertEqual(len(manifest['removals']), 2)
        self.assertEqual(manifest['large_file_policy'], 'remove_backed_up_server_original')

    def test_one_byte_below_limit_included(self):
        self.add(original=b'A' * (tool.LIMIT + 1), compressed=b'B' * (tool.LIMIT - 1))
        _, manifest = self.run_build()
        self.assertEqual(manifest['batches'][0]['files'][0]['output_bytes'], tool.LIMIT - 1)

    def test_identical_originals_keep_names_and_one_payload(self):
        self.add('products/datasheets/a.pdf', compressed=b'PDF1')
        self.add('products/datasheets/b.pdf', compressed=b'PDF')
        folder, manifest = self.run_build()
        batch = manifest['batches'][0]
        self.assertEqual(len(batch['files']), 2)
        self.assertEqual(len({r['output_sha256'] for r in batch['files']}), 1)
        with zipfile.ZipFile(folder / batch['archive']) as z:
            self.assertEqual(len(z.namelist()), 1)

    def test_altered_local_file_stops_before_archives_created(self):
        self.add()
        (self.root / 'LR-PDF/originaux/products/datasheets/a.pdf').write_bytes(b'altered')
        with self.assertRaises(ValueError):
            self.run_build()
        self.assertFalse(list(self.root.glob('LR-REMPLACEMENT-*')))

    def test_incomplete_or_external_report_rejected(self):
        self.add()
        self.report['complete'] = False
        with self.assertRaises(ValueError):
            self.run_build()
        self.report.update(complete=True, site='https://example.org')
        with self.assertRaises(ValueError):
            self.run_build()

    def test_traversal_and_mismatched_kind_rejected(self):
        for key, kind in [('products/images/../a', 'image'), ('products/images/a.pdf', 'pdf'),
                          ('products/datasheets/CON.pdf', 'pdf')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                tool.relative_path(key, kind)

    def test_small_batches_are_split(self):
        self.add('products/datasheets/a.pdf', original=b'A' * 10, compressed=b'aa')
        self.add('products/datasheets/b.pdf', original=b'B' * 10, compressed=b'bb')
        with patch.object(tool, 'BATCH_LIMIT', 3):
            _, manifest = self.run_build()
        self.assertEqual(len(manifest['batches']), 2)

    def test_large_removal_requires_both_verified_local_copies(self):
        self.add(original=b'A' * (tool.LIMIT + 1), compressed=b'B' * tool.LIMIT)
        (self.root / 'LR-PDF/compresses/products/datasheets/a.pdf').write_bytes(b'bad')
        with self.assertRaises(ValueError):
            self.run_build()

    def test_unchanged_large_file_is_removed_only_with_verified_backup(self):
        row = self.add(original=b'A' * (tool.LIMIT + 1), compressed=b'B')
        self.report['files'][row['path']] = {'path': row['path'], 'kind': 'pdf', 'status': 'sans_version_compressee'}
        raw = {'site': tool.SITE, 'files': {row['path']: {'original_bytes': row['original_bytes'],
                'original_sha256': row['original_sha256'], 'status': 'pas de gain — conserver original'}}}
        (self.root / 'LR-PDF/rapport.json').write_text(json.dumps(raw))
        _, manifest = self.run_build()
        self.assertEqual(len(manifest['removals']), 1)
        self.assertTrue(manifest['removals'][0]['server_existence_unverified'])
        self.assertEqual(manifest['batches'], [])

    def test_server_preflight_checks_all_before_deletion(self):
        root = self.root / 'server'
        rows = []
        for name in ('a.pdf', 'b.pdf'):
            key = 'products/datasheets/' + name
            path = root / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'original')
            rows.append({'path': key, 'original_bytes': 8, 'original_sha256': hashlib.sha256(b'original').hexdigest(),
                         'output_bytes': tool.LIMIT})
        (root / rows[1]['path']).write_bytes(b'changed!')
        with self.assertRaises(ValueError):
            tool.retire_server(root, rows, apply=True)
        self.assertTrue(all((root / row['path']).exists() for row in rows))

    def test_server_simulation_and_application_preserve_unlisted_hardlink(self):
        root = self.root / 'server'
        key = 'products/datasheets/a.pdf'
        path = root / key
        path.parent.mkdir(parents=True)
        path.write_bytes(b'original')
        sibling = path.with_name('manual.pdf')
        os.link(path, sibling)
        rows = [{'path': key, 'original_bytes': 8, 'original_sha256': hashlib.sha256(b'original').hexdigest(),
                 'output_bytes': tool.LIMIT}]
        self.assertEqual(tool.retire_server(root, rows), 0)
        self.assertTrue(path.exists())
        self.assertEqual(tool.retire_server(root, rows, apply=True), 0)
        self.assertFalse(path.exists())
        self.assertEqual(sibling.read_bytes(), b'original')
        self.assertEqual(tool.retire_server(root, rows, apply=True), 0)

    def test_server_refuses_small_files_and_symlinks(self):
        root = self.root / 'server'
        root.mkdir()
        with self.assertRaises(ValueError):
            tool.retire_server(root, [{'path': 'products/images/a', 'output_bytes': 12}], apply=True)
        outside = self.root / 'outside'
        outside.write_bytes(b'x')
        link = root / 'products/images/a'
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        with self.assertRaises(ValueError):
            tool.retire_server(root, [{'path': 'products/images/a', 'output_bytes': tool.LIMIT}], apply=True)
        self.assertEqual(outside.read_bytes(), b'x')

    def test_generated_command_is_standalone_and_simulates_by_default(self):
        import subprocess
        # Empty list makes a safe real shell check of quoting and standalone imports.
        command = tool.removal_command([]).replace('/home/samr/gestion_stock/venv/bin/python', 'python')
        result = subprocess.run(['bash', '-c', command], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Aucun fichier serveur modifié', result.stdout)


if __name__ == '__main__':
    unittest.main()
