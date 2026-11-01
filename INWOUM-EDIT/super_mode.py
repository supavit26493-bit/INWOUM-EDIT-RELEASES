"""Audio-only Super workflow; preserves source tracks and never invokes the cut engine."""
import copy,csv,hashlib,json,math,time,uuid,unicodedata
from pathlib import Path
from fractions import Fraction
from urllib.parse import urlparse,unquote
import xml.etree.ElementTree as E
import numpy as np
import bootstrap
from engine import Progress,save,Cancelled
from audio_io import read_pcm,write_pcm
from super_prompt import POLICY,request

def validate_config(config):
    source=Path(config.get('xml',''))
    if not str(config.get('xml','')).strip() or not source.is_file() or source.suffix.lower()!='.xml':raise ValueError('เลือกไฟล์ XML ที่มีอยู่ก่อนทำ Super')
    from model_catalog import model_info
    model=config.get('model','gpt-6.1-sol')
    if not model_info(model)['supported']:raise ValueError('โมเดลนี้ไม่รองรับการทำ Super')
    if config.get('effort','medium') not in ('low','medium','high','default'):raise ValueError('ระดับการคิดไม่ถูกต้อง')
    layout=config.get('super_layout','auto')
    if layout not in ('auto','landscape','portrait'):raise ValueError('สัดส่วน Super ไม่ถูกต้อง')
    track=config.get('audio_track',1)
    if type(track)!=int or track<1:raise ValueError('เลือกแทร็กเสียงหลักให้ถูกต้อง')
    # Whitelist: API keys never become persisted configuration.
    return dict(content_mode='super',xml=str(source.resolve()),relink=str(config.get('relink','')),
        audio_track=track,model=model,effort=config.get('effort','medium'),super_layout=layout,super_policy=POLICY)

