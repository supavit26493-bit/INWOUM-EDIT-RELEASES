"""Deterministic audio/XML fixture; no API, GPU, or customer footage."""
from pathlib import Path
import xml.etree.ElementTree as E
import numpy as np

from audio_io import write_pcm


def create(folder, two_cameras=False):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    pcm = np.zeros(12 * 16000, dtype=np.int16)
    for a, b, hz in [(0.6, 2.4, 220), (4.0, 5.8, 330), (8.0, 10.5, 440)]:
        t = np.arange(round((b - a) * 16000)) / 16000
        pcm[round(a * 16000):round(b * 16000)] = (np.sin(t * 2 * np.pi * hz) * 7000).astype(np.int16)
    media = folder / 'demo-tones.wav'
    write_pcm(media, pcm)
    root = E.Element('xmeml', version='4')
    seq = E.SubElement(root, 'sequence', id='demo')

    def field(parent, key, value):
        E.SubElement(parent, key).text = str(value)

    field(seq, 'name', 'FAST DEMO - synthetic tones')
    field(seq, 'duration', 600)
    rate = E.SubElement(seq, 'rate')
    field(rate, 'timebase', 50)
    field(rate, 'ntsc', 'FALSE')
    m = E.SubElement(seq, 'media')
    video = E.SubElement(m, 'video')
    audio = E.SubElement(m, 'audio')

    def add_clip(parent, token, kind, start=0, end=600, src=0):
        track = E.SubElement(parent, 'track')
        field(track, 'enabled', 'TRUE')
        clip = E.SubElement(track, 'clipitem', id=token)
        for key, value in dict(name=media.name, enabled='TRUE', start=start, end=end,
                               duration=600, **{'in': src, 'out': src + end - start},
                               pproTicksIn=src * 5080320000,
                               pproTicksOut=(src + end - start) * 5080320000).items():
            field(clip, key, value)
        clip.append(E.fromstring(E.tostring(rate)))
        file = E.SubElement(clip, 'file', id='demo-file')
        field(file, 'name', media.name)
        field(file, 'pathurl', media.resolve().as_uri())
        field(file, 'duration', 600)
        file.append(E.fromstring(E.tostring(rate)))
        fm = E.SubElement(file, 'media')
        fa = E.SubElement(fm, 'audio')
        field(fa, 'channelcount', 1)
        st = E.SubElement(clip, 'sourcetrack')
        field(st, 'mediatype', kind)
        field(st, 'trackindex', 1)
        return clip

    add_clip(audio, 'audio-main', 'audio')
    if two_cameras:
        # XML structure test only, not a playable video reference.
        add_clip(video, 'camera-a', 'video')
        add_clip(video, 'camera-b', 'video', 100, 500, 30)
        add_clip(audio, 'audio-stereo', 'audio')
    source = folder / 'demo.xml'
    E.indent(root)
    E.ElementTree(root).write(source, encoding='utf8', xml_declaration=True)
    transcript = [dict(start=a, end=b, text=text, words=[dict(start=a, end=b, text=text)]) for a, b, text in [
        (0.6, 2.4, 'ตัวอย่างเสียงช่วงที่หนึ่ง'), (4, 5.8, 'ตัวอย่างเสียงช่วงที่สอง'), (8, 10.5, 'ตัวอย่างเสียงช่วงที่สาม')]]
    return source, pcm, transcript
