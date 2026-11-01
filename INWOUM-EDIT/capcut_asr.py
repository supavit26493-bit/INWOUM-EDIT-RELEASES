"""CapCut text + local forced alignment; never treat subtitle blocks as word times."""
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.parse import urlsplit

POLICY = 'capcut-th-utterance-force-align-v1'
SOURCE_COMMIT = '7ab87fbd12cbafa60bea4a48903eddf42a939b07'


def contract(pcm):
    return dict(policy=POLICY, audio_sha256=hashlib.sha256(pcm.tobytes()).hexdigest(),
                samples=len(pcm), language='th-TH', source_commit=SOURCE_COMMIT)


def transcript_hash(transcript):
    return hashlib.sha256(json.dumps(transcript, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def verify_ready(pcm, folder, transcript):
    path = folder / 'capcut-transcription.json'
    record = json.loads(path.read_text(encoding='utf8')) if path.is_file() else {}
    if (record.get('contract') != contract(pcm) or record.get('status') != 'completed'
            or record.get('aligned_transcript_sha256') != transcript_hash(transcript)):
        raise ValueError('ข้อความ CapCut ไม่ตรงเสียงหรือยังจับเวลาไม่เสร็จ กรุณาเริ่มงานใหม่')


def make_client():
    import requests
    from capcut_tts_api import CapCutClient
    from capcut_tts_api.config import BASE_URL

    class Session(requests.Session):
        def __init__(self):
            super().__init__()
            self.trust_env = False  # No ambient credentials, proxies or netrc.

        def request(self, method, url, **kwargs):
            parsed = urlsplit(url)
            host = parsed.hostname or ''
            domains = ('capcutapi.com', 'capcut.com', 'byteoversea.com', 'ibytedtos.com',
                       'bytedanceapi.com', 'bytedance.net', 'bytevcloud.com', 'tiktokcdn.com',
                       'tos-my216-share.vodupload.com', 'tos-my16-share.vodupload.com')
            if parsed.scheme != 'https' or not any(host == d or host.endswith('.' + d) for d in domains):
                raise ValueError('CapCut ส่งปลายทางอัปโหลดที่ยังไม่รองรับ')
            kwargs['allow_redirects'] = False
            kwargs['timeout'] = min(kwargs.get('timeout', 60), 60)
            return super().request(method, url, **kwargs)

    return CapCutClient(session=Session(), base_url=BASE_URL, timeout=60)


def validate_blocks(blocks, seconds):
    if not isinstance(blocks, list) or not blocks:
        raise ValueError('CapCut ไม่ส่งข้อความถอดเสียงกลับมา')
    previous = 0.
    for block in blocks:
        text = block.get('text', '')
        a, b = float(block['start_time']) / 1000, float(block['end_time']) / 1000
        if (not isinstance(text, str) or not text.strip() or not all(math.isfinite(x) for x in (a, b))
                or not 0 <= a < b <= seconds + .04 or a < previous - .04 or b - a > 28):
            raise ValueError('ท่อน CapCut ไม่อยู่ในขอบเสียง หรือยาวเกิน 28 วินาทีสำหรับจับเวลา')
        previous = b


def merge_overlaps(segments):
    """Keep uncertain touching speech together; never trim or fabricate token times."""
    result = []
    for segment in segments:
        if result and segment['start'] < result[-1]['end']:
            prior = result[-1]
            prior['end'] = max(prior['end'], segment['end'])
            prior['text'] += ' ' + segment['text']
            prior['words'].extend(segment.get('words', []))
            prior['alignment_uncertain'] = True
            prior['force_whole_segment'] = True
        else:
            result.append(dict(segment, words=list(segment.get('words', []))))
    return result


def run(pcm, folder, config, emit, aligner=None, client_factory=None, cache_root=None, sleep=time.sleep):
    import bootstrap
    from engine import save, Cancelled
    from api_asr import Aligner
    expected = contract(pcm)
    local = folder / 'capcut-transcription.json'
    root = Path(cache_root) if cache_root is not None else bootstrap.ROOT / 'cache' / 'capcut'
    root.mkdir(parents=True, exist_ok=True)
    shared = root / (expected['audio_sha256'] + '.json')
    record = None
    for path in (local, shared):
        if path.is_file():
            candidate = json.loads(path.read_text(encoding='utf8'))
            if candidate.get('contract') != expected:
                raise ValueError('CapCut cache ไม่ตรงเสียง/เวอร์ชัน กรุณาเริ่มงานใหม่')
            record = candidate
            break

    def persist():
        save(local, record)
        save(shared, record)

    if record is None:
        record = dict(contract=expected, status='upload_sent', automatic_resubmission=False,
                      account_credentials_used=False, human_alignment_review='pending')
        emit(message='ส่งเสียงไป CapCut เพื่อถอดภาษาไทย · ไม่ใช้บัญชี Premium')
        persist()  # Unknown upload/submission results must never be resubmitted automatically.
        client = None
        try:
            client = (client_factory or make_client)()
            upload = client.upload_audio(folder / 'timeline.wav')
            emit(message='CapCut รับเสียงแล้ว กำลังส่งงานถอดเสียง')
            record['status'] = 'submission_sent'
            persist()
            created = client.create_stt_task(upload.vid, upload.md5, upload.duration_ms,
                                             language='th-TH', use_translation=False)
            rows = (created.get('data') or {}).get('tasks') or []
            if not rows:
                raise RuntimeError('missing task')
            task = rows[0]
            bind = task.get('bind_id') or (created.get('data') or {}).get('bind_id') or ''
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                emit(message='รอ CapCut ถอดเสียง · ไม่ส่งคำขอสร้างงานซ้ำ')
                response = client.query_stt_task(task['id'], task['token'], bind)
                rows = (response.get('data') or {}).get('tasks') or []
                state = str(rows[0].get('status', '')).lower() if rows else ''
                if state in ('success', 'succeed', 'succeeded', 'completed', 'done'):
                    parsed = asdict(client.extract_subtitles(response))
                    # Persist only text/times, never task IDs, auth or signed media URLs.
                    record['utterances'] = [dict(text=u['text'], start_time=u['start_time'],
                                                 end_time=u['end_time']) for u in parsed['utterances']]
                    record['status'] = 'completed'
                    persist()  # Always save service result BEFORE local GPU work.
                    break
                if state in ('failed', 'failure', 'error'):
                    raise RuntimeError('remote failure')
                for _ in range(4):
                    sleep(1)
                    emit()
            else:
                raise TimeoutError()
        except Cancelled:
            raise
        except Exception as exc:
            record['failure_type'] = type(exc).__name__
            persist()
            raise RuntimeError('CapCut ยังไม่สำเร็จหรือไม่ทราบผล ไม่ส่งซ้ำอัตโนมัติ '
                               'ตรวจ capcut-transcription.json ก่อนเริ่มงานใหม่') from None
        finally:
            if client is not None and getattr(client, 'session', None) is not None:
                client.session.close()
    if record.get('status') != 'completed':
        raise ValueError('มีคำขอ CapCut ที่ไม่ทราบผล ไม่ส่งซ้ำอัตโนมัติ กรุณาตรวจงานเดิมก่อน')
    persist()
    validate_blocks(record['utterances'], len(pcm) / 16000)
    aligner = aligner or Aligner(config)
    transcript = []
    for i, block in enumerate(record['utterances']):
        emit(message=f'จับเวลา CapCut บน GPU {i + 1}/{len(record["utterances"])} · ใช้ข้อความที่เก็บไว้')
        # Small acoustic context; never invent word times from subtitle block duration.
        a = max(0, round(block['start_time'] * 16) - 2400)
        b = min(len(pcm), round(block['end_time'] * 16) + 2400)
        segments = aligner(pcm[a:b], block['text'], a / 16000)
        transcript.extend(segments)
    transcript = merge_overlaps(transcript)
    for segment in transcript:
        if segment.get('alignment_uncertain'):
            segment['force_whole_segment'] = True
    record['alignment'] = 'GPU cross-attention against same audio; CapCut block times are not word times'
    record['aligned_transcript_sha256'] = transcript_hash(transcript)
    persist()
    return transcript
