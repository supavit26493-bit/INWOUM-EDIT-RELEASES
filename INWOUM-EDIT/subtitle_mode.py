"""CapCut-only subtitles. Local Thai grouping, genuine aligned edges, no paid LLM."""
import hashlib,json,math,re,time,uuid,unicodedata
from pathlib import Path
import numpy as np
import capcut_asr
from audio_io import digest,read_pcm,write_pcm

POLICY='thai-srt-v3'
IGNORED=set('ะัาำิีึืุูเแโใไๅฤฦ็่้๊๋์ํ๎')
MAIYAMOK={'จริง','ค่อย','เรื่อย','ต่าง','เด็ก','สาว','หนุ่ม','คล้าย','มาก','น้อย',
          'บ่อย','ใกล้','ไกล','เร็ว','ช้า','เล็ก','ใหญ่','เบา','ใหม่','เก่า','อื่น','ทั่ว'}

def maiyamok_text(text):
    """Only two complete allowlisted lexical tokens; never arbitrary repetition."""
    from pythainlp.tokenize import word_tokenize
    tokens=word_tokenize(text,engine='newmm',keep_whitespace=True)
    result=[];i=0
    while i<len(tokens):
        token=tokens[i];j=i+1
        while j<len(tokens) and tokens[j].isspace():j+=1
        if token in MAIYAMOK and j<len(tokens) and tokens[j]==token:
            before=next((t for t in reversed(tokens[:i]) if not t.isspace()),None)
            after=next((t for t in tokens[j+1:] if not t.isspace()),None)
            if before!=token and after not in (token,'ๆ'):
                result.append(token+' ๆ');i=j+1;continue
        result.append(token);i+=1
    # Existing marks are spaced without changing which word/phrase they repeat.
    return re.sub(r'\s*ๆ\s*',' ๆ ',''.join(result)).rstrip() if 'ๆ' in ''.join(result) else ''.join(result)

def maiyamok_atoms(words):
    """Join two nearby repeated standalone tokens; retain both source word times."""
    result=[];changes=[];i=0
    while i<len(words):
        item=dict(words[i]);base=item['text'].strip()
        if i+1<len(words) and base in MAIYAMOK and words[i+1]['text'].strip()==base and 0<=words[i+1]['start']-item['end']<=.25:
            previous=words[i-1]['text'].strip() if i else None
            following=words[i+2]['text'].strip() if i+2<len(words) else None
            if previous!=base and following!=base:
                item['text']+=words[i+1]['text'];item['end']=words[i+1]['end'];i+=1
        original=item['text'];item['text']=maiyamok_text(original) if not item.get('has_long_pause') else original
        if item['text'].endswith('ๆ') and i+1<len(words) and not words[i+1]['text'][:1].isspace():item['text']+=' '
        if item['text']!=original:changes.append(dict(start=item['start'],end=item['end'],before=original,after=item['text']))
        result.append(item);i+=1
    return result,changes

def count(text):
    return sum(c not in IGNORED and not unicodedata.combining(c) for c in text)

def validate_config(config):
    import subtitle_cleanup
    subtitle_cleanup.validate(config.get('subtitle_cleanup','off'),config.get('subtitle_keep_words',''))
    from subtitle_asr import PROVIDERS
    source=Path(config.get('subtitle_source',''))
    if not str(config.get('subtitle_source','')).strip() or not source.is_file():
        raise ValueError('เลือกไฟล์ XML เสียง หรือวิดีโอที่มีอยู่ก่อน')
    limit=config.get('subtitle_chars',40)
    if type(limit) is not int or not 10<=limit<=120:raise ValueError('จำนวนตัวอักษรต่อซับต้องเป็นจำนวนเต็ม 10–120')
    if type(config.get('subtitle_ai_polish',False)) is not bool:raise ValueError('ตัวเลือก AI เกลาคำไม่ถูกต้อง')
    if config.get('subtitle_ai_polish') and config.get('subtitle_provider','capcut')!='capcut':raise ValueError('AI เกลาคำรองรับ CapCut ในรุ่นนี้')
    if not isinstance(config.get('subtitle_glossary',''),str) or len(config.get('subtitle_glossary',''))>3000:raise ValueError('รายการชื่อเฉพาะยาวเกิน 3000 ตัว')
    if config.get('subtitle_ai_polish'):
        from model_catalog import model_info
        if not model_info(config.get('model','gpt-6.1-terra'))['supported']:raise ValueError('โมเดลนี้ไม่รองรับการเกลาซับ')
        if config.get('effort','medium') not in ('default','low','medium','high'):raise ValueError('ระดับการคิดไม่ถูกต้อง')
    provider=config.get('subtitle_provider','capcut')
    if provider not in PROVIDERS:raise ValueError('ตัวถอดซับไม่ถูกต้อง')
    return dict(config,model=config.get('model','gpt-6.1-terra'),effort=config.get('effort','medium'),subtitle_chars=limit,subtitle_ai_polish=config.get('subtitle_ai_polish',False),content_mode='subtitles',subtitle_provider=provider,
                transcription='capcut' if provider=='capcut' else 'whisper',subtitle_policy=POLICY)

