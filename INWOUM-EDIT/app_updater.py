"""Signed GitHub release updates. Standard library only; never reads API keys/jobs."""
import base64,hashlib,json,os,re,shutil,stat,subprocess,sys,threading,time,urllib.request,urllib.error,zipfile
from pathlib import Path,PurePosixPath
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parent
MAX_ZIP=128*1024*1024
MAX_UNPACKED=512*1024*1024
PROTECTED={'.venv','jobs','models','cache','backups','releases','.git','.codex','.agents','.updates','publisher-private'}
KEEP={'local-runtime.json','CONVERSATION-CONTEXT.md','HANDOFF-NEW-PC.md'}
GUARD=threading.Lock();_cache=None;_checked=0
STATE=dict(phase='idle',message='กดเพื่อตรวจสอบและติดตั้งรุ่นล่าสุด')
BUSY_PHASES={'checking','downloading','installing','restarting'}

def version(value):
 if not isinstance(value,str) or not re.fullmatch(r'\d+\.\d+\.\d+',value):raise ValueError('เลขเวอร์ชันอัปเดตไม่ถูกต้อง')
 return tuple(map(int,value.split('.')))

def source(root=ROOT):
 data=json.loads((root/'update-source.json').read_text('utf8'))
 if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+',data.get('repository','')):raise ValueError('ยังไม่ได้ตั้งแหล่งอัปเดต GitHub')
 return data

def canonical(payload):return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')

def verify_document(document,config):
 """RSA PKCS#1 v1.5 SHA-256 verify with a pinned publisher public key."""
 payload=document['payload'];sig=base64.b64decode(document['signature'],validate=True)
 if document.get('key_id')!=config['key_id']:raise ValueError('ลายเซ็นไม่ใช่ผู้เผยแพร่ที่ตั้งไว้')
 n=int(config['rsa_n'],16);e=int(config['rsa_e'],16);width=(n.bit_length()+7)//8
 if width<256 or e!=65537 or len(sig)!=width or int.from_bytes(sig,'big')>=n:raise ValueError('ลายเซ็นอัปเดตไม่ถูกต้อง')
 digest=bytes.fromhex('3031300d060960864801650304020105000420')+hashlib.sha256(canonical(payload)).digest()
 expected=b'\x00\x01'+b'\xff'*(width-len(digest)-3)+b'\x00'+digest
 import hmac
 if not hmac.compare_digest(pow(int.from_bytes(sig,'big'),e,n).to_bytes(width,'big'),expected):raise ValueError('ลายเซ็นอัปเดตไม่ผ่าน จึงไม่ติดตั้ง')
 version(payload['version'])
 if payload.get('format')!=1 or payload.get('min_updater',1)>1:raise ValueError('ต้องติดตั้งตัวอัปเดตรุ่นใหม่ด้วยตนเองก่อน')
 if not re.fullmatch(r'INWOUM-EDIT-\d+\.\d+\.\d+-TEAM\.zip',payload.get('asset','')):raise ValueError('ชื่อชุดอัปเดตไม่ถูกต้อง')
 if payload['asset']!='INWOUM-EDIT-'+payload['version']+'-TEAM.zip':raise ValueError('ชื่อไฟล์ไม่ตรงเวอร์ชัน')
 if not re.fullmatch('[a-f0-9]{64}',payload.get('sha256','')) or type(payload.get('size')) is not int or not 0<payload['size']<=MAX_ZIP:raise ValueError('ข้อมูลตรวจไฟล์อัปเดตไม่ครบ')
 return payload

def allowed_url(url):
 parsed=urlsplit(url)
 return parsed.scheme=='https' and not parsed.username and not parsed.password and parsed.port in (None,443) and parsed.hostname in {'api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com','github-releases.githubusercontent.com'}

class SafeRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  if not allowed_url(newurl):raise ValueError('ลิงก์ดาวน์โหลดออกนอก GitHub')
  return super().redirect_request(req,fp,code,msg,headers,newurl)

