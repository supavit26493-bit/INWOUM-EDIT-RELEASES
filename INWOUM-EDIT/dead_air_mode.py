"""Local-only recut of an existing timeline: no ASR, text model or network calls."""
import copy,json,math,time,uuid
from pathlib import Path
import xml.etree.ElementTree as E
import numpy as np
import bootstrap
from audio_io import extract_audio,vad_intervals,write_pcm
from project import intake,build_outputs,repair_links
from fast_logic import merge,subtract

PROFILES={
    'gentle':dict(min_gap=.50,pre=.14,post=.20,ceiling=-42),
    'balanced':dict(min_gap=.35,pre=.10,post=.16,ceiling=-38),
    'tight':dict(min_gap=.25,pre=.08,post=.12,ceiling=-35)}

def validate_config(data):
    path=Path(str(data.get('xml',''))).expanduser()
    if not str(data.get('xml','')).strip() or path.suffix.lower()!='.xml' or not path.is_file():raise ValueError('เลือกไฟล์ XML ที่มีอยู่ก่อน')
    if path.stat().st_size>50_000_000:raise ValueError('XML ต้องไม่เกิน 50 MB')
    profile=data.get('dead_air_profile','gentle')
    if profile not in PROFILES:raise ValueError('ระดับตัด Dead Air ไม่ถูกต้อง')
    for key in ('heal_edges','merge_short'):
        if type(data.get(key,True)) is not bool:raise ValueError('ตัวเลือกซ่อมรอยต่อต้องเป็นจริงหรือเท็จ')
    index=data.get('sequence_index',0)
    if type(index)!=int or index<0:raise ValueError('ลำดับ Sequence ไม่ถูกต้อง')
    return dict(content_mode='dead_air',xml=str(path.resolve()),relink=str(data.get('relink','')).strip(),
        sequence_index=index,dead_air_profile=profile,heal_edges=data.get('heal_edges',True),
        merge_short=data.get('merge_short',True),audio_track=1)

def inspect(data):
    cfg=validate_config(data);root=E.parse(cfg['xml']).getroot();sequences=root.findall('.//sequence')
    if not sequences:raise ValueError('XML ไม่มี Sequence')
    rows=[]
    for i,s in enumerate(sequences):
        rate=int(s.findtext('rate/timebase','0'));duration=int(s.findtext('duration','0'))
        rows.append(dict(index=i,name=s.findtext('name') or f'Sequence {i+1}',seconds=duration/rate if rate else 0,fps=rate))
    return dict(sequences=rows,api_calls=0)

def _set(clip,key,value):clip.find(key).text=str(value)

def normalize(cfg,folder):
    """Select exactly one sequence and undo only our known native 2-frame fades."""
    root=E.parse(cfg['xml']).getroot();seqs=root.findall('.//sequence')
    if cfg['sequence_index']>=len(seqs):raise ValueError('ไม่พบ Sequence ที่เลือก')
    seq=seqs[cfg['sequence_index']]
    if seq.find('.//sequence') is not None:raise ValueError('ยังไม่รองรับ nested หรือ multicam ในโหมดนี้')
    if seq.findtext('rate/timebase')!='50' or seq.findtext('rate/ntsc')!='FALSE':raise ValueError('โหมดนี้รองรับ Sequence 50 fps เช่นเดียวกับระบบตัดเดิม')
    definitions={f.get('id'):copy.deepcopy(f) for f in root.iter('file') if f.find('pathurl') is not None}
    # Materialize definitions before removing other sequences that may own them.
    for parent in list(seq.iter()):
        for f in list(parent.findall('file')):
            if f.get('id') not in definitions:raise ValueError('ข้อมูลไฟล์สื่อไม่ครบ')
            at=list(parent).index(f);parent.remove(f);parent.insert(at,copy.deepcopy(definitions[f.get('id')]))
    for parent in list(root.iter()):
        for other in list(parent.findall('sequence')):
            if other is not seq:parent.remove(other)
    fades=0
    for track in seq.findall('media/video/track')+seq.findall('media/audio/track'):
        for tr in list(track.findall('transitionitem')):
            items=list(track);i=items.index(tr)
            if tr.findtext('effect/effectid')!='KGAudioTransCrossFade3dB' or track not in seq.findall('media/audio/track') or not 0<i<len(items)-1:raise ValueError('พบ transition ที่ยังไม่รองรับ กรุณาส่งออก XML แบบคัทตรง')
            a,b=int(tr.findtext('start')),int(tr.findtext('end'));left,right=items[i-1:i+2:2]
            if b-a!=2 or left.tag!='clipitem' or right.tag!='clipitem' or left.findtext('end')!='-1' or right.findtext('start')!='-1':raise ValueError('โครงสร้าง crossfade ไม่ใช่รูปแบบเดิมที่คืนเป็นคัทตรงได้')
            cut=a+1
            for c,k,v in [(left,'end',cut),(left,'out',int(left.findtext('out'))-1),(right,'start',cut),(right,'in',int(right.findtext('in'))+1)]:_set(c,k,v)
            for c,k,delta in [(left,'pproTicksOut',-1),(right,'pproTicksIn',1)]:
                if c.find(k) is not None:_set(c,k,int(c.findtext(k))+delta*5080320000)
            track.remove(tr);fades+=1
    from project import strip_markers
    strip_markers(seq)
    path=folder/'input-normalized.xml';E.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)
    return path,fades

