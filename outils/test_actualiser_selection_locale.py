import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import actualiser_selection_locale as tool


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.key = 'products/datasheets/test.pdf'
        self.original = self.folder / 'originaux' / self.key
        self.output = self.folder / 'compresses' / self.key
        for file, content in [(self.original, b'original PDF'), (self.output, b'PDF')]:
            file.parent.mkdir(parents=True)
            file.write_bytes(content)
        self.entry = {'original_sha256': hashlib.sha256(b'original PDF').hexdigest(),
                      'compressed_sha256': hashlib.sha256(b'PDF').hexdigest()}

    def check(self):
        return tool.check_entry(self.folder, 'pdf', self.key, self.entry)

    def test_matching_server_and_verified_backups_selected(self):
        with patch.object(tool, 'open_url', return_value=io.BytesIO(b'original PDF')) as network:
            self.assertEqual(self.check()['status'], 'selectionne')
        network.assert_called_once_with(tool.SITE + '/media/' + self.key)
        self.assertEqual(self.original.read_bytes(), b'original PDF')

    def test_deleted_server_not_selected_or_restored(self):
        for code in (404, 410):
            with self.subTest(code=code), patch.object(tool, 'open_url', side_effect=HTTPError('u', code, 'x', {}, None)):
                self.assertEqual(self.check()['status'], 'absent_serveur_ne_pas_retablir')

    def test_forbidden_is_error_not_missing(self):
        with patch.object(tool, 'open_url', side_effect=HTTPError('u', 403, 'x', {}, None)):
            with self.assertRaises(HTTPError):
                self.check()

    def test_changed_remote_content_excluded(self):
        for body in (b'modified PDF', b'larger content than the old original', b''):
            with self.subTest(body=body), patch.object(tool, 'open_url', return_value=io.BytesIO(body)):
                self.assertEqual(self.check()['status'], 'serveur_modifie')

    def test_modified_local_backup_or_output_aborts_before_network(self):
        for file in (self.original, self.output):
            old = file.read_bytes()
            file.write_bytes(b'changed')
            with patch.object(tool, 'open_url') as network:
                with self.assertRaises(ValueError):
                    self.check()
                network.assert_not_called()
            file.write_bytes(old)

    def test_unsafe_paths_rejected(self):
        for key in ('/etc/passwd', 'products/images/../a', 'products/images/a\\b',
                    'products/images/NUL.png', 'products/images/a:stream', 'products/images/a. '):
            with self.subTest(key=key), self.assertRaises(ValueError):
                tool.safe_path(key)

    def test_external_redirect_rejected_before_request(self):
        from urllib.request import Request
        with self.assertRaises(ValueError):
            tool.SameSite().redirect_request(Request(tool.SITE + '/media/a'), None, 302, '', {}, 'https://example.org/a')

    def test_image_selection(self):
        key = 'products/images/test.png'
        for part, content in [('originaux', b'original image'), ('optimisees', b'image')]:
            path = self.folder / part / key
            path.parent.mkdir(parents=True)
            path.write_bytes(content)
        entry = {'original_sha256': hashlib.sha256(b'original image').hexdigest(),
                 'optimized_sha256': hashlib.sha256(b'image').hexdigest()}
        with patch.object(tool, 'open_url', return_value=io.BytesIO(b'original image')):
            self.assertEqual(tool.check_entry(self.folder, 'image', key, entry)['status'], 'selectionne')


if __name__ == '__main__':
    unittest.main()
