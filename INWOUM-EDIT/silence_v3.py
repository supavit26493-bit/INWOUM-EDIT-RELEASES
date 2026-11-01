"""Opt-in pause tightening; original detector remains available for old jobs."""
import math
import numpy as np
from fast_logic import detect_silence, acoustic_evidence, merge, frames, PROFILES


def detect(pcm, transcript, profile='compact', protected=(), vad_speech=None):
    original=detect_silence(pcm,transcript,profile,protected,vad_speech)
    if vad_speech is None:return original
    count=len(pcm)//320
    blocks=pcm[:count*320].astype(np.float32).reshape(-1,320)/32768
    db=20*np.log10(np.maximum(np.sqrt(np.mean(blocks*blocks,axis=1)),1e-9))
    # Modest 3 dB sensitivity increase, only where VAD is negative AND the phrase
    # contains independent audible speech. No change to VAD-positive/soft-only phrases.
    threshold=original['threshold_db']+3
    voice=np.zeros(count,dtype=bool)
    for a,b in vad_speech:
        voice[max(0,math.floor(a*50)):min(count,math.ceil(b*50))]=True
    occupied,contrast=acoustic_evidence(transcript,count,db,threshold,voice)
    quiet=~voice & ((~occupied)|(contrast & (db<threshold)))
    # Bridge at most 60ms of transient noise, never a VAD-positive speech frame.
    edges=np.flatnonzero(np.diff(np.r_[False,~quiet,False].astype(np.int8)))
    for a,b in zip(edges[::2],edges[1::2]):
        if a>0 and b<count and b-a<=3 and not np.any(voice[a:b]) and np.all(contrast[a:b]):quiet[a:b]=True
    for a,b in [frames(a,b,count) for a,b in protected]:quiet[a:b]=False
    edges=np.flatnonzero(np.diff(np.r_[False,quiet,False].astype(np.int8)))
    p=PROFILES[profile];removed=[]
    for a,b in zip(edges[::2],edges[1::2]):
        if b-a<math.ceil(p['min_gap']*50):continue
        left=int(a+math.ceil(p['gap_post']*50));right=int(b-math.ceil(p['gap_pre']*50))
        if left<right:removed.append((left,right))
    original.update(policy='vad-negative-adaptive-v3',method='silero-vad+energy+3db+transient-bridge',
        threshold_db=round(threshold,2),removed_frames=removed,
        removed_seconds=sum(b-a for a,b in removed)/50)
    return original
