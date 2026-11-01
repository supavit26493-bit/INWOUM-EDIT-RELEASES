"""Frame-based, reusable audio cuts. All positions remain on the source timeline."""
import math
import re
import numpy as np

FPS = 50
PROFILES = {
    'natural': dict(min_gap=0.50, pre=0.16, post=0.24, gap_pre=0.08, gap_post=0.10),
    'compact': dict(min_gap=0.30, pre=0.12, post=0.18, gap_pre=0.04, gap_post=0.06),
    'fast': dict(min_gap=0.22, pre=0.10, post=0.14, gap_pre=0.02, gap_post=0.04),
}


def merge(spans):
    result = []
    for a, b in sorted(spans):
        if a >= b:
            continue
        if result and a <= result[-1][1]:
            result[-1] = (result[-1][0], max(b, result[-1][1]))
        else:
            result.append((a, b))
    return result


def subtract(spans, removed):
    result = []
    for a, b in spans:
        cursor = a
        for x, y in removed:
            if y <= cursor:
                continue
            if x >= b:
                break
            if x > cursor:
                result.append((cursor, min(x, b)))
            cursor = max(cursor, y)
            if cursor >= b:
                break
        if cursor < b:
            result.append((cursor, b))
    return result


def frames(a, b, duration):
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in (a, b)):
        raise ValueError('เวลาต้องเป็นตัวเลขที่ถูกต้อง')
    if not 0 <= a < b <= duration / FPS + 0.02:
        raise ValueError('เวลานอกต้นฉบับ')
    return max(0, math.floor(a * FPS)), min(duration, math.ceil(b * FPS))


def validate_transcript(transcript, duration):
    if not isinstance(transcript, list) or not transcript:
        raise ValueError('ไม่พบข้อความถอดเสียง')
    previous = -1
    for s in transcript:
        frames(s['start'], s['end'], duration)
        if s['start'] < previous or not isinstance(s.get('text'), str):
            raise ValueError('ข้อความถอดเสียงไม่ถูกต้องหรือไม่เรียงเวลา')
        previous = s['start']
        for w in s.get('words', []):
            # Whisper occasionally emits zero-duration words; retain their text in the segment fallback.
            if w['start'] == w['end']:
                continue
            frames(w['start'], w['end'], duration)


def timed_words(segment):
    """Join Thai subword pieces and zero-duration combining marks before selecting edges.

    Whisper's 'words' are not necessarily Thai lexical words. Space-delimited groups
    are intentionally coarser, so the planner cannot cut between a consonant and vowel.
    If alignment text disagrees with decoded text, preserve the whole segment instead.
    """
    if segment.get('alignment_uncertain'):
        return []
    raw = segment.get('words', [])
    joined = ''.join(str(w.get('text', '')) for w in raw)
    normalize = lambda s: re.sub(r'\s+', '', s)
    if not raw or normalize(joined) != normalize(segment['text']):
        return []
    groups = []
    for w in raw:
        text = w.get('text', '')
        if not text:
            continue
        if not groups or (text[0].isspace() and text.strip()):
            groups.append(dict(start=w['start'], end=w['end'], text=text))
        else:
            groups[-1]['text'] += text
            groups[-1]['end'] = max(groups[-1]['end'], w['end'])
    if any(w['end'] <= w['start'] for w in groups):
        return []
    return groups


def speech_guards(transcript, duration, profile):
    p = PROFILES[profile]
    spans = []
    for s in transcript:
        words = timed_words(s)
        for w in words or [s]:
            a = max(0, math.floor((w['start'] - p['pre']) * FPS))
            b = min(duration, math.ceil((w['end'] + p['post']) * FPS))
            spans.append((a, b))
    return merge(spans)


