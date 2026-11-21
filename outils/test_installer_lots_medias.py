import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import installer_lots_medias as tool


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'server'
        self.media = self.home / 'gestion_stock/media'
        self.media.mkdir(parents=True)
        self.downloads = self.root / 'Downloads'
        self.package = self.downloads / 'LR-REMPLACEMENT-test'
        self.package.mkdir(parents=True)
        self.original = b'original PDF' * 1024
        self.compressed = b'compressed PDF'
        self.old_hash = hashlib.sha256(self.original).hexdigest()
        self.new_hash = hashlib.sha256(self.compressed).hexdigest()
        self.keys = ['products/datasheets/a.pdf', 'products/datasheets/b.pdf']
        for key in self.keys:
            backup = self.downloads / 'LR-PDF/originaux' / key
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(self.original)
        first = self.media / self.keys[0]
        first.parent.mkdir(parents=True)
        first.write_bytes(self.original)
        os.link(first, self.media / self.keys[1])
        self.archive = self.package / 'lot-001.zip'
        with zipfile.ZipFile(self.archive, 'w') as stream:
            stream.writestr('blobs/' + self.new_hash, self.compressed)
        self.archive_sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.spec = {'rows': [[key, self.old_hash, len(self.original), self.new_hash, len(self.compressed), 0] for key in self.keys],
                     'batches': [[self.archive.name, self.archive_sha, self.archive.stat().st_size]]}
        self.manifest = {'schema': 2, 'complete': True, 'site': 'https://samr.pythonanywhere.com',
                         'large_file_policy': 'remove_backed_up_server_original',
                         'batches': [{'archive': self.archive.name, 'archive_sha256': self.archive_sha,
                                      'archive_bytes': self.archive.stat().st_size,
                                      'files': [{'path': key, 'kind': 'pdf', 'original_sha256': self.old_hash,
                                                 'original_bytes': len(self.original), 'output_sha256': self.new_hash,
                                                 'output_bytes': len(self.compressed)} for key in self.keys]}]}
        self.manifest_path = self.package / 'manifest-remplacement.json'
        self.manifest_path.write_text(json.dumps(self.manifest))

    def run_server(self, mode):
        return tool.server_operation(self.spec, mode, str(self.home), str(self.media))

    def upload(self):
        shutil.copyfile(self.archive, self.home / self.archive.name)

    def test_control_does_not_remove_or_create_journal(self):
        self.run_server('controle')
        self.assertTrue(all((self.media / key).exists() for key in self.keys))
        self.assertFalse((self.home / '.lr-remplacement').exists())

    def test_release_install_and_repeat_keep_hardlinks_and_database(self):
        db = self.home / 'gestion_stock/db.sqlite3'
        db.write_bytes(b'untouched business database')
        state = self.run_server('liberer')
        self.assertTrue(state['release_complete'])
        self.assertTrue(all(not (self.media / key).exists() for key in self.keys))
        self.upload()
        state = self.run_server('installer')
        self.assertTrue(state['complete'])
        self.assertEqual(len(state['installed']), 2)
        self.assertTrue(os.path.samefile(*(self.media / key for key in self.keys)))
        self.assertEqual((self.media / self.keys[0]).read_bytes(), self.compressed)
        self.assertFalse((self.home / self.archive.name).exists())
        self.run_server('liberer')
        self.run_server('installer')
        self.assertEqual(db.read_bytes(), b'untouched business database')
        self.assertTrue(self.archive.exists())

    def test_new_manual_deletion_aborts_release_without_removing_others(self):
        (self.media / self.keys[1]).unlink()
        with self.assertRaises(ValueError):
            self.run_server('liberer')
        self.assertTrue((self.media / self.keys[0]).exists())
        self.assertFalse((self.home / '.lr-remplacement').exists())

    def test_changed_source_aborts_before_other_original_is_removed(self):
        (self.media / self.keys[1]).unlink()
        (self.media / self.keys[1]).write_bytes(b'new user file')
        with self.assertRaises(ValueError):
            self.run_server('liberer')
        self.assertEqual((self.media / self.keys[0]).read_bytes(), self.original)

    def test_unlisted_original_hardlink_keeps_original_content(self):
        retained = self.home / 'retained-original.pdf'
        os.link(self.media / self.keys[0], retained)
        self.run_server('liberer')
        self.upload()
        self.run_server('installer')
        self.assertEqual(retained.read_bytes(), self.original)
        self.assertFalse(os.path.samefile(retained, self.media / self.keys[0]))

    def test_corrupt_archive_is_not_consumed(self):
        self.run_server('liberer')
        self.upload()
        uploaded = self.home / self.archive.name
        uploaded.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            self.run_server('installer')
        self.assertTrue(uploaded.exists())
        self.assertTrue(all(not (self.media / key).exists() for key in self.keys))

    def test_new_destination_is_never_overwritten(self):
        self.run_server('liberer')
        (self.media / self.keys[0]).write_bytes(b'user-created file')
        self.upload()
        with self.assertRaises(ValueError):
            self.run_server('installer')
        self.assertEqual((self.media / self.keys[0]).read_bytes(), b'user-created file')
        self.assertTrue((self.home / self.archive.name).exists())

    def test_interrupted_install_can_resume_after_reupload(self):
        self.run_server('liberer')
        self.upload()
        with patch('os.link', side_effect=OSError('interrupted')), self.assertRaises(OSError):
            self.run_server('installer')
        self.assertFalse((self.home / self.archive.name).exists())
        self.assertTrue(self.archive.exists())
        self.assertFalse(list(self.media.rglob('.lr-install-*')))
        self.upload()
        self.assertTrue(self.run_server('installer')['complete'])

    def test_partial_hardlink_group_resumes_without_duplicate_content(self):
        self.run_server('liberer')
        self.upload()
        real_link = os.link
        calls = 0
        def interrupted(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('interrupted second alias')
            return real_link(src, dst)
        with patch('os.link', side_effect=interrupted), self.assertRaises(OSError):
            self.run_server('installer')
        self.assertEqual((self.media / self.keys[0]).read_bytes(), self.compressed)
        self.upload()
        self.assertTrue(self.run_server('installer')['complete'])
        self.assertTrue(os.path.samefile(*(self.media / key for key in self.keys)))

    def test_identical_payloads_are_shared_across_batches(self):
        second = self.package / 'lot-002.zip'
        shutil.copyfile(self.archive, second)
        self.spec['batches'].append([second.name, self.archive_sha, second.stat().st_size])
        self.spec['rows'][1][5] = 1
        self.run_server('liberer')
        self.upload()
        shutil.copyfile(second, self.home / second.name)
        self.assertTrue(self.run_server('installer')['complete'])
        self.assertTrue(os.path.samefile(*(self.media / key for key in self.keys)))

    def test_missing_after_completed_install_is_not_restored(self):
        self.run_server('liberer')
        self.upload()
        self.run_server('installer')
        (self.media / self.keys[0]).unlink()
        state = self.run_server('installer')
        self.assertFalse((self.media / self.keys[0]).exists())
        self.assertFalse(state['complete'])

    def test_interrupted_release_uses_intent_journal_for_explicit_resume(self):
        real_replace = os.replace
        calls = 0
        def interrupted(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('interrupted journal write')
            return real_replace(src, dst)
        with patch('os.replace', side_effect=interrupted), self.assertRaises(OSError):
            self.run_server('liberer')
        self.assertTrue(list((self.home / '.lr-remplacement').rglob('etat.json')))
        with self.assertRaises(ValueError):
            self.run_server('liberer')
        state = self.run_server('reprendre')
        self.assertTrue(state['release_complete'])
        self.upload()
        self.assertTrue(self.run_server('installer')['complete'])

    def test_local_preparation_checks_backups_and_generated_source_compiles(self):
        output = tool.prepare(self.manifest_path, self.downloads)
        for mode in ('controle', 'liberer', 'reprendre', 'installer'):
            command = (output / ('commande-' + mode + '.txt')).read_text()
            source = shlex.split(command)[2]
            compile(source, '<generated command>', 'exec')
        (self.downloads / 'LR-PDF/originaux' / self.keys[0]).write_bytes(b'changed backup')
        with self.assertRaises(ValueError):
            tool.prepare(self.manifest_path, self.downloads)

    def test_one_mib_payload_is_rejected_locally(self):
        self.manifest['batches'][0]['files'][0]['output_bytes'] = 1048576
        self.manifest_path.write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):
            tool.prepare(self.manifest_path, self.downloads)


if __name__ == '__main__':
    unittest.main()
