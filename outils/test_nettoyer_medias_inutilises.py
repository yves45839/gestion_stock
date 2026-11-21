import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import nettoyer_medias_inutilises as tool


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = 'products/images/ancien.jpg'
        self.path = self.root / self.key
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'image')
        self.row = [self.key, 5, hashlib.sha256(b'image').hexdigest()]

    def cleanup(self, apply=False, refs=lambda: set(), rows=None):
        return tool.server_cleanup(self.root, rows or [self.row], apply, refs)

    def test_default_simulation_preserves_file(self):
        self.assertEqual(self.cleanup()['deleted'], 0)
        self.assertEqual(self.path.read_bytes(), b'image')

    def test_application_removes_only_verified_unused_file(self):
        blocks = self.path.stat().st_blocks * 512
        result = self.cleanup(apply=True)
        self.assertEqual(result['deleted'], 1)
        self.assertEqual(result['freed'], blocks)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.cleanup(apply=True)['skipped']['missing'], 1)

    def test_current_or_historical_reference_keeps_file(self):
        result = self.cleanup(apply=True, refs=lambda: {self.key})
        self.assertEqual(result['skipped']['referenced'], 1)
        self.assertTrue(self.path.exists())

    def test_reference_added_during_preflight_keeps_file(self):
        refs = Mock(side_effect=[set(), {self.key}])
        self.assertEqual(self.cleanup(apply=True, refs=refs)['deleted'], 0)
        self.assertTrue(self.path.exists())

    def test_changed_file_is_not_deleted(self):
        self.path.write_bytes(b'other')
        self.assertEqual(self.cleanup(apply=True)['skipped']['changed'], 1)
        self.assertEqual(self.path.read_bytes(), b'other')

    def test_hardlink_outside_removal_list_preserves_content_and_blocks(self):
        sibling = self.path.with_name('referenced.jpg')
        os.link(self.path, sibling)
        result = self.cleanup(apply=True)
        self.assertEqual(result['freed'], 0)
        self.assertEqual(result['estimated'], 0)
        self.assertEqual(sibling.read_bytes(), b'image')

    def test_symlinks_and_traversal_are_refused(self):
        self.path.unlink()
        outside = self.root / 'outside'
        outside.write_bytes(b'image')
        self.path.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.cleanup(apply=True)
        with self.assertRaises(ValueError):
            self.cleanup(apply=True, rows=[['products/../outside', 5, self.row[2]]])
        self.assertTrue(outside.exists())

    def prepare_report(self):
        original = self.root / 'originaux' / self.key
        original.parent.mkdir(parents=True)
        original.write_bytes(b'image')
        inventory = {'schema': 1, 'site': 'https://samr.pythonanywhere.com', 'files': [self.row]}
        report = {'schema': 1, 'site': inventory['site'], 'complete': True,
                  'inventory_sha256': hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest(),
                  'files': {self.key: {'path': self.key, 'status': 'sauvegarde_verifiee',
                                      'original_bytes': 5, 'original_sha256': self.row[2]}}}
        path = self.root / 'rapport-inutilises.json'
        path.write_text(json.dumps(report))
        return path, original

    def test_preparation_rechecks_backups_and_default_is_simulation(self):
        report, original = self.prepare_report()
        command = tool.prepare(report)
        self.assertIn('apply=False', command.read_text())
        self.assertIn('PYTHONDONTWRITEBYTECODE=1', command.read_text())
        self.assertIn('apply=True', tool.prepare(report, apply=True).read_text())
        original.write_bytes(b'other')
        with self.assertRaises(ValueError):
            tool.prepare(report, apply=True)

    def test_reference_query_failure_aborts_without_mutations(self):
        refs = Mock(side_effect=[set(), RuntimeError('database unavailable')])
        with self.assertRaises(RuntimeError):
            self.cleanup(apply=True, refs=refs)
        self.assertTrue(self.path.exists())


if __name__ == '__main__':
    unittest.main()
