import hashlib
import json
import os
from pathlib import Path
import sys
import wave
import numpy as np


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_pcm(path):
    with wave.open(str(path), 'rb') as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise ValueError('เสียงต้องเป็น WAV 16kHz mono PCM16')
        return np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').copy()


def write_pcm(path, pcm):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.asarray(pcm, dtype='<i2').tobytes())


def extract_audio(project, out, emit):
    try:
        import av
    except ImportError:
        raise RuntimeError('ยังไม่ได้ติดตั้งตัวอ่านสื่อ เปิด SETUP.cmd ก่อน') from None
    if not callable(getattr(av,'open',None)) or not callable(getattr(av,'AudioResampler',None)):
        raise RuntimeError('ตัวอ่านสื่อ PyAV ติดตั้งไม่ครบหรือโหลดผิดตัว กรุณาปิดโปรแกรมแล้วเปิด REPAIR-MEDIA.cmd จากนั้นเปิด START.cmd และทำต่องานเดิม · av: '+str(getattr(av,'__file__',None)))
    pcm = np.zeros(project['duration'] * 320, dtype=np.int16)
    for index, clip in enumerate(project['primary']):
        emit(message=f'อ่านเสียงต้นฉบับ {index + 1}/{len(project["primary"])}')
        a, b = clip['start'], clip['end']
        start, seconds = clip['in_frame'] / 50, (b - a) / 50
        target = np.zeros((b - a) * 320, dtype=np.int16)
        filled = np.zeros(len(target), dtype=bool)
        with av.open(clip['path']) as container:
            channel = clip.get('source_channel', 1) - 1
            selected = None
            for stream in container.streams.audio:
                if channel < len(stream.layout.channels):
                    selected = stream
                    break
                channel -= len(stream.layout.channels)
            if selected is None:
                raise ValueError('หา source audio channel ที่เลือกไม่พบ')
            container.seek(int(max(0, start - 2) / selected.time_base), stream=selected)
            resampler = av.AudioResampler(format='fltp', layout=selected.layout, rate=16000)
            for frame in container.decode(selected):
                if frame.time is None:
                    raise ValueError('สื่อไม่มี timestamp เสียง')
                if frame.time >= start + seconds + 1:
                    break
                for audio in resampler.resample(frame):
                    pos = round((float(audio.pts * audio.time_base) - start) * 16000)
                    data = audio.to_ndarray()[channel]
                    left, right = max(0, pos), min(len(target), pos + len(data))
                    if left < right:
                        target[left:right] = np.clip(data[left - pos:right - pos] * 32768, -32768, 32767).astype(np.int16)
                        filled[left:right] = True
            for audio in resampler.resample(None):
                pos = round((float(audio.pts * audio.time_base) - start) * 16000)
                data = audio.to_ndarray()[channel]
                left, right = max(0, pos), min(len(target), pos + len(data))
                if left < right:
                    target[left:right] = np.clip(data[left - pos:right - pos] * 32768, -32768, 32767).astype(np.int16)
                    filled[left:right] = True
        if filled.mean() < .99:
            raise ValueError(f'อ่านเสียงได้ไม่ครบ {filled.mean():.1%}')
        pcm[a * 320:b * 320] = target
    write_pcm(out / 'timeline.wav', pcm)
    return pcm


def cuda_handles(shared_home=None):
    handles = []
    if os.name == 'nt':
        locations = [Path(sys.prefix) / 'Lib/site-packages/nvidia']
        if shared_home:
            locations.insert(0, Path(shared_home) / '.venv/Lib/site-packages/nvidia')
        for location in locations:
            for p in location.glob('*/bin'):
                os.environ['PATH'] = str(p) + os.pathsep + os.environ.get('PATH', '')
                handles.append(os.add_dll_directory(str(p)))
    return handles


def transcribe_gpu(pcm, model_path, emit, shared_home=None, compute_type='float16'):
    try:
        from faster_whisper import WhisperModel
        import ctranslate2
    except ImportError:
        raise RuntimeError('ยังไม่ได้ติดตั้ง Whisper/GPU เปิด SETUP.cmd ก่อน') from None
    handles = cuda_handles(shared_home)
    if ctranslate2.get_cuda_device_count() < 1:
        raise RuntimeError('ไม่พบ NVIDIA GPU ที่ใช้ CUDA ได้ ตรวจ driver หรือใช้โหมดข้อมูลจำลอง')
    model = WhisperModel(model_path, device='cuda', compute_type=compute_type, cpu_threads=8)
    segments, _ = model.transcribe(pcm.astype(np.float32) / 32768, language='th', beam_size=5, temperature=0,
                                   word_timestamps=True, condition_on_previous_text=False, vad_filter=True)
    transcript = []
    for s in segments:
        transcript.append(dict(start=s.start, end=s.end, text=s.text,
                               words=[dict(start=w.start, end=w.end, text=w.word) for w in s.words or []]))
        emit(message=f'ถอดเสียง GPU {s.end:.1f}/{len(pcm) / 16000:.1f} วินาที')
    return transcript


def vad_intervals(pcm):
    try:
        from faster_whisper.vad import get_speech_timestamps, VadOptions
    except ImportError:
        return None
    spans = get_speech_timestamps(pcm.astype(np.float32) / 32768,
                                 VadOptions(min_silence_duration_ms=200, speech_pad_ms=0))
    return [(s['start'] / 16000, s['end'] / 16000) for s in spans]