def atoms(transcript,hard_limit=45):
    from pythainlp.tokenize import word_tokenize
    result=[]
    for segment in transcript:
        raw=segment.get('words',[])
        if not raw:
            raw=[dict(start=segment['start'],end=segment['end'],text=segment['text'])]
        text=''.join(w['text'] for w in raw)
        if re.sub(r'\s+','',text)!=re.sub(r'\s+','',segment['text']):
            raise ValueError('เวลาคำไม่ตรงข้อความ หยุดก่อนแบ่งซับ')
        tokens=word_tokenize(text,engine='newmm',keep_whitespace=True)
        ends=set();pos=0
        numeric=lambda t:bool(re.fullmatch(r'(?:(?:ศูนย์|หนึ่ง|สอง|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า|สิบ|ร้อย|พัน|หมื่น|แสน|ล้าน|ยี่|เอ็ด)|[0-9๐-๙.,])+',t))
        for index,token in enumerate(tokens):
            pos+=len(token)
            following=tokens[index+1] if index+1<len(tokens) else ''
            inseparable=(token==following and bool(token.strip())) or (numeric(token) and numeric(following)) or (token.strip()=='นะ' and following.strip() in ('ครับ','คะ','ค่ะ'))
            if not inseparable:ends.add(pos)
        group=[];pos=0
        for index,w in enumerate(raw):
            if not all(isinstance(w[k],(int,float)) and math.isfinite(w[k]) for k in ('start','end')) or w['start']<0 or w['end']<w['start']:
                raise ValueError('เวลาคำไม่ถูกต้อง')
            group.append(w);pos+=len(w['text'])
            safe=index==len(raw)-1 or (pos in ends and w['end']<=raw[index+1]['start']+.001
                and raw[index+1]['start']>group[0]['start']
                and not unicodedata.combining(raw[index+1]['text'][:1] or ' ')
                and raw[index+1]['text'][:1] not in set('ะัาำิีึืุู็่้๊๋์ํ๎'))
            if safe:
                value=''.join(t['text'] for t in group)
                if value.strip():
                    a,b=group[0]['start'],max(t['end'] for t in group)
                    if b<=a:
                        if not result:raise ValueError('คำแรกไม่มีช่วงเวลาที่ใช้ได้')
                        result[-1]['text']+=value
                    else:result.append(dict(start=a,end=b,text=value,has_long_pause=any(y['start']-x['end']>.35 for x,y in zip(group,group[1:]))))
                elif result:result[-1]['text']+=value
                group=[]
    for a in result:
        if count(a['text'].strip())>hard_limit:
            raise ValueError(f'มีคำหรือช่วงที่จับเวลาแยกไม่ได้ยาวเกิน {hard_limit} ตัว ไม่เดาเวลาคำ กรุณาตรวจข้อความช่วง '+str(round(a['start'],2)))
    if not result:raise ValueError('ไม่พบคำสำหรับสร้างซับ')
    return result

def boundary(text,gap):
    if gap>=.55:return -24
    if re.search(r'[.!?。！？]$|(?:ครับ|ค่ะ|คะ|นะครับ|นะคะ|เลย|แล้วนะ)$',text.strip()):return -15
    if re.search(r'(?:และ|หรือ|เพราะ|แต่ว่า|ที่|ซึ่ง|เป็น|สำหรับ|บอกว่า|แล้วก็)$',text.strip()):return 24
    return 0