def load_project(config):
    path=Path(config['xml'])
    if path.stat().st_size>50_000_000:raise ValueError('XML ต้องไม่เกิน 50 MB')
    raw=path.read_bytes()
    if b'<!ENTITY' in raw.upper():raise ValueError('ไม่รองรับ XML ที่ประกาศ ENTITY')
    root=E.fromstring(raw);sequences=root.findall('.//sequence')
    if len(sequences)!=1:raise ValueError('Super รองรับ XML ที่มีหนึ่ง sequence และไม่มี nested sequence')
    seq=sequences[0];tb=int(seq.findtext('rate/timebase','0'));ntsc=seq.findtext('rate/ntsc','FALSE')=='TRUE'
    if tb not in (24,25,30,50,60) or ntsc and tb not in (24,30,60):raise ValueError('เฟรมเรต XML นี้ยังไม่รองรับ Super')
    fps=Fraction(tb*1000,1001) if ntsc else Fraction(tb)
    duration=int(seq.findtext('duration','0'))
    if duration<=0:raise ValueError('XML ไม่มีความยาวที่ใช้ได้')
    chars=seq.find('media/video/format/samplecharacteristics')
    if chars is None:raise ValueError('XML ไม่มีขนาดภาพ')
    width=int(chars.findtext('width','0'));height=int(chars.findtext('height','0'))
    if min(width,height)<=0:raise ValueError('ขนาดภาพ XML ไม่ถูกต้อง')
    layout=config['super_layout'];portrait=height>width
    if layout!='auto' and (layout=='portrait')!=portrait:raise ValueError('สัดส่วนที่เลือกไม่ตรง XML กรุณาเลือกให้ตรงต้นฉบับ · โปรแกรมไม่ครอปหรือยืดภาพ')
    files={f.get('id'):f for f in root.iter('file') if f.find('pathurl') is not None}
    paths={}
    # Resolve only file definitions used by clips; preserve all other XML data.
    used={f.get('id') for c in seq.iter('clipitem') for f in c.findall('file')}
    for fid in used:
        if fid not in files:raise ValueError('XML มี file reference ไม่ครบ')
        url=urlparse(files[fid].findtext('pathurl'))
        if url.scheme!='file' or url.netloc not in ('','localhost'):raise ValueError('Super รองรับสื่อในเครื่องหรือ mapped drive เท่านั้น')
        value=unquote(url.path)
        if len(value)>3 and value[0]=='/' and value[2]==':':value=value[1:]
        p=Path(value)
        if not p.is_file():
            matches=list(Path(config['relink']).rglob(p.name)) if config['relink'] else []
            if len(matches)!=1:raise ValueError('หาไฟล์ไม่พบหรือชื่อซ้ำ: '+str(p)+' — เลือกโฟลเดอร์สื่อใน PRO เพื่อ relink')
            p=matches[0].resolve();files[fid].find('pathurl').text=p.as_uri()
        paths[fid]=p.resolve()
    tracks=seq.findall('media/audio/track');index=config['audio_track']-1
    if index>=len(tracks):raise ValueError('ไม่มีแทร็กเสียงหลักที่เลือก')
    track=tracks[index]
    if track.findtext('enabled','TRUE')=='FALSE' or track.find('transitionitem') is not None:raise ValueError('เลือกแทร็กเสียงที่เปิดไว้และไม่มี transition')
    clips=[]
    for c in track.findall('clipitem'):
        if c.findtext('enabled','TRUE')=='FALSE':continue
        f=c.find('file')
        if f is None or f.get('id') not in paths:raise ValueError('เสียงหลักต้องอ้างอิงไฟล์ปกติ ไม่มี nested หรือ generator')
        a,b,ci,co=[int(c.findtext(k,'-1')) for k in ('start','end','in','out')]
        clip_tb=int(c.findtext('rate/timebase',str(tb)));clip_ntsc=c.findtext('rate/ntsc','TRUE' if ntsc else 'FALSE')=='TRUE'
        if (clip_tb,clip_ntsc)!=(tb,ntsc):raise ValueError('เสียงหลักมีเฟรมเรตคลิปต่างจาก sequence กรุณาส่งออกเสียงรวมแล้วนำเข้า XML อีกครั้ง')
        if min(a,ci)<0 or b<=a or b>duration or b-a!=co-ci or c.find('.//speed') is not None or any(e.findtext('effectid') in ('timeremap','Time Remap') for e in c.findall('.//effect')):raise ValueError('เสียงหลักต้องไม่มี speed change หรือ transition')
        clips.append(dict(start=a,end=b,in_frame=ci,path=str(paths[f.get('id')]),channel=int(c.findtext('sourcetrack/trackindex','1'))))
    clips.sort(key=lambda c:c['start'])
    if not clips or any(a['end']>b['start'] for a,b in zip(clips,clips[1:])):raise ValueError('เสียงหลักว่างหรือมีคลิปซ้อนกัน')
    identity=dict(xml_sha256=hashlib.sha256(raw).hexdigest(),audio_track=config['audio_track'],clips=clips,
        media=[dict(path=str(p),bytes=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns) for p in sorted(set(paths.values()))])
    return dict(root=root,seq=seq,fps=fps,duration=duration,seconds=float(duration/fps),width=width,height=height,
        portrait=portrait,clips=clips,identity=identity,font_size=100*width/1920)

def inspect(config):
    p=load_project(validate_config(config))
    return dict(seconds=p['seconds'],width=p['width'],height=p['height'],fps=float(p['fps']),
        name=p['seq'].findtext('name'),videos=[],audios=[],super=True)

def extract(p,folder,progress):
    import av
    fps=float(p['fps']);pcm=np.zeros(round(p['seconds']*16000),dtype=np.int16)
    for i,c in enumerate(p['clips'],1):
        progress(message=f'อ่านเสียงทำ Super {i}/{len(p["clips"])}')
        begin=c['in_frame']/fps;seconds=(c['end']-c['start'])/fps
        target=np.zeros(round(seconds*16000),dtype=np.int16);filled=np.zeros(len(target),dtype=bool)
        with av.open(c['path']) as container:
            channel=c['channel']-1;stream=None
            for s in container.streams.audio:
                if 0<=channel<len(s.layout.channels):stream=s;break
                channel-=len(s.layout.channels)
            if stream is None:raise ValueError('ไม่พบช่องเสียงที่ XML อ้างอิง')
            container.seek(int(max(0,begin-2)/stream.time_base),stream=stream)
            resampler=av.AudioResampler(format='fltp',layout=stream.layout,rate=16000)
            def accept(block):
                if block.pts is None:raise ValueError('เสียงไม่มี timestamp')
                pos=round((float(block.pts*block.time_base)-begin)*16000)
                data=block.to_ndarray()[channel];left=max(0,pos);right=min(len(target),pos+len(data))
                if left<right:
                    target[left:right]=np.clip(data[left-pos:right-pos]*32768,-32768,32767).astype(np.int16);filled[left:right]=True
            for frame in container.decode(stream):
                if progress.cancelled():raise Cancelled()
                if frame.time is None:raise ValueError('เสียงไม่มี timestamp')
                if frame.time>begin+seconds+1:break
                for block in resampler.resample(frame):accept(block)
            for block in resampler.resample(None):accept(block)
        if filled.mean()<.99:raise ValueError('อ่านเสียงจากคลิป XML ได้ไม่ครบ')
        a=round(c['start']/fps*16000);b=min(len(pcm),a+len(target));pcm[a:b]=target[:b-a]
    write_pcm(folder/'timeline.wav',pcm);return pcm