def _db(pcm):
    x=np.asarray(pcm,dtype=np.float64)
    return float(20*np.log10(max(1e-6,np.sqrt(np.mean(x*x))/32768))) if len(x) else -120.

def _signature(clip):
    c=copy.deepcopy(clip);c.attrib.pop('id',None)
    for key in ('start','end','in','out','pproTicksIn','pproTicksOut','file','link','marker'):
        for item in list(c.findall(key)):c.remove(item)
    return E.tostring(c)

def merge_contiguous(seq,files):
    """Only redundant, unlinked same-media edits; retain all real take switches."""
    count=0;referenced={l.findtext('linkclipref') for c in seq.iter('clipitem') for l in c.findall('link')}
    for track in seq.findall('media/video/track')+seq.findall('media/audio/track'):
        clips=track.findall('clipitem');i=0
        while i+1<len(clips):
            a,b=clips[i:i+2]
            same=files[a.find('file').get('id')].findtext('pathurl')==files[b.find('file').get('id')].findtext('pathurl')
            if (a.get('id') not in referenced and b.get('id') not in referenced and not a.findall('link') and not b.findall('link') and same and a.findtext('end')==b.findtext('start') and a.findtext('out')==b.findtext('in') and _signature(a)==_signature(b)):
                for k in ('end','out','pproTicksOut'):
                    if b.find(k) is not None:_set(a,k,b.findtext(k))
                track.remove(b);clips.pop(i+1);count+=1
            else:i+=1
    repair_links(seq);return count

