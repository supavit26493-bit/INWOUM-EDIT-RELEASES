"""Bounded audio transcription uploads; key stays in memory, no automatic retry."""
import io
import json
import secrets
import urllib.error
import urllib.request
import wave

import numpy as np


MODELS = ('gpt-transcribe', 'gpt-4o-transcribe', 'gpt-4o-mini-transcribe')


def wav_bytes(pcm):
    target = io.BytesIO()
    with wave.open(target, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.asarray(pcm, dtype='<i2').tobytes())
    return target.getvalue()


def multipart(audio, model, prompt='', keywords=()):
    if model not in MODELS:
        raise ValueError('เลือกโมเดลถอดเสียงที่รองรับ')
    if len(audio) > 25_000_000:
        raise ValueError('ไฟล์เสียงสำหรับ API ต้องไม่เกิน 25 MB')
    boundary = 'fast-asr-' + secrets.token_hex(16)
    chunks = []
    fields = [('model', model), ('response_format', 'json')]
    if prompt:
        fields.append(('prompt', prompt[:1800]))
    if model == 'gpt-transcribe':
        fields.append(('languages[]', 'th'))
        for word in keywords:
            if not isinstance(word, str) or not word.strip() or any(c in word for c in '\r\n<>'):
                raise ValueError('คำศัพท์แต่ละคำต้องเป็นบรรทัดเดียว ไม่ใช้ < หรือ >')
            fields.append(('keywords[]', word[:100]))
    else:
        fields.append(('language', 'th'))
    for name, value in fields:
        chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode('utf8'))
    chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode())
    chunks.extend((audio, f'\r\n--{boundary}--\r\n'.encode()))
    return b''.join(chunks), 'multipart/form-data; boundary=' + boundary


def transcribe_once(pcm, key, model='gpt-transcribe', prompt='', keywords=()):
    if not key.strip():
        raise ValueError('ใส่ API key ในหน้าโปรแกรมก่อนทดสอบ ไม่ต้องส่งในแชต')
    body, content_type = multipart(wav_bytes(pcm), model, prompt, keywords)
    req = urllib.request.Request('https://api.openai.com/v1/audio/transcriptions', data=body, method='POST',
                                 headers={'Authorization': 'Bearer ' + key.strip(), 'Content-Type': content_type})
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (400, 404):
            raise RuntimeError(f'API ถอดเสียง HTTP {error.code}: ตรวจสิทธิ์โมเดล/รูปแบบคำขอ ไม่มีเปลี่ยนโมเดลหรือส่งซ้ำอัตโนมัติ') from None
        raise RuntimeError(f'API ถอดเสียง HTTP {error.code}: ตรวจ key/เครดิต/สิทธิ์บัญชี ไม่มีส่งซ้ำอัตโนมัติ') from None
    except (TimeoutError, urllib.error.URLError):
        raise RuntimeError('ติดต่อ API ถอดเสียงไม่สำเร็จหรือหมดเวลา คำขออาจมีค่าใช้จ่าย ไม่ส่งซ้ำอัตโนมัติ') from None
    if not isinstance(result, dict) or not isinstance(result.get('text'), str) or not result['text'].strip():
        raise RuntimeError('API ไม่ส่งข้อความถอดเสียงที่สมบูรณ์')
    return dict(text=result['text'], usage=result.get('usage'), languages=result.get('languages'),
                word_timestamps_available=False, source='audio/transcriptions')