def transcribe(pcm,folder,progress):
    import capcut_asr
    from dataclasses import asdict
    expected=capcut_asr.contract(pcm);cache=bootstrap.ROOT/'cache'/'super-capcut';cache.mkdir(parents=True,exist_ok=True)
    local=folder/'super-capcut.json';shared=cache/(expected['audio_sha256']+'.json')
    record=None
    for path in (local,shared,bootstrap.ROOT/'cache'/'capcut'/(expected['audio_sha256']+'.json')):
        if path.exists():
            candidate=json.loads(path.read_text('utf8'))
            if candidate.get('contract')==expected:
                record=candidate;break
    if record is not None and record.get('status')!='completed':raise ValueError('คำขอ CapCut เดิมยังไม่ทราบผล ไม่ส่งซ้ำอัตโนมัติ')
    if record is None:
        record=dict(contract=expected,status='upload_sent');save(local,record)
        client=capcut_asr.make_client()
        try:
            progress(message='ส่งเสียงไป CapCut เพื่อทำ Super · ไม่ต้องจับเวลาคำบน GPU')
            upload=client.upload_audio(folder/'timeline.wav')
            record['status']='submission_sent';save(local,record)
            created=client.create_stt_task(upload.vid,upload.md5,upload.duration_ms,language='th-TH',use_translation=False)
            tasks=(created.get('data') or {}).get('tasks') or []
            if not tasks:raise ValueError('CapCut ไม่ส่ง task')
            task=tasks[0];bind=task.get('bind_id') or (created.get('data') or {}).get('bind_id') or '';deadline=time.monotonic()+600
            while time.monotonic()<deadline:
                progress(message='รอ CapCut ถอดเสียงเป็นท่อน · ไม่ส่งคำขอสร้างซ้ำ')
                response=client.query_stt_task(task['id'],task['token'],bind);rows=(response.get('data') or {}).get('tasks') or []
                state=str(rows[0].get('status','')).lower() if rows else ''
                if state in ('success','succeed','succeeded','completed','done'):
                    parsed=asdict(client.extract_subtitles(response));record['utterances']=[dict(text=u['text'],start_time=u['start_time'],end_time=u['end_time']) for u in parsed['utterances']]
                    record['status']='completed';save(local,record);break
                if state in ('failed','failure','error'):raise ValueError('CapCut ทำงานไม่สำเร็จ')
                for _ in range(4):time.sleep(1);progress()
            else:raise TimeoutError('CapCut หมดเวลารอ')
        finally:client.session.close()
    # Super needs block timestamps only; the old 28s forced-alignment limit does not apply.
    blocks=record.get('utterances');previous=0
    if not isinstance(blocks,list) or not blocks:raise ValueError('CapCut ไม่ส่งข้อความถอดเสียง')
    for u in blocks:
        a,b=float(u['start_time'])/1000,float(u['end_time'])/1000
        if not isinstance(u.get('text'),str) or not u['text'].strip() or not all(math.isfinite(v) for v in (a,b)) or not 0<=a<b<=len(pcm)/16000+.04 or a<previous-.04:raise ValueError('เวลาข้อความ CapCut ไม่ตรงเสียง')
        previous=b
    save(local,record);save(shared,record)
    return [dict(id=i,start=u['start_time']/1000,end=u['end_time']/1000,text=u['text']) for i,u in enumerate(record['utterances'],1)]

