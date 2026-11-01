"""Account for used and unused speech without forcing every second into an edit."""
from fast_logic import merge, subtract


def report(plan, catalog, edits):
    by_id = {u['id']: u for u in catalog}
    selected = {uid: [] for uid in by_id}
    for index, edit in enumerate(plan['edits'], 1):
        for item in edit['selections']:
            unit = by_id[item['unit_id']]
            if unit['words']:
                a, b = item['first_word'], item['last_word'] + 1
            else:
                a, b = 0, 1
            selected[unit['id']].append((a, b, index))
    reviews = {r['unit_id']: r for r in plan.get('source_review', [])}
    units = []
    for u in catalog:
        uses = selected[u['id']]
        covered = merge([(a, b) for a, b, _ in uses])
        missing = subtract([(0, max(1, len(u['words'])))], covered)
        units.append(dict(unit_id=u['id'], start=u['start'], end=u['end'], text=u['text'],
                          versions=sorted({v for _, _, v in uses}),
                          status='unused' if not uses else 'partial' if missing else 'used',
                          unselected_word_spans=missing, review=reviews.get(u['id'])))
    return dict(total_units=len(units), used_units=sum(bool(u['versions']) for u in units),
                unused_units=sum(u['status'] == 'unused' for u in units),
                partial_units=sum(u['status'] == 'partial' for u in units),
                reviewed_units=len(reviews), units=units,
                short_fragments=[dict(version=i, start=r['start'], end=r['end'], seconds=round(r['end'] - r['start'], 2))
                                 for i, e in enumerate(edits, 1) for r in e['ranges'] if round((r['end'] - r['start']) * 50) < 12],
                meaning_review='pending', audio_review='pending')


def validate_source_review(plan, catalog):
    rows = plan.get('source_review')
    expected = {u['id'] for u in catalog}
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError('AI ยังทบทวนฟุตเทจไม่ครบทุกท่อน แผนเดิมถูกเก็บไว้ให้ตรวจ')
    seen = set()
    used = {s['unit_id'] for e in plan['edits'] for s in e['selections']}
    for row in rows:
        uid = row.get('unit_id')
        if uid not in expected or uid in seen or not isinstance(row.get('reason'), str) or not row['reason'].strip():
            raise ValueError('รายการทบทวนท่อนขาด/ซ้ำ หรือไม่มีเหตุผล')
        if row.get('decision') not in ('used', 'duplicate', 'production', 'money', 'incomplete', 'uncertain', 'alternative'):
            raise ValueError('สถานะทบทวนท่อนไม่ถูกต้อง')
        if (row['decision'] == 'used') != (uid in used):
            raise ValueError('เหตุผลทบทวนไม่ตรงกับท่อนที่เลือกจริง')
        seen.add(uid)
    return plan
