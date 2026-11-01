"""API text, locally forced-aligned to its own audio; never reuse old word times."""
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
from api_transcription import transcribe_once
from audio_io import cuda_handles

PROMPT = 'ถอดบทสนทนาภาษาไทยตามเสียงจริง รวมคำซ้ำ พูดเล่น และการเตรียมเทค ไม่สรุป ไม่แต่งเติม อาจมีชื่อแบรนด์และศัพท์อังกฤษปน'
KEYWORDS = ('Pacsafe', 'RFID', 'ลวดสลิง', 'ตาข่าย', 'ซิปล็อก')
POLICY = 'gpt-transcribe-force-align-v1'


def windows(samples, vad):
    """Contiguous non-overlapping chunks <=28s, preferring centers of speech gaps."""
    total = samples / 16000
    gaps = []
    cursor = 0.
    for a, b in sorted(vad or []):
        if a - cursor >= .18:
            gaps.append((cursor + a) / 2)
        cursor = max(cursor, b)
    if total - cursor >= .18:
        gaps.append((cursor + total) / 2)
    out, start = [], 0
    while start < samples:
        if samples - start <= 28 * 16000:
            end = samples
        else:
            choices = [round(t * 16000) for t in gaps if start + 16 * 16000 <= t * 16000 <= start + 28 * 16000]
            end = min(choices, key=lambda n: abs(n - start - 24 * 16000)) if choices else start + 24 * 16000
        out.append((start, end))
        start = end
    return out


def normalize(text):
    return re.sub(r'\s+', '', text)


def aligned_segments(text, raw, offset, seconds):
    """Reject missing/misaligned text; group on Thai phrase spaces, never syllables."""
    if not raw or normalize(''.join(w['word'] for w in raw)) != normalize(text):
        raise ValueError('จับเวลาไม่ครบข้อความ API หยุดก่อนสร้างคัท กรุณาตรวจผลถอดเสียง')
    words = []
    previous = 0.
    for w in raw:
        a, b = float(w['start']), float(w['end'])
        if not all(math.isfinite(v) for v in (a, b)) or not 0 <= a <= b <= seconds + .04 or a < previous - .04:
            raise ValueError('เวลาจาก forced alignment ไม่ถูกต้อง หยุดก่อนสร้างคัท')
        previous = b
        words.append(dict(start=offset + min(a, seconds), end=offset + min(b, seconds), text=w['word']))
    # Do not create cuts around unaligned/collapsed tokens or implausibly long tokens.
    letters = [w for w in words if re.search(r'\w', w['text'])]
    if not letters or all(w['end'] <= w['start'] for w in letters):
        raise ValueError('forced alignment ไม่มีช่วงเวลาคำที่ใช้ได้ หยุดก่อนสร้างคัท')
    result, group = [], []
    for w in words:
        if group and w['text'][:1].isspace() and group[-1]['end'] > group[0]['start'] and w['start'] - group[0]['start'] >= 5:
            result.append(dict(start=group[0]['start'], end=group[-1]['end'], text=''.join(x['text'] for x in group), words=group))
            group = []
        group.append(w)
    if group:
        # Last punctuation can have zero duration. Preserve it in the same phrase.
        result.append(dict(start=group[0]['start'], end=group[-1]['end'], text=''.join(x['text'] for x in group), words=group))
    for segment in result:
        parts = segment['words']
        if segment['end'] <= segment['start']:
            raise ValueError('forced alignment ไม่พบขอบท่อนที่ใช้ได้ หยุดก่อนสร้างคัท')
        segment['alignment_uncertain'] = (any(w['end'] - w['start'] > 4 for w in parts)
            or sum(w['end'] <= w['start'] for w in parts) / len(parts) > .20)
        # Preserve raw alignment for acoustic analysis. The catalog exposes whole-phrase selection
        # only for uncertain spans, rather than unsafe word cuts or fabricated timing fixes.
    return result


class Aligner:
    def __init__(self, config):
        import bootstrap
        from faster_whisper import WhisperModel
        from faster_whisper.tokenizer import Tokenizer
        self.handles = cuda_handles(config.get('shared_home'))
        name = config.get('whisper_model', 'large-v3-turbo')
        location = name
        for home in [bootstrap.ROOT, Path(config.get('shared_home') or bootstrap.ROOT)]:
            if (home / 'models' / name / 'model.bin').is_file():
                location = str(home / 'models' / name)
                break
        self.model = WhisperModel(location, device='cuda', compute_type='float16', cpu_threads=8)
        self.tokenizer = Tokenizer(self.model.hf_tokenizer, self.model.model.is_multilingual, task='transcribe', language='th')

    def __call__(self, pcm, text, offset):
        from faster_whisper.audio import pad_or_trim
        features = self.model.feature_extractor(pcm.astype(np.float32) / 32768)
        encoder = self.model.encode(pad_or_trim(features))
        tokens = self.tokenizer.encode(text)
        if len(tokens) > 440:
            raise ValueError('ข้อความ API ยาวเกินช่วง alignment หยุดก่อนสร้างคัท')
        raw = self.model.find_alignment(self.tokenizer, [tokens], encoder, math.ceil(len(pcm) / 160))[0]
        return aligned_segments(text, raw, offset, len(pcm) / 16000)


def run(pcm, folder, config, key, emit, vad, aligner=None):
    from engine import save
    audio_hash = hashlib.sha256(pcm.tobytes()).hexdigest()
    path = folder / 'api-transcription.json'
    contract = dict(policy=POLICY, audio_sha256=audio_hash, model='gpt-transcribe', prompt=PROMPT,
                    keywords=list(KEYWORDS), windows=windows(len(pcm), vad))
    # JSON roundtrip makes tuple/list comparison stable on explicit resume.
    contract = json.loads(json.dumps(contract))
    record = json.loads(path.read_text(encoding='utf8')) if path.exists() else dict(contract=contract, chunks=[], human_alignment_review='pending')
    if record['contract'] != contract:
        raise ValueError('การถอด API เดิมไม่ตรงเสียง/การตั้งค่า กรุณาเริ่มงานใหม่')
    aligner = aligner or Aligner(config)
    transcript = []
    for index, (a, b) in enumerate(contract['windows']):
        emit(message=f'API ถอดเสียง + จับเวลา {index + 1}/{len(contract["windows"])} · {a/16000:.1f}–{b/16000:.1f}s')
        chunk = pcm[a:b]
        if index < len(record['chunks']):
            entry = record['chunks'][index]
            if entry['status'] != 'completed':
                raise ValueError('มีคำขอ API ที่ไม่ทราบผล ไม่ส่งซ้ำอัตโนมัติ กรุณาเริ่มงานใหม่เมื่อต้องการลองอีกครั้ง')
        else:
            if not key.strip():
                raise ValueError('ใส่ API key เพื่อถอดเสียงด้วย gpt-transcribe')
            entry = dict(start=a/16000, end=b/16000, status='sent')
            record['chunks'].append(entry)
            save(path, record)
            response = transcribe_once(chunk, key, 'gpt-transcribe', PROMPT, KEYWORDS)
            entry.update(status='completed', **response)
            save(path, record)  # Persist paid response BEFORE local alignment, so resume never reuploads it.
        segments = aligner(chunk, entry['text'], a/16000)
        transcript.extend(segments)
    record['completed'] = True
    record['alignment'] = 'Whisper cross-attention forced alignment of API text against the same audio; not human-verified'
    save(path, record)
    return transcript
