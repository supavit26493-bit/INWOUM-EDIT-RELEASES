import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock,MagicMock
import runtime_session as session

class DesktopSessionTests(unittest.TestCase):
 def test_identity_is_case_insensitive_and_different_for_other_folder(self):
  self.assertEqual(session.identity(Path.cwd()),session.identity(str(Path.cwd()).upper()))
  self.assertNotEqual(session.identity(Path.cwd()),session.identity(Path.cwd()/'other'))
 def test_probe_requires_exact_installation_and_loopback_url(self):
  root=Path.cwd();reply=Mock();reply.__enter__=Mock(return_value=reply);reply.__exit__=Mock(return_value=False)
  reply.read.return_value=json.dumps(dict(instance_id=session.identity(root),version='0.15.22')).encode()
  with patch('urllib.request.urlopen',return_value=reply) as openurl:
   self.assertIsNotNone(session.probe('http://127.0.0.1:8776',root))
   for url in ['https://evil.com','http://localhost:8776','http://127.0.0.1:8776/path','http://user@127.0.0.1:8776','http://127.0.0.1:8776?key=x']:
    self.assertIsNone(session.probe(url,root))
   self.assertEqual(openurl.call_count,1)
 def test_health_from_other_program_cannot_be_reused(self):
  reply=Mock();reply.__enter__=Mock(return_value=reply);reply.__exit__=Mock(return_value=False);reply.read.return_value=b'{"instance_id":"other"}'
  with patch('urllib.request.urlopen',return_value=reply):self.assertIsNone(session.probe('http://127.0.0.1:8776',Path.cwd()))
 def test_registered_dynamic_port_is_reused(self):
  with tempfile.TemporaryDirectory() as tmp,patch('runtime_session.home',return_value=Path(tmp)):
   session.register(Path.cwd(),'http://127.0.0.1:19999')
   with patch('runtime_session.probe',side_effect=lambda url,root:dict(url=url) if url.endswith('19999') else None):
    self.assertEqual(session.existing(Path.cwd())['url'],'http://127.0.0.1:19999')
 def test_desktop_reuses_server_without_spawning_or_loading_jobs(self):
  with patch('runtime_session.existing',return_value=dict(url='http://127.0.0.1:8776')),patch('subprocess.Popen') as spawn:
   self.assertEqual(session.ensure(Path.cwd()),'http://127.0.0.1:8776');spawn.assert_not_called()
 def test_server_lock_prevents_second_start_and_can_release(self):
  with tempfile.TemporaryDirectory() as tmp,patch('runtime_session.home',return_value=Path(tmp)):
   first=session.Lock(Path.cwd());second=session.Lock(Path.cwd());self.assertTrue(first.acquire())
   try:self.assertFalse(second.acquire())
   finally:first.release()
   self.assertTrue(second.acquire());second.release()
 def test_desktop_uses_persistent_profile_and_download_support(self):
  import desktop_app
  with tempfile.TemporaryDirectory() as tmp,patch.object(desktop_app.os,'name','nt'),patch('runtime_session.home',return_value=Path(tmp)),patch('runtime_session.ensure',return_value='http://127.0.0.1:8776'),patch('runtime_session.Lock') as lock:
   lock.return_value.acquire.return_value=True;webview=MagicMock();webview.settings={}
   with patch.dict('sys.modules',webview=webview):desktop_app.run()
   self.assertTrue(webview.settings['ALLOW_DOWNLOADS']);self.assertFalse(webview.settings['ALLOW_FILE_URLS'])
   self.assertEqual(webview.start.call_args.kwargs['gui'],'edgechromium');self.assertFalse(webview.start.call_args.kwargs['private_mode']);self.assertNotIn('js_api',webview.create_window.call_args.kwargs);lock.return_value.release.assert_called_once()
 def test_duplicate_backend_returns_before_reading_or_interrupting_jobs(self):
  import server
  with patch('sys.argv',['server.py','--no-browser']),patch('runtime_session.Lock') as lock,patch('runtime_session.existing',return_value=dict(url='http://127.0.0.1:8776')),patch.object(server.JOB_ROOT.__class__,'glob',side_effect=AssertionError('Must not reread running jobs')):
   lock.return_value.acquire.return_value=False;server.main()

if __name__=='__main__':unittest.main()
