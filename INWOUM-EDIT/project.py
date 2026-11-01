"""Sequence-time cuts, including synchronized separate camera tracks."""
import copy,csv,hashlib,json,math,uuid
from pathlib import Path
from urllib.parse import unquote,urlparse
import xml.etree.ElementTree as E
import bootstrap
from premiere_xml import inspect,window
from assemble_edits import assemble,package

def media_path(url):
 u=urlparse(url)
 if u.scheme!='file' or u.netloc not in ('','localhost'):raise ValueError('รองรับสื่อ file:// ในเครื่องหรือ mapped drive เท่านั้น')
 s=unquote(u.path)
 if len(s)>3 and s[0]=='/' and s[2]==':':s=s[1:]
 return Path(s)

def intake(path,relink=None,audio_track=1):
 path=Path(path).resolve()
 if path.suffix.lower()!='.xml' or path.stat().st_size>50_000_000:raise ValueError('เลือก Premiere XML ขนาดไม่เกิน50MB')
 root,seq,fps,files=inspect(path)
 if fps!=50:raise ValueError('Sequence ต้องเป็น50fps')
 if seq.find('.//transitionitem') is not None or seq.find('.//speed') is not None or seq.find('.//sequence') is not None:raise ValueError('กรุณาใช้คลิปปกติแยกแทร็ก ไม่มี nested/multicam/transition/speed change')
 duration=int(seq.findtext('duration'));paths={}
 for fid,f in files.items():
  p=media_path(f.findtext('pathurl'))
  if not p.is_file():
   if not relink:raise ValueError('หาไฟล์ไม่พบ: '+str(p)+' — เลือกโฟลเดอร์สื่อเพื่อRelink')
   found=list(Path(relink).rglob(p.name))
   if len(found)!=1:raise ValueError('Relinkต้องพบชื่อไฟล์เดียว: '+p.name)
   p=found[0].resolve();f.find('pathurl').text=p.as_uri()
  paths[fid]=str(p.resolve())
 tracks={};videos=[];audios=[]
 for kind in ('video','audio'):
  for ti,t in enumerate(seq.findall('media/'+kind+'/track'),1):
   clips=[]
   for c in t.findall('clipitem'):
    if c.find('file') is None or c.find('file').get('id') not in paths:raise ValueError('ไม่รองรับ nested/multicam หรือ file reference ไม่ครบ')
    a,b,ci,co=[int(c.findtext(k)) for k in ('start','end','in','out')]
    if min(a,ci)<0 or b-a!=co-ci:raise ValueError('ไม่รองรับtransitionหรือเปลี่ยนความเร็ว')
    enabled=t.findtext('enabled','TRUE')!='FALSE' and c.findtext('enabled','TRUE')!='FALSE'
    clips.append(dict(start=a,end=b,in_frame=ci,out_frame=co,path=paths[c.find('file').get('id')],enabled=enabled,source_channel=int(c.findtext('sourcetrack/trackindex','1'))))
   tracks[(kind,ti)]=clips
   if clips:(videos if kind=='video' else audios).append(dict(track=ti,clips=len(clips),enabled=all(x['enabled'] for x in clips),files=sorted({Path(x['path']).name for x in clips})))
 if audio_track is None:
  audio_track=next((t['track'] for t in audios if t['enabled']),1)
 primary=tracks.get(('audio',int(audio_track)),[])
 if not primary or not all(c['enabled'] for c in primary):raise ValueError('เลือกแทร็กเสียงหลักที่มีคลิปและเปิดเสียงไว้')
 if any(a['end']>b['start'] for a,b in zip(primary,primary[1:])):raise ValueError('เสียงหลักซ้อนกันในแทร็กเดียว')
 return dict(xml=str(path),root=root,sequence=seq,fps=fps,duration=duration,paths=paths,tracks=tracks,primary=primary,audio_track=int(audio_track),videos=videos,audios=audios)

def prepare_source(project,out):
 if project.get('preserve_source_tracks'):
  root=copy.deepcopy(project['root'])
  target=out/'source.xml';E.indent(root);target.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'+E.tostring(root,encoding='unicode'),encoding='utf8')
  return target
 # Preserve stereo channels of the primary take, remove other camera audio,
 # never export them as disabled clips. Link references are rebuilt afterwards.
 root=copy.deepcopy(project['root']);s=root.find('.//sequence');primary=project['primary']
 for t in s.findall('media/audio/track'):
  for c in list(t.findall('clipitem')):
   fid=c.find('file').get('id');a,b,ci,co=[int(c.findtext(k)) for k in ('start','end','in','out')]
   if not any(x['path']==project['paths'][fid] and (x['start'],x['end'],x['in_frame'],x['out_frame'])==(a,b,ci,co) for x in primary):t.remove(c)
   elif t.findtext('enabled','TRUE')=='FALSE' or c.findtext('enabled','TRUE')=='FALSE':raise ValueError('ช่องเสียงสเตอริโอของเสียงหลักถูกปิดไว้ กรุณาแก้ในPremiereก่อน')
 repair_links(s)
 target=out/'source.xml';E.indent(root);target.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'+E.tostring(root,encoding='unicode'),encoding='utf8')
 return target