def validate_plan(plan,units,p):
    from super_font import Metrics
    metrics=Metrics(bootstrap.ROOT/'web/fonts/Kanit-Bold.ttf');lookup={u['id']:u for u in units};previous=0
    if not isinstance(plan,dict) or not isinstance(plan.get('items'),list) or not plan['items']:raise ValueError('API ไม่ส่งรายการ Super')
    for i,x in enumerate(plan['items'],1):
        a,b=x.get('start'),x.get('end');text=x.get('text');ids=x.get('source_ids')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (a,b)) or not previous<=a<b<=p['seconds']+.001:raise ValueError(f'Super {i}: เวลาซ้อนหรือเกินคลิป')
        fa,fb=round(a*p['fps']),round(b*p['fps'])
        if not 0<=fa<fb<=p['duration'] or not 2.98<=b-a<=9.02:raise ValueError(f'Super {i}: ช่วงเวลาไม่ถูกต้อง')
        if not isinstance(text,str) or not text.strip() or any(c in text for c in '\r\n') or any(ord(c)<32 for c in text):raise ValueError(f'Super {i}: ต้องเป็นข้อความบรรทัดเดียว')
        if sum(unicodedata.category(c) not in ('Mn','Mc','Me') for c in text)>46 or metrics.width(text,p['font_size'])>p['width']*.8:raise ValueError(f'Super {i}: ข้อความยาวเกินพื้นที่บรรทัดเดียว · เก็บคำตอบไว้แล้ว')
        if not isinstance(ids,list) or not ids or any(type(k)!=int or k not in lookup for k in ids):raise ValueError(f'Super {i}: อ้างอิงท่อนเสียงไม่ถูกต้อง')
        if a<min(lookup[k]['start'] for k in ids)-.02 or b>max(lookup[k]['end'] for k in ids)+.02:raise ValueError(f'Super {i}: เวลาอยู่นอกท่อนเสียงที่อ้างอิง')
        if fa<round(previous*p['fps']):raise ValueError('Super ซ้อนหลังปัดตามเฟรม')
        previous=b
    return plan

def text_track(plan,p,include_anchor=False):
    from super_graphic import clip
    track=E.Element('track')
    items=[(False,x) for x in plan['items']]
    if include_anchor and items and round(items[0][1]['start']*p['fps'])>0:
        items.insert(0,(True,dict(start=0,end=1/p['fps'],text='')))
    for anchor,x in items:
        a,b=round(x['start']*p['fps']),round(x['end']*p['fps'])
        track.append(clip(x['text'],a,b,p,anchor=anchor))
    E.SubElement(track,'enabled').text='TRUE';E.SubElement(track,'locked').text='FALSE'
    return track