def download(url,limit,destination=None,progress=None):
 if not allowed_url(url):raise ValueError('ลิงก์อัปเดตไม่ถูกต้อง')
 req=urllib.request.Request(url,headers={'User-Agent':'INWOUM-EDIT-Updater/1','Accept':'application/vnd.github+json' if urlsplit(url).hostname=='api.github.com' else 'application/octet-stream'})
 try:
  with urllib.request.build_opener(SafeRedirect()).open(req,timeout=30) as response:
   if not allowed_url(response.geturl()):raise ValueError('ปลายทางดาวน์โหลดไม่ถูกต้อง')
   total=int(response.headers.get('Content-Length','0'))
   if total>limit:raise ValueError('ไฟล์อัปเดตใหญ่เกินขอบเขต')
   data=bytearray();written=0;stream=destination.open('wb') if destination else None
   try:
    while True:
     chunk=response.read(65536)
     if not chunk:break
     written+=len(chunk)
     if written>limit:raise ValueError('ขนาดดาวน์โหลดเกินข้อมูลที่ระบุ')
     if stream:stream.write(chunk)
     else:data.extend(chunk)
     if progress:progress(written,total)
   finally:
    if stream:stream.close()
   return bytes(data) if destination is None else written
 except urllib.error.HTTPError as error:
  if error.code==404:raise ValueError('ยังไม่มี Release ที่เผยแพร่ หรือยังไม่พบ repository อัปเดต') from None
  if error.code in (403,429):raise ValueError('GitHub จำกัดการตรวจชั่วคราว กรุณาลองใหม่ภายหลัง') from None
  raise ValueError('ดาวน์โหลดอัปเดตไม่สำเร็จ (GitHub '+str(error.code)+')') from None
 except (urllib.error.URLError,TimeoutError):raise ValueError('เชื่อมต่อ GitHub ไม่สำเร็จ โปรแกรมเดิมยังใช้งานได้') from None

def release_asset(release,name,repository):
 matches=[a for a in release.get('assets',[]) if a.get('name')==name and a.get('state')=='uploaded']
 if len(matches)!=1:raise ValueError('Release ยังไม่มีไฟล์ครบ: '+name)
 asset=matches[0];url=asset.get('browser_download_url','')
 prefix='https://github.com/'+repository+'/releases/download/'+release['tag_name']+'/'
 if url!=prefix+name:raise ValueError('ไฟล์อัปเดตอยู่นอก Release ที่กำหนด')
 return asset

def check(root=ROOT,force=False):
 global _cache,_checked
 config=source(root)
 if not force and _cache is not None and time.time()-_checked<300:return dict(_cache)
 release=json.loads(download('https://api.github.com/repos/'+config['repository']+'/releases/latest',1024*1024))
 if release.get('draft') or release.get('prerelease'):raise ValueError('ไม่ติดตั้งรุ่นทดลองอัตโนมัติ')
 if not re.fullmatch(r'v?\d+\.\d+\.\d+',release.get('tag_name','')):raise ValueError('ป้าย Release ไม่ใช่เลขรุ่น')
 meta=release_asset(release,'update.json',config['repository'])
 document=json.loads(download(meta['browser_download_url'],65536));payload=verify_document(document,config)
 if release['tag_name'].removeprefix('v')!=payload['version']:raise ValueError('Release และชุดอัปเดตคนละรุ่น')
 asset=release_asset(release,payload['asset'],config['repository'])
 if asset.get('size')!=payload['size']:raise ValueError('ขนาดไฟล์ไม่ตรงชุดที่เซ็นไว้')
 from runtime_contract import VERSION
 result=dict(available=version(payload['version'])>version(VERSION),current=VERSION,latest=payload['version'],notes=str(payload.get('notes',''))[:6000],document=document,url=asset['browser_download_url'])
 _cache=result;_checked=time.time();return dict(result)

