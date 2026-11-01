import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import media_runtime as media

class MediaRuntimeTests(unittest.TestCase):
    def test_missing_open_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake=SimpleNamespace(__file__=str(Path(tmp)/'av/__init__.py'),AudioResampler=lambda:None)
            self.assertIn('incomplete',media.module_problem(fake,[Path(tmp)]))
    def test_namespace_module_is_not_accepted(self):
        self.assertIn('no package file',media.module_problem(SimpleNamespace(),[]))
    def test_local_module_shadowing_is_reported(self):
        fake=SimpleNamespace(__file__=str(Path.cwd()/'av.py'),open=lambda:None,AudioResampler=lambda:None)
        self.assertIn('shadowing',media.module_problem(fake,[Path.cwd()/'not-site-packages']))
    def test_valid_package_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake=SimpleNamespace(__file__=str(Path(tmp)/'av/__init__.py'),open=lambda:None,AudioResampler=lambda:None)
            self.assertIsNone(media.module_problem(fake,[Path(tmp)]))
    def test_shadowing_does_not_trigger_reinstall(self):
        with patch('sys.argv',['media_runtime.py','--repair']),patch.object(media,'inspect_media',return_value=dict(status='shadowed')),patch.object(media.subprocess,'run') as send:
            self.assertEqual(media.main(),1);send.assert_not_called()
