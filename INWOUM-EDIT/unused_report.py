"""Read-only explanations from saved edits. No AI calls and no changes to export logic."""
import json,math
from pathlib import Path
FPS=50
LABELS={'duplicate':'เนื้อหาซ้ำ','production':'บทสนทนาหรือส่วนที่พักไว้','money':'เรื่องราคา','incomplete':'ประโยคไม่ครบ','uncertain':'ยังไม่แน่ใจ','alternative':'เทคทางเลือก'}
UNKNOWN='ช่วงนี้ยังไม่ได้ถูกเลือก และไม่มีเหตุผลเฉพาะที่บันทึกไว้ว่าทำไมจึงไม่ใช้'

def overlap(a,b,c,d):return min(b,d)>max(a,c)+1e-8

def number(n):
 if isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) or n<0:raise ValueError('เวลาการใช้งานไม่ถูกต้อง')
 return n

def intervals(usage):
 """USAGE is the actual union of EVERY exported sequence, including reserves."""
 rows=[];last=0
 for row in usage:
  a,b=number(row['start'])/FPS,number(row['end'])/FPS
  if a<last-1e-8 or b<=a:raise ValueError('ช่วงการใช้งานซ้อนหรือเรียงผิด')
  if not isinstance(row.get('uses'),list):raise ValueError('ข้อมูล sequence ไม่ครบ')
  # Missing intervals are unknown, never silently classified as unused.
  rows.append((a,b,bool(row['uses'])));last=b
 return rows

