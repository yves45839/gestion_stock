import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import verifier_espace_installation as tool


class SpaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / 'media'
        self.media.mkdir()

    def add(self, name='a.pdf'):
        key = 'products/datasheets/' + name
        file = self.media / key
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'original file')
        return file, [key, hashlib.sha256(file.read_bytes()).hexdigest(), file.stat().st_size,
                      hashlib.sha256(b'new').hexdigest(), 3]

    def check(self, rows):
        return tool.server_check(rows, root=str(self.media), home=str(self.root))

    def test_read_only_and_exact_du_usage(self):
        file, row = self.add()
        before = file.read_bytes()
        result = self.check([row])
        self.assertEqual(result['valid'], 1)
        self.assertEqual(result['freed'], file.stat().st_blocks * 512)
        self.assertEqual(file.read_bytes(), before)
        self.assertEqual(result['projected'], result['used'] - result['freed'] + result['new_blocks'])

    def test_hardlinked_source_not_counted_twice(self):
        file, row = self.add()
        second = file.with_name('b.pdf')
        os.link(file, second)
        copy = list(row)
        copy[0] = 'products/datasheets/b.pdf'
        result = self.check([row, copy])
        self.assertEqual(result['freed'], file.stat().st_blocks * 512)
        self.assertEqual(result['new_blocks'], os.statvfs(self.media).f_frsize)

    def test_unlisted_hardlink_prevents_reclaim(self):
        file, row = self.add()
        os.link(file, self.root / 'outside-list.pdf')
        self.assertEqual(self.check([row])['freed'], 0)

    def test_missing_and_modified_are_reported_without_changes(self):
        file, row = self.add()
        file.write_bytes(b'changed')
        result = self.check([row])
        self.assertEqual(result['changed'], [row[0]])
        self.assertEqual(result['freed'], 0)
        file.unlink()
        result = self.check([row])
        self.assertEqual(result['missing'], [row[0]])
        self.assertFalse(file.exists())

    def test_symlink_and_traversal_rejected(self):
        file, row = self.add()
        file.unlink()
        file.symlink_to(self.root / 'outside')
        with self.assertRaises(ValueError):
            self.check([row])
        row[0] = 'products/datasheets/../a'
        with self.assertRaises(ValueError):
            self.check([row])

    def test_command_requires_intact_archives(self):
        file, row = self.add()
        archive = self.root / 'lot-001.zip'
        archive.write_bytes(b'archive payload')
        manifest = {'schema': 2, 'complete': True, 'site': 'https://samr.pythonanywhere.com',
                    'large_file_policy': 'remove_backed_up_server_original',
                    'batches': [{'archive': archive.name, 'archive_bytes': archive.stat().st_size,
                                 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                                 'files': [dict(zip(['path','original_sha256','original_bytes','output_sha256','output_bytes'], row))]}]}
        path = self.root / 'manifest-remplacement.json'
        path.write_text(json.dumps(manifest))
        command = tool.prepare(path)
        self.assertIn('PYTHONDONTWRITEBYTECODE=1', command.read_text())
        self.assertNotIn('unlink', command.read_text())
        archive.write_bytes(b'bad')
        with self.assertRaises(ValueError):
            tool.prepare(path)


if __name__ == '__main__':
    unittest.main()
