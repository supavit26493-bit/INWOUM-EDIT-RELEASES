"""Opt-in editorial refinement; existing Default/Original requests stay unchanged."""
STYLE='natural_v2'

def enabled(config):
    mode=config.get('content_mode','sales')
    return mode in ('sales','single') and config.get('single_style' if mode=='single' else 'sales_style','original')==STYLE

REFINEMENT='''
DEFAULT 02 — refine delivery in THIS assembly request; no new story inventory or extra review stage.
Keep a sound existing outline and retained topic order. Improve directness, not a duration/cut-count quota.
Remove redundant verbal run-ups, false starts and repeated premises before a useful statement, including
inside a source passage. These are contextual decisions: 'ตรงนี้' may introduce a necessary demonstration.
Within the SAME unit you may select multiple ordered, continuous phrase ranges, excluding only complete
unneeded words/phrases between them. Each retained range must still form a coherent spoken continuation.
Never collect isolated words from distant takes or cut through a syllable to force an elegant transcript.
Illustrative only, not source speech: 'มันเป็นสเปคห้าวัน / สำหรับสเปคห้าวันนี่คือ / ก็คุ้มละราคานี้'
can omit the redundant middle run-up if the actual source supports the resulting natural sentence.
Keep enthusiasm, useful reactions, source superlatives and emphatic reduplication (ดีมากมาก). Trim the
empty lead-in to praise, not the praise itself. Preserve negation, conditions, referents and uncertainty.
Short complete utterances can be valid: no blanket 0.4s minimum, no obligation to reduce cut count.
Reject orphan syllables and abandoned tails by choosing complete source word boundaries and context.
Read the assembled wording once before returning the SAME required JSON schema. Do not add review fields,
rewrite words, invent speech or repeat unchanged rejection reasons. Do not remove unique useful ideas.
'''

def instructions(base):
    return base.replace('DEFAULT / NATURAL CONTINUITY','DEFAULT 02 / NATURAL CONTINUITY').replace(
        'Within a passage keep its original word order and a continuous first_word..last_word range.',
        'Keep original local word order; each retained first_word..last_word range is continuous. The Default 02 rules below govern internal trimming.')+REFINEMENT

def prune_padding(pieces,cores,speech_frames,protected=False):
    """Remove only <=100ms non-speech islands outside every selected word core."""
    if protected or speech_frames is None:return pieces
    kept=[]
    for a,b in pieces:
        has_word=any(max(a,x)<min(b,y) for x,y in cores)
        has_voice=any(max(a,x)<min(b,y) for x,y in speech_frames)
        if b-a<=5 and not has_word and not has_voice:continue
        kept.append((a,b))
    return kept
