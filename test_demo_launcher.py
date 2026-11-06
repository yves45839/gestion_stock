"""Standard-library tests: python -m unittest test_demo_launcher."""
from pathlib import Path
from collections import namedtuple
import subprocess
import sys
import tempfile
from unittest import TestCase
from unittest.mock import patch

import start_demo


class DemoLauncherTests(TestCase):
    def test_python_314_stops_before_attempting_installation(self):
        version = namedtuple('Version', 'major minor')(3, 14)
        with patch.object(start_demo.sys, 'version_info', version):
            with patch.object(start_demo.shutil, 'which', return_value=None):
                with patch.object(start_demo.subprocess, 'run') as run:
                    with self.assertRaisesRegex(SystemExit, '3.12'):
                        start_demo.check_runtime()
                    run.assert_not_called()

    def test_existing_compatible_environment_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / '.venv' / ('Scripts/python.exe' if start_demo.os.name == 'nt' else 'bin/python')
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch.object(start_demo.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, f'[{sys.version_info.major}, {sys.version_info.minor}]')):
                with patch.object(start_demo.venv, 'create') as create:
                    self.assertEqual(start_demo.prepare_python(root), executable)
                    create.assert_not_called()

    def test_incompatible_environment_is_preserved_and_demo_data_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.venv').mkdir()
            (root / '.venv' / 'failed-install.txt').write_text('keep this')
            database = root / 'var' / 'sales-demo' / 'db.sqlite3'
            database.parent.mkdir(parents=True)
            database.write_bytes(b'demo data')
            with patch.object(start_demo.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '[3, 14]')):
                with patch.object(start_demo.venv, 'create') as create:
                    start_demo.prepare_python(root)
                    create.assert_called_once_with(root / '.venv', with_pip=True)
            backups = list(root.glob('.venv-incompatible-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / 'failed-install.txt').read_text(), 'keep this')
            self.assertEqual(database.read_bytes(), b'demo data')

    def test_missing_interpreter_is_recovered_without_deleting_the_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.venv').mkdir()
            with patch.object(start_demo.subprocess, 'run', side_effect=FileNotFoundError):
                with patch.object(start_demo.venv, 'create') as create:
                    start_demo.prepare_python(root)
                    create.assert_called_once()
            self.assertEqual(len(list(root.glob('.venv-incompatible-*'))), 1)
