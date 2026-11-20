from contextlib import redirect_stdout
import hashlib
from io import BytesIO, StringIO
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

import compresser_pdf_local as tool

SITE='https://samr.pythonanywhere.com'


class PathTests(unittest.TestCase):
    def test_safe_url_preserves_names_and_uses_https(self):
        path,url=tool.media_path('http://samr.pythonanywhere.com/media/products/datasheets/Fiche%20%C3%A9t%C3%A9.pdf',SITE)
        self.assertEqual(str(path),'products/datasheets/Fiche été.pdf')
        self.assertTrue(url.startswith(SITE+'/media/'))

    def test_external_and_unsafe_paths_are_rejected(self):
        for value in ['https://other.example/media/products/datasheets/a.pdf', '/media/products/datasheets/../db.pdf',
                      '/media/products/datasheets/NUL.pdf','/media/products/datasheets/a:b.pdf',
                      '/media/products/datasheets/a%5Cb.pdf','/media/products/datasheets/a.pdf.',
                      '/media/products/images/a.pdf']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                tool.media_path(value,SITE)

    def test_redirect_refuses_other_hosts_and_https_downgrade(self):
        handler=tool.SameHostRedirect()
        for target in ['https://other.example/a','http://samr.pythonanywhere.com/a']:
            with self.assertRaises(ValueError):
                handler.redirect_request(Request(SITE+'/a'),None,302,'Found',{},target)

    def test_catalog_deduplicates_pdf_files(self):
        class Response(StringIO):
            url=SITE+'/api/products/'
        body={'count':3,'results':[{'datasheet_pdf_url':SITE+tool.PREFIX+'a.pdf'},
                                {'datasheet_pdf_url':SITE+tool.PREFIX+'a.pdf'},{}]}
        with patch.object(tool,'open_url',return_value=Response(json.dumps(body))):
            self.assertEqual(len(tool.fetch_catalog(SITE)),1)

    def test_catalog_never_silently_accepts_a_partial_page(self):
        class Response(StringIO):
            url=SITE+'/api/products/'
        with patch.object(tool,'open_url',return_value=Response('{"count":5,"results":[]}')):
            with self.assertRaises(ValueError):tool.fetch_catalog(SITE)


class GhostscriptTests(unittest.TestCase):
    def test_nonzero_and_silent_closing_errors_are_rejected(self):
        for result in [subprocess.CompletedProcess([],1,'','error'),
                       subprocess.CompletedProcess([],0,'Processing pages 1 through 1.','ERROR: ioerror (-12) on closing pdfwrite device.')]:
            with patch.object(tool.subprocess,'run',return_value=result):
                with self.assertRaises(ValueError):tool.run_gs('gs',[])

    def test_page_mismatch_keeps_original_and_discards_only_generated_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'original.pdf';destination=root/'compressed.pdf'
            source.write_bytes(b'%PDF-original'*100)
            before=source.read_bytes()
            def fake_gs(executable,args):
                output=next((a.split('=',1)[1] for a in args if a.startswith('-sOutputFile=')),None)
                if output:Path(output).write_bytes(b'%PDF-small')
                return 2 if output else 1
            with patch.object(tool,'run_gs',side_effect=fake_gs):
                with self.assertRaises(ValueError):tool.compress('gs',source,destination)
            self.assertEqual(source.read_bytes(),before)
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob('travail-*')),[])

    def test_larger_outputs_are_not_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'original.pdf';source.write_bytes(b'%PDF-a')
            destination=root/'compressed.pdf'
            def fake_gs(executable,args):
                for a in args:
                    if a.startswith('-sOutputFile='):Path(a.split('=',1)[1]).write_bytes(b'%PDF-larger output')
                return 1
            with patch.object(tool,'run_gs',side_effect=fake_gs):
                self.assertIsNone(tool.compress('gs',source,destination))
            self.assertEqual(source.read_bytes(),b'%PDF-a');self.assertFalse(destination.exists())

    @unittest.skipUnless(shutil.which('gs'),'Ghostscript requis pour cette intégration')
    def test_real_compression_and_resume_keep_verified_backup(self):
        try:
            from PIL import Image
        except ImportError:self.skipTest('Pillow requis uniquement pour créer le PDF de test')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fixture=root/'fixture.pdf'
            Image.effect_noise((1200,1200),80).convert('RGB').save(fixture,'PDF',resolution=600)
            original_hash=tool.sha256(fixture)
            output=root/'result';key='products/datasheets/Fiche test.pdf'
            def copy(url,target):
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(fixture,target)
            argv=['compresser_pdf_local.py','--output',str(output)]
            with patch.object(tool.sys,'argv',argv),patch.object(tool,'fetch_catalog',return_value={key:SITE+tool.PREFIX+'a.pdf'}),patch.object(tool,'download',side_effect=copy) as download,redirect_stdout(StringIO()):
                self.assertEqual(tool.main(),0)
                self.assertEqual(tool.main(),0)
                self.assertEqual(download.call_count,1)
            report=json.loads((output/'rapport.json').read_text())
            entry=report['files'][key]
            self.assertEqual(entry['original_sha256'],original_hash)
            self.assertEqual(tool.sha256(output/'originaux'/key),original_hash)
            self.assertLess(entry['compressed_bytes'],entry['original_bytes'])
            self.assertEqual(entry['pages'],1)
            self.assertTrue((output/'compresses'/key).is_file())


class DownloadTests(unittest.TestCase):
    def response(self, data):
        result=BytesIO(data)
        result.url=SITE+tool.PREFIX+'test.pdf'
        return result

    def test_backup_contains_exact_downloaded_bytes(self):
        data=b'%PDF-1.7\nexample bytes\n%%EOF'
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'original.pdf'
            with patch.object(tool,'open_url',return_value=self.response(data)):
                tool.download(SITE+tool.PREFIX+'test.pdf',target)
            self.assertEqual(target.read_bytes(),data)

    def test_html_login_page_is_not_saved_as_a_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'original.pdf'
            with patch.object(tool,'open_url',return_value=self.response(b'<html>login</html>')):
                with self.assertRaises(ValueError):tool.download(SITE+tool.PREFIX+'test.pdf',target)
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_existing_backup_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'original.pdf';target.write_bytes(b'%PDF-old')
            with patch.object(tool,'open_url',return_value=self.response(b'%PDF-new')):
                with self.assertRaises(ValueError):tool.download(SITE+tool.PREFIX+'test.pdf',target)
            self.assertEqual(target.read_bytes(),b'%PDF-old')


if __name__=='__main__':unittest.main()
