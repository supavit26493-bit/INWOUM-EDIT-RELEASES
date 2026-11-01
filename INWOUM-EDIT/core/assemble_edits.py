"""Assemble reviewed source ranges while preserving Premiere frame/tick sync."""
import copy
import math
import uuid
import xml.etree.ElementTree as ET
from premiere_xml import inspect, window


def assemble(source, ranges, name, token, primary_audio='C1324.MP4', max_seconds=180, include_markers=True):
    root, original, fps, _ = inspect(source)
    if not ranges:
        raise ValueError('No edit ranges')
    duration = int(original.findtext('duration'))
    cuts = []
    for start, end in ranges:
        if not all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in (start, end)):
            raise ValueError('Cut times must be finite numbers')
        a, b = round(start*fps), round(end*fps)
        if not 0 <= a < b <= duration:
            raise ValueError('Source range outside sequence')
        cuts.append((a, b))
    total = sum(b-a for a, b in cuts)
    if max_seconds is not None and total > fps*max_seconds:
        raise ValueError('Draft exceeds 180 seconds')
    combined = copy.deepcopy(original)
    combined.set('id', token)
    combined.find('name').text = name
    combined.find('duration').text = str(total)
    for tag in ('uuid', 'in', 'out', 'marker'):
        for element in combined.findall(tag):
            combined.remove(element)
    ET.SubElement(combined, 'uuid').text = str(uuid.uuid4())
    for key, value in {'MZ.WorkInPoint': 0, 'MZ.WorkOutPoint': total*254016000000//fps,
                       'Monitor.ProgramZoomIn': 0, 'Monitor.ProgramZoomOut': total*254016000000//fps,
                       'MZ.EditLine': 0}.items():
        if key in combined.attrib:
            combined.set(key, str(value))
    track_paths = ('media/video/track', 'media/audio/track')
    targets = [t for path in track_paths for t in combined.findall(path)]
    for track in targets:
        for clip in list(track.findall('clipitem')):
            track.remove(clip)
    cursor = 0
    manifest = []
    for index, (a, b) in enumerate(cuts, 1):
        piece = ET.fromstring(window(source, a/fps, (b-a)/fps, primary_audio)).find('.//sequence')
        id_map = {c.get('id'): f'{token}-cut{index}-{c.get("id")}' for c in piece.iter('clipitem')}
        pieces = [t for path in track_paths for t in piece.findall(path)]
        for target, track in zip(targets, pieces):
            for c in track.findall('clipitem'):
                clip = copy.deepcopy(c)
                clip.set('id', id_map[c.get('id')])
                for tag in ('start', 'end'):
                    clip.find(tag).text = str(int(clip.findtext(tag))+cursor)
                for link in clip.findall('link'):
                    link.find('linkclipref').text = id_map[link.findtext('linkclipref')]
                # Place clipitems ahead of track control tags.
                target.insert(len(target.findall('clipitem')), clip)
        if include_markers:
            marker = ET.SubElement(combined, 'marker')
            for key, value in [('name', f'{index:02d} SOURCE {a/fps:.2f}-{b/fps:.2f}s'),
                               ('comment', 'Draft edit; inspect the join by ear and picture'),
                               ('in', cursor), ('out', cursor+b-a)]:
                ET.SubElement(marker, key).text = str(value)
        manifest.append({'cut': index, 'source_start_frame': a, 'source_end_frame': b,
                         'timeline_start_frame': cursor, 'timeline_end_frame': cursor+b-a})
        cursor += b-a
    addresses = {}
    for kind in ('video', 'audio'):
        for track_index, track in enumerate(combined.findall(f'media/{kind}/track'), 1):
            for clip_index, clip in enumerate(track.findall('clipitem'), 1):
                addresses[clip.get('id')] = (kind, track_index, clip_index)
    for clip in combined.iter('clipitem'):
        for link in clip.findall('link'):
            kind, track_index, clip_index = addresses[link.findtext('linkclipref')]
            for tag, value in [('mediatype', kind), ('trackindex', track_index), ('clipindex', clip_index)]:
                link.find(tag).text = str(value)
    return combined, manifest


def package(source, sequences):
    root, original, _, _ = inspect(source)
    # Keep canonical source definitions even when every selected fragment is an
    # id-only reference (common when editing an earlier exported usage sequence).
    source_definitions={f.get('id'):copy.deepcopy(f) for f in root.iter('file') if f.find('pathurl') is not None}
    root = copy.deepcopy(root)
    for parent in root.iter():
        old = parent.find('sequence')
        if old is not None:
            position = list(parent).index(old)
            parent.remove(old)
            for i, sequence in enumerate(sequences):
                parent.insert(position+i, sequence)
            break
    # XML exported from a sequence can carry full file definitions directly in
    # clipitems. After combining many cuts, emit each definition once, followed
    # by id-only references, including when the first cut is from media #2.
    definitions=source_definitions
    for file in root.iter('file'):
        if file.find('pathurl') is not None:
            key=file.get('id')
            if key in definitions and definitions[key].findtext('pathurl')!=file.findtext('pathurl'):
                raise ValueError('Conflicting file identities')
            definitions[key]=copy.deepcopy(file)
    seen=set()
    for parent in root.iter():
        for file in list(parent.findall('file')):
            key=file.get('id')
            if key not in definitions:
                raise ValueError('Unresolved media file reference')
            replacement=copy.deepcopy(definitions[key]) if key not in seen else ET.Element('file',id=key)
            replacement.tail=file.tail
            position=list(parent).index(file)
            parent.remove(file);parent.insert(position,replacement)
            seen.add(key)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'+ET.tostring(root, encoding='unicode')
