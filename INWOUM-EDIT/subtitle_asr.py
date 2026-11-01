"""Free local subtitle backends, isolated from the sales editing engine."""
import hashlib,json,math,os,platform,subprocess,time,zipfile
from pathlib import Path
import bootstrap
from audio_io import digest,transcribe_gpu

PROVIDERS={'capcut':'CapCut · ออนไลน์', 'whisper-turbo':'Whisper Large-v3 Turbo · GPU',
           'whisper-large':'Whisper Large-v3 · GPU', 'whisper-cpp':'whisper.cpp Small · CPU'}
MODELS={'whisper-turbo':'large-v3-turbo','whisper-large':'large-v3','whisper-cpp':'small'}
CPP_TAG='b5454'
CPP_ZIP_SHA='6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb'
CPP_URL=f'https://github.com/ggml-org/whisper.cpp/releases/download/{CPP_TAG}/whisper-bin-x64.zip'
CPP_MODEL_URL='https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin'

def download(url,path,progress,sha=None):
    import requests
    path.parent.mkdir(parents=True,exist_ok=True);part=path.with_suffix(path.suffix+'.part')
    with requests.get(url,stream=True,timeout=(30,60)) as response:
        response.raise_for_status();total=int(response.headers.get('Content-Length',0));done=0;last=0
        with part.open('wb') as out:
            for chunk in response.iter_content(1024*1024):
                if chunk:out.write(chunk);done+=len(chunk)
                if time.monotonic()-last>1:
                    progress(message=f'ดาวน์โหลด {path.name} · {done//1048576} / {total//1048576 if total else "?"} MB');last=time.monotonic()
    if not part.stat().st_size:raise ValueError('ดาวน์โหลดไฟล์ว่าง')
    if sha and digest(part)!=sha:raise ValueError('checksum whisper.cpp ไม่ตรง หยุดก่อนติดตั้ง')
    part.replace(path)

def ensure_cpp(progress,root=None):
    root=Path(root or bootstrap.ROOT)
    if os.name!='nt' or platform.machine().lower() not in ('amd64','x86_64'):
        raise ValueError('ตัวติดตั้ง whisper.cpp นี้รองรับ Windows x64')
    home=root/'tools'/'whisper-cpp'/CPP_TAG
    executable=next(home.rglob('whisper-cli.exe'),None) if home.exists() else None
    if executable is None:
        archive=root/'cache'/'downloads'/f'whisper-cpp-{CPP_TAG}.zip'
        if not archive.exists() or digest(archive)!=CPP_ZIP_SHA:download(CPP_URL,archive,progress,CPP_ZIP_SHA)
        home.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if not (home/name).resolve().is_relative_to(home.resolve()):raise ValueError('Unsafe archive path')
            z.extractall(home)
        executable=next(home.rglob('whisper-cli.exe'),None)
    if executable is None:raise ValueError('แพ็กเกจทางการไม่มี whisper-cli.exe')
    model=root/'models'/'whisper-cpp'/'ggml-small.bin'
    if not model.exists():download(CPP_MODEL_URL,model,progress)
    if model.stat().st_size<1000000:raise ValueError('โมเดล whisper.cpp ไม่ครบ')
    return executable,model

def cpp_transcript(raw):
    result=[]
    for row in raw.get('transcription',[]):
        text=row.get('text','');offset=row.get('offsets',{});words=[]
        if not text.strip():continue
        for token in row.get('tokens',[]):
            if token.get('id',50257)>=50257:continue
            bounds=token.get('offsets',{})
            if 'from' not in bounds or 'to' not in bounds:words=[];break
            words.append(dict(start=bounds['from']/1000,end=bounds['to']/1000,text=token['text']))
        normalize=lambda s:''.join(s.split())
        if normalize(''.join(w['text'] for w in words))!=normalize(text):words=[]
        if 'from' not in offset or 'to' not in offset:raise ValueError('whisper.cpp ไม่มีเวลาท่อน')
        result.append(dict(start=offset['from']/1000,end=offset['to']/1000,text=text,words=words))
    if not result:raise ValueError('whisper.cpp ไม่พบเสียงพูด')
    return result

def cpp_run(folder,progress,executable,model):
    from engine import Cancelled
    prefix=folder/'whisper-cpp';output=Path(str(prefix)+'.json')
    if output.exists():output.unlink() # Only this job's disposable raw decoder output.
    args=[str(executable),'-m',str(model),'-f',str(folder/'timeline.wav'),'-l','th','-ng','-ojf','-of',str(prefix),'-t','8']
    with (folder/'whisper-cpp.log').open('wb') as log:
        process=subprocess.Popen(args,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            while process.poll() is None:
                if progress.cancelled():raise Cancelled('หยุด whisper.cpp แล้ว')
                time.sleep(.2)
            if process.returncode:raise RuntimeError('whisper.cpp ไม่สำเร็จ ตรวจ whisper-cpp.log ในงานนี้')
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=10)
    return cpp_transcript(json.loads(output.read_text('utf8')))

def validate(transcript,seconds):
    if not isinstance(transcript,list) or not transcript:raise ValueError('ไม่พบข้อความ')
    previous=0
    for row in transcript:
        a,b=row['start'],row['end']
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (a,b)) or not 0<=a<b<=seconds+.1 or a<previous-.02:
            raise ValueError('เวลาถอดเสียงไม่ตรงต้นฉบับ')
        if not isinstance(row['text'],str):raise ValueError('ข้อความถอดเสียงไม่ถูกต้อง')
        previous=b

def run(pcm,folder,config,progress):
    from engine import save
    provider=config['subtitle_provider'];model=MODELS[provider]
    contract=dict(provider=provider,model=model,language='th',audio_sha256=hashlib.sha256(pcm.tobytes()).hexdigest(),
                  policy='subtitle-local-asr-v1',cpp_build=CPP_TAG if provider=='whisper-cpp' else None)
    if provider=='whisper-large':contract['compute_type']='int8_float16'
    record_path=folder/'subtitle-asr.json';transcript_path=folder/'transcript.json'
    if record_path.exists() and transcript_path.exists():
        record=json.loads(record_path.read_text('utf8'));transcript=json.loads(transcript_path.read_text('utf8'))
        if record.get('contract')==contract and record.get('transcript_sha256')==digest(transcript_path):
            validate(transcript,len(pcm)/16000);progress(message='ใช้ข้อความเดิมที่ตรงเสียงและตัวถอด ไม่ถอดซ้ำ');return transcript
    with progress.stage('model','เตรียมโมเดลฟรีในเครื่อง · ครั้งแรกอาจต้องดาวน์โหลด'):
        if provider=='whisper-cpp':executable,cpp_model=ensure_cpp(progress)
        else:
            location=model
            for home in (bootstrap.ROOT,Path(config.get('shared_home') or bootstrap.ROOT)):
                if (home/'models'/model/'model.bin').exists():location=str(home/'models'/model);break
    with progress.stage('transcribe','ถอดภาษาไทยด้วย '+PROVIDERS[provider]+' · ไม่ใช้ API'):
        if provider=='whisper-cpp':transcript=cpp_run(folder,progress,executable,cpp_model)
        elif provider=='whisper-large':transcript=transcribe_gpu(pcm,location,progress,config.get('shared_home'),compute_type='int8_float16')
        else:transcript=transcribe_gpu(pcm,location,progress,config.get('shared_home'))
        validate(transcript,len(pcm)/16000)
        save(transcript_path,transcript)
        save(record_path,dict(contract=contract,transcript_sha256=digest(transcript_path)))
    return transcript