def split(transcript,maiyamok=True,changes=None,soft_limit=40,cleanup='off',keep_words='',cleanup_changes=None):
    hard_limit=soft_limit+5
    words=atoms(transcript,hard_limit)
    if cleanup!='off':
        import subtitle_cleanup
        words,removed=subtitle_cleanup.clean(words,cleanup,keep_words)
        if cleanup_changes is not None:cleanup_changes.extend(removed)
    if maiyamok:
        words,edited=maiyamok_atoms(words)
        if changes is not None:changes.extend(edited)
    n=len(words);cost=[float('inf')]*(n+1);cost[n]=0;nexts={}
    for i in range(n-1,-1,-1):
        text=''
        for j in range(i,n):
            if j>i and words[j]['start']-words[j-1]['end']>=.8:break
            text+=words[j]['text'];size=count(text.strip())
            if size>hard_limit:break
            duration=words[j]['end']-words[i]['start']
            gap=words[j+1]['start']-words[j]['end'] if j+1<n else 1
            penalty=20+max(0,min(12,soft_limit)-size)*1.8+max(0,size-soft_limit)**2*3
            penalty+=max(0,.8-duration)*24+max(0,duration-6)*7+boundary(text,gap)
            candidate=penalty+cost[j+1]
            if candidate<cost[i]:cost[i]=candidate;nexts[i]=j+1
    if 0 not in nexts:raise ValueError(f'ไม่สามารถแบ่งซับตามเพดาน {hard_limit} ตัวได้')
    cues=[];i=0
    while i<n:
        end=nexts[i];cues.append(dict(start=words[i]['start'],end=words[end-1]['end'],text=''.join(w['text'] for w in words[i:end]).strip()));i=end
    validate_cues(cues,hard_limit)
    return cues

def validate_cues(cues,hard_limit=45):
    if not isinstance(cues,list) or not cues:raise ValueError('ไม่พบซับ')
    previous=0
    for cue in cues:
        a,b=cue.get('start'),cue.get('end');text=cue.get('text')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (a,b)) or a<previous-.001 or b<=a or round(b*1000)<=round(a*1000):
            raise ValueError('เวลาซับซ้อนกันหรือไม่ถูกต้อง')
        if not isinstance(text,str) or not text.strip() or '\n' in text or '\r' in text or count(text)>hard_limit:
            raise ValueError(f'ซับต้องมีข้อความหนึ่งบรรทัด และไม่เกิน {hard_limit} ตัวที่นับ')
        previous=b