def heal_tiny_gaps(project,pcm,folder,emit):
    """Restore <=120ms only when every active track has matching real source handles."""
    root=project['root'];seq=project['sequence'];tracks=seq.findall('media/video/track')+seq.findall('media/audio/track')
    files={f.get('id'):f for f in root.iter('file') if f.find('pathurl') is not None}
    primary=seq.findall('media/audio/track')[0].findall('clipitem');restores=[];skipped=[]
    for left,right in zip(primary,primary[1:]):
        emit()
        cut=int(left.findtext('end'));gap=int(right.findtext('in'))-int(left.findtext('out'))
        if cut!=int(right.findtext('start')) or not 0<gap<=6:continue
        pairs=[];safe=True
        for track in tracks:
            clips=track.findall('clipitem')
            touching=[c for c in clips if int(c.findtext('start'))<cut<int(c.findtext('end')) or int(c.findtext('start'))==cut or int(c.findtext('end'))==cut]
            if not touching:continue
            ls=[c for c in touching if int(c.findtext('end'))==cut];rs=[c for c in touching if int(c.findtext('start'))==cut]
            if len(ls)!=1 or len(rs)!=1 or len(touching)!=2:safe=False;break
            l,r=ls[0],rs[0]
            if files[l.find('file').get('id')].findtext('pathurl')!=files[r.find('file').get('id')].findtext('pathurl') or int(r.findtext('in'))-int(l.findtext('out'))!=gap or _signature(l)!=_signature(r):safe=False;break
            pairs.append((l,r))
        if not safe:skipped.append(dict(cut_frame=cut,reason='แทร็กต่าง ๆ ไม่มีขอบสื่อที่คืนพร้อมกันได้'));continue
        source=next((x for x in project['primary'] if x['end']==cut),None)
        if source is None:continue
        probe=dict(duration=gap,primary=[dict(source,start=0,end=gap,in_frame=source['out_frame'])])
        missing=extract_audio(probe,folder,emit)
        # Actual sound must exist in the missing source and at an adjoining edge.
        edge=pcm[max(0,cut*320-960):min(len(pcm),cut*320+960)]
        if _db(missing)<-42 or _db(edge)<-42:continue
        restores.append(dict(cut_frame=cut,gap_frames=gap,pcm=missing,pairs=pairs))
    shift=0
    for entry in restores:
        cut=entry['cut_frame']+shift;gap=entry['gap_frames']
        for track in tracks:
            for c in track.findall('clipitem'):
                if int(c.findtext('start'))>=cut:
                    for k in ('start','end'):_set(c,k,int(c.findtext(k))+gap)
        for l,r in entry['pairs']:
            for k in ('end','out'):_set(l,k,int(l.findtext(k))+gap)
            if l.find('pproTicksOut') is not None:_set(l,'pproTicksOut',int(l.findtext('pproTicksOut'))+gap*5080320000)
        shift+=gap
    if restores:
        chunks=[];cursor=0
        for row in restores:
            at=row['cut_frame']*320;chunks.extend((pcm[cursor:at],row['pcm']));cursor=at
        chunks.append(pcm[cursor:]);pcm=np.concatenate(chunks)
        _set(seq,'duration',int(seq.findtext('duration'))+shift)
    return pcm,[{k:v for k,v in r.items() if k not in ('pcm','pairs')} for r in restores],skipped

def cut_ranges(pcm,speech,profile,primary):
    if speech is None:raise ValueError('ยังไม่มีตัวตรวจเสียงพูดในเครื่อง กรุณาเปิด SETUP.cmd ไม่จำเป็นต้องใส่ API key')
    p=PROFILES[profile];frames=len(pcm)//320
    rms=np.sqrt(np.mean(np.asarray(pcm[:frames*320],dtype=np.float64).reshape(frames,320)**2,axis=1))/32768
    db=20*np.log10(np.maximum(1e-6,rms));threshold=max(-55,min(p['ceiling'],float(np.percentile(db,20))+8))
    quiet=db<threshold
    for a,b in speech:
        quiet[max(0,math.floor((a-p['pre'])*50)):min(frames,math.ceil((b+p['post'])*50))]=False
    edges=np.flatnonzero(np.diff(np.r_[False,quiet,False].astype(int)));removed=[]
    for a,b in zip(edges[::2],edges[1::2]):
        a,b=int(a),int(b)
        if b-a<math.ceil(p['min_gap']*50):continue
        x=a+(0 if a==0 else 2);y=b-(0 if b==frames else 2)
        if x<y:removed.append((x,y))
    available=merge([(c['start'],c['end']) for c in primary]);kept=subtract(available,removed)
    if not kept:raise ValueError('ไม่พบช่วงเสียงให้เก็บ ไม่มีการแก้ไฟล์ต้นฉบับ')
    return [dict(start=a/50,end=b/50,reason='ตัดเฉพาะช่วงเงียบในเครื่อง') for a,b in kept],dict(threshold_db=round(threshold,2),removed_frames=removed,removed_seconds=(frames-sum(b-a for a,b in kept))/50)