def safe_path(root,name):
 if not isinstance(name,str) or '\\' in name or ':' in name or '\x00' in name:raise ValueError('พาธอัปเดตไม่ปลอดภัย')
 parts=PurePosixPath(name).parts
 if not parts or any(p in ('..','.') or p.endswith((' ','.')) for p in parts) or name.startswith('/') or str(PurePosixPath(name))!=name:raise ValueError('พาธอัปเดตไม่ถูกต้อง')
 if any(re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',p) for p in parts):raise ValueError('ชื่อไฟล์สงวนของ Windows')
 if parts[0].lower() in PROTECTED or name.casefold() in {p.casefold() for p in KEEP} or parts[0].lower().startswith('local-') or name=='update-source.json':raise ValueError('อัปเดตห้ามทับข้อมูลส่วนตัวหรือแหล่งอัปเดต')
 root=root.resolve();dest=root.joinpath(*parts)
 for parent in [dest,*dest.parents]:
  if parent==root:break
  if parent.exists() and (parent.is_symlink() or getattr(parent,'is_junction',lambda:False)()):raise ValueError('พาธอัปเดตเป็นลิงก์ไปโฟลเดอร์อื่น')
 if not dest.resolve().is_relative_to(root):raise ValueError('อัปเดตออกนอกโฟลเดอร์โปรแกรม')
 return dest

def validate_archive(archive,payload,target=None):
 if archive.stat().st_size!=payload['size'] or hashlib.sha256(archive.read_bytes()).hexdigest()!=payload['sha256']:raise ValueError('ไฟล์อัปเดตเสียหาย ไม่แก้โปรแกรมเดิม')
 with zipfile.ZipFile(archive) as z:
  entries=z.infolist()
  if len(entries)>5000 or sum(e.file_size for e in entries)>MAX_UNPACKED:raise ValueError('ชุดอัปเดตขยายใหญ่เกินขอบเขต')
  names=[e.filename for e in entries if not e.is_dir()]
  if len({n.casefold() for n in names})!=len(names):raise ValueError('ชุดอัปเดตมีไฟล์ชื่อซ้ำ')
  manifest=json.loads(z.read('INWOUM-EDIT/INSTALL-MANIFEST.json'))
  if manifest.get('version')!=payload['version'] or not isinstance(manifest.get('files'),dict):raise ValueError('รายการไฟล์ไม่ตรงเวอร์ชัน')
  files=manifest['files']
  if not {'server.py','runtime_contract.py','START.cmd'}.issubset(files):raise ValueError('ชุดโปรแกรมไม่ครบ')
  # The publisher key and source are pinned. Future signed packages must carry exactly the same source.
  expected={'INWOUM-EDIT/'+name for name in files}|{'INWOUM-EDIT/INSTALL-MANIFEST.json'}
  if set(names)!=expected:raise ValueError('มีไฟล์นอกชุดอัปเดตที่ระบุ')
  if len({n.casefold() for n in files})!=len(files):raise ValueError('รายการไฟล์ชื่อซ้ำ')
  for info in entries:
   if stat.S_ISLNK(info.external_attr>>16):raise ValueError('ไม่อนุญาตลิงก์ใน ZIP')
  for name,digest in files.items():
   if name=='update-source.json':
    if target and z.read('INWOUM-EDIT/'+name)!=(target/name).read_bytes():raise ValueError('ชุดอัปเดตเปลี่ยนผู้เผยแพร่ ต้องติดตั้งด้วยตนเอง')
   else:safe_path(target or ROOT,name)
   if hashlib.sha256(z.read('INWOUM-EDIT/'+name)).hexdigest()!=digest:raise ValueError('ไฟล์ภายในเสียหาย: '+name)
 return manifest

def status():
 with GUARD:return dict(STATE)

def set_state(**values):
 with GUARD:STATE.update(values)

def busy():return status()['phase'] in BUSY_PHASES

def prepare(root,port,shutdown):
 """Runs on a background thread; shutdown only after download and full preflight."""
 try:
  found=check(root)
  if not found['available']:
   set_state(phase='current',message='เป็นเวอร์ชันล่าสุดแล้ว · '+found['current']);return
  import uuid
  folder=root/'.updates'/uuid.uuid4().hex;folder.mkdir(parents=True)
  archive=folder/'program.zip';document=found['document'];payload=verify_document(document,source(root))
  set_state(phase='downloading',message='ดาวน์โหลดรุ่น '+payload['version'],latest=payload['version'],percent=0)
  def progress(n,total):set_state(percent=min(99,int(n*100/payload['size'])))
  download(found['url'],payload['size'],archive,progress)
  validate_archive(archive,payload,root)
  (folder/'update.json').write_text(json.dumps(document,ensure_ascii=False),'utf8')
  # Copy trusted helper and source BEFORE any files are replaced.
  for name in ('app_updater.py','update_worker.py','update-source.json'):shutil.copy2(root/name,folder/name)
  args=[sys.executable,str(folder/'update_worker.py'),'--root',str(root),'--pid',str(os.getpid()),'--port',str(port)]
  log=(folder/'worker.log').open('ab')
  try:subprocess.Popen(args,cwd=str(folder),stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),close_fds=True)
  finally:log.close()
  set_state(phase='restarting',message='ตรวจไฟล์ผ่านแล้ว กำลังสำรอง ติดตั้ง และเปิดโปรแกรมใหม่',percent=100,latest=payload['version'])
  shutdown()
 except Exception as error:set_state(phase='error',message=str(error)[:500])