def acoustic_evidence(transcript, duration, db, threshold, voice):
    """Decouple acoustic occupancy from the longer Thai phrases used by the planner.

    A stretched ASR token is not continuous speech evidence. Quiet, VAD-negative
    portions may be shortened when the surrounding aligned phrase also has voiced
    or clearly audible samples. Entirely soft/unconfirmed phrases stay protected.
    """
    occupied = np.zeros(duration, dtype=bool)
    contrast = np.zeros(duration, dtype=bool)
    for segment in transcript:
        raw = segment.get('words', [])
        valid = [w for w in raw if w['end'] > w['start'] and str(w.get('text', '')).strip()]
        for word in valid or [segment]:
            a = max(0, math.floor(word['start'] * FPS))
            b = min(duration, math.ceil(word['end'] * FPS))
            occupied[a:b] = True
        for phrase in timed_words(segment) or [segment]:
            a = max(0, math.floor(phrase['start'] * FPS))
            b = min(duration, math.ceil(phrase['end'] * FPS))
            if np.count_nonzero(voice[a:b] | (db[a:b] > threshold + 8)) >= 2:
                contrast[a:b] = True
    return occupied, contrast


def detect_silence(pcm, transcript, profile='compact', protected=(), vad_speech=None):
    """Find real inter-word gaps without treating a stretched ASR token as speech.

    Energy-only mode is conservative and works without an installed VAD runtime.
    Normal new jobs supply local Silero VAD speech spans (seconds).
    """
    if profile not in PROFILES:
        raise ValueError('ไม่รู้จักระดับความกระชับ')
    count = len(pcm) // 320
    if not count:
        raise ValueError('เสียงสั้นเกินไป')
    p = PROFILES[profile]
    validate_transcript(transcript, count)
    blocks = np.asarray(pcm[:count * 320], dtype=np.float32).reshape(-1, 320) / 32768
    db = 20 * np.log10(np.maximum(np.sqrt(np.mean(blocks * blocks, axis=1)), 1e-9))
    # Do not let all-zero blocks lower the noise floor for the rest of a recording.
    nonzero = db[db > -90]
    floor = float(np.percentile(nonzero, 10)) if len(nonzero) else -90
    threshold = max(-55, min(-38, floor + 6))
    low_energy = db < threshold
    quiet = low_energy.copy()
    if vad_speech is not None:
        voice = np.zeros(count, dtype=bool)
        for a, b in vad_speech:
            left = max(0, math.floor(a * FPS))
            right = min(count, math.ceil(b * FPS))
            voice[left:right] = True
        occupied, contrast = acoustic_evidence(transcript, count, db, threshold, voice)
        # Two independent audio signals may override a word timestamp stretched
        # across silence, but never VAD-positive speech or an unconfirmed soft phrase.
        quiet = ~voice & (~occupied | (low_energy & contrast))
        guards = []
    else:
        # Without VAD do not override ASR solely on the basis of low energy.
        guards = speech_guards(transcript, count, profile)
    protected_frames = [frames(a, b, count) for a, b in protected]
    for a, b in merge(guards + protected_frames):
        quiet[a:b] = False
    edges = np.flatnonzero(np.diff(np.r_[False, quiet, False].astype(np.int8)))
    removed = []
    for a, b in zip(edges[::2], edges[1::2]):
        if b - a < math.ceil(p['min_gap'] * FPS):
            continue
        # Test the original detected gap length, then retain edge room ONCE.
        # Padding the voice first and then testing min_gap used to miss 0.3–0.7s rests.
        left = int(a + math.ceil(p['gap_post'] * FPS))
        right = int(b - math.ceil(p['gap_pre'] * FPS))
        if left < right:
            removed.append((left, right))
    return dict(profile=profile, policy='interword-acoustic-v2',
                method='silero-vad+energy+token-guards' if vad_speech is not None else 'energy+asr-guards',
                min_gap_seconds=p['min_gap'], retained_gap_edge_seconds=p['gap_pre'] + p['gap_post'],
                threshold_db=round(threshold, 2), removed_frames=removed,
                removed_seconds=sum(b - a for a, b in removed) / FPS,
                protected_frames=protected_frames, human_audio_review='pending')


def make_catalog(transcript, duration, protected=()):
    """Small planning units with selectable word edges, independent of silence settings."""
    validate_transcript(transcript, duration)
    units = []
    for s in transcript:
        if not s['text'].strip():
            continue
        words = timed_words(s)
        units.append(dict(id=f'U{len(units) + 1:04}', start=s['start'], end=s['end'],
                          text=s['text'], words=words, protected=False))
    for a, b in protected:
        frames(a, b, duration)
        import hashlib
        token = hashlib.sha256(f'{a:.6f}:{b:.6f}'.encode()).hexdigest()[:10]
        units.append(dict(id='P' + token, start=a, end=b,
                          text='ช่วงสาธิตที่ผู้ใช้ระบุให้เก็บเมื่อใช้ในเวอร์ชัน', words=[], protected=True))
    if not units:
        raise ValueError('ไม่มีท่อนสำหรับเลือก')
    return units


