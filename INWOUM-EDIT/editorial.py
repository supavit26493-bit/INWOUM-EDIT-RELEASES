"""Planning-only phrase boundaries and bounded post-assembly text review."""
import copy
import hashlib
import json
from pathlib import Path
import re

POLICY = 'story-v2'


def phrase_words(segment):
    """Treat standalone space tokens as boundaries, not as Thai syllable tokens.

    Zero-time Thai vowel pieces stay WITH their phrase. No evenly distributed or
    invented timing, and no change to the original transcript used for dead air.
    """
    if segment.get('force_whole_segment'):
        return []  # Keep uncertain CapCut/GPU spans whole, including overlapping words.
    raw = segment.get('words', [])
    norm = lambda s: re.sub(r'\s+', '', s)
    if not raw or norm(''.join(w['text'] for w in raw)) != norm(segment['text']):
        return []
    groups, current, pending = [], [], ''
    for word in raw:
        text = word['text']
        if not text:
            continue
        if text[0].isspace():
            if current:
                groups.append(current)
                current = []
            if not text.strip():
                pending += text
                continue
        item = dict(word)
        item['text'] = pending + text
        pending = ''
        current.append(item)
    if current:
        groups.append(current)
    result = []
    for group in groups:
        a, b = group[0]['start'], max(w['end'] for w in group)
        text = ''.join(w['text'] for w in group)
        if any(w['end'] - w['start'] > 4 for w in group):
            return []  # Long stretched token: keep coarse whole-unit fallback.
        if (b <= a or not re.search(r'[\w\u0e00-\u0e7f]', text) or text.strip() in ('ๆ', 'ฯ')) and result:
            result[-1]['text'] += text
            result[-1]['end'] = max(result[-1]['end'], b)
        elif b > a:
            result.append(dict(start=a, end=b, text=text))
        else:
            return []
    if not result or norm(''.join(w['text'] for w in result)) != norm(segment['text']):
        return []
    if any(a['end'] > b['start'] + .001 for a, b in zip(result, result[1:])):
        return []
    return result


def refine_catalog(catalog, transcript):
    result = copy.deepcopy(catalog)
    by_span = {(s['start'], s['end'], s['text']): s for s in transcript}
    for unit in result:
        segment = by_span.get((unit['start'], unit['end'], unit['text']))
        if segment and not unit['protected']:
            unit['words'] = phrase_words(segment)
    return result


def rules_text():
    import bootstrap
    return (bootstrap.RULES / 'EDITORIAL-STORY-v2.md').read_text(encoding='utf8')


def assembled_scripts(plan, catalog):
    by_id = {u['id']: u for u in catalog}
    scripts = []
    for index, edit in enumerate(plan['edits']):
        rows = []
        for selected in edit['selections']:
            u = by_id[selected['unit_id']]
            words = u['words']
            a, b = selected['first_word'], selected['last_word']
            text = ''.join(w['text'] for w in words[a:b+1]) if words else u['text']
            rows.append(dict(**selected, selected_text=text))
        scripts.append(dict(version=index+1, name=edit['name'], purpose=edit.get('purpose',''),
                            outline=edit.get('story_outline',[]), selections=rows,
                            assembled_text=' '.join(r['selected_text'].strip() for r in rows)))
    return scripts


def record_rules(folder):
    from engine import save
    text = rules_text()
    (folder / 'rules-used.txt').write_text(text, encoding='utf8')
    save(folder/'editorial-policy.json', dict(policy=POLICY, rules_sha256=hashlib.sha256(text.encode()).hexdigest(),
          source='rules/EDITORIAL-STORY-v2.md', max_text_calls=2,
          transcript_binding='audio-identity.json + transcript.json; see timings for ASR reuse',
          listening_review='pending'))


def validate_hooks(plan, catalog):
    ids = {u['id']:u for u in catalog}
    for hook in plan.get('hook_inventory',[]):
        u = ids.get(hook['unit_id'])
        if u is None:
            raise ValueError('Hook อ้างท่อนที่ไม่มีจริง')
        a,b = hook['first_word'],hook['last_word']
        if not 0 <= a <= b < max(1,len(u['words'])):
            raise ValueError('Hook อ้างขอบ phrase ไม่ถูกต้อง')
        if hook['decision']=='use' and not any(
            e['selections'][0]['unit_id']==hook['unit_id'] and
            e['selections'][0]['first_word']==a and e['selections'][0]['last_word']>=b
            for e in plan['edits']):
            raise ValueError('Hook ที่ระบุว่าใช้ยังไม่ได้เปิดเวอร์ชันจริง แผนถูกเก็บไว้ให้ตรวจ')


def write_script_report(folder, plan, catalog):
    lines=['# บทที่เรียบเรียงแล้ว', '', 'ข้อความจากท่อนที่เลือกจริง ไม่ใช่บทพากย์ใหม่ ยังต้องฟังรอยต่อและตรวจใน Premiere', '']
    for script in assembled_scripts(plan,catalog):
        lines += ['## '+script['name'],'',script['purpose'],'',
                  'โครงเรื่อง: '+' → '.join(script['outline']),'',script['assembled_text'],'',
                  '| ท่อน | หน้าที่ | ข้อความที่เลือก |','| --- | --- | --- |']
        for row in script['selections']:
            lines.append('| '+row['unit_id']+' ['+str(row['first_word'])+'–'+str(row['last_word'])+'] | '+row.get('role','')+' | '+row['selected_text'].replace('|','/')+' |')
        lines.append('')
    lines += ['## บันทึกจากการเกลาบท','']+['- '+note for note in plan.get('notes',[])]
    (folder/'ASSEMBLED-SCRIPTS.md').write_text('\n'.join(lines),encoding='utf8')
