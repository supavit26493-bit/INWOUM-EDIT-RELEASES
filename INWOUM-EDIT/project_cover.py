"""One local footage frame for the confirmation UI; never part of the editing pipeline."""
import base64,hashlib,json,threading
from pathlib import Path
from project import intake

LOCK=threading.Lock()
POLICY='project-cover-v1'

def choose_frame(project):
    target=min(5.0,project['duration']/100)
    clips=[(track,c) for (kind,track),rows in project['tracks'].items() if kind=='video' for c in rows if c['enabled']]
    if not clips:raise ValueError('ไม่มีคลิปวิดีโอสำหรับภาพปก')
    active=[(t,c) for t,c in clips if c['start']/50<=target<c['end']/50]
    if not active:
        first=min(clips,key=lambda pair:(pair[1]['start'],-pair[0]))[1]
        target=first['start']/50+min(5.0,(first['end']-first['start'])/100)
        active=[(t,c) for t,c in clips if c['start']/50<=target<c['end']/50]
    track,clip=max(active,key=lambda pair:pair[0])
    return dict(path=clip['path'],source_seconds=(clip['in_frame']+target*50-clip['start'])/50,
                timeline_seconds=round(target,2),track=track)

def render_png(path,seconds):
    import av,io
    with av.open(str(path)) as source:
        stream=source.streams.video[0]
        source.seek(max(0,int(seconds/float(stream.time_base))),stream=stream,backward=True)
        for count,frame in enumerate(source.decode(stream)):
            if count>600:break
            if frame.time is None or frame.time+.01<seconds:continue
            scale=min(1,640/frame.width,360/frame.height)
            width=max(1,round(frame.width*scale));height=max(1,round(frame.height*scale))
            buffer=io.BytesIO()
            with av.open(buffer,'w',format='image2pipe') as output:
                encoder=output.add_stream('png',rate=1);encoder.width=width;encoder.height=height;encoder.pix_fmt='rgb24'
                image=frame.reformat(width=width,height=height,format='rgb24');image.pts=0
                for packet in encoder.encode(image):output.mux(packet)
                for packet in encoder.encode():output.mux(packet)
            return buffer.getvalue()
    raise ValueError('อ่านเฟรมสำหรับภาพปกไม่สำเร็จ')

def create(config,cache):
    project=intake(config['xml'],config.get('relink') or None,int(config.get('audio_track',1)))
    selected=choose_frame(project);media=Path(selected['path']);stat=media.stat()
    fingerprint=dict(policy=POLICY,xml=hashlib.sha256(Path(project['xml']).read_bytes()).hexdigest(),
        path=str(media.resolve()),bytes=stat.st_size,mtime_ns=stat.st_mtime_ns,**{k:v for k,v in selected.items() if k!='path'})
    key=hashlib.sha256(json.dumps(fingerprint,sort_keys=True).encode()).hexdigest()
    cache=Path(cache);image_file=cache/(key+'.png')
    with LOCK:
        if image_file.exists():png=image_file.read_bytes()
        else:
            png=render_png(media,selected['source_seconds'])
            if not png.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('รูปแบบภาพปกไม่ถูกต้อง')
            cache.mkdir(parents=True,exist_ok=True)
            temp=image_file.with_suffix('.tmp');temp.write_bytes(png);temp.replace(image_file)
    return dict(image='data:image/png;base64,'+base64.b64encode(png).decode(),
        timeline_seconds=selected['timeline_seconds'],source_name=media.name,track=selected['track'],
        note='ภาพจากฟุตเทจต้นฉบับ · ไม่รวมสีและเอฟเฟกต์ใน Premiere')