def repair_links(seq):
 address={c.get('id'):(kind,ti,ci) for kind in ('video','audio') for ti,t in enumerate(seq.findall('media/'+kind+'/track'),1) for ci,c in enumerate(t.findall('clipitem'),1)}
 for c in seq.iter('clipitem'):
  for l in list(c.findall('link')):
   ref=l.findtext('linkclipref')
   if ref not in address:c.remove(l);continue
   for key,v in zip(('mediatype','trackindex','clipindex'),address[ref]):l.find(key).text=str(v)

def validate_edits(edits,project):
 if not isinstance(edits,list) or not 1<=len(edits)<=100:raise ValueError('AIต้องส่งรายการเวอร์ชัน1–100แบบ')
 if project.get('content_mode')=='single' and len(edits)!=1:raise ValueError('โหมดคลิปเดียวต้องมีหนึ่งเวอร์ชัน')
 libraries=[e for e in edits if e.get('sequence_role')=='hook_library']
 if libraries and (project.get('cut_style')!='sales_2_2' or len(libraries)!=1):raise ValueError('คลัง Hook ต้องมีเพียงหนึ่ง sequence และใช้เฉพาะ 2.2')
 for e in edits:
  if not isinstance(e.get('name'),str) or not isinstance(e.get('ranges'),list) or not e['ranges']:raise ValueError('แผนคัทไม่ครบ')
  total=0
  for r in e['ranges']:
   a,b=r.get('start'),r.get('end')
   if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in (a,b)):raise ValueError('เวลาAIไม่ถูกต้อง')
   a,b=round(a*50),round(b*50)
   if not 0<=a<b<=project['duration']:raise ValueError('AIเลือกคัทนอกต้นฉบับ')
   if any(r.get(x) for x in ('mute_audio','mute')):raise ValueError('ห้ามmuteเสียง')
   r['start'],r['end']=a/50,b/50;total+=b-a
   # Do not synthesize silence across gaps in primary audio.
   covered=sum(max(0,min(b,c['end'])-max(a,c['start'])) for c in project['primary'])
   if covered!=b-a:raise ValueError('คัทคร่อมช่วงที่เสียงหลักไม่มีคลิป')
  if total>9000 and project.get('content_mode')!='single' and e.get('sequence_role')!='hook_library':raise ValueError('เวอร์ชันเกิน3นาที')
  if total>9000 and project.get('content_mode')=='single' and project.get('single_max_seconds')==180:raise ValueError('แผนคลิปเดียวเกิน3นาที จึงยังไม่ส่งออก XML · เก็บคำตอบไว้แล้ว ไม่ตัดจบกลางประโยคหรือเรียก API ซ้ำอัตโนมัติ')
 return edits

def tc(n):
 seconds,f=divmod(n,50);minutes,s=divmod(seconds,60);h,m=divmod(minutes,60)
 return f'{h:02}:{m:02}:{s:02}:{f:02}'

def usage_intervals(edits,duration):
 uses=[];bounds={0,duration}
 for e in edits:
  cursor=0
  for r in e['ranges']:
   a,b=round(r['start']*50),round(r['end']*50);bounds.update((a,b))
   uses.append(dict(id=e['id'],name=e['name'],start=a,end=b,timeline=cursor,reason=r.get('reason','')));cursor+=b-a
 points=sorted(bounds)
 return [dict(start=a,end=b,uses=[u for u in uses if u['start']<=a and b<=u['end']]) for a,b in zip(points,points[1:])]

