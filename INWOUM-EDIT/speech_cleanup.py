"""Remove immediate speech stutters from API selections; never rewrite source speech.

Contextual fillers remain the assembler's task. Meaningful Thai reduplication is
protected. This is a deterministic text operation, not listening approval.
"""
import copy,re

POLICY='adjacent-speech-repeat-v1'
REDUPLICATION={'จริง','คล้าย','สาว','เด็ก','ต่าง','เร็ว','ช้า','มาก','น้อย','เล็ก','ใหญ่','เบา','ค่อย','เรื่อย','บ่อย','ใกล้','ไกล','ดี'}
STUTTERS={'เรา','ผม','มัน','คือ','ก็','แบบ','เขา','คุณ','อันนี้','นี่','นะ','ครับ','ค่ะ'}

def clean(plan,catalog):
    units={u['id']:u for u in catalog};result=copy.deepcopy(plan);changes=[]
    norm=lambda t:re.sub(r'[\s.,!?…ๆ]+','',t)
    for edit in result['edits']:
        tokens=[]
        for si,s in enumerate(edit['selections']):
            u=units[s['unit_id']]
            if not u['words'] or u.get('protected'):
                tokens.append(dict(text='',selection=si,index=None));continue
            tokens += [dict(text=norm(u['words'][i]['text']),selection=si,index=i) for i in range(s['first_word'],s['last_word']+1)]
        kept=[];removed=set()
        for i,token in enumerate(tokens):
            kept.append(i)
            for size in range(min(6,len(kept)//2),0,-1):
                left=kept[-2*size:-size];right=kept[-size:]
                texts=[tokens[j]['text'] for j in right]
                if not all(texts) or texts!=[tokens[j]['text'] for j in left]:continue
                phrase=''.join(texts)
                if size==1 and (phrase in REDUPLICATION or (phrase not in STUTTERS and len(phrase)<4)):continue
                if size>1 and len(phrase)<4:continue
                removed.update(right);del kept[-size:]
                changes.append(dict(map_id=edit.get('map_id'),name=edit['name'],text=phrase,
                    removed=[dict(unit_id=edit['selections'][tokens[j]['selection']]['unit_id'],word=tokens[j]['index']) for j in right]))
                break
        if not removed:continue
        rebuilt=[]
        for si,s in enumerate(edit['selections']):
            indexes=[t['index'] for j,t in enumerate(tokens) if t['selection']==si and j not in removed and t['index'] is not None]
            if not units[s['unit_id']]['words'] or units[s['unit_id']].get('protected'):
                rebuilt.append(s);continue
            runs=[]
            for index in indexes:
                if runs and index==runs[-1][1]+1:runs[-1][1]=index
                else:runs.append([index,index])
            rebuilt += [dict(s,first_word=a,last_word=b) for a,b in runs]
        edit['selections']=rebuilt
    used={s['unit_id'] for e in result['edits'] for s in e['selections']}
    for row in result.get('source_review',[]):
        if row['unit_id'] not in used and row['decision']=='used':
            row.update(decision='duplicate',reason='โปรแกรมตัดคำหรือวลีที่ซ้ำติดกัน',review_source=POLICY)
    return result,changes