def stitch_fragments(pieces, minimum_frames=12, max_bridge_frames=18):
    """Keep audible fragments, bridging only short local silence within one selection.

    Never delete a short syllable. Never cross a deliberately omitted editorial
    passage, a source reorder, or a gap in primary media coverage.
    """
    pieces = list(pieces)
    while len(pieces) > 1:
        options = []
        for i, (a, b) in enumerate(pieces):
            if b - a >= minimum_frames:
                continue
            if i and 0 <= a - pieces[i - 1][1] <= max_bridge_frames:
                options.append((a - pieces[i - 1][1], i - 1))
            if i + 1 < len(pieces) and 0 <= pieces[i + 1][0] - b <= max_bridge_frames:
                options.append((pieces[i + 1][0] - b, i))
        if not options:
            break
        _, index = min(options)
        pieces[index:index + 2] = [(pieces[index][0], pieces[index + 1][1])]
    return pieces


def resolve_plan(plan, catalog, silence, project):
    from project import validate_edits
    if not isinstance(plan, dict) or not isinstance(plan.get('edits'), list) or not 1 <= len(plan['edits']) <= 100:
        raise ValueError('ต้องมีเวอร์ชัน 1–100 แบบ')
    lookup = {u['id']: u for u in catalog}
    removed = [tuple(p) for p in silence['removed_frames']]
    p = PROFILES[silence['profile']]
    result = []
    for edit in plan['edits']:
        if not isinstance(edit.get('name'), str) or not edit['name'].strip():
            raise ValueError('ต้องมีชื่อเวอร์ชัน')
        selections = edit.get('selections')
        if not isinstance(selections, list) or not selections:
            raise ValueError('เวอร์ชันไม่มีท่อน')
        ranges = []
        chosen = []
        blocks = []
        natural_sales=silence.get('sales_continuity')=='sales-continuity-v1'
        for selected in selections:
            u = lookup.get(selected.get('unit_id'))
            if u is None:
                raise ValueError('รหัสท่อนไม่ตรงต้นฉบับ')
            first, last = selected.get('first_word'), selected.get('last_word')
            if type(first) is not int or type(last) is not int:
                raise ValueError('ขอบคำต้องเป็นจำนวนเต็ม')
            if u['words']:
                if not 0 <= first <= last < len(u['words']):
                    raise ValueError('ขอบคำอยู่นอกท่อน')
                a = max(0, math.floor((u['words'][first]['start'] - p['pre']) * FPS))
                b = min(project['duration'], math.ceil((u['words'][last]['end'] + p['post']) * FPS))
                if silence.get('policy') in ('vad-negative-adaptive-v3','vad-negative-noise-v4'):
                    # Padding must not reintroduce a deliberately excluded phrase
                    # (countdown, profanity, abandoned take) at either selected edge.
                    if first > 0:
                        a = max(a, math.ceil(u['words'][first-1]['end'] * FPS))
                    if last + 1 < len(u['words']):
                        b = min(b, math.floor(u['words'][last+1]['start'] * FPS))
            else:
                if first != 0 or last != 0:
                    raise ValueError('ท่อนที่ไม่มีเวลาคำต้องเลือก 0,0')
                a, b = frames(u['start'], u['end'], project['duration'])
            core_a = u['words'][first]['start'] if u['words'] else u['start']
            core_b = u['words'][last]['end'] if u['words'] else u['end']
            if any(max(core_a, x) < min(core_b, y) for x, y in chosen):
                raise ValueError('เลือกคำหรือช่วงเดียวกันซ้ำในเวอร์ชัน')
            chosen.append((core_a, core_b))
            block = dict(start=a, end=b, last_core=core_b, last_unit=u['id'],last_word=last,reason=selected.get('reason', ''), action=u['protected'])
            if silence.get('default02'):
                block['word_cores']=[(math.floor(w['start']*FPS),math.ceil(w['end']*FPS)) for w in (u['words'][first:last+1] if u['words'] else [u]) if w['end']>w['start']]
            # Union adjacent chronological source windows BEFORE applying the shared
            # silence map. Per-unit subtraction used to replay tiny padding slivers.
            adjacent_words=(natural_sales and blocks and blocks[-1]['last_unit']==u['id'] and blocks[-1]['last_word']+1==first and 0<=core_a-blocks[-1]['last_core']<=.12)
            if blocks and core_a >= blocks[-1]['last_core'] and (blocks[-1]['start'] <= a <= blocks[-1]['end'] or adjacent_words):
                blocks[-1]['end'] = max(b, blocks[-1]['end'])
                blocks[-1]['last_core'] = core_b
                blocks[-1].update(last_unit=u['id'],last_word=last)
                blocks[-1]['action'] |= u['protected']
                if silence.get('default02'):blocks[-1]['word_cores'].extend(block['word_cores'])
            else:
                blocks.append(block)
        emitted = []
        default_groups=[]
        for block in blocks:
            # Handle each continuous primary-media span separately: never synthesize
            # silence or restore excluded speech when connecting tiny fragments.
            coverage = merge([(c['start'], c['end']) for c in project['primary']])
            for ca, cb in coverage:
                a, b = max(block['start'], ca), min(block['end'], cb)
                if a >= b:
                    continue
                available = subtract([(a, b)], merge(emitted))
                for left, right in available:
                    raw_pieces=subtract([(left, right)], removed)
                    if silence.get('default02'):
                        from default02 import prune_padding
                        raw_pieces=prune_padding(raw_pieces,block['word_cores'],silence.get('default02_speech_frames'),block['action'])
                    if natural_sales:
                        default_groups.append((raw_pieces,block))
                        emitted.extend(raw_pieces)
                        continue
                    pieces = stitch_fragments(raw_pieces,
                        minimum_frames=12,
                        max_bridge_frames=40 if silence['profile']=='natural' else 18)
                    for x, y in pieces:
                        ranges.append(dict(start=x / FPS, end=y / FPS, reason=block['reason'], role='detail', action=block['action']))
                        emitted.append((x, y))
        if natural_sales:
            from default_audio import smooth_groups
            groups=smooth_groups([pieces for pieces,block in default_groups])
            for pieces,(_,block) in zip(groups,default_groups):
                for x,y in pieces:ranges.append(dict(start=x/FPS,end=y/FPS,reason=block['reason'],role='detail',action=block['action']))
        if not ranges:
            raise ValueError('เวอร์ชันไม่มีเนื้อหาเหลือหลังตัดช่วงเงียบ')
        result.append(dict(name=edit['name'], purpose=edit.get('purpose', ''), ranges=ranges))
    return validate_edits(result, project)


