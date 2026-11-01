"""Optional lexical-only subtitle cleanup; never edits audio or invents word times."""
import re

POLICY='subtitle-interjection-v1'
HESITATIONS={'เอ่อ','เออ','เอิ่ม','อ่า','อืม','อื้ม','อือ'}
EXCLAMATIONS={'โห','โอ้โห','โอ้ว','โอ้','ว้าว','อู้หู','อุ๊ย','โอ๊ย','โอ้ย','เฮ้ย','อ้าว','อ๋อ','ฮะ'}
# Always retain contextual grammar/lexical items even if a tokenizer isolates them.
CONTEXTUAL={'แบบ','คือ','ก็','นะ','อะ','อ่ะ','หรอ','เหรอ','ครับ','ค่ะ','คะ'}
MEANINGFUL={'เจ็บ','ปวด','ร้อน','หนาว','แสบ','ตกใจ','ช่วย','เข้าใจ','รู้','ทำไม','อะไร','เหรอ','หรอ','ไม่','อย่า'}

def exclusions(value):
    if not isinstance(value,str) or len(value)>500:raise ValueError('คำที่ต้องการเก็บยาวเกิน 500 ตัวอักษร')
    return {s.strip() for s in re.split(r'[,\n;]+',value) if s.strip()}

def validate(mode,keep):
    if mode not in ('off','light','strong'):raise ValueError('ระดับตัดคำอุทานไม่ถูกต้อง')
    exclusions(keep)

def plain(text):return re.sub(r'[\s.,!?…“”"\u200b]+','',text)

def clean(words,mode='off',keep=''):
    """Only whole timed lexical atoms. Mixed/unresolved ASR atoms stay untouched."""
    validate(mode,keep)
    if mode=='off':return words,[]
    protected=exclusions(keep);out=[];changes=[]
    for i,w in enumerate(words):
        token=plain(w['text']);remove=False;reason=''
        if token in (HESITATIONS|EXCLAMATIONS)-protected:
            previous=words[i-1] if i else None;following=words[i+1] if i+1<len(words) else None
            before=w['start']-previous['end'] if previous else float('inf')
            after=following['start']-w['end'] if following else float('inf')
            next_text=plain(following['text']) if following else ''
            contextual=any(next_text.startswith(t) for t in MEANINGFUL)
            if token in HESITATIONS:
                # Short pauses can be assent rather than hesitation: retain in light mode.
                remove=token in {'เอ่อ','เอิ่ม','อ่า'} or (mode=='strong' and not contextual)
                reason='เสียงลังเล'
            elif not contextual:
                remove=(before>=.35 and after>=.35) or (mode=='strong' and before>=.25)
                reason='คำอุทานเดี่ยว' if after>=.35 else 'คำอุทานต้นวลี'
        if remove:
            changes.append(dict(start=w['start'],end=w['end'],before=w['text'],after='',reason=reason))
        else:out.append(dict(w))
    if not out:raise ValueError('หลังตัดคำอุทานไม่มีข้อความซับเหลือ ลองระดับเบาหรือปิดตัวเลือกนี้')
    return out,changes