def stamp(seconds):
    ms=round(seconds*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def srt(cues,hard_limit=45):
    validate_cues(cues,hard_limit)
    return '\n\n'.join(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i,c in enumerate(cues,1))+'\n'

def write_result(folder,cues,soft_limit=40,metadata=None):
    from engine import save
    hard_limit=soft_limit+5
    validate_cues(cues,hard_limit)
    name='subtitles-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.srt'
    (folder/name).write_text(srt(cues,hard_limit),'utf-8-sig',newline='\n')
    result=dict(policy=POLICY,file=name,cues=cues,soft_limit=soft_limit,hard_limit=hard_limit,
        count_rule='ไม่นับสระไทย วรรณยุกต์ และเครื่องหมายประกอบ; นับช่องว่าง ตัวเลข และอักษรอื่น',
        over_soft=sum(count(c['text'])>soft_limit for c in cues),openai_calls=0)
    if metadata:result.update({k:v for k,v in metadata.items() if k in ('ai_polish','openai_calls','provider','alignment_warnings','maiyamok','cleanup')})
    save(folder/'subtitles.json',result)
    return result

def decode_media(source,folder,progress):
    import av
    chunks=[];length=0
    with av.open(str(source)) as media:
        if not media.streams.audio:raise ValueError('ไฟล์นี้ไม่มีเสียง')
        origin=float(media.start_time or 0)/1000000
        resampler=av.AudioResampler(format='s16',layout='mono',rate=16000)
        def accept(frame):
            nonlocal length
            for block in resampler.resample(frame):
                data=block.to_ndarray().reshape(-1).astype(np.int16)
                pos=round((float(block.pts*block.time_base)-origin)*16000) if block.pts is not None else length
                if pos<0:data=data[-pos:];pos=0
                chunks.append((pos,data));length=max(length,pos+len(data))
        for frame in media.decode(audio=0):
            if progress.cancelled():
                from engine import Cancelled
                raise Cancelled()
            accept(frame)
        accept(None)
    if not length:raise ValueError('อ่านเสียงไม่ได้')
    pcm=np.zeros(length,dtype=np.int16)
    for pos,data in chunks:pcm[pos:pos+len(data)]=data
    write_pcm(folder/'timeline.wav',pcm)
    return pcm

def run(config,folder,emit=lambda **kw:None,cancelled=lambda:False,key=''):
    from engine import Progress,save,prepare
    config=validate_config(config);save(folder/'config.json',{k:v for k,v in config.items() if k!='api_key'})
    progress=Progress(folder,emit,cancelled);source=Path(config['subtitle_source'])
    provider=config['subtitle_provider']
    if source.suffix.lower()=='.xml' and provider=='capcut':
        _,pcm,transcript,_=prepare(dict(config,xml=str(source),transcription='capcut'),folder,progress,'')
    else:
        with progress.stage('audio','อ่านเสียงเพื่อทำซับ'):
            identity=dict(path=str(source.resolve()),sha256=digest(source))
            old=folder/'subtitle-source.json'
            if old.exists() and json.loads(old.read_text('utf8'))!=identity:raise ValueError('ไฟล์ต้นฉบับเปลี่ยน ต้องเริ่มงานใหม่')
            save(old,identity)
            if source.suffix.lower()=='.xml':
                from project import intake,prepare_source
                from engine import identity as project_identity
                from audio_io import extract_audio
                project=intake(str(source),config.get('relink') or None,int(config.get('audio_track',1)))
                ident=project_identity(project)
                ident_path=folder/'intake.json'
                if ident_path.exists() and json.loads(ident_path.read_text('utf8'))!=ident:raise ValueError('สื่อ XML เปลี่ยน กรุณาเริ่มงานใหม่')
                if (folder/'audio-identity.json').exists() and (folder/'timeline.wav').exists():
                    pcm=read_pcm(folder/'timeline.wav');audio_id=json.loads((folder/'audio-identity.json').read_text('utf8'))
                    if len(pcm)!=project['duration']*320 or hashlib.sha256(pcm.tobytes()).hexdigest()!=audio_id['pcm_sha256']:raise ValueError('เสียง cache เปลี่ยน')
                else:pcm=extract_audio(project,folder,progress)
                prepare_source(project,folder);save(ident_path,ident);save(folder/'source-sha256.json',digest(folder/'source.xml'))
                save(folder/'audio-identity.json',dict(pcm_sha256=hashlib.sha256(pcm.tobytes()).hexdigest(),samples=len(pcm)));save(folder/'vad.json',None)
            else:pcm=read_pcm(folder/'timeline.wav') if (folder/'timeline.wav').exists() else decode_media(source,folder,progress)
        if provider=='capcut':
            with progress.stage('transcribe','ถอดเสียงและจับเวลาคำด้วย CapCut'):
                cached=folder/'transcript.json'
                if cached.exists():
                    transcript=json.loads(cached.read_text('utf8'));capcut_asr.verify_ready(pcm,folder,transcript)
                else:
                    transcript=capcut_asr.run(pcm,folder,config,progress);save(cached,transcript)
        else:
            import subtitle_asr
            transcript=subtitle_asr.run(pcm,folder,config,progress)
    polish=None
    if config.get('subtitle_ai_polish'):
        import subtitle_polish
        with progress.stage('draft','AI เกลาคำที่มั่นใจ · ส่งเฉพาะข้อความ ไม่ส่งเสียง'):
            transcript,polish=subtitle_polish.run(transcript,config,folder,key,progress)
    limit=config['subtitle_chars']
    with progress.stage('subtitle',f'แบ่งซับภาษาไทย {limit}–{limit+5} ตัวในเครื่อง'):
        changes=[]
        enabled=config.get('subtitle_maiyamok',True)
        removed=[]
        cleanup=config.get('subtitle_cleanup','off')
        cues=split(transcript,maiyamok=enabled,changes=changes,soft_limit=limit,cleanup=cleanup,
                   keep_words=config.get('subtitle_keep_words',''),cleanup_changes=removed)
        from subtitle_cleanup import POLICY as cleanup_policy
        cleanup_report=dict(policy=cleanup_policy,mode=cleanup,changes=removed,extra_api_calls=0,audio_unchanged=True)
        save(folder/'subtitle-cleanup.json',cleanup_report)
        save(folder/'subtitle-text-normalization.json',dict(policy='conservative-maiyamok-v1',enabled=enabled,changes=changes))
        result=write_result(folder,cues,soft_limit=limit)
        result['maiyamok']=dict(enabled=enabled,changes=len(changes))
        result['cleanup']=cleanup_report
        result['ai_polish']=polish
        result['openai_calls']=sum(r.get('status')=='completed' for r in json.loads((folder/'api-usage.json').read_text('utf8'))) if (folder/'api-usage.json').exists() else 0
        result['provider']=provider
        result['alignment_warnings']=sum(bool(s.get('alignment_uncertain')) for s in transcript)
        save(folder/'subtitles.json',result)
    progress(stage='done',message=f'สร้าง SRT แล้ว {len(cues)} ช่วง · '+(f'AI เกลาคำ {len(polish["changes"])} จุด' if polish else 'ไม่เรียก OpenAI API'),result=dict(subtitle=True,cues=len(cues),srt_file=result['file']))
    return result
