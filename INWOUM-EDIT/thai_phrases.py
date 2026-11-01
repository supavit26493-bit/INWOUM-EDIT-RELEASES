"""Thai lexical boundaries intersected with real aligned token boundaries only."""
import copy
import re

POLICY = 'thai-lexical-aligned-v1'


def phrases(segment, lexical_review=False):
    from pythainlp.tokenize import word_tokenize
    norm = lambda s: re.sub(r'\s+', '', s)
    raw = segment.get('words', [])
    text = ''.join(w['text'] for w in raw)
    if not raw or norm(text) != norm(segment['text']):
        return []
    lexical = word_tokenize(text, engine='newmm', keep_whitespace=True)
    if ''.join(lexical) != text:
        return []
    ends, pos = set(), 0
    for part in lexical:
        pos += len(part)
        ends.add(pos)
    safe, pos = {}, 0
    for i, word in enumerate(raw[:-1]):
        pos += len(word['text'])
        following = raw[i + 1]
        if (pos in ends and word['end'] <= following['start'] + .001
                and word['end']-word['start'] < 2 and following['end']-following['start'] < 2):
            safe[i + 1] = pos
    groups, start = [], 0
    countdown=re.match(r'(?:(?:โอเค|อะ|นะ|\s)*สามสองหนึ่ง)+',text)
    prefix_end=countdown.end() if countdown else 0
    for i in range(1, len(raw)):
        if i not in safe:
            continue
        if start==0 and prefix_end and safe[i]<prefix_end:
            continue
        previous = ''.join(w['text'] for w in raw[start:i])
        after = ''.join(w['text'] for w in raw[i:i+8])
        boundary = (lexical_review or (start==0 and prefix_end and safe[i]>=prefix_end) or len(previous.strip()) >= 12 or
            (len(previous.strip()) >= 4 and re.match(r'แต่ว่า|แต่|เพราะ|แล้ว|คือ|ใคร|ถ้า|เดี๋ยว|นี่ครับ|ทุกคน', after)) or
            (previous.strip() in ('อีก','อะ','เอ่อ') and after.startswith('คุณ')) or
            re.search(r'สามสองหนึ่ง\s*$', previous))
        if boundary and raw[i-1]['end'] > raw[start]['start']:
            groups.append(raw[start:i]); start=i
    groups.append(raw[start:])
    out=[]
    for group in groups:
        a,b=group[0]['start'],max(w['end'] for w in group)
        value=''.join(w['text'] for w in group)
        if b<=a or not value.strip():
            if not out:return []
            out[-1]['text']+=value;out[-1]['end']=max(out[-1]['end'],b)
        else:out.append(dict(start=a,end=b,text=value))
    if (not out or ''.join(w['text'] for w in out)!=text or
            any(a['end']>b['start']+.001 for a,b in zip(out,out[1:]))):return []
    return out


def refine(catalog, transcript, lexical_review=False):
    result=copy.deepcopy(catalog)
    by_span={(s['start'],s['end'],s['text']):s for s in transcript}
    for unit in result:
        segment=by_span.get((unit['start'],unit['end'],unit['text']))
        if segment and not unit['protected']:
            unit['words']=phrases(segment, lexical_review)
    return result
