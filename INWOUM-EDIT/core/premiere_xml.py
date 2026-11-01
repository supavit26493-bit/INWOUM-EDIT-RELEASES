"""Inspect Premiere FCP7 XML and extract an unedited sync test window."""
import argparse
import copy
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def inspect(path):
    root = ET.parse(path).getroot()
    sequences = root.findall('.//sequence')
    if len(sequences) != 1:
        raise ValueError('Expected exactly one sequence')
    sequence = sequences[0]
    if sequence.findtext('rate/ntsc') != 'FALSE':
        raise ValueError('Only integer frame rates supported')
    fps = int(sequence.findtext('rate/timebase'))
    files = {f.get('id'): f for f in root.iter('file') if f.find('pathurl') is not None}
    return root, sequence, fps, files


def report(path):
    root, sequence, fps, files = inspect(path)
    tracks = []
    for kind in ('video', 'audio'):
        for index, track in enumerate(sequence.findall(f'media/{kind}/track'), 1):
            clips = []
            for clip in track.findall('clipitem'):
                source = files[clip.find('file').get('id')]
                clips.append({
                    'name': clip.findtext('name'), 'pathurl': source.findtext('pathurl'),
                    **{key: int(clip.findtext(key)) for key in ('start', 'end', 'in', 'out')},
                    'enabled': clip.findtext('enabled', 'TRUE'),
                    'source_channel': clip.findtext('sourcetrack/trackindex')})
            tracks.append({'kind': kind, 'index': index, 'enabled': track.findtext('enabled', 'TRUE'), 'clips': clips})
    return {'source_xml': str(Path(path).resolve()), 'sequence': sequence.findtext('name'),
            'fps': fps, 'duration_seconds': int(sequence.findtext('duration')) / fps,
            'tracks': tracks}


def window(path, start, seconds, primary_audio=None):
    original_root, sequence, fps, files = inspect(path)
    begin, finish = round(start * fps), round((start + seconds) * fps)
    if begin < 0 or finish <= begin or finish > int(sequence.findtext('duration')):
        raise ValueError('Window outside sequence')
    sequence = copy.deepcopy(sequence)
    sequence.set('id', 'sync-test-sequence')
    sequence.find('name').text = f'SYNC TEST {start:g}s + {seconds:g}s - no editorial cuts'
    sequence.find('duration').text = str(finish - begin)
    for tag in ('uuid', 'in', 'out', 'marker'):
        for element in sequence.findall(tag):
            sequence.remove(element)
    for track in sequence.findall('media/video/track') + sequence.findall('media/audio/track'):
        if track.find('transitionitem') is not None:
            raise ValueError('Transitions are not supported in sync test extraction')
        for clip in list(track.findall('clipitem')):
            a, b, src_in, src_out = [int(clip.findtext(k)) for k in ('start', 'end', 'in', 'out')]
            if b - a != src_out - src_in or a < 0:
                raise ValueError('Speed changes and transition boundaries are unsupported')
            if b <= begin or a >= finish:
                track.remove(clip)
                continue
            left, right = max(a, begin), min(b, finish)
            for key, value in {'start': left-begin, 'end': right-begin,
                               'in': src_in+left-a, 'out': src_in+right-a}.items():
                clip.find(key).text = str(value)
            # Premiere also exports source positions in ticks. Keep these in
            # agreement with frame edits, preserving any original subframe offset.
            for tag, delta in (('pproTicksIn', left-a), ('pproTicksOut', right-b)):
                field = clip.find(tag)
                if field is not None:
                    field.text = str(int(field.text) + delta * 254016000000 // fps)
    kept = {clip.get('id') for clip in sequence.iter('clipitem')}
    for clip in sequence.iter('clipitem'):
        for link in list(clip.findall('link')):
            if link.findtext('linkclipref') not in kept:
                clip.remove(link)
    if primary_audio is not None:
        audio_clips = sequence.findall('media/audio/track/clipitem')
        if not any(c.findtext('name') == primary_audio for c in audio_clips):
            raise ValueError('Primary audio not present in this window')
        for clip in audio_clips:
            if clip.findtext('name') != primary_audio:
                enabled = clip.find('enabled')
                if enabled is None:
                    enabled = ET.SubElement(clip, 'enabled')
                enabled.text = 'FALSE'
    # Retain the original master clips and canonical file definitions.
    root = copy.deepcopy(original_root)
    for parent in root.iter():
        old = parent.find('sequence')
        if old is not None:
            index = list(parent).index(old)
            parent.remove(old)
            parent.insert(index, sequence)
            break
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding='unicode')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--report', required=True)
    parser.add_argument('--window-output')
    parser.add_argument('--start', type=float, default=100)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--primary-audio', help='Keep only this named source audible; retain all track positions')
    args = parser.parse_args()
    data = report(args.source)
    xml = window(args.source, args.start, args.seconds, args.primary_audio) if args.window_output else None
    with Path(args.report).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    if xml is not None:
        with Path(args.window_output).open('x', encoding='utf-8') as stream:
            stream.write(xml)
