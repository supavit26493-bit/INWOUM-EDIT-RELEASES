"""Check generated artifacts against exact source audio; does not approve listening."""
import argparse
import json
from pathlib import Path
import wave
import xml.etree.ElementTree as E
import bootstrap
from project import verify_sequence
from audio_io import read_pcm


def check(folder):
    latest = json.loads((folder / 'latest.json').read_text(encoding='utf8'))['folder']
    if Path(latest).name != latest:
        raise ValueError('Invalid revision path')
    revision = folder / latest
    manifest = json.loads((revision / 'manifest.json').read_text(encoding='utf8'))
    pcm = read_pcm(folder / 'timeline.wav')
    sequences = E.parse(revision / 'EDITS-WITH-USAGE.xml').findall('.//sequence')
    assert len(sequences) == len(manifest['edits']) + 1
    assert int(sequences[0].findtext('duration')) * 320 == len(pcm)
    for seq in sequences:
        verify_sequence(seq,preserve_disabled=manifest.get('export_policy')=='source-tracks-links-v1')
    usage = json.loads((revision / 'USAGE.json').read_text(encoding='utf8'))
    cursor = 0
    for span in usage:
        assert span['start'] == cursor < span['end']
        cursor = span['end']
    assert cursor * 320 == len(pcm)
    if manifest.get('markers') is False:
        assert not any(seq.findall('.//marker') for seq in sequences)
        if not manifest.get('crossfades'):assert not any(seq.findall('.//transitionitem') for seq in sequences)
    else:assert len(sequences[0].findall('marker')) == len(usage)
    for edit in manifest['edits']:
        expected = b''.join(pcm[round(r['start'] * 50) * 320:round(r['end'] * 50) * 320].tobytes() for r in edit['ranges'])
        if edit.get('audio_policy'):
            from default_audio import render_preview
            expected=render_preview(pcm,edit['ranges'],edit['audio_crossfades'],edit['preview_audio_track']).tobytes()
        with wave.open(str(revision / (edit['id'] + '.wav'))) as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
            assert wav.readframes(wav.getnframes()) == expected
    return dict(revision=latest, versions=len(manifest['edits']), source_frames=cursor,
                wav_exact_samples=True, source_usage_complete=True, human_audio_review='pending', premiere_import='pending')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('job', type=Path)
    print(json.dumps(check(parser.parse_args().job), ensure_ascii=False, indent=2))
