"""VAD-negative pauses may contain noise: amplitude alone must not veto a cut.

Only relax inside aligned tokens that independently contain VAD-confirmed speech.
Entirely soft/unconfirmed speech, VAD-positive speech and protected action survive.
"""
import math
import numpy as np
import silence_v3
from fast_logic import FPS,PROFILES,merge,frames

def detect(pcm,transcript,profile='compact',protected=(),vad_speech=None):
    result=silence_v3.detect(pcm,transcript,profile,protected,vad_speech)
    if vad_speech is None:return result
    count=len(pcm)//320;voice=np.zeros(count,dtype=bool);confirmed=np.zeros(count,dtype=bool)
    for a,b in vad_speech:voice[max(0,math.floor(a*FPS)):min(count,math.ceil(b*FPS))]=True
    for segment in transcript:
        if segment.get('alignment_uncertain'):continue
        for word in segment.get('words',[]) or [segment]:
            a=max(0,math.floor(word['start']*FPS));b=min(count,math.ceil(word['end']*FPS))
            if np.count_nonzero(voice[a:b])>=2:confirmed[a:b]=True
    # Keep 60ms next to every VAD-positive span before evaluating pause length.
    guarded=voice.copy()
    for a,b in vad_speech:guarded[max(0,math.floor((a-.06)*FPS)):min(count,math.ceil((b+.06)*FPS))]=True
    candidate=confirmed&~guarded
    for a,b in protected:
        x,y=frames(a,b,count);candidate[x:y]=False
    edges=np.flatnonzero(np.diff(np.r_[False,candidate,False].astype(int)))
    p=PROFILES[profile];additional=[]
    for a,b in zip(edges[::2],edges[1::2]):
        a,b=int(a),int(b)
        if b-a<math.ceil(p['min_gap']*FPS):continue
        left=a+math.ceil(p['gap_post']*FPS);right=b-math.ceil(p['gap_pre']*FPS)
        if left<right:additional.append((left,right))
    removed=[(int(a),int(b)) for a,b in merge([tuple(r) for r in result['removed_frames']]+additional)]
    total=float(sum(b-a for a,b in removed)/FPS)
    result.update(policy='vad-negative-noise-v4',method='silero-vad+confirmed-token-noise-pauses',
        removed_frames=removed,extra_removed_seconds=round(total-result['removed_seconds'],2),removed_seconds=total)
    return result
