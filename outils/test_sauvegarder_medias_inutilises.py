import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request
import zlib

import sauvegarder_medias_inutilises as tool


class BackupUnusedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.row = ['products/images/ancien.jpg', 5, hashlib.sha256(b'image').hexdigest()]

    def token(self, rows):
        value = {'schema': 1, 'site': tool.SITE, 'files': rows}
        encoded = base64.b64encode(zlib.compress(json.dumps(value).encode())).decode()
        return '28 objects imported automatically\n' + tool.BEGIN + '\n' + encoded + '\n' + tool.END + '\n01:52 ~ $'

    def test_inventory_token_parses_surrounding_console_output(self):
        self.assertEqual(tool.parse_inventory(self.token([self.row]))['files'], [self.row])

    def test_truncated_or_unsafe_inventory_rejected(self):
        with self.assertRaises(ValueError):
            tool.parse_inventory(self.token([self.row]).replace(tool.END, ''))
        for path in ['products/images/../a', '/etc/passwd', 'products/images/a:stream', 'products/images/CON.pdf']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                tool.parse_inventory(self.token([[path, 5, self.row[2]]]))

    def test_current_and_historical_references_excluded_from_scan(self):
        for name in ['courant.jpg', 'historique.jpg', 'ancien.jpg']:
            path = self.root / 'products/images' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'image')
        references = {'products/images/courant.jpg', 'products/images/historique.jpg'}
        result = tool.server_inventory(self.root, references)
        self.assertEqual(result['files'], [self.row])
        self.assertTrue((self.root / 'products/images/historique.jpg').exists())

    def test_symlink_is_not_backed_up_as_orphan(self):
        target = self.root / 'outside'
        target.write_bytes(b'x')
        link = self.root / 'products/images/link.jpg'
        link.parent.mkdir(parents=True)
        link.symlink_to(target)
        self.assertEqual(tool.server_inventory(self.root, set())['files'], [])

    def test_successful_download_and_verified_resume(self):
        opener = __import__('unittest.mock', fromlist=['Mock']).Mock()
        opener.open.return_value = io.BytesIO(b'image')
        with patch.object(tool, 'build_opener', return_value=opener):
            self.assertEqual(tool.backup(self.row, self.root), 'sauvegarde_verifiee')
        with patch.object(tool, 'build_opener') as network:
            self.assertEqual(tool.backup(self.row, self.root), 'sauvegarde_verifiee')
            network.assert_not_called()

    def test_wrong_hash_never_creates_backup(self):
        opener = __import__('unittest.mock', fromlist=['Mock']).Mock()
        opener.open.return_value = io.BytesIO(b'other')
        with patch.object(tool, 'build_opener', return_value=opener), self.assertRaises(ValueError):
            tool.backup(self.row, self.root)
        self.assertFalse((self.root / 'originaux' / self.row[0]).exists())
        self.assertFalse(list(self.root.rglob('*.part-*')))

    def test_404_is_absent_but_403_is_error(self):
        opener = __import__('unittest.mock', fromlist=['Mock']).Mock()
        for code in (404, 403):
            opener.open.side_effect = HTTPError('url', code, 'error', {}, None)
            with patch.object(tool, 'build_opener', return_value=opener):
                if code == 404:
                    self.assertEqual(tool.backup(self.row, self.root), 'deja_absent')
                else:
                    with self.assertRaises(HTTPError):
                        tool.backup(self.row, self.root)

    def test_redirect_to_other_host_or_http_is_refused(self):
        for url in ('http://samr.pythonanywhere.com/media/x', 'https://example.org/x'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                tool.SameSite().redirect_request(Request(tool.SITE + '/x'), None, 302, '', {}, url)


if __name__ == '__main__':
    unittest.main()
