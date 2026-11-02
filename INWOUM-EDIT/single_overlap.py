"""Repair only an exact duplicated clause-opening edge, never arbitrary repeated content."""
import copy
import re

POLICY='single-clause-edge-overlap-v1'
HEADS={'อันนี้','อันนั้น','อันเนี้ย','อันเนี่ย','นี่','นั่น','แต่','แล้ว','ก็','เพราะ','คือ','ซึ่ง','สำหรับ','โดย'}


def repair(plan,catalog):
    units={u['id']:u for u in catalog};fixed=copy.deepcopy(plan);changes=[]
    for ei,edit in enumerate(fixed.get('edits',[])):
        groups={}
        for si,s in enumerate(edit.get('selections',[])):groups.setdefault(s.get('unit_id'),[]).append((si,s))
        for uid,rows in groups.items():
            unit=units.get(uid)
            if not unit or unit.get('protected') or not unit.get('words'):continue
            words=unit['words']
            for i,(si,left) in enumerate(rows):
                for sj,right in rows[i+1:]:
                    a,b=left.get('first_word'),left.get('last_word');c,d=right.get('first_word'),right.get('last_word')
                    if any(type(x) is not int for x in (a,b,c,d)) or not (0<=a<=b<len(words) and 0<=c<=d<len(words)):continue
                    # Exactly one shared source token, at opposite nonempty clause edges.
                    if b==c and a<b<d:ending,opening,ending_index,opening_index=left,right,si,sj
                    elif d==a and c<d<b:ending,opening,ending_index,opening_index=right,left,sj,si
                    else:continue
                    wi=opening['first_word'];word=words[wi]
                    if not 0<word['end']-word['start']<=.6:continue
                    token=re.sub(r'[\s.,!?…ๆ]+','',word['text'])
                    if token=='อัน':
                        following=re.sub(r'[\s.,!?…ๆ]+','',words[wi+1]['text'])
                        if following not in {'นี้','นั้น','เนี้ย','เนี่ย','นู้น'}:continue
                    elif token not in HEADS:continue
                    before=copy.deepcopy(ending);ending['last_word']-=1
                    changes.append(dict(edit_index=ei,unit_id=uid,trimmed_selection=ending_index,
                        retained_selection=opening_index,word_index=wi,text=word['text'],
                        seconds=round(word['end']-word['start'],4),before=before,after=copy.deepcopy(ending),
                        reason='ขอบเดียวกันใช้คำเริ่มประโยคซ้ำ เก็บไว้กับวลีที่ตามมา ตัดเฉพาะคำท้ายที่ค้าง'))
    if not changes:return plan,[]
    return fixed,changes