def build(usage,coverage,catalog,revision=''):
 actual=intervals(usage);used=[(a,b) for a,b,yes in actual if yes];missing=[]
 for a,b,yes in actual:
  if yes:continue
  if missing and abs(missing[-1][1]-a)<1e-8:missing[-1]=(missing[-1][0],b)
  else:missing.append((a,b))
 units=(coverage or {}).get('units',[]);lookup={u['id']:u for u in catalog};rows=[]
 sequence_ids={str(u.get('id','')) for r in usage for u in r['uses'] if u.get('id')}
 for start,end in missing:
  explanations=[];seen=set()
  for unit in units:
   a,b=unit['start'],unit['end']
   if not overlap(start,end,a,b):continue
   uid=unit['unit_id'];cat=lookup.get(uid,{});aligned=(cat.get('text')==unit.get('text') and cat.get('start')==a and cat.get('end')==b)
   words=cat.get('words',[]) if aligned else []
   indices=[i for i,w in enumerate(words) if overlap(start,end,w['start'],w['end'])]
   if words and not indices:continue
   text=''.join(words[i]['text'] for i in indices) if words else unit.get('text','')
   if not str(text).strip():continue
   review=unit.get('review') or {};reason=UNKNOWN;evidence='unknown';note='';category='ไม่มีเหตุผลเฉพาะ';scope='omitted_words' if words else 'whole_unit'
   partial=review.get('partial_omission_review') or {}
   partial_ids=partial.get('omitted_phrase_indices',[])
   complete_words=bool(indices) and all(start<=words[i]['start']+1e-8 and words[i]['end']<=end+1e-8 for i in indices)
   unit_used=any(overlap(a,b,c,d) for c,d in used)
   if unit_used and words and complete_words and set(indices).issubset(set(partial_ids)) and partial.get('reason'):
    reason=partial['reason'];evidence='saved_partial_review';category=LABELS.get(partial.get('decision'),'เหตุผลของวลีที่ตัด');note='เหตุผลเดิมครอบคลุมชุดคำที่พักไว้ ไม่ใช่การตรวจฟังใหม่'
   elif not unit_used and review.get('reason') and review.get('decision')!='used' and review.get('review_source')!='inherited_story_map':
    reason=review['reason'];evidence='saved_review';category=LABELS.get(review.get('decision'),'เหตุผลที่บันทึกไว้')
   elif not unit_used and review.get('reason') and review.get('review_source')=='inherited_story_map' and review.get('decision') in ('production','money','duplicate','incomplete','uncertain'):
    reason=review['reason'];evidence='saved_plan_review';category=LABELS[review['decision']];note='เหตุผลที่พักท่อนจากขั้นจัดกลุ่มเดิม ไม่ใช่การตรวจฟังใหม่'
   else:
    if review.get('review_source')=='inherited_story_map' and review.get('reason'):
     note='บันทึกตอนจัดกลุ่ม: '+review['reason']+' — เป็นบริบทของท่อน ยังไม่ใช่เหตุผลยืนยันว่าช่วงนี้ควรถูกตัด'
    elif unit_used:
     reason='ท่อนนี้ใช้ไปบางส่วน แต่ช่วงคำนี้ไม่ได้อยู่ใน XML และไม่มีเหตุผลเฉพาะของช่วงคำที่บันทึกไว้'
   if words and not complete_words:note=(note+' · ' if note else '')+'ขอบคัทกินบางส่วนของช่วงเวลาคำ ไม่ใช่คำที่ถูกตัดออกทั้งคำ'
   if not words:note=(note+' · ' if note else '')+'แสดงข้อความทั้งท่อน เพราะไม่มีเวลาคำที่ยืนยันตรงกับผลรุ่นนี้'
   key=(uid,text,reason,note)
   if key in seen:continue
   seen.add(key);explanations.append(dict(unit_id=uid,text=text,start=max(start,a),end=min(end,b),reason=reason,category=category,evidence=evidence,note=note,text_scope=scope))
  if not explanations:
   explanations=[dict(unit_id='',text='',start=start,end=end,reason='ไม่มีคำถอดเสียงครอบคลุมช่วงนี้ อาจเป็นช่วงพักหรือการสาธิต จึงยังระบุเหตุผลแน่ชัดไม่ได้',category='ช่วงที่ไม่มีข้อความ',evidence='no_transcript',note='ไม่ได้ยืนยันว่าเป็นเสียงเงียบหรือฟุตที่ใช้ไม่ได้',text_scope='none')]
  rows.append(dict(start=start,end=end,seconds=round(end-start,3),has_speech=any(p['text'] for p in explanations),parts=explanations))
 return dict(revision=revision,rows=rows,unused_seconds=round(sum(b-a for a,b in missing),3),unused_ranges=len(rows),speech_ranges=sum(r['has_speech'] for r in rows),saved_reason_parts=sum(p['evidence'] in ('saved_review','saved_partial_review','saved_plan_review') for r in rows for p in r['parts']),exported_sequences=len(sequence_ids),scope='all_exported_sequences',api_calls_added=0,explanation='อ้างการใช้จริงจาก XML ทุก sequence รวมเทคสำรอง เหตุผลอ่านจากบันทึกเดิม หากไม่มีหลักฐานจะบอกว่าไม่ทราบ ไม่เหมาว่าใช้ไม่ได้')

def read(folder):
 folder=Path(folder);latest=folder/'latest.json'
 if not latest.exists():return dict(available=False,reason='งานนี้ยังไม่มี XML ที่ส่งออก')
 revision=json.loads(latest.read_text('utf8'))['folder'];path=(folder/revision).resolve()
 if Path(revision).name!=revision or not path.is_relative_to(folder.resolve()):raise ValueError('Revision ไม่ถูกต้อง')
 usage=json.loads((path/'USAGE.json').read_text('utf8'))
 coverage_path=path/'source-coverage.json';coverage=json.loads(coverage_path.read_text('utf8')) if coverage_path.exists() else {}
 catalog_path=folder/'catalog.json';catalog=json.loads(catalog_path.read_text('utf8')) if catalog_path.exists() else []
 report=build(usage,coverage,catalog,revision)
 report['available']=True
 if not coverage.get('units'):report['explanation']+=' · งานเก่านี้ไม่มีบันทึกรายท่อน จึงอธิบายสาเหตุจากข้อความไม่ได้'
 return report
