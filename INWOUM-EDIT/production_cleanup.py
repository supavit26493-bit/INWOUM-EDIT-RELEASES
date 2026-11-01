"""Conservative local trimming of aligned preparation countdowns; no new speech."""
import copy,re

PREP=r'(?:(?:โอเค|พร้อม|เอ้า|เอาล่ะ|นะ|ครับ|ค่ะ|อะ)[\s,!.]*)*'
COUNT=re.compile(r'^\s*'+PREP+r'(?:3[\s,!.]+2[\s,!.]+1(?!\d)|๓[\s,!.]+๒[\s,!.]+๑|สาม[\s,!.]*สอง[\s,!.]*หนึ่ง)(?:[\s,!.]*(?:เริ่ม|แอ็กชัน|แอคชั่น))?[\s,!.]*')

def hook_prefix_count(before,after):
    """A removed opening selection must not promote body selections to hooks."""
    old=before.get('hook_count')
    if type(old)!=int:return None
    hooks=before['selections'][:old];count=0
    for row in after:
        if not any(h['unit_id']==row['unit_id'] and max(h['first_word'],row['first_word'])<=min(h['last_word'],row['last_word']) for h in hooks):break
        count+=1
    return count

def clean(plan,catalog):
    units={u['id']:u for u in catalog};out=copy.deepcopy(plan);changes=[]
    for edit in out['edits']:
        before=copy.deepcopy(edit);kept=[]
        for row in edit['selections']:
            u=units[row['unit_id']];words=u['words']
            if u.get('protected') or not words or row['first_word']!=0:
                kept.append(row);continue
            text=' '.join(w['text'] for w in words);match=COUNT.match(text)
            if not match or re.match(r'(?:บาท|ระดับ|ชิ้น|คน|วินาที)',text[match.end():]):
                kept.append(row);continue
            ends=[];pos=0
            for i,w in enumerate(words):
                if i:pos+=1
                pos+=len(w['text']);ends.append(pos)
            # Never slice inside an aligned token carrying useful speech.
            n=sum(end<=match.end() for end in ends)
            if not n or ends[n-1]<len(text[:match.end()].rstrip()):
                kept.append(row);continue
            row['first_word']=max(row['first_word'],n)
            changes.append(dict(unit_id=u['id'],removed_prefix=''.join(w['text'] for w in words[:n]),kind='production_countdown'))
            if row['first_word']<=row['last_word']:kept.append(row)
        if not kept:raise ValueError('คลิปมีแต่ท่อนนับเตรียมถ่าย ไม่มีเนื้อหาให้ส่งออก')
        edit['selections']=kept
        count=hook_prefix_count(before,kept)
        if count is not None:edit['hook_count']=count
    used={s['unit_id'] for e in out['edits'] for s in e['selections']}
    for row in out.get('source_review',[]):
        if row['unit_id'] not in used and row.get('decision')=='used':
            row.update(decision='production',reason='ท่อนนับเตรียมถ่าย',review_source='production-countdown-v1')
    return out,changes