def full_selection(unit):
    return dict(unit_id=unit['id'], first_word=0, last_word=max(0, len(unit['words']) - 1), reason='เลือกทั้งท่อน')


def local_plan(catalog, silence, project):
    """Offline preview only: chronological chunks <= 170s, no invented AI choices."""
    edits, current = [], []
    for unit in sorted(catalog, key=lambda u: u['start']):
        if unit['protected']:
            continue  # Protect already-overlapping source; user can explicitly add this unit in the plan.
        selection = full_selection(unit)
        candidate = dict(edits=[dict(name='ทดลองตัดช่วงเงียบ', purpose='เรียงต้นฉบับ ไม่ได้เลือกเนื้อหาด้วย AI', selections=current + [selection])])
        try:
            resolved = resolve_plan(candidate, catalog, silence, project)
        except ValueError as error:
            if '3นาที' not in str(error):
                raise
            resolved = None
        if current and (resolved is None or sum(r['end'] - r['start'] for r in resolved[0]['ranges']) > 170):
            edits.append(dict(name=f'เสียงกระชับ {len(edits) + 1}', purpose='ตัวอย่างออฟไลน์ เรียงต้นฉบับ', selections=current))
            current = []
        current.append(selection)
    if current:
        edits.append(dict(name=f'เสียงกระชับ {len(edits) + 1}', purpose='ตัวอย่างออฟไลน์ เรียงต้นฉบับ', selections=current))
    return dict(edits=edits, notes=['ตัวอย่างตัดช่วงเงียบ ไม่ได้ใช้ AI เลือก Hook หรือเนื้อหา'])
