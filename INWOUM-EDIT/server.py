import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse, unquote
import uuid
import webbrowser

import bootstrap
import engine
import runtime_contract as runtime
import runtime_session
import app_updater
UPDATE_NONCE = os.environ.pop('INWOUM_UPDATE_NONCE', '')

TOKEN = os.environ.pop('AI_EDIT_SESSION_TOKEN', '') or secrets.token_urlsafe(32)
JOBS = {}
LOCK = threading.RLock()
ACTIVE = None
JOB_ROOT = bootstrap.ROOT / 'jobs'

def open_job_folder(job_id):
    if not isinstance(job_id,str) or job_id not in JOBS or Path(job_id).name!=job_id:
        raise ValueError('ไม่พบงาน')
    root=JOB_ROOT.resolve();folder=(root/job_id).resolve()
    if folder==root or not folder.is_relative_to(root) or not folder.is_dir():
        raise ValueError('โฟลเดอร์งานไม่ถูกต้อง')
    os.startfile(str(folder))

PREPARED_FILES=('intake.json','audio-identity.json','transcript.json','vad.json','source.xml','source-sha256.json','timeline.wav')

def prepared_source(job_id):
    seen=set()
    while job_id in JOBS and job_id not in seen:
        seen.add(job_id);folder=JOB_ROOT/job_id
        if all((folder/name).is_file() for name in PREPARED_FILES):return job_id
        meta=folder/'grouping-source.json'
        if not meta.exists():break
        job_id=json.loads(meta.read_text('utf8'))['source_job']
    raise ValueError('ไม่พบงานต้นทางที่มีเสียงและข้อความครบ')

def copy_prepared(source,folder):
    import shutil
    if source.resolve()==folder.resolve():return
    for name in PREPARED_FILES:shutil.copy2(source/name,folder/name)
    for name in ('api-transcription.json','capcut-transcription.json','subtitle-asr.json'):
        if (source/name).is_file():shutil.copy2(source/name,folder/name)

class LocalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address=False
    def server_bind(self):
        if os.name=='nt':self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


def public(job):
    return {k: v for k, v in job.items() if k not in ('cancel',)}


def update(job, **event):
    with LOCK:
        job.update(event)
        job['updated'] = time.time()
        if 'message' in event:
            job['events'] = (job.get('events', []) + [event['message']])[-60:]
        engine.save(JOB_ROOT / job['id'] / 'status.json', public(job))


def pick(kind):
    script = "import tkinter as t;from tkinter import filedialog as f;import json;r=t.Tk();r.withdraw();r.attributes('-topmost',True);p="
    script += ("f.askopenfilename(filetypes=[('Premiere XML','*.xml')])" if kind == 'xml' else
               "f.askopenfilename(filetypes=[('XML / Audio / Video','*.xml *.wav *.mp3 *.m4a *.aac *.flac *.mp4 *.mov *.mkv *.webm'),('All files','*.*')])" if kind=='subtitle' else 'f.askdirectory()')
    script += ';r.destroy();print(json.dumps(p))'
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=300,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('เปิดหน้าต่างเลือกไฟล์ไม่ได้ วาง path ในช่องแทนได้')
    return json.loads(result.stdout)


