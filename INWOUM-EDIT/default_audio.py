"""Default-only word guards and short native audio crossfades; video timing stays fixed."""
import copy,math
import xml.etree.ElementTree as E
import numpy as np

POLICY='default-audio-v2'

def enabled(config):
    mode=config.get('content_mode','sales')
    return mode in ('sales','single') and config.get('single_style' if mode=='single' else 'sales_style','original') in ('natural','natural_v2','sales_2_1','sales_2_2')

def smooth_groups(groups,budget=20,max_gap=12,minimum=30):
    """Prioritize the shortest speech islands across a version; never cross selections.

    Frames at 50fps: at most 0.24s per bridge and 0.4s total restored pauses.
    Keep every speech fragment. Only remove a local cut by retaining its short pause.
    """
    groups=[list(pieces) for pieces in groups]
    while True:
        choices=[]
        for gi,pieces in enumerate(groups):
            for i,((a,b),(c,d)) in enumerate(zip(pieces,pieces[1:])):
                gap=c-b;shortest=min(b-a,d-c)
                if shortest<minimum and 0<=gap<=min(max_gap,budget):choices.append((shortest,gap,gi,i))
        if not choices:break
        _,gap,gi,i=min(choices);pieces=groups[gi]
        pieces[i:i+2]=[(pieces[i][0],pieces[i+1][1])];budget-=gap
    return groups

def guard_words(silence,transcript):
    from fast_logic import subtract,merge
    result=copy.deepcopy(silence);guards=[]
    for segment in transcript:
        for w in segment.get('words',[]):
            # Short aligned phonemes are easy for VAD to miss; never cut their cores.
            if w['text'].strip() and 0<w['end']-w['start']<=.18:
                a,b=math.floor(w['start']*50),math.ceil(w['end']*50)
                # A token wholly inside a rejected pause has no independent speech
                # support. Protect only a clipped edge adjoining retained speech.
                overlap=sum(max(0,min(b,y)-max(a,x)) for x,y in result['removed_frames'])
                if 0<overlap<b-a:guards.append((max(0,a-1),b+1))
    result['removed_frames']=subtract(result['removed_frames'],merge(guards))
    result['removed_seconds']=sum(b-a for a,b in result['removed_frames'])/50
    result['default_audio_policy']=POLICY
    return result

def effective_edges(track,clip):
    items=list(track);i=items.index(clip)
    a,b=int(clip.findtext('start')),int(clip.findtext('end'))
    if a==-1:
        assert i and items[i-1].tag=='transitionitem'
        a=int(items[i-1].findtext('start'))
    if b==-1:
        assert i+1<len(items) and items[i+1].tag=='transitionitem'
        b=int(items[i+1].findtext('end'))
    return a,b

def apply_crossfades(seq,source,cuts,*,track_number=None,candidate_frames=None):
    """40ms centered fades, using one real media frame of handle on each side."""
    files={f.get('id'):f for f in E.parse(source).iter('file') if f.find('pathurl') is not None}
    boundaries={c['timeline_end_frame'] for c in cuts[:-1]};joins=[]
    for ti,track in enumerate(seq.findall('media/audio/track'),1):
        if track_number is not None and ti!=track_number:continue
        if track.findall('transitionitem'):continue
        if track.findtext('enabled','TRUE')=='FALSE':continue
        clips=track.findall('clipitem')
        original={id(c):{k:int(c.findtext(k)) for k in ('start','end','in','out')} for c in clips}
        for left,right in zip(clips,clips[1:]):
            l,r=original[id(left)],original[id(right)];t=l['end']
            if t!=r['start'] or t not in boundaries or min(t-l['start'],r['end']-t)<3:continue
            if candidate_frames is not None and t not in candidate_frames:continue
            if any(c.findtext('enabled','TRUE')=='FALSE' or c.find('filter/effect/effectid') is not None and any('time' in (f.findtext('effect/effectid','').lower()) for f in c.findall('filter')) for c in (left,right)):continue
            lf,rf=left.find('file').get('id'),right.find('file').get('id')
            if lf not in files or rf not in files:continue
            if lf==rf and l['out']==r['in']:continue
            duration=files[lf].findtext('duration') or left.findtext('duration')
            if not duration or l['out']+1>int(duration) or r['in']<1:continue
            # Do not change speed, clip count, links or video positions.
            left.find('out').text=str(l['out']+1);left.find('end').text='-1'
            right.find('in').text=str(r['in']-1);right.find('start').text='-1'
            for clip,tag,delta in [(left,'pproTicksOut',1),(right,'pproTicksIn',-1)]:
                field=clip.find(tag)
                if field is not None:field.text=str(int(field.text)+delta*5080320000)
            tr=E.Element('transitionitem')
            for k,v in [('start',t-1),('end',t+1),('alignment','center'),('cutPointTicks',t*5080320000)]:E.SubElement(tr,k).text=str(v)
            rate=E.SubElement(tr,'rate');E.SubElement(rate,'timebase').text='50';E.SubElement(rate,'ntsc').text='FALSE'
            effect=E.SubElement(tr,'effect')
            for k,v in [('name','Cross Fade (+3dB)'),('effectid','KGAudioTransCrossFade3dB'),('effecttype','transition'),('mediatype','audio'),('wipecode','0'),('wipeaccuracy','100'),('startratio','0'),('endratio','1'),('reverse','FALSE')]:E.SubElement(effect,k).text=v
            track.insert(list(track).index(left)+1,tr)
            joins.append(dict(track=ti,cut_frame=t,frames=2))
    return joins

def render_preview(pcm,ranges,joins,audio_track):
    spans=[(round(r['start']*50)*320,round(r['end']*50)*320) for r in ranges]
    output=np.concatenate([pcm[a:b] for a,b in spans]).copy()
    points={j['cut_frame'] for j in joins if j['track']==audio_track}
    cursor=0
    for i,((a,b),(c,d)) in enumerate(zip(spans,spans[1:])):
        cursor+=b-a
        if cursor//320 not in points:continue
        if b+320>len(pcm) or c<320:continue
        theta=np.linspace(0,np.pi/2,640)
        mixed=pcm[b-320:b+320].astype(np.float64)*np.cos(theta)+pcm[c-320:c+320].astype(np.float64)*np.sin(theta)
        output[cursor-320:cursor+320]=np.clip(np.rint(mixed),-32768,32767).astype('<i2')
    return output


def discontinuous_joins(pcm,ranges):
    """Conservative local click detector on A1; no ASR, API, or timing changes."""
    pcm=np.asarray(pcm,dtype=np.float64)
    points=set();cursor=0
    for left,right in zip(ranges,ranges[1:]):
        a,b=round(left['start']*50),round(left['end']*50)
        c=round(right['start']*50);cursor+=b-a
        if b==c or b*320<160 or c*320+160>len(pcm):continue
        tail=pcm[b*320-160:b*320];head=pcm[c*320:c*320+160]
        if len(tail)!=160 or len(head)!=160:continue
        jump=abs(tail[-1]-head[0]);normal=max(float(np.median(np.abs(np.diff(tail)))),float(np.median(np.abs(np.diff(head)))),1)
        if jump>=1311 and jump>=4*normal:points.add(cursor)
    return points
