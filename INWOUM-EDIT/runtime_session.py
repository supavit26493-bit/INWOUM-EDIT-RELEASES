"""One local backend per installed folder; desktop and browser share the same jobs."""
import hashlib,json,os,subprocess,sys,time,urllib.request
from pathlib import Path
from urllib.parse import urlsplit

def identity(root):return hashlib.sha256(str(Path(root).resolve()).casefold().encode('utf8')).hexdigest()

def home(root):
 base=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'INWOUM-EDIT'
 return base/'instances'/identity(root)

def probe(url,root,timeout=1):
 try:
  parsed=urlsplit(url)
  if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.username or parsed.password or parsed.path not in ('','/') or not parsed.port or parsed.query or parsed.fragment:return None
  with urllib.request.urlopen(url.rstrip('/')+'/update-health',timeout=timeout) as response:data=json.load(response)
  return dict(data,url=url.rstrip('/')) if data.get('instance_id')==identity(root) else None
 except (OSError,ValueError):return None

def existing(root,preferred=8776):
 urls=['http://127.0.0.1:'+str(preferred)]
 try:
  saved=json.loads((home(root)/'server.json').read_text('utf8'))
  if saved.get('instance_id')==identity(root):urls.insert(0,saved['url'])
 except (OSError,ValueError,KeyError):pass
 for url in dict.fromkeys(urls):
  found=probe(url,root)
  if found:return found
 return None

class Lock:
 def __init__(self,root,kind='server'):self.path=home(root)/(kind+'.lock');self.stream=None
 def acquire(self):
  self.path.parent.mkdir(parents=True,exist_ok=True);stream=self.path.open('a+b')
  if stream.seek(0,2)==0:stream.write(b'0');stream.flush()
  stream.seek(0)
  try:
   if os.name=='nt':
    import msvcrt
    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
   else:
    import fcntl
    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
  except OSError:stream.close();return False
  self.stream=stream;return True
 def release(self):
  if self.stream:
   try:
    self.stream.seek(0)
    if os.name=='nt':
     import msvcrt
     msvcrt.locking(self.stream.fileno(),msvcrt.LK_UNLCK,1)
    else:
     import fcntl
     fcntl.flock(self.stream.fileno(),fcntl.LOCK_UN)
   finally:self.stream.close();self.stream=None

def register(root,url):
 folder=home(root);folder.mkdir(parents=True,exist_ok=True);path=folder/'server.json';tmp=folder/('server-'+str(os.getpid())+'.tmp')
 tmp.write_text(json.dumps(dict(url=url,instance_id=identity(root),pid=os.getpid())),'utf8');os.replace(tmp,path)

def ensure(root,port=8776,timeout=60,python=sys.executable):
 found=existing(root,port)
 if found:return found['url']
 folder=home(root);folder.mkdir(parents=True,exist_ok=True);log=(folder/'server.log').open('ab')
 try:proc=subprocess.Popen([python,'-X','utf8',str(Path(root)/'server.py'),'--port',str(port),'--no-browser'],cwd=str(root),stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),close_fds=True)
 finally:log.close()
 deadline=time.time()+timeout
 while time.time()<deadline:
  found=existing(root,port)
  if found:return found['url']
  if proc.poll() not in (None,0):raise RuntimeError('เปิดระบบตัดต่อไม่สำเร็จ ดูบันทึก '+str(folder/'server.log'))
  time.sleep(.25)
 raise RuntimeError('ระบบเปิดไม่ทันเวลา กรุณาเปิด START-WEB.cmd หรือตรวจ '+str(folder/'server.log'))
