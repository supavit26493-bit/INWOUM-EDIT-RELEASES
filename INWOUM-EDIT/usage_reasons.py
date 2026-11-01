"""Annotate unused timeline spans from supplied reviews; never infer silence or invent AI reasons."""
import csv
import json
import xml.etree.ElementTree as E


def explain(coverage,catalog,start,end):
    by_id={u['id']:u for u in catalog};reasons=[]
    for row in coverage['units']:
        if row['status']=='used' or min(end,row['end'])<=max(start,row['start']):continue
        unit=by_id[row['unit_id']]
        if row['status']=='unused':
            review=row.get('review') or {}
            reason=review.get('reason') or 'ยังไม่มีเหตุผลเฉพาะจากผู้เรียบเรียง ต้องตรวจเพิ่ม'
            reasons.append(row['unit_id']+': '+reason)
        else:
            phrases=unit['words'] or [unit]
            texts=[]
            for a,b in row['unselected_word_spans']:
                omitted=phrases[int(a):int(b)]
                if omitted and min(end,omitted[-1]['end'])>max(start,omitted[0]['start']):
                    texts.append(''.join(w['text'] for w in omitted))
            if texts:
                reasons.append(row['unit_id']+' ใช้บางส่วน ไม่เลือกวลี «'+' / '.join(texts)+'»; แผนไม่ได้ให้เหตุผลเฉพาะวลีนี้ จึงยังไม่สรุปว่าใช้ไม่ได้')
    return ' | '.join(reasons) or 'ขอบเสียง/ช่องว่างที่ไม่ทับวลีที่ถูกตัดในข้อความ อาจมีพักหรือภาพสาธิต ต้องตรวจเสียง/ภาพ ไม่ยืนยันว่าเงียบจากข้อความอย่างเดียว'


def annotate(revision,coverage,catalog):
    usage=json.loads((revision/'USAGE.json').read_text(encoding='utf8'))
    rows=[dict(start=r['start']/50,end=r['end']/50,reason=explain(coverage,catalog,r['start']/50,r['end']/50)) for r in usage if not r['uses']]
    by_frame={round(r['start']*50):r for r in rows}
    path=revision/'EDITS-WITH-USAGE.xml';tree=E.parse(path)
    for marker in tree.find('.//sequence').findall('marker'):
        row=by_frame.get(int(marker.findtext('in')))
        if row:marker.find('comment').text=row['reason']
    tree.write(path,encoding='unicode')
    (revision/'UNUSED-REASONS.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
    with (revision/'UNUSED-REASONS.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['source start seconds','source end seconds','reason'])
        for row in rows:w.writerow([row['start'],row['end'],row['reason']])
    report=['# เหตุผลช่วงที่ไม่ได้ใช้','', 'เหตุผลจากแผนที่บันทึกจริง ไม่ใช่การตรวจฟัง/ภาพ ข้อความใช้บางส่วนที่ไม่มีเหตุผลเฉพาะจะแจ้งว่ายังไม่ได้อธิบาย ไม่เหมาว่าเป็นฟุตเสีย','']
    for row in rows:report.extend([f"- {row['start']:.2f}–{row['end']:.2f}s: {row['reason']}",''])
    (revision/'UNUSED-REASONS.md').write_text('\n'.join(report),encoding='utf8')
