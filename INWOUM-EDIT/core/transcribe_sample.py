"""Extract a bounded source audio window and transcribe locally in Thai."""
import argparse
import json
from pathlib import Path
import wave
import sys
import os


def extract(source, output, start, seconds):
    import av
    import numpy as np
    if start < 0 or seconds <= 0:
        raise ValueError('Invalid audio window')
    samples = np.zeros(round(seconds * 16000), dtype=np.int16)
    filled = np.zeros(len(samples), dtype=bool)
    with av.open(source) as container:
        stream = container.streams.audio[0]
        container.seek(int(max(0, start-2) / stream.time_base), stream=stream)
        resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
        for frame in container.decode(stream):
            if frame.time is None:
                raise ValueError('Missing audio timestamps')
            if frame.time >= start + seconds + 1:
                break
            for audio in resampler.resample(frame):
                pos = round((float(audio.pts * audio.time_base) - start) * 16000)
                data = audio.to_ndarray().reshape(-1)
                left, right = max(0, pos), min(len(samples), pos+len(data))
                if right > left:
                    samples[left:right] = data[left-pos:right-pos]
                    filled[left:right] = True
    if filled.mean() < .99:
        raise ValueError(f'Incomplete audio coverage: {filled.mean():.1%}')
    with wave.open(str(output), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(samples.tobytes())


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--model', default='small')
    parser.add_argument('--reuse-audio', action='store_true', help='Reuse an already extracted sample WAV')
    parser.add_argument('--threads', type=int, default=8)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    parser.add_argument('--audio-input', help='Use an already extracted PCM WAV; source argument is retained as provenance')
    parser.add_argument('--vad', action='store_true')
    parser.add_argument('--carry-context', action='store_true')
    parser.add_argument('--compute-type', choices=('int8', 'int8_float16', 'float16'), default=None)
    parser.add_argument('--no-prompt', action='store_true')
    parser.add_argument('--prompt', help='Optional source-specific vocabulary; no product prompt is added by default')
    args = parser.parse_args()
    prefix = Path(args.prefix)
    wav = Path(args.audio_input) if args.audio_input else prefix.with_suffix('.wav')
    outputs = [wav, prefix.with_suffix('.json'), prefix.with_suffix('.txt')]
    if any(p.exists() for p in outputs[1:]) or (wav.exists() and not (args.reuse_audio or args.audio_input)):
        raise FileExistsError('Output exists; use a new prefix')
    if args.reuse_audio or args.audio_input:
        with wave.open(str(wav), 'rb') as existing:
            if (existing.getframerate(), existing.getnchannels(), existing.getsampwidth(), existing.getnframes()) != (16000, 1, 2, round(args.seconds*16000)):
                raise ValueError('Existing audio format or duration does not match')
    else:
        extract(args.source, wav, args.start, args.seconds)
    print(f'Audio extracted: {wav}', flush=True)
    dll_handles = []
    if args.device == 'cuda' and os.name == 'nt':
        for dll_dir in (Path(sys.prefix) / 'Lib/site-packages/nvidia').glob('*/bin'):
            os.environ['PATH'] = str(dll_dir) + os.pathsep + os.environ.get('PATH', '')
            dll_handles.append(os.add_dll_directory(str(dll_dir)))
    from faster_whisper import WhisperModel
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type or ('int8_float16' if args.device == 'cuda' else 'int8'), cpu_threads=args.threads,
                         download_root=str(Path('.cache/models').resolve()))
    # Supply PCM directly; avoids version-specific PyAV decode arguments.
    import numpy as np
    with wave.open(str(wav), 'rb') as sample:
        audio = np.frombuffer(sample.readframes(sample.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    segments, info = model.transcribe(audio, language='th', word_timestamps=True,
                                      beam_size=5, condition_on_previous_text=args.carry_context,
                                      vad_filter=args.vad,
                                      vad_parameters={'threshold': .35, 'min_silence_duration_ms': 500} if args.vad else None,
                                      initial_prompt=None if args.no_prompt else args.prompt)
    result = []
    progress_path = prefix.with_suffix('.progress.jsonl')
    progress = progress_path.open('x', encoding='utf-8')
    for s in segments:
        segment = {'start': args.start+s.start, 'end': args.start+s.end, 'text': s.text,
                   'avg_logprob': s.avg_logprob, 'no_speech_prob': s.no_speech_prob,
                   'words': [{'start': args.start+w.start, 'end': args.start+w.end,
                              'text': w.word, 'probability': w.probability} for w in s.words or []]}
        result.append(segment)
        progress.write(json.dumps(segment, ensure_ascii=False) + '\n')
        progress.flush()
        print(f'{segment["start"]:.2f}-{segment["end"]:.2f}: {s.text}', flush=True)
    progress.close()
    payload = {'source': str(Path(args.source).resolve()), 'source_start': args.start,
               'seconds': args.seconds, 'model': args.model, 'language': info.language,
               'device': args.device, 'vad': args.vad, 'audio_file': str(wav.resolve()),
               'reviewed': False, 'transcript': result}
    outputs[1].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    outputs[2].write_text('\n'.join(f'{s["start"]:.2f}-{s["end"]:.2f} {s["text"]}' for s in result), encoding='utf-8')


if __name__ == '__main__':
    main()