def worker(job, config, key, action, plan=None, source_job=None):
    global ACTIVE
    folder = JOB_ROOT / job['id']
    try:
        if action == 'dead_air':
            import dead_air_mode
            dead_air_mode.run(config,folder,lambda **e:update(job,**e),job['cancel'].is_set)
        elif action == 'super_rebuild':
            import super_edit
            super_edit.rebuild(folder,plan,lambda **e:update(job,**e),job['cancel'].is_set)
        elif action == 'super':
            import super_mode
            super_mode.run(config,folder,key,lambda **e:update(job,**e),job['cancel'].is_set)
        elif action == 'subtitle':
            import subtitle_mode
            if source_job:copy_prepared(JOB_ROOT/source_job,folder)
            subtitle_mode.run(config,folder,lambda **e:update(job,**e),job['cancel'].is_set,key=key)
        elif action == 'group_preview':
            if config.get('ruleset')==runtime.RULESET:
                import sales_map as group_preview
            else:
                import group_preview
            group_preview.run(JOB_ROOT/source_job,folder,config,key,lambda **e:update(job,**e),job['cancel'].is_set)
            if config.get('auto_export') and config.get('ruleset')==runtime.RULESET:
                origin=JOB_ROOT/prepared_source(source_job)
                copy_prepared(origin,folder)
                base=json.loads((origin/'config.json').read_text('utf8'))
                base=runtime.configure(dict(base,**{k:config[k] for k in ('model','effort','notes','auto_export','sales_style','audio_crossfade') if k in config},approved_story_map_job=folder.name))
                base.pop('stop_after',None)
                engine.run(base,folder,key,lambda **e:update(job,**e),job['cancel'].is_set)
        elif action == 'text_preview':
            import text_preview
            text_preview.run(JOB_ROOT/source_job,folder,config,key,lambda **e:update(job,**e),job['cancel'].is_set)
        elif action == 'asr_compare':
            import asr_compare
            asr_compare.run(JOB_ROOT / source_job, folder, key, config,
                            lambda **e: update(job, **e), job['cancel'].is_set)
        elif action in ('replan','assemble_map'):
            import shutil
            for name in ('intake.json', 'audio-identity.json', 'transcript.json', 'vad.json',
                         'source.xml', 'source-sha256.json', 'timeline.wav'):
                shutil.copy2(JOB_ROOT / source_job / name, folder / name)
            for name in ('api-transcription.json', 'capcut-transcription.json'):
                if (JOB_ROOT / source_job / name).is_file():
                    shutil.copy2(JOB_ROOT / source_job / name, folder / name)
            from editorial_cache import reuse_upgrade
            if action=='replan' and config.get('ruleset')!=runtime.RULESET and reuse_upgrade(JOB_ROOT/source_job,folder,config):
                update(job,message='ใช้คำตอบ FULL 4 ขั้นเดิมที่ตรงข้อมูล เรียก API เฉพาะขั้นเกลารอยต่อใหม่')
            engine.run(config, folder, key, lambda **e: update(job, **e), job['cancel'].is_set)
        elif action == 'demo':
            from demo import create
            from audio_io import write_pcm
            source, pcm, transcript = create(folder / 'demo-media')
            config = {**config, 'xml': str(source), 'mode': 'local', 'audio_track': 1}
            project = engine.intake(source, audio_track=1)
            engine.save(folder / 'intake.json', engine.identity(project))
            engine.prepare_source(project, folder)
            write_pcm(folder / 'timeline.wav', pcm)
            engine.save(folder / 'transcript.json', transcript)
            engine.save(folder / 'vad.json', None)
            import hashlib
            engine.save(folder / 'audio-identity.json', dict(pcm_sha256=hashlib.sha256(pcm.tobytes()).hexdigest(), samples=len(pcm)))
            engine.run(config, folder, '', lambda **e: update(job, **e), job['cancel'].is_set,
                       prepared=(project, pcm, transcript, None))
        elif action == 'hook_library':
            engine.add_hook_library(folder,key,lambda **e:update(job,**e),job['cancel'].is_set)
        elif action == 'rebuild':
            engine.rebuild(folder, config, plan, lambda **e: update(job, **e), job['cancel'].is_set)
        else:
            engine.run(config, folder, key, lambda **e: update(job, **e), job['cancel'].is_set)
        update(job, status='done')
    except engine.Cancelled:
        update(job, status='cancelled', message='หยุดแล้ว เก็บข้อมูลที่ทำเสร็จไว้')
    except Exception as error:
        message = str(error)
        if key:
            message = message.replace(key, '[redacted]')
        update(job, status='error', message=message[:800])
    finally:
        key = ''
        with LOCK:
            ACTIVE = None