def build_outputs(project,source,edits,out,audio):
 import wave
 preserve=project.get('preserve_source_tracks',False)
 validate_edits(edits,project);sequences=[];manifest_edits=[]
 for i,e in enumerate(edits,1):
  e['id']=f'EDIT_{i:02}';token=e['id'];name=f'{i:02} '+e['name']
  seq,cuts=assemble(source,[(r['start'],r['end']) for r in e['ranges']],name,token,primary_audio=None,max_seconds=(project.get('single_max_seconds') or None) if project.get('content_mode')=='single' else (None if e.get('sequence_role')=='hook_library' else 180),include_markers=not preserve)
  if e.get('sequence_role')=='hook_library':
   for track in seq.findall('media/video/track')+seq.findall('media/audio/track'):
    for clip in track.findall('clipitem'):
     position=int(clip.findtext('start'))
     index=next(i for i,c in enumerate(cuts) if c['timeline_start_frame']<=position<c['timeline_end_frame'])
     labels=clip.find('labels')
     if labels is None:labels=E.SubElement(clip,'labels')
     label=labels.find('label2')
     if label is None:label=E.SubElement(labels,'label2')
     label.text=e['ranges'][index]['clip_color']
  joins=[]
  if project.get('default_audio') and not preserve:
   from default_audio import apply_crossfades
   joins=apply_crossfades(seq,source,cuts)
  if preserve and project.get('selective_crossfade') and e.get('sequence_role')!='hook_library':
   import numpy as np
   from default_audio import discontinuous_joins,apply_crossfades
   points=discontinuous_joins(np.frombuffer(audio,dtype='<i2'),e['ranges'])
   joins=apply_crossfades(seq,source,cuts,track_number=project['audio_track'],candidate_frames=points)
  if preserve:strip_markers(seq)
  verify_sequence(seq,preserve_disabled=preserve)
  (out/(token+'.xml')).write_text(package(source,[seq]),encoding='utf8')
  with wave.open(str(out/(token+'.wav')),'wb') as w:
   w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
   if project.get('default_audio') or project.get('selective_crossfade'):
    import numpy as np
    from default_audio import render_preview
    w.writeframesraw(render_preview(np.frombuffer(audio,dtype='<i2'),e['ranges'],joins,project['audio_track']).tobytes())
   else:
    for r in e['ranges']:w.writeframesraw(audio[round(r['start']*50)*640:round(r['end']*50)*640])
  sequences.append(seq)
  manifest_edits.append(dict(id=token,name=name,seconds=sum(round(r['end']*50)-round(r['start']*50) for r in e['ranges'])/50,ranges=[dict(media='SEQUENCE_TIMELINE',start=r['start'],end=r['end'],label=r.get('reason',''),role=r.get('role','detail'),action=r.get('action',False)) for r in e['ranges']],sequence_cuts=cuts))
  if e.get('sequence_role')=='hook_library':manifest_edits[-1].update(sequence_role='hook_library',hooks=e['hooks'])
  if project.get('default_audio') or project.get('selective_crossfade'):manifest_edits[-1].update(audio_crossfades=joins,preview_audio_track=project['audio_track'],audio_policy=project.get('default_audio') or 'selective-click-crossfade-v1')
 intervals=usage_intervals(edits,project['duration']);mapped=copy.deepcopy(E.parse(source).find('.//sequence'));mapped.set('id','SOURCE_USAGE');mapped.find('name').text='00 SOURCE USAGE MAP - ORIGINAL TIMELINE'
 for tag in ('uuid','marker'):
  for x in list(mapped.findall(tag)):mapped.remove(x)
 E.SubElement(mapped,'uuid').text=str(uuid.uuid4())
 paths=('media/video/track','media/audio/track');target=[t for p in paths for t in mapped.findall(p)]
 for t in target:
  for c in list(t.findall('clipitem')):t.remove(c)
 for n,r in enumerate(intervals,1):
  a,b=r['start'],r['end'];piece=E.fromstring(window(source,a/50,(b-a)/50)).find('.//sequence');ids={c.get('id'):f'MAP_{n}_{c.get("id")}' for c in piece.iter('clipitem')}
  for to,fr in zip(target,[t for p in paths for t in piece.findall(p)]):
   for old in fr.findall('clipitem'):
    c=copy.deepcopy(old);c.set('id',ids[old.get('id')])
    for tag in ('start','end'):c.find(tag).text=str(int(c.findtext(tag))+a)
    for l in c.findall('link'):l.find('linkclipref').text=ids[l.findtext('linkclipref')]
    labels=c.find('labels')
    if labels is None:labels=E.SubElement(c,'labels')
    label=labels.find('label2')
    if label is None:label=E.SubElement(labels,'label2')
    label.text='Forest' if r['uses'] else 'Iris';to.insert(len(to.findall('clipitem')),c)
  if not preserve:
   mark=E.SubElement(mapped,'marker');dest=' | '.join(u['id']+' @ '+tc(u['timeline']+a-u['start']) for u in r['uses'])
   for k,v in [('name',('USED ' if r['uses'] else 'NOT USED ')+','.join(u['id'] for u in r['uses'])),('comment',dest or 'ไม่ได้เลือกในเวอร์ชันนี้ ยังเก็บต้นฉบับครบ'),('in',a),('out',b)]:E.SubElement(mark,k).text=str(v)
 if preserve:strip_markers(mapped)
 repair_links(mapped);verify_sequence(mapped,preserve_disabled=preserve)
 (out/'EDITS.xml').write_text(package(source,sequences),encoding='utf8');(out/'EDITS-WITH-USAGE.xml').write_text(package(source,[mapped]+sequences),encoding='utf8')
 (out/'USAGE.json').write_text(json.dumps(intervals,ensure_ascii=False,indent=2),encoding='utf8')
 with (out/'USAGE.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.writer(f);w.writerow(['source in','source out exclusive','status','used in'])
  for r in intervals:w.writerow([tc(r['start']),tc(r['end']),'USED' if r['uses'] else 'NOT USED',' | '.join(u['id']+' @ '+tc(u['timeline']+r['start']-u['start']) for u in r['uses'])])
 manifest=dict(source_xml=str(source),package_name='EDITS.xml',edits=manifest_edits,coordinate_system='sequence timeline, not individual media in/out',final_approved=False)
 if preserve:manifest.update(export_policy='source-tracks-links-v1',markers=False,crossfades=any(e.get('audio_crossfades') for e in manifest_edits),analysis_audio_track=project['audio_track'])
 if project.get('cut_style') in ('natural_v2','sales_2_1','sales_2_2'):manifest['cut_style']=project['cut_style']
 (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
 from delivery_gate import initialize
 initialize(out)
 (out/'final-collection-audit.json').write_text(json.dumps(dict(status='pending',reason='New revision: no prior AI or human review applies',audio_review='pending',premiere_import='pending'),ensure_ascii=False,indent=2),encoding='utf8')
 return manifest

def strip_markers(seq):
 for parent in seq.iter():
  for marker in list(parent.findall('marker')):parent.remove(marker)

def configure_export(project,config):
 mode=config.get('content_mode','sales')
 project['cut_style']=config.get('single_style' if mode=='single' else 'sales_style','original')
 project['preserve_source_tracks']=mode in ('sales','single') and config.get('single_style' if mode=='single' else 'sales_style','original') in ('natural','natural_v2','sales_2_1','sales_2_2')
 project['selective_crossfade']=project['preserve_source_tracks'] and config.get('audio_crossfade') is True
 return project

def refresh_source_snapshot(project,folder):
 """Recover export tracks from verified original XML, never from a pruned snapshot."""
 if not project.get('preserve_source_tracks'):return False
 policy=folder/'source-export-policy.json'
 if policy.exists() and json.loads(policy.read_text('utf8')).get('policy')=='source-tracks-links-v1':return False
 prepare_source(project,folder)
 (folder/'source-sha256.json').write_text(json.dumps(hashlib.sha256((folder/'source.xml').read_bytes()).hexdigest()),'utf8')
 policy.write_text(json.dumps(dict(policy='source-tracks-links-v1',analysis_audio_track=project['audio_track'],api_calls_added=0)),'utf8')
 return True

def verify_sequence(seq,preserve_disabled=False):
 seen=set();duration=int(seq.findtext('duration'))
 for t in seq.findall('media/video/track')+seq.findall('media/audio/track'):
  cursor=-1
  previous=None
  for c in t.findall('clipitem'):
   a,b,ci,co=[int(c.findtext(k)) for k in ('start','end','in','out')]
   if a==-1 or b==-1:
    from default_audio import effective_edges
    a,b=effective_edges(t,c)
   if a<cursor:
    children=list(t);ix=children.index(c);tr=children[ix-1]
    assert tr.tag=='transitionitem' and int(tr.findtext('start'))==a and int(tr.findtext('end'))==cursor
    assert previous is not None and previous.findtext('end')=='-1'
   assert 0<=a<b<=duration and b-a==co-ci;cursor=b;previous=c
   assert c.get('id') not in seen;seen.add(c.get('id'))
   if not preserve_disabled and c.find('sourcetrack/mediatype') is not None and c.findtext('sourcetrack/mediatype')=='audio':assert c.findtext('enabled','TRUE')!='FALSE'
   pi,po=c.findtext('pproTicksIn'),c.findtext('pproTicksOut')
   if pi and po:assert int(po)-int(pi)==(co-ci)*5080320000
  for tr in t.findall('transitionitem'):
   assert t in seq.findall('media/audio/track')
   assert tr.findtext('effect/effectid')=='KGAudioTransCrossFade3dB'
   assert 0<=int(tr.findtext('start'))<int(tr.findtext('end'))<=duration
 for c in seq.iter('clipitem'):
  for l in c.findall('link'):assert l.findtext('linkclipref') in seen
