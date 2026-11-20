import hashlib
import os
from pathlib import Path
import tempfile
import json
import shlex
import subprocess
import unittest
from unittest.mock import patch

from partager_pdf_identiques import fingerprint, manifest_signature, share_pdfs, prepare_command


class ShareTests(unittest.TestCase):
    def fixture(self, root):
        root=Path(root);folder=root/'products/datasheets';folder.mkdir(parents=True)
        for name,data in [('a.pdf',b'%PDF-'+b'A'*4096),('b.pdf',b'%PDF-'+b'A'*4096),('c.pdf',b'%PDF-'+b'C'*4096)]:
            (folder/name).write_bytes(data)
        names=['products/datasheets/'+n for n in ['a.pdf','b.pdf','c.pdf']]
        expected=manifest_signature([(n,fingerprint(root/n)) for n in names])
        return names,expected

    def test_dry_run_never_changes_inodes_or_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);names,expected=self.fixture(root)
            inodes=[(root/n).stat().st_ino for n in names]
            changed,_,_=share_pdfs(root,names,expected)
            self.assertEqual(changed,1)
            self.assertEqual([ (root/n).stat().st_ino for n in names],inodes)

    def test_atomic_sharing_keeps_all_names_content_and_can_be_repeated(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);names,expected=self.fixture(root)
            before={n:(root/n).read_bytes() for n in names}
            changed,freed,skipped=share_pdfs(root,names,expected,apply=True)
            self.assertEqual((changed,skipped),(1,0));self.assertGreater(freed,0)
            self.assertTrue(os.path.samefile(root/names[0],root/names[1]))
            self.assertFalse(os.path.samefile(root/names[0],root/names[2]))
            self.assertEqual({n:(root/n).read_bytes() for n in names},before)
            self.assertEqual(share_pdfs(root,names,expected,apply=True)[0],0)

    def test_changed_source_aborts_before_any_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);names,expected=self.fixture(root)
            (root/names[2]).write_bytes(b'changed')
            with patch('partager_pdf_identiques.os.replace') as replace:
                with self.assertRaises(RuntimeError):share_pdfs(root,names,expected,apply=True)
                replace.assert_not_called()

    def test_link_failure_leaves_original_paths_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);names,expected=self.fixture(root)
            before={n:(root/n).read_bytes() for n in names}
            with patch('partager_pdf_identiques.os.link',side_effect=OSError(122,'Disk quota exceeded')):
                with self.assertRaises(OSError):share_pdfs(root,names,expected,apply=True)
            self.assertEqual({n:(root/n).read_bytes() for n in names},before)
            self.assertEqual(list((root/'products/datasheets').glob('.lien-*')),[])

    def test_replace_failure_removes_only_temporary_link(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);names,expected=self.fixture(root)
            before={n:(root/n).read_bytes() for n in names}
            with patch('partager_pdf_identiques.os.replace',side_effect=OSError('refused')):
                with self.assertRaises(OSError):share_pdfs(root,names,expected,apply=True)
            self.assertEqual({n:(root/n).read_bytes() for n in names},before)
            self.assertEqual(list((root/'products/datasheets').glob('.lien-*')),[])

    def test_permissions_are_not_changed_by_sharing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);names,expected=self.fixture(root)
            (root/names[0]).chmod(0o644)
            (root/names[1]).chmod(0o600)
            changed,_,skipped=share_pdfs(root,names,expected,apply=True)
            self.assertEqual((changed,skipped),(0,1))
            self.assertFalse(os.path.samefile(root/names[0],root/names[1]))

    def test_existing_partial_sharing_is_counted_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);names,expected=self.fixture(root)
            extra='products/datasheets/d.pdf'
            os.link(root/names[1],root/extra)
            names.append(extra)
            expected=manifest_signature([(n,fingerprint(root/n)) for n in names])
            planned=share_pdfs(root,names,expected)[1]
            applied=share_pdfs(root,names,expected,apply=True)[1]
            self.assertEqual(planned,applied)
            self.assertGreater(applied,0)

    def test_symbolic_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);names,expected=self.fixture(root)
            path=root/names[1];path.unlink();path.symlink_to(root/names[0])
            with self.assertRaises(RuntimeError):share_pdfs(root,names,expected,apply=True)

    def test_generated_command_defaults_to_simulation_and_contains_backup_signature(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'rapport.json'
            entries={'products/datasheets/a.pdf':{'original_sha256':'a'*64}}
            path.write_text(json.dumps({'site':'https://samr.pythonanywhere.com','files':entries}))
            command=prepare_command(path).read_text()
            self.assertIn('apply=False',command)
            self.assertIn(manifest_signature([('products/datasheets/a.pdf','a'*64)]),command)
            tokens=shlex.split(command)
            body=tokens[tokens.index('printf')+2]
            compile(body,'server-command','exec')
            pipe_source=command.split('&&\n',1)[1].rsplit(' | ',1)[0]
            output=subprocess.run(['bash','-c',pipe_source],capture_output=True,text=True,check=True).stdout
            self.assertEqual(output,body+'\n')

    def test_report_data_cannot_inject_python_into_generated_command(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'rapport.json'
            path.write_text(json.dumps({'site':'https://samr.pythonanywhere.com',
                'files':{"products/datasheets/a'; print('injected')":{'original_sha256':'a'*64}}}))
            command=prepare_command(path,apply=True).read_text()
            self.assertNotIn("print('injected')",command)
            self.assertIn('apply=True',command)

    def test_generated_command_refuses_unexpected_site_and_invalid_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'rapport.json'
            for data in [{'site':'https://other.example','files':{}},
                         {'site':'https://samr.pythonanywhere.com','files':{'a':{'original_sha256':'bad'}}}]:
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):prepare_command(path)


if __name__=='__main__':unittest.main()