def export(plan,p,folder):
    from super_graphic import item_text
    from super_edit import validate_export
    plan=validate_export(copy.deepcopy(plan),p)
    revision='super-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6];out=folder/revision;out.mkdir()
    for combined,name in ((False,'SUPER-ONLY.xml'),(True,'SUPER-WITH-ORIGINAL.xml')):
        track=text_track(plan,p,include_anchor=not combined)
        if combined:root=copy.deepcopy(p['root']);seq=root.findall('.//sequence')[0];seq.find('media/video').append(copy.deepcopy(track))
        else:
            root=E.Element('xmeml',version='5');seq=E.SubElement(root,'sequence');E.SubElement(seq,'duration').text=str(p['duration']);seq.append(copy.deepcopy(p['seq'].find('rate')))
            video=E.SubElement(E.SubElement(seq,'media'),'video');video.append(copy.deepcopy(p['seq'].find('media/video/format')));video.append(copy.deepcopy(track))
        seq.set('id','super-'+uuid.uuid4().hex)
        for u in list(seq.findall('uuid')):seq.remove(u)
        title=seq.find('name')
        if title is None:title=E.SubElement(seq,'name')
        title.text=p['seq'].findtext('name','Sequence')+(' · Super + Original' if combined else ' · Super Only')
        E.indent(root,space='  ');raw=E.tostring(root,encoding='utf-8',xml_declaration=True).replace(b'?>',b'?>\n<!DOCTYPE xmeml>',1);(out/name).write_bytes(raw)
        parsed=E.parse(out/name).getroot();actual=parsed.findall('.//sequence')[0].findall('media/video/track')[-1].findall('clipitem')
        editorial=[g for g in actual if not g.get('id','').startswith('inwoum-super-anchor-')]
        if [item_text(g) for g in editorial]!=[x['text'] for x in plan['items']]:raise ValueError('ตรวจข้อความ XML หลังส่งออกไม่ตรงแผน')
    with (out/'SUPER-LIST.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['Start seconds','End seconds','Super']);w.writerows((x['start'],x['end'],x['text']) for x in plan['items'])
    result=dict(policy=POLICY,revision=revision,count=len(plan['items']),items=plan['items'],combined_file=revision+'/SUPER-WITH-ORIGINAL.xml',text_file=revision+'/SUPER-ONLY.xml',csv_file=revision+'/SUPER-LIST.csv',font='Kanit Bold',font_postscript='Kanit-Bold',style_template='supersetting-v1',font_size=100,display_font_size=p['font_size'],graphic_scale=p['width']/1920*100,color='#E8B923',layout='portrait' if p['portrait'] else 'landscape',width=p['width'],height=p['height'],seconds=p['seconds'],timing_basis='CapCut utterance times, not word alignment')
    result['fps']=float(p['fps'])
    if plan.get('timing_adjustment'):result['timing_adjustment']=plan['timing_adjustment']
    if plan.get('edit_info'):result.update(plan['edit_info'])
    save(out/'SUPER-EDIT.json',dict(items=plan['items'],timing_adjustment=result.get('timing_adjustment'),edit_info=plan.get('edit_info')))
    save(folder/'super-result.json',result);return result

def run(config,folder,key,emit=lambda **e:None,cancelled=lambda:False):
    from parallel_requests import call
    config=validate_config(config);save(folder/'config.json',config);progress=Progress(folder,emit,cancelled)
    with progress.stage('intake','อ่าน XML เพื่อทำ Super · คงภาพและเสียงต้นฉบับ'):
        p=load_project(config);identity=p['identity'];old=folder/'super-source.json'
        if old.exists() and json.loads(old.read_text('utf8'))!=identity:raise ValueError('ต้นฉบับเปลี่ยน กรุณาสร้างงานใหม่')
        save(old,identity)
    with progress.stage('audio','อ่านเสียงตามไทม์ไลน์ XML เพื่อทำ Super'):
        wav=folder/'timeline.wav';aid=folder/'super-audio.json'
        if wav.exists() and aid.exists():
            pcm=read_pcm(wav);saved=json.loads(aid.read_text('utf8'))
            if saved!=dict(sha256=hashlib.sha256(pcm.tobytes()).hexdigest(),samples=len(pcm)) or len(pcm)!=round(p['seconds']*16000):raise ValueError('เสียง cache ไม่ตรงต้นฉบับ')
        else:
            pcm=extract(p,folder,progress)
            if load_project(config)['identity']!=identity:raise ValueError('สื่อเปลี่ยนระหว่างอ่านเสียง กรุณาสร้างงานใหม่')
            save(aid,dict(sha256=hashlib.sha256(pcm.tobytes()).hexdigest(),samples=len(pcm)))
    with progress.stage('transcribe','ถอดเสียงเป็นท่อนด้วย CapCut · ไม่จับเวลาคำบน GPU'):
        units=transcribe(pcm,folder,progress);save(folder/'super-transcript.json',units)
    with progress.stage('draft','เลือกใจความและเกลา Super · 1 คำขอ API'):
        answer=call(request(units,p['seconds'],config),folder,key,'super-plan',max_calls=1)
        validate_plan(answer,units,p)
        save(folder/'super-validation.json',dict(passed=True,single_line=True,shadow=False))
    with progress.stage('export','สร้าง XML Super ที่แก้ข้อความต่อได้'):
        from super_edit import adjust_times
        answer=adjust_times(answer,p)
        result=export(answer,p,folder)
    progress(stage='done',message=f'สร้าง Super แล้ว {result["count"]} ข้อความ · บรรทัดเดียว',result=dict(super=True,super_count=result['count'],super_file=result['combined_file'],super_text_file=result['text_file'],revision=result['revision']))
    return result
