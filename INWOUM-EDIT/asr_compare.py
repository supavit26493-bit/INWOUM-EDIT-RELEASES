"""Compare API and fixed-temperature Whisper on identical audio, without editing cuts."""
import difflib
import hashlib
import json
from pathlib import Path
import time
import unicodedata

import numpy as np
import bootstrap
from audio_io import read_pcm, write_pcm, cuda_handles
from api_transcription import transcribe_once, MODELS

WINDOWS = [(36, 60), (144, 168), (240, 264), (384, 408)]
KEYWORDS = ['Pacsafe', 'RFID', 'ลวดสลิง', 'ตาข่าย', 'ซิปล็อก']
PROMPT = 'บทสนทนาภาษาไทยเกี่ยวกับกระเป๋ากันขโมยและการเดินทาง อาจมีชื่อแบรนด์และศัพท์อังกฤษปน ถอดตามเสียงจริง รวมคำซ้ำ คำพูดเล่น และการเตรียมเทค ไม่สรุปหรือแต่งเติมประโยค'


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(path)


def text_differences(left, right):
    def normalize(text):
        return ''.join(c for c in unicodedata.normalize('NFC', text) if not c.isspace())
    a, b = normalize(left), normalize(right)
    sequence = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return dict(similarity=round(sequence.ratio(), 3),
                differences=[dict(type=tag, whisper=a[i:j], api=b[x:y]) for tag, i, j, x, y in sequence.get_opcodes() if tag != 'equal'],
                interpretation='Text disagreement only; not accuracy, WER or a human listening result')


def run(source, target, key, settings, emit, cancelled):
    model_name = settings.get('asr_model', 'gpt-transcribe')
    if model_name not in MODELS:
        raise ValueError('ไม่รองรับโมเดลถอดเสียงนี้')
    if not key.strip():
        raise ValueError('ใส่ API key ในหน้าโปรแกรมก่อนทดสอบ')
    pcm = read_pcm(source / 'timeline.wav')
    identity = json.loads((source / 'audio-identity.json').read_text(encoding='utf8'))
    sha = hashlib.sha256(pcm.tobytes()).hexdigest()
    if sha != identity['pcm_sha256']:
        raise ValueError('เสียงต้นทางเปลี่ยน ห้ามเทียบจาก cache ที่ไม่ตรง')
    duration = len(pcm) / 16000
    if duration < 408:
        windows = [(a, min(duration, a + 24)) for a in range(0, min(96, int(duration)), 24) if a < duration]
    else:
        windows = WINDOWS
    report = dict(kind='asr_comparison', source_job=source.name, audio_sha256=sha, model=model_name,
                  total_seconds=sum(b - a for a, b in windows), uploaded_seconds=0, api_calls=0,
                  prompt=PROMPT, keywords=KEYWORDS, rows=[], review='pending',
                  cuts_changed=False, human_reference_available=False, started=time.time())
    save(target / 'asr-comparison.json', report)
    config = json.loads((source / 'config.json').read_text(encoding='utf8'))
    shared = Path(config.get('shared_home') or bootstrap.ROOT)
    handles = cuda_handles(shared)
    from faster_whisper import WhisperModel
    model_path = shared / 'models/large-v3-turbo'
    if not (model_path / 'model.bin').exists():
        model_path = bootstrap.ROOT / 'models/large-v3-turbo'
    if not (model_path / 'model.bin').exists():
        raise ValueError('ไม่พบโมเดล Whisper ในเครื่อง ยังไม่เริ่มส่งเสียงไป API')
    emit(stage='asr_whisper', message='ถอด Whisper temperature=0 จากเสียงตัวอย่างชุดเดียวกัน')
    whisper = WhisperModel(str(model_path), device='cuda', compute_type='float16', cpu_threads=8)
    for index, (a, b) in enumerate(windows, 1):
        if cancelled():
            raise RuntimeError('หยุดการทดสอบแล้ว เก็บผลบางส่วนไว้')
        sample = pcm[round(a * 16000):round(b * 16000)]
        write_pcm(target / f'sample-{index}.wav', sample)
        started = time.perf_counter()
        segments, _ = whisper.transcribe(sample.astype(np.float32) / 32768, language='th', beam_size=5,
                                         temperature=0, condition_on_previous_text=False,
                                         word_timestamps=True, vad_filter=True)
        local = list(segments)
        text = ' '.join(s.text for s in local)
        row = dict(index=index, start=a, end=b, audio_file=f'sample-{index}.wav', whisper=text,
                   whisper_seconds=round(time.perf_counter() - started, 2), api=None,
                   word_alignment='API text is not mapped to Whisper word times')
        report['rows'].append(row)
        save(target / 'asr-comparison.json', report)
        emit(stage='asr_api', message=f'ส่งเสียงตัวอย่าง {index}/{len(windows)} ไป {model_name} · {a}–{b} วินาที')
        if cancelled():
            raise RuntimeError('หยุดก่อนส่งคำขอ API')
        report['api_calls'] += 1
        report['uploaded_seconds'] += b - a
        row['api_status'] = 'sent'
        save(target / 'asr-comparison.json', report)
        started = time.perf_counter()
        try:
            response = transcribe_once(sample, key, model_name, PROMPT, KEYWORDS)
        except Exception:
            row['api_status'] = 'failed_or_unknown'
            save(target / 'asr-comparison.json', report)
            raise
        row.update(api=response['text'], api_seconds=round(time.perf_counter() - started, 2),
                   api_status='completed', usage=response['usage'], languages=response['languages'],
                   comparison=text_differences(text, response['text']))
        save(target / 'asr-comparison.json', report)
    report['completed'] = True
    report['elapsed_seconds'] = round(time.time() - report['started'], 2)
    save(target / 'asr-comparison.json', report)
    lines = ['# เปรียบเทียบ API กับ Whisper', '', f'โมเดล API: {model_name}',
             f'เสียงรวม {report["total_seconds"]} วินาที / {report["api_calls"]} คำขอ', '',
             'ไม่มี transcript อ้างอิงที่คนฟังยืนยัน จึงยังไม่รายงานเปอร์เซ็นต์ความแม่นยำ ผลไม่เปลี่ยนคัทเดิม', '']
    for row in report['rows']:
        lines.extend([f'## {row["start"]}–{row["end"]} วินาที', '', '**Whisper temperature=0:**', '', row['whisper'], '',
                      f'**{model_name}:**', '', row['api'], ''])
    (target / 'ASR-COMPARISON.md').write_text('\n'.join(lines), encoding='utf8')
    emit(stage='done', message='เทียบ API กับ Whisper เสร็จแล้ว — เปิดฟังและตรวจข้อความคู่กันได้', result=report)
    return report