def launch(config, key='', action='run', job_id=None, plan=None, source_job=None):
    if action=='hook_library' or config.get('sales_style')=='sales_2_2':raise ValueError('พักโหมด 2.2 แล้ว กรุณาเลือกโหมด 2.1')
    global ACTIVE
    with LOCK:
        if app_updater.busy():
            raise ValueError('กำลังอัปเดตโปรแกรม กรุณารอให้เปิดใหม่ก่อน')
        if ACTIVE is not None:
            raise ValueError('มีงานกำลังทำอยู่ หยุดหรือรอให้เสร็จก่อน')
        if job_id is not None and job_id not in JOBS:
            raise ValueError('ไม่พบงาน')
        if action in ('replan', 'asr_compare', 'text_preview','group_preview','assemble_map') and (source_job not in JOBS or JOBS[source_job]['status'] == 'running'):
            raise ValueError('ไม่พบงานต้นทางที่พร้อมวิเคราะห์ใหม่')
        if action=='super_rebuild':
            import super_edit
            if job_id is None:raise ValueError('เลือกงาน Super ก่อนแก้ไข')
            super_edit.prepare(JOB_ROOT/job_id,plan)
        if action not in ('rebuild','text_preview','group_preview','super_rebuild','hook_library'):
            settings = bootstrap.ROOT / 'local-runtime.json'
            if settings.exists() and not config.get('shared_home'):
                config = {**config, 'shared_home': json.loads(settings.read_text(encoding='utf8')).get('shared_home', '')}
            if action=='dead_air':
                import dead_air_mode
                config=dead_air_mode.validate_config(config)
            elif action=='super':
                import super_mode
                config=super_mode.validate_config(config)
            elif action=='subtitle':
                import subtitle_mode
                config=subtitle_mode.validate_config(config)
            else:engine.validate_config(config)
        if (action in ('super','super_rebuild') or config.get('subtitle_ai_polish') or config.get('ruleset')==runtime.RULESET) and runtime.status()['integrity']!='verified':
            raise ValueError('ชุดโปรแกรมไม่ตรง'+runtime.VERSION+' ตรวจไฟล์ติดตั้งก่อนเริ่มAPI')
        if not job_id:
            job_id = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
            (JOB_ROOT / job_id).mkdir(parents=True)
            JOBS[job_id] = dict(id=job_id, events=[], result=None)
        job = JOBS[job_id]
        if action in ('subtitle','super','dead_air'):engine.save(JOB_ROOT/job_id/'config.json',{k:v for k,v in config.items() if k!='api_key'})
        job.update(cancel=threading.Event(), status='running', started=time.time())
        ACTIVE = job_id
        update(job, stage='starting', message='เริ่มทำงาน')
        threading.Thread(target=worker, args=(job, config, key, action, plan, source_job), daemon=True).start()
        return public(job)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def auth(self):
        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
            raise ValueError('Host ไม่ถูกต้อง')
        if not hmac.compare_digest(self.headers.get('X-Token', ''), TOKEN):
            raise ValueError('Session ไม่ตรง กรุณารีเฟรชหน้าเว็บ')
        origin = self.headers.get('Origin')
        if origin and origin != f'http://127.0.0.1:{self.server.server_port}':
            raise ValueError('Origin ไม่ถูกต้อง')

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        size = path.stat().st_size
        first, last = 0, size - 1
        partial = self.headers.get('Range')
        if partial:
            try:
                unit, span = partial.split('=')
                a, b = span.split('-')
                if unit != 'bytes' or not a or ',' in span:
                    raise ValueError()
                first, last = int(a), min(size - 1, int(b)) if b else size - 1
                if not 0 <= first <= last < size:
                    raise ValueError()
            except ValueError:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.end_headers()
                return
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', 'application/x-subrip; charset=utf-8' if path.suffix=='.srt' else (mimetypes.guess_type(str(path))[0] or 'application/octet-stream'))
        if path.suffix=='.srt':self.send_header('Content-Disposition','attachment; filename="SUBTITLES.srt"')
        elif path.suffix.lower() in ('.xml','.csv'):
            from urllib.parse import quote
            self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(path.name,safe=''))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(last - first + 1))
        if partial:
            self.send_header('Content-Range', f'bytes {first}-{last}/{size}')
        self.end_headers()
        with path.open('rb') as f:
            f.seek(first)
            remaining = last - first + 1
            while remaining:
                data = f.read(min(65536, remaining))
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)

    def do_GET(self):
        try:
            path = unquote(urlparse(self.path).path)
            if path == '/update-health':
                self.send_json(dict(version=runtime.VERSION,update_nonce=UPDATE_NONCE,instance_id=runtime_session.identity(bootstrap.ROOT),pid=os.getpid()))
                return
            if path == '/update-ui.js':
                self.send_file(bootstrap.ROOT / 'web' / 'update-ui.js')
                return
            if path == '/unused-report-ui.js':
                self.send_file(bootstrap.ROOT / 'web' / 'unused-report-ui.js')
                return
            if path == '/favicon.svg':
                self.send_file(bootstrap.ROOT / 'web' / 'favicon.svg')
                return
            if path == '/asr-ui.js':
                self.send_file(bootstrap.ROOT / 'web' / 'asr-ui.js')
                return
            if path == '/dead-air-ui.js':
                self.send_file(bootstrap.ROOT / 'web' / 'dead-air-ui.js')
                return
            if path == '/super-ui.js':
                self.send_file(bootstrap.ROOT / 'web' / 'super-ui.js')
                return
            if path == '/super-editor.js':
                self.send_file(bootstrap.ROOT / 'web' / 'super-editor.js')
                return
            if path == '/super-font':
                self.send_file(bootstrap.ROOT / 'web/fonts/Kanit-Bold.ttf')
                return
            if path == '/':
                body = (bootstrap.ROOT / 'web' / 'index.html').read_text(encoding='utf8').replace('__TOKEN__', TOKEN).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            # Audio/download links cannot carry X-Token; use a scoped session token in query.
            if path.startswith('/files/'):
                from urllib.parse import parse_qs
                token = parse_qs(urlparse(self.path).query).get('token', [''])[0]
                if not hmac.compare_digest(token, TOKEN):
                    raise ValueError('ไม่มีสิทธิ์อ่านไฟล์')
                relative = Path(path[len('/files/'):])
                file = (JOB_ROOT / relative).resolve()
                if not file.is_relative_to(JOB_ROOT.resolve()) or not file.is_file() or file.suffix not in ('.wav', '.xml', '.csv', '.md', '.srt'):
                    raise ValueError('ไม่พบไฟล์')
                self.send_file(file)
                return
            self.auth()
            if path == '/api/runtime':
                self.send_json(dict(runtime.status(),models=runtime.MODELS))
            elif path == '/api/update-status':
                result=app_updater.status()
                result.update(current=runtime.VERSION,repository=app_updater.source()['repository'])
                previous=bootstrap.ROOT/'.updates'/'last-result.json'
                if previous.is_file():
                    try:result['previous']=json.loads(previous.read_text('utf8'))
                    except (ValueError,OSError):pass
                self.send_json(result)
            elif path == '/api/jobs':
                with LOCK:
                    self.send_json([public(j) for j in JOBS.values()][::-1])
            elif path.startswith('/api/token-usage/'):
                job_id=path.removeprefix('/api/token-usage/')
                if job_id not in JOBS or Path(job_id).name!=job_id:raise ValueError('ไม่พบงาน')
                import token_usage
                import fx_rates
                self.send_json(fx_rates.attach(token_usage.read(JOB_ROOT/job_id),JOB_ROOT/job_id))
            elif path == '/api/subtitle-sources':
                sources=[]
                for job_id,job in list(JOBS.items()):
                    folder=JOB_ROOT/job_id
                    if job['status']=='running' or not all((folder/name).is_file() for name in PREPARED_FILES):continue
                    config=json.loads((folder/'config.json').read_text('utf8'))
                    if config.get('transcription')=='capcut' or (folder/'subtitle-asr.json').exists():sources.append(dict(id=job_id,source=config.get('xml') or config.get('subtitle_source','')))
                self.send_json(sources)
            elif path.startswith('/api/job/'):
                job_id = path.removeprefix('/api/job/')
                if job_id not in JOBS:
                    raise ValueError('ไม่พบงาน')
                folder = JOB_ROOT / job_id
                result = dict(job=public(JOBS[job_id]))
                for name in ('catalog', 'plan', 'silence', 'timings', 'config', 'api-usage', 'asr-comparison','text-preview','subtitles','super-result','dead-air-result'):
                    f = folder / (name + '.json')
                    result[name] = json.loads(f.read_text(encoding='utf8')) if f.exists() else None
                latest = folder / 'latest.json'
                result['transcript_review_available']=(folder/'TRANSCRIPT-REVIEW.md').is_file()
                result['group_review_available']=(folder/'CONTENT-GROUPS-REVIEW.md').is_file()
                result['draft_review_available']=(folder/'DRAFT-REVIEW.md').is_file()
                result['map_candidate_available']=(folder/'LUNA-CANDIDATE-REVIEW.md').is_file()
                if latest.exists():
                    revision = json.loads(latest.read_text(encoding='utf8'))['folder']
                    if Path(revision).name != revision:
                        raise ValueError('Revision ไม่ถูกต้อง')
                    result['revision'] = revision
                    result['usage'] = json.loads((folder / revision / 'USAGE.json').read_text(encoding='utf8'))
                    coverage = folder / revision / 'source-coverage.json'
                    result['coverage'] = json.loads(coverage.read_text(encoding='utf8')) if coverage.exists() else None
                    result['full_coverage_available'] = (folder / 'FULL-COVERAGE.md').is_file()
                    result['fact_coverage_available'] = (folder / 'FACT-COVERAGE.md').is_file()
                    result['collection_review_available'] = (folder / 'COLLECTION-REVIEW.md').is_file()
                    result['editorial_review_available'] = (folder / 'EDITORIAL-REVIEW.md').is_file()
                    result['workflow_report_available'] = (folder / 'SELF-EDIT-WORKFLOW.md').is_file()
                    result['unused_reasons_available'] = (folder / revision / 'UNUSED-REASONS.md').is_file()
                    import unused_report
                    try:
                        result['unused_report']=dict(unused_report.build(result['usage'],result['coverage'],result['catalog'] or [],revision),available=True)
                    except (ValueError,KeyError,TypeError):
                        result['unused_report']=dict(available=False,reason='บันทึกงานนี้ไม่ครบพอสำหรับอธิบายช่วงที่ไม่ได้ใช้')
                self.send_json(result)
            else:
                self.send_json(dict(error='ไม่พบหน้า'), 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.send_json(dict(error=str(error)[:400]), 400)

    def do_POST(self):
        try:
            self.auth()
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 2_000_000:
                raise ValueError('ขนาดข้อมูลไม่ถูกต้อง')
            data = json.loads(self.rfile.read(length))
            path = urlparse(self.path).path
            if path == '/api/update-start':
                with LOCK:
                    if ACTIVE is not None or any(j.get('status')=='running' for j in JOBS.values()):
                        raise ValueError('มีงานกำลังทำอยู่ รอให้เสร็จก่อนอัปเดต')
                    if app_updater.busy():raise ValueError('กำลังอัปเดตอยู่แล้ว')
                    app_updater.set_state(phase='checking',message='กำลังตรวจรุ่นล่าสุดบน GitHub',percent=0)
                    threading.Thread(target=app_updater.prepare,args=(bootstrap.ROOT,self.server.server_port,self.server.shutdown),daemon=True).start()
                self.send_json(dict(started=True))
            elif path == '/api/open-job-folder':
                open_job_folder(data.get('id'))
                self.send_json(dict(opened=True))
            elif path == '/api/dead-air-inspect':
                import dead_air_mode
                self.send_json(dead_air_mode.inspect(data))
            elif path == '/api/dead-air':
                import dead_air_mode
                self.send_json(launch(dead_air_mode.validate_config(data),action='dead_air'))
            elif path == '/api/super':
                import super_mode
                config=super_mode.validate_config(data)
                key=data.get('api_key','')
                if not isinstance(key,str) or not key.strip():raise ValueError('ใส่ OpenAI API key ก่อนทำ Super')
                self.send_json(launch(config,key,action='super'))
            elif path == '/api/super-save':
                job_id=data.get('id')
                if not isinstance(job_id,str) or job_id not in JOBS:raise ValueError('ไม่พบงาน Super')
                self.send_json(launch({},action='super_rebuild',job_id=job_id,plan={k:data.get(k) for k in ('base_revision','items','timing_policy')}))
            elif path == '/api/subtitle':
                import subtitle_mode
                config=dict(content_mode='subtitles',subtitle_source=data.get('subtitle_source',''),
                    audio_track=int(data.get('audio_track',1)),relink=data.get('relink',''),transcription='capcut',profile='compact',
                    subtitle_maiyamok=data.get('subtitle_maiyamok',True),subtitle_provider=data.get('subtitle_provider','capcut'),
                    subtitle_chars=data.get('subtitle_chars',40),subtitle_ai_polish=data.get('subtitle_ai_polish',False),
                    subtitle_cleanup=data.get('subtitle_cleanup','off'),subtitle_keep_words=data.get('subtitle_keep_words',''),
                    subtitle_glossary=data.get('subtitle_glossary',''),model=data.get('model','gpt-6.1-terra'),effort=data.get('effort','medium'))
                if type(config['subtitle_maiyamok']) is not bool:raise ValueError('ตัวเลือกไม้ยมกต้องเป็นจริงหรือเท็จ')
                source_job=data.get('reuse_job') or None
                if source_job:
                    if source_job not in JOBS or JOBS[source_job]['status']=='running':raise ValueError('งานต้นทางไม่พร้อม')
                    folder=JOB_ROOT/source_job
                    if not all((folder/name).is_file() for name in PREPARED_FILES):raise ValueError('งานนี้ไม่มีเสียงและข้อความพร้อมใช้')
                    old=json.loads((folder/'config.json').read_text('utf8'))
                    if config['subtitle_provider']=='capcut' and old.get('transcription')!='capcut':raise ValueError('งานเดิมไม่ได้ถอดด้วย CapCut เลือกไฟล์เพื่อถอดใหม่ หรือเลือกตัวถอดเดิม')
                    config.update(subtitle_source=old.get('xml') or old.get('subtitle_source',''),audio_track=old.get('audio_track',1),relink=old.get('relink',''))
                config=subtitle_mode.validate_config(config)
                if config['subtitle_ai_polish'] and not str(data.get('api_key','')).strip():raise ValueError('ใส่ API key เพื่อเกลาคำด้วย AI')
                self.send_json(launch(config,data.get('api_key','') if config['subtitle_ai_polish'] else '',action='subtitle',source_job=source_job))
            elif path == '/api/subtitle-save':
                import subtitle_mode
                job_id=data.get('id')
                if job_id not in JOBS or JOBS[job_id]['status']=='running':raise ValueError('งานยังไม่พร้อมบันทึกซับ')
                folder=JOB_ROOT/job_id
                saved=json.loads((folder/'subtitles.json').read_text('utf8'));texts=data.get('texts')
                if not isinstance(texts,list) or len(texts)!=len(saved['cues']):raise ValueError('จำนวนซับเปลี่ยน กรุณาโหลดใหม่')
                cues=[dict(c,text=text) for c,text in zip(saved['cues'],texts)]
                result=subtitle_mode.write_result(folder,cues,soft_limit=saved.get('soft_limit',40),metadata=saved)
                if 'alignment_warnings' in saved:
                    result['alignment_warnings']=saved['alignment_warnings']
                    engine.save(folder/'subtitles.json',result)
                if 'maiyamok' in saved:
                    result['maiyamok']=saved['maiyamok']
                    engine.save(folder/'subtitles.json',result)
                if 'provider' in saved:
                    result['provider']=saved['provider']
                    engine.save(folder/'subtitles.json',result)
                with LOCK:
                    JOBS[job_id]['result']=dict(JOBS[job_id].get('result') or {},srt_file=result['file'])
                    update(JOBS[job_id],message='บันทึก SRT ฉบับแก้ไขแล้ว')
                self.send_json(result)
            elif path == '/api/models':
                from model_catalog import list_account_models
                self.send_json(dict(models=list_account_models(data.get('api_key','')),source='account',compatibility='Model listing is availability only; generation support is not verified.'))
            elif path == '/api/pick':
                self.send_json(dict(path=pick(data.get('kind'))))
            elif path == '/api/project-cover':
                import project_cover
                self.send_json(project_cover.create(data,bootstrap.ROOT/'cache'/'project-covers'))
            elif path == '/api/inspect':
                if data.get('content_mode')=='super':
                    import super_mode
                    self.send_json(super_mode.inspect(data));return
                p = engine.intake(data['xml'], data.get('relink') or None, int(data.get('audio_track', 1)))
                self.send_json(dict(seconds=p['duration'] / 50, videos=p['videos'], audios=p['audios']))
            elif path in ('/api/run', '/api/demo'):
                key = data.pop('api_key', '')
                if path == '/api/run' and data.get('mode') == 'ai':
                    data['editorial_policy'] = 'story-v4'
                    data['silence_policy'] = 'speech-v3'
                    data['phrase_granularity'] = 'lexical-review'
                    data['editorial_rules_version'] = '1.5'
                    data['editorial_workflow'] = 'self-edit-v6'
                    data['editorial_request_version'] = 'parallel-facts-v2'
                    data['hook_policy'] = 'hook-sales-v1'
                    data=runtime.configure(data)
                self.send_json(launch(data, key, 'demo' if path == '/api/demo' else 'run'))
            elif path == '/api/hook-library':
                raise ValueError('พักโหมด 2.2 และการเพิ่ม Hook สำรองแล้ว')
            elif path == '/api/rebuild':
                self.send_json(launch(data.get('changes', {}), action='rebuild', job_id=data['id'], plan=data['plan']))
            elif path == '/api/auto-xml':
                source_job=prepared_source(data['id'])
                config=json.loads((JOB_ROOT/source_job/'config.json').read_text('utf8'))
                for name in ('model','effort','notes','sales_style','single_style','audio_crossfade'):
                    if name in data:config[name]=data[name]
                for name in ('stop_after','approved_story_map_job','defer_map_ids','group_preview','refine_map_job','known_output_retry'):
                    config.pop(name,None)
                config=runtime.configure(dict(config,auto_export=True,mode='ai'))
                key=data.get('api_key','')
                if not key.strip():raise ValueError('ใส่ API key ก่อนทำจนได้ XML')
                self.send_json(launch(config,key,action='replan',source_job=source_job))
            elif path == '/api/replan':
                source_job = data['id']
                if source_job not in JOBS:
                    raise ValueError('ไม่พบงานต้นทาง')
                config = json.loads((JOB_ROOT / source_job / 'config.json').read_text(encoding='utf8'))
                for name in ('model', 'effort', 'notes','sales_style','single_style','audio_crossfade'):
                    if name in data:
                        config[name] = data[name]
                config['mode'] = 'ai'
                config['editorial_policy'] = 'story-v4'
                config['silence_policy'] = 'speech-v3'
                config['phrase_granularity'] = 'lexical-review'
                config['editorial_rules_version'] = '1.5'
                config['editorial_workflow'] = 'self-edit-v6'
                config['editorial_request_version'] = 'parallel-facts-v2'
                config['hook_policy'] = 'hook-sales-v1'
                config=runtime.configure(config)
                config.pop('stop_after',None)
                if (JOB_ROOT / source_job / 'plan.json').is_file():
                    config['editorial_seed_job'] = source_job
                key = data.get('api_key', '')
                if not key.strip():
                    raise ValueError('ใส่ API key ก่อนวิเคราะห์เนื้อหาใหม่')
                self.send_json(launch(config, key, action='replan', source_job=source_job))
            elif path == '/api/group-preview':
                source_job=data['id'];key=data.get('api_key','')
                if source_job not in JOBS or not (JOB_ROOT/source_job/'transcript.json').is_file():raise ValueError('เลือกงานที่ถอดข้อความแล้วก่อน')
                if not key.strip():raise ValueError('ใส่ API key ในหน้าโปรแกรมก่อนจัดกลุ่ม')
                original=json.loads((JOB_ROOT/source_job/'config.json').read_text('utf8'))
                model=data.get('model') or original.get('model');effort=data.get('effort') or original.get('effort','medium')
                if not isinstance(model,str) or not model.strip() or effort not in ('low','medium','high','default'):raise ValueError('โมเดลหรือระดับการคิดไม่ถูกต้อง')
                config=runtime.configure(dict(model=model,effort=effort,notes=data.get('notes',original.get('notes','')),sales_style=data.get('sales_style',original.get('sales_style','original')),group_preview=True,grouping_source=source_job,auto_export=False))
                self.send_json(launch(config,key,action='group_preview',source_job=source_job))
            elif path == '/api/assemble-map':
                map_id=data['id'];key=data.get('api_key','')
                if map_id not in JOBS or not (JOB_ROOT/map_id/'content-groups.json').is_file():raise ValueError('เลือกแผนใหม่ที่ตรวจผ่านก่อน')
                map_config=json.loads((JOB_ROOT/map_id/'config.json').read_text('utf8'))
                if map_config.get('ruleset')!=runtime.RULESET:raise ValueError('แผนนี้ใช้กฎเก่า ให้จัดกลุ่มใหม่ก่อนประกอบบท')
                meta=json.loads((JOB_ROOT/map_id/'grouping-source.json').read_text('utf8'))
                source_job=meta['source_job']
                if source_job not in JOBS or not key.strip():raise ValueError('ไม่พบต้นทางหรือยังไม่ใส่API key')
                config=json.loads((JOB_ROOT/source_job/'config.json').read_text('utf8'))
                config=runtime.configure(dict(config,auto_export=False,sales_style=data.get('sales_style',map_config.get('sales_style',config.get('sales_style','original')))));config.update(approved_story_map_job=map_id,stop_after='draft')
                config['defer_map_ids']=data.get('defer_map_ids',{})
                import sales_draft
                sales_draft.draft_schema(json.loads((JOB_ROOT/map_id/'content-groups.json').read_text('utf8')),config['defer_map_ids'])
                for name in ('model','effort','notes'):
                    if name in data:config[name]=data[name]
                self.send_json(launch(config,key,action='assemble_map',source_job=source_job))
            elif path == '/api/text-preview':
                source_job=data['id'];key=data.get('api_key','')
                if source_job not in JOBS or not (JOB_ROOT/source_job/'plan.json').is_file():raise ValueError('เลือกงานที่มีบทเรียบเรียงแล้วก่อน')
                if not key.strip():raise ValueError('ใส่ API key ในหน้าโปรแกรมก่อนทดสอบ')
                original=json.loads((JOB_ROOT/source_job/'config.json').read_text('utf8'))
                model=data.get('model') or original.get('model')
                if not isinstance(model,str) or not model.strip():raise ValueError('ระบุโมเดลก่อนทดสอบ')
                self.send_json(launch(dict(model=model,text_preview=True),key,action='text_preview',source_job=source_job))
            elif path == '/api/asr-compare':
                source_job = data['id']
                if source_job not in JOBS or not (JOB_ROOT / source_job / 'timeline.wav').exists():
                    raise ValueError('เลือกงานที่มีเสียงต้นฉบับพร้อมทดสอบก่อน')
                key = data.get('api_key', '')
                if not key.strip():
                    raise ValueError('ใส่ API key ในหน้าโปรแกรมก่อนทดสอบ')
                from api_transcription import MODELS
                model = data.get('asr_model', 'gpt-transcribe')
                if model not in MODELS:
                    raise ValueError('โมเดลถอดเสียงไม่ถูกต้อง')
                self.send_json(launch(dict(asr_model=model), key, action='asr_compare', source_job=source_job))
            elif path == '/api/retry':
                job_id = data['id']
                if job_id not in JOBS:
                    raise ValueError('ไม่พบงาน')
                config = json.loads((JOB_ROOT / job_id / 'config.json').read_text(encoding='utf8'))
                if config.get('content_mode')=='dead_air':
                    self.send_json(launch(config,action='dead_air',job_id=job_id))
                    return
                if config.get('content_mode')=='super':
                    self.send_json(launch(config,data.get('api_key',''),action='super',job_id=job_id))
                    return
                if config.get('content_mode')=='subtitles':
                    self.send_json(launch(config,data.get('api_key','') if config.get('subtitle_ai_polish') else '',action='subtitle',job_id=job_id))
                    return
                if config.get('editorial_workflow')=='sales-v8':
                    config=runtime.configure(dict(config,auto_export=True,sales_style=config.get('sales_style','original')))
                import sales_map
                if sales_map.is_map_job(config,JOB_ROOT/job_id):
                    repaired=sales_map.validation_retry(JOB_ROOT/job_id,config)
                    if repaired:
                        self.send_json(launch(repaired,data.get('api_key',''),action='group_preview',source_job=config['grouping_source']))
                        return
                    ledger_file=JOB_ROOT/job_id/'api-usage.json'
                    ledger=json.loads(ledger_file.read_text('utf8')) if ledger_file.exists() else []
                    output_retry=sales_map.output_limit_retry(JOB_ROOT/job_id,config)
                    if output_retry:
                        # Explicit retry action only; no background paid retry or unknown resend.
                        self.send_json(launch(output_retry,data.get('api_key',''),action='group_preview',source_job=config['grouping_source']))
                        return
                    if config.get('ruleset')==runtime.RULESET and config.get('map_revision')!=sales_map.MAP_REVISION:
                        config.update(map_revision=sales_map.MAP_REVISION,refine_map_job=job_id)
                        config['notes']=(data.get('notes') or config.get('notes',''))
                        self.send_json(launch(config,data.get('api_key',''),action='group_preview',source_job=config['grouping_source']))
                        return
                    self.send_json(launch(config,data.get('api_key',''),action='group_preview',job_id=job_id,source_job=config['grouping_source']))
                    return
                if config.get('text_preview'):
                    self.send_json(launch(config,data.get('api_key',''),action='text_preview',job_id=job_id,source_job=config['text_preview_source']))
                    return
                if config.get('editorial_policy') == 'story-v2':
                    raise ValueError('งานนี้ใช้คำสั่งรุ่นเก่า เลือก “ให้ AI ทบทวนฟุตทั้งหมดใหม่” เพื่อใช้คำสั่งที่ปรับให้เบาลงโดยไม่ถอดเสียงซ้ำ')
                # Existing prepared data can be resumed without GPU/audio extraction.
                self.send_json(launch(config, data.get('api_key', ''), action='run', job_id=job_id))
            elif path == '/api/retry-unknown':
                job_id=data['id']
                key=data.get('api_key','')
                with LOCK:
                    if ACTIVE is not None or job_id not in JOBS or JOBS[job_id]['status']=='running':
                        raise ValueError('งานยังไม่พร้อมทำต่อ')
                    if data.get('confirm_unknown_charge') is not True or not key.strip():
                        raise ValueError('ต้องยืนยันค่าคำขอเดิมที่อาจถูกคิดแล้ว และใส่ API key ก่อน')
                    config=json.loads((JOB_ROOT/job_id/'config.json').read_text(encoding='utf8'))
                    if config.get('editorial_workflow') not in ('self-edit-v1','self-edit-v2','self-edit-v3','self-edit-v4','self-edit-v5','self-edit-v6'):
                        raise ValueError('รองรับงานเกลา 4 ขั้นเท่านั้น')
                    from story_editorial import authorize_unknown_retry
                    authorize_unknown_retry(JOB_ROOT/job_id,22 if config.get('editorial_workflow')=='self-edit-v6' else (10 if config.get('editorial_request_version')=='sparse-review-v4' else (8 if config.get('editorial_workflow') in ('self-edit-v3','self-edit-v4') else 6)))
                    self.send_json(launch(config,key,action='run',job_id=job_id))
            elif path == '/api/stop':
                with LOCK:
                    job = JOBS[data['id']]
                    if job['status'] == 'running':
                        job['cancel'].set()
                        update(job, message='รอหยุดหลังขั้นที่กำลังทำ คำขอ API ที่ส่งแล้วอาจยังมีค่าใช้จ่าย')
                self.send_json(dict(ok=True))
            else:
                self.send_json(dict(error='ไม่พบคำสั่ง'), 404)
        except Exception as error:
            self.send_json(dict(error=str(error)[:400]), 400)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8772)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    instance_lock=runtime_session.Lock(bootstrap.ROOT)
    if not instance_lock.acquire():
        for _ in range(60):
            found=runtime_session.existing(bootstrap.ROOT,args.port)
            if found:
                print('INWOUM EDIT already running: '+found['url'],flush=True)
                if not args.no_browser:webbrowser.open(found['url'])
                return
            time.sleep(.25)
        raise RuntimeError('The existing server is starting or closing. Try again shortly.')
    try:
        JOB_ROOT.mkdir(exist_ok=True)
        for f in JOB_ROOT.glob('*/status.json'):
            try:
                job = json.loads(f.read_text(encoding='utf8'))
                if job['id'] != f.parent.name:
                    continue
                if job.get('status') == 'running':
                    job.update(status='interrupted', message='งานหยุดเมื่อปิดโปรแกรม กดทำต่อได้')
                job['cancel'] = threading.Event()
                JOBS[job['id']] = job
            except (ValueError, KeyError, OSError):
                continue
        try:
            server = LocalHTTPServer(('127.0.0.1', args.port), Handler)
        except OSError:
            server = LocalHTTPServer(('127.0.0.1', 0), Handler)
        url = f'http://127.0.0.1:{server.server_port}'
        runtime_session.register(bootstrap.ROOT,url)
        print('INWOUM EDIT: ' + url, flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:server.serve_forever()
        finally:server.server_close()
    finally:instance_lock.release()


if __name__ == '__main__':
    main()
