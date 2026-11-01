"""Detached installer. Runs only after the server exits; rollback on write/start failure."""
import argparse,hashlib,json,os,shutil,subprocess,sys,time,urllib.request,uuid,zipfile
from pathlib import Path
import app_updater as up

def save(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf8');os.replace(tmp,path)

def wait_closed(pid,timeout=90):
 if os.name=='nt':
  import ctypes
  dll=ctypes.WinDLL('kernel32',use_last_error=True);dll.OpenProcess.restype=ctypes.c_void_p;dll.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_bool,ctypes.c_uint32]
  dll.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32];dll.CloseHandle.argtypes=[ctypes.c_void_p]
  handle=dll.OpenProcess(0x100000,False,pid)
  if not handle:return
  try:
   if dll.WaitForSingleObject(handle,int(timeout*1000))!=0:raise ValueError('โปรแกรมเดิมยังไม่ปิด จึงไม่ทับไฟล์')
  finally:dll.CloseHandle(handle)
 else:
  end=time.time()+timeout
  while time.time()<end:
   try:os.kill(pid,0)
   except ProcessLookupError:return
   time.sleep(.2)
  raise ValueError('โปรแกรมเดิมยังไม่ปิด')

def backup_and_install(root,archive,payload,area):
 manifest=up.validate_archive(archive,payload,root)
 installed=json.loads((root/'INSTALL-MANIFEST.json').read_text('utf8'))
 if up.version(payload['version'])<=up.version(installed['version']):raise ValueError('ไม่ติดตั้งรุ่นเดิมหรือรุ่นเก่าทับอัตโนมัติ')
 names=[n for n in manifest['files'] if n!='update-source.json']+['INSTALL-MANIFEST.json']
 backup=root/'backups'/('auto-'+payload['version']+'-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]);backup.mkdir(parents=True)
 records=[]
 for name in names:
  dest=up.safe_path(root,name);saved=up.safe_path(backup,name);existed=dest.is_file()
  if dest.exists() and not existed:raise ValueError('พาธไฟล์ชนกับโฟลเดอร์ '+name)
  if existed:saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dest,saved)
  records.append(dict(name=name,existed=existed))
 save(backup/'restore-plan.json',dict(files=records,target=str(root)))
 # A local recovery command uses the trusted helper, not downloaded arbitrary instructions.
 for name in ('app_updater.py','update_worker.py','update-source.json'):shutil.copy2(area/name,backup/name)
 (backup/'RESTORE.cmd').write_text('@echo off\r\n"'+sys.executable+'" "'+str(backup/'update_worker.py')+'" --restore "'+str(backup)+'"\r\npause\r\n','utf8')
 try:
  with zipfile.ZipFile(archive) as z:
   for name in names:
    dest=up.safe_path(root,name);dest.parent.mkdir(parents=True,exist_ok=True)
    temp=dest.with_name(dest.name+'.update-tmp');temp.write_bytes(z.read('INWOUM-EDIT/'+name));os.replace(temp,dest)
  for name,digest in manifest['files'].items():
   if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest:raise ValueError('ตรวจไฟล์หลังติดตั้งไม่ผ่าน: '+name)
 except Exception:
  restore(backup);raise
 return backup

def restore(backup):
 plan=json.loads((backup/'restore-plan.json').read_text('utf8'));root=Path(plan['target']).resolve()
 if backup.resolve().parent!=root/'backups':raise ValueError('โฟลเดอร์สำรองไม่อยู่ในโปรแกรม')
 for row in reversed(plan['files']):
  name=row['name'];dest=up.safe_path(root,name)
  if row['existed']:
   original=up.safe_path(backup,name);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,dest)
  elif dest.is_file():dest.unlink()
 return root

def launch(root,port,nonce,python):
 env=dict(os.environ,INWOUM_UPDATE_NONCE=nonce)
 log=(root/'.updates'/'restart.log').open('ab')
 try:return subprocess.Popen([python,'-X','utf8',str(root/'server.py'),'--port',str(port),'--no-browser'],cwd=str(root),env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),close_fds=True)
 finally:log.close()

def health(port,nonce,expected,timeout=35):
 end=time.time()+timeout
 while time.time()<end:
  try:
   with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/update-health',timeout=2) as r:data=json.load(r)
   if data.get('update_nonce')==nonce and data.get('version')==expected:return True
  except Exception:pass
  time.sleep(.5)
 return False

def stop_spawned(process):
 if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 elif process.poll() is None:process.terminate();process.wait(timeout=10)

def install(root,area,port,pid,python=sys.executable):
 config=up.source(root);document=json.loads((area/'update.json').read_text('utf8'));payload=up.verify_document(document,config)
 up.validate_archive(area/'program.zip',payload,root)
 if (area/'update-source.json').read_bytes()!=(root/'update-source.json').read_bytes():raise ValueError('แหล่งอัปเดตเปลี่ยนระหว่างดาวน์โหลด')
 wait_closed(pid)
 try:backup=backup_and_install(root,area/'program.zip',payload,area)
 except Exception:
  launch(root,port,uuid.uuid4().hex,python);raise
 nonce=uuid.uuid4().hex;process=None
 try:
  process=launch(root,port,nonce,python)
  if not health(port,nonce,payload['version']):raise ValueError('รุ่นใหม่เปิดไม่สำเร็จ จึงย้อนกลับรุ่นเดิม')
 except Exception:
  if process:stop_spawned(process)
  restore(backup);launch(root,port,uuid.uuid4().hex,python)
  save(root/'.updates'/'last-result.json',dict(ok=False,message='อัปเดตเปิดไม่สำเร็จ ย้อนกลับรุ่นเดิมแล้ว',backup=str(backup)));raise
 save(root/'.updates'/'last-result.json',dict(ok=True,version=payload['version'],message='อัปเดตเป็น '+payload['version']+' แล้ว',backup=str(backup),time=time.time()))
 return backup

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--root');parser.add_argument('--pid',type=int);parser.add_argument('--port',type=int,default=8776);parser.add_argument('--restore');args=parser.parse_args()
 if args.restore:
  backup=Path(args.restore).resolve();plan=json.loads((backup/'restore-plan.json').read_text('utf8'));root=Path(plan['target'])
  # A reachable server means a user must close it first; never overwrite a running application.
  try:
   urllib.request.urlopen('http://127.0.0.1:8776/update-health',timeout=2)
  except Exception:pass
  else:raise ValueError('ปิดโปรแกรมก่อนย้อนกลับ')
  restore(backup);print('Restored');return
 root=Path(args.root).resolve();area=Path(__file__).resolve().parent
 if area.parent!=root/'.updates' or not (root/'server.py').is_file():raise ValueError('โฟลเดอร์อัปเดตไม่ถูกต้อง')
 try:install(root,area,args.port,args.pid)
 except Exception as error:
  save(root/'.updates'/'last-result.json',dict(ok=False,message=str(error)[:500],time=time.time()))
  try:
   urllib.request.urlopen('http://127.0.0.1:'+str(args.port)+'/update-health',timeout=2)
  except Exception:launch(root,args.port,uuid.uuid4().hex,sys.executable)
  raise

if __name__=='__main__':main()