def run(data,folder,emit=lambda **kw:None,cancelled=lambda:False):
    from engine import Progress,save
    cfg=validate_config(data);folder.mkdir(parents=True,exist_ok=True);save(folder/'config.json',cfg);save(folder/'api-usage.json',[])
    progress=Progress(folder,emit,cancelled)
    def event(percent,message):progress(stage='dead_air',percent=percent,message=message)
    with progress.stage('intake','ตรวจ XML · โหมดในเครื่อง ไม่ใช้ token'):
        source,fades=normalize(cfg,folder);p=intake(source,cfg['relink'] or None,audio_track=1);p['preserve_source_tracks']=True
    with progress.stage('audio','อ่านเฉพาะเสียงแทร็ก 1'):
        event(10,'อ่านเสียงแทร็ก 1 · ไม่ถอดข้อความ');pcm=extract_audio(p,folder,progress)
    with progress.stage('edges','ตรวจขอบคัทและคัทสั้น'):
        event(35,'ตรวจขอบเสียงจากสื่อจริง · ไม่เดาหรือสร้างคำใหม่');restores=[];skipped=[]
        if cfg['heal_edges']:pcm,restores,skipped=heal_tiny_gaps(p,pcm,folder,progress)
        files={f.get('id'):f for f in p['root'].iter('file') if f.find('pathurl') is not None}
        merged=merge_contiguous(p['sequence'],files) if cfg['merge_short'] else 0
        E.ElementTree(p['root']).write(source,encoding='utf-8',xml_declaration=True)
        p=intake(source,cfg['relink'] or None,audio_track=1);p.update(preserve_source_tracks=True,content_mode='single',single_max_seconds=0)
        write_pcm(folder/'timeline.wav',pcm)
    with progress.stage('vad','ตรวจเสียงพูดในเครื่อง'):
        event(55,'ตรวจเสียงพูด · ไม่ใช้ CapCut หรือ OpenAI');speech=vad_intervals(pcm);progress()
    with progress.stage('silence','ตัดเฉพาะช่วงเงียบ'):
        event(72,'เก็บเสียงพูดเบาและเผื่อขอบพยางค์');ranges,silence=cut_ranges(pcm,speech,cfg['dead_air_profile'],p['primary'])
        save(folder/'silence.json',silence)
    with progress.stage('export','สร้าง XML ใหม่ · ทุกแทร็กและลิงก์เดิม'):
        event(88,'ส่งออกคัทตรง · ไม่มี crossfade หรือ marker');rev='revision-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:4];out=folder/rev;out.mkdir()
        manifest=build_outputs(p,source,[dict(name='DEAD AIR · LOCAL',ranges=ranges)],out,pcm.tobytes())
        save(folder/'latest.json',dict(folder=rev))
        actual=E.parse(out/'EDIT_01.xml').find('.//sequence/media/audio/track').findall('clipitem')
        short=[dict(index=i+1,start=int(c.findtext('start'))/50,end=int(c.findtext('end'))/50,
            seconds=(int(c.findtext('end'))-int(c.findtext('start')))/50,coordinate='output timeline',
            reason='เสียงยังมีอยู่ จึงไม่ลบเพียงเพราะคัทสั้น') for i,c in enumerate(actual) if int(c.findtext('end'))-int(c.findtext('start'))<20]
        result=dict(xml_file=rev+'/EDIT_01.xml',report_file='DEAD-AIR-REPORT.json',versions=1,api_calls=0,
            original_seconds=(len(pcm)/16000-sum(r['gap_frames'] for r in restores)/50),output_seconds=manifest['edits'][0]['seconds'],
            removed_seconds=silence['removed_seconds'],restored_gaps=restores,merged_redundant_cuts=merged,
            remaining_short_ranges=short,unresolved_edges=skipped,removed_old_crossfades=fades,
            limitation='ไม่เข้าใจคำหรือแก้เสียงที่ไม่มีต้นฉบับ; คัทต่างเทคหรือบริบทไม่แน่ชัดไม่ลบทิ้ง')
        save(folder/'DEAD-AIR-REPORT.json',result);save(folder/'dead-air-result.json',result)
    progress(stage='done',percent=100,message='ตัด Dead Air ในเครื่องเสร็จ · ไม่ใช้ token',result=result)
    return result
