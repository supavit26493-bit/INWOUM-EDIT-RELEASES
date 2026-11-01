import base64,copy,hashlib,io,json,os,tempfile,unittest,xml.etree.ElementTree as ET,zipfile
from pathlib import Path
from unittest.mock import patch
import app_updater as up,update_worker as worker

class UpdateTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  key=ET.fromstring((Path(__file__).parent/'update-test-key.xml').read_text('utf8'))
  cls.key={k:int.from_bytes(base64.b64decode(key.findtext(k)),'big') for k in ['Modulus','Exponent','D']}
  cls.config=dict(repository='example/INWOUM-EDIT-RELEASES',key_id='test-only',rsa_n=hex(cls.key['Modulus'])[2:],rsa_e=hex(cls.key['Exponent'])[2:])
 def sign(self,payload):
  n=self.key['Modulus'];width=(n.bit_length()+7)//8;digest=bytes.fromhex('3031300d060960864801650304020105000420')+hashlib.sha256(up.canonical(payload)).digest();encoded=b'\x00\x01'+b'\xff'*(width-len(digest)-3)+b'\x00'+digest
  return dict(payload=payload,key_id='test-only',signature=base64.b64encode(pow(int.from_bytes(encoded,'big'),self.key['D'],n).to_bytes(width,'big')).decode())
 def fixture(self,area,extra=None):
  root=area/'ทีม & Test';root.mkdir();stage=root/'.updates'/'stage';stage.mkdir(parents=True)
  source=json.dumps(self.config).encode();(root/'update-source.json').write_bytes(source)
  files={'server.py':b'# new server','runtime_contract.py':b"VERSION='0.15.21'",'START.cmd':b'echo start','added.py':b'new=1','update-source.json':source}
  files.update(extra or {});manifest=dict(version='0.15.21',files={n:hashlib.sha256(v).hexdigest() for n,v in files.items()})
  archive=stage/'program.zip'
  with zipfile.ZipFile(archive,'w') as z:
   for name,data in files.items():z.writestr('INWOUM-EDIT/'+name,data)
   z.writestr('INWOUM-EDIT/INSTALL-MANIFEST.json',json.dumps(manifest))
  payload=dict(format=1,min_updater=1,version='0.15.21',asset='INWOUM-EDIT-0.15.21-TEAM.zip',sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),size=archive.stat().st_size)
  (stage/'update.json').write_text(json.dumps(self.sign(payload)),'utf8')
  for name in ('app_updater.py','update_worker.py'): (stage/name).write_bytes((up.ROOT/name).read_bytes())
  (stage/'update-source.json').write_bytes(source)
  (root/'server.py').write_bytes(b'# old server');(root/'runtime_contract.py').write_bytes(b"VERSION='0.15.20'");(root/'START.cmd').write_bytes(b'old start');(root/'INSTALL-MANIFEST.json').write_bytes(b'{"version":"0.15.20"}')
  return root,stage,archive,payload
 def test_pinned_signature_valid_tamper_and_wrong_publisher_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root,stage,archive,payload=self.fixture(Path(tmp));doc=self.sign(payload);self.assertEqual(up.verify_document(doc,self.config),payload)
   altered=copy.deepcopy(doc);altered['payload']['version']='9.9.9'
   with self.assertRaises(ValueError):up.verify_document(altered,self.config)
   altered=copy.deepcopy(doc);altered['key_id']='other'
   with self.assertRaises(ValueError):up.verify_document(altered,self.config)
 def test_download_accepts_only_https_github_hosts(self):
  for url in ['http://github.com/a','https://github.com.evil.com/a','https://evil.com/a','https://user:pass@github.com/a','file:///x','https://github.com:80/a']:
   self.assertFalse(up.allowed_url(url))
  self.assertTrue(up.allowed_url('https://release-assets.githubusercontent.com/a?token=public-download'))
 def test_paths_block_traversal_personal_and_windows_devices(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   for name in ['../server.py','/server.py','a/../b','a\\b','jobs/a','.venv/a','LOCAL-runtime.json','local-runtime.json','CON','a/NUL.txt','backups/a','update-source.json','a./file','a//file']:
    with self.subTest(name=name),self.assertRaises(ValueError):up.safe_path(root,name)
   self.assertEqual(up.safe_path(root,'web/update-ui.js'),(root/'web/update-ui.js').resolve())
 def test_archive_corruption_rejected_before_backup(self):
  with tempfile.TemporaryDirectory() as tmp:
   root,stage,archive,payload=self.fixture(Path(tmp));archive.write_bytes(archive.read_bytes()+b'x')
   with self.assertRaises(ValueError):worker.backup_and_install(root,archive,payload,stage)
   self.assertFalse((root/'backups').exists());self.assertEqual((root/'server.py').read_bytes(),b'# old server')
 def test_signed_protected_paths_still_rejected(self):
  for name in ['jobs/secret.json','local-runtime.json','../evil.py']:
   with self.subTest(name=name),tempfile.TemporaryDirectory() as tmp:
    root,stage,archive,payload=self.fixture(Path(tmp),{name:b'danger'})
    with self.assertRaises(ValueError):up.validate_archive(archive,payload,root)
 def test_archive_case_collision_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root,stage,archive,payload=self.fixture(Path(tmp),{'SERVER.py':b'collision'})
   with self.assertRaises(ValueError):up.validate_archive(archive,payload,root)
 def test_source_cannot_be_silently_replaced(self):
  with tempfile.TemporaryDirectory() as tmp:
   root,stage,archive,payload=self.fixture(Path(tmp),{'update-source.json':b'{}'})
   with self.assertRaises(ValueError):up.validate_archive(archive,payload,root)
 def test_install_and_restore_preserve_jobs_settings_and_environment(self):
  with tempfile.TemporaryDirectory() as tmp:
   root,stage,archive,payload=self.fixture(Path(tmp));protected={'jobs/J/keep.txt':b'job','.venv/keep':b'venv','local-runtime.json':b'settings','notes.txt':b'note'}
   for name,data in protected.items():p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
   backup=worker.backup_and_install(root,archive,payload,stage);self.assertEqual((root/'server.py').read_bytes(),b'# new server');self.assertTrue((root/'added.py').exists())
   worker.restore(backup);self.assertEqual((root/'server.py').read_bytes(),b'# old server');self.assertFalse((root/'added.py').exists())
   for name,data in protected.items():self.assertEqual((root/name).read_bytes(),data)
 def test_partial_write_failure_rolls_back_files(self):
  with tempfile.TemporaryDirectory() as tmp:
   root,stage,archive,payload=self.fixture(Path(tmp));replace=os.replace
   def fail(src,dest):
    if str(src).endswith('added.py.update-tmp'):raise OSError('disk full')
    return replace(src,dest)
   with patch('os.replace',side_effect=fail),self.assertRaises(OSError):worker.backup_and_install(root,archive,payload,stage)
   self.assertEqual((root/'server.py').read_bytes(),b'# old server');self.assertFalse((root/'added.py').exists())
 def test_new_server_health_failure_restores_and_restarts_old(self):
  with tempfile.TemporaryDirectory() as tmp:
   root,stage,archive,payload=self.fixture(Path(tmp))
   with patch('update_worker.wait_closed') as wait,patch('update_worker.launch') as launch,patch('update_worker.health',return_value=False),patch('update_worker.stop_spawned') as stop:
    with self.assertRaises(ValueError):worker.install(root,stage,8776,123)
    wait.assert_called_once_with(123);self.assertEqual(launch.call_count,2);stop.assert_called_once()
   self.assertEqual((root/'server.py').read_bytes(),b'# old server');self.assertFalse((root/'added.py').exists())
 def test_good_install_health_records_success(self):
  with tempfile.TemporaryDirectory() as tmp:
   root,stage,archive,payload=self.fixture(Path(tmp))
   with patch('update_worker.wait_closed'),patch('update_worker.launch'),patch('update_worker.health',return_value=True):worker.install(root,stage,8776,123)
   self.assertTrue(json.loads((root/'.updates/last-result.json').read_text('utf8'))['ok'])
 def test_download_or_signature_failure_never_stops_server(self):
  with tempfile.TemporaryDirectory() as tmp:
   root,stage,archive,payload=self.fixture(Path(tmp))
   with patch('app_updater.check',side_effect=ValueError('unavailable')) as check:
    shutdown=unittest.mock.Mock();up.prepare(root,8776,shutdown);shutdown.assert_not_called();self.assertEqual(up.status()['phase'],'error')
 def test_latest_release_pinned_asset_and_cache(self):
  with tempfile.TemporaryDirectory() as tmp:
   root,stage,archive,payload=self.fixture(Path(tmp));repo=self.config['repository'];prefix='https://github.com/'+repo+'/releases/download/v0.15.21/'
   release=dict(tag_name='v0.15.21',draft=False,prerelease=False,assets=[dict(name='update.json',state='uploaded',size=200,browser_download_url=prefix+'update.json'),dict(name=payload['asset'],state='uploaded',size=payload['size'],browser_download_url=prefix+payload['asset'])])
   with patch('runtime_contract.VERSION','0.15.20'),patch.object(up,'_cache',None),patch('app_updater.download',side_effect=[json.dumps(release).encode(),json.dumps(self.sign(payload)).encode()]) as network:
    found=up.check(root);self.assertTrue(found['available']);self.assertEqual(up.check(root),found);self.assertEqual(network.call_count,2)
 def test_downgrade_refused_before_backup(self):
  with tempfile.TemporaryDirectory() as tmp:
   root,stage,archive,payload=self.fixture(Path(tmp));(root/'INSTALL-MANIFEST.json').write_text('{"version":"0.15.22"}')
   with self.assertRaises(ValueError):worker.backup_and_install(root,archive,payload,stage)
   self.assertFalse((root/'backups').exists())
 def test_active_update_blocks_edit_start(self):
  import server
  with patch('app_updater.busy',return_value=True),self.assertRaisesRegex(ValueError,'อัปเดต'):server.launch({},action='rebuild')

if __name__=='__main__':unittest.main()
