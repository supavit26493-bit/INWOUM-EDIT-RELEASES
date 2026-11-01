"""Prepare once -> local audio cut map -> optional single AI plan -> XML + usage map."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import time
import uuid

import bootstrap
from audio_io import digest, extract_audio, read_pcm, transcribe_gpu, vad_intervals
from fast_logic import make_catalog, detect_silence, local_plan, resolve_plan, validate_transcript
from planner import request_body, fingerprint, plan_once, review_body, add_reference
from project import intake, prepare_source, build_outputs
import coverage_report


def save(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    # Windows readers/AV can briefly deny replacement of an open JSON file.
    # Retry only this local atomic rename, never repeat the producing API call.
    for attempt in range(6):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if attempt==5:raise
            time.sleep(.05*(attempt+1))


class Cancelled(Exception):
    pass


class Progress:
    def __init__(self, folder, emit, cancelled):
        self.folder, self.emit, self.cancelled = folder, emit, cancelled
        self.started = time.perf_counter()
        self.timings = {}
        self.run_id=uuid.uuid4().hex
        history_file=folder/'timing-history.json'
        self.history=json.loads(history_file.read_text(encoding='utf8')) if history_file.exists() else []
        previous_file=folder/'timings.json'
        if previous_file.exists():
            previous=json.loads(previous_file.read_text(encoding='utf8'))
            previous_id=previous.get('run_id') or hashlib.sha256(previous_file.read_bytes()).hexdigest()
            if not any(row['run_id']==previous_id for row in self.history):
                self.history.append(dict(run_id=previous_id,elapsed_seconds=previous['elapsed_seconds'],stage_seconds=previous['stage_seconds']))
                save(history_file,self.history)
        self.prior_seconds=sum(row['elapsed_seconds'] for row in self.history)
        save(previous_file,dict(run_id=self.run_id,elapsed_seconds=0,stage_seconds={},cumulative_seconds=round(self.prior_seconds,2)))

    def __call__(self, **event):
        if self.cancelled():
            raise Cancelled('หยุดแล้ว ข้อมูลที่ทำเสร็จยังอยู่')
        self.emit(**event)

    @contextmanager
    def stage(self, name, message):
        self(stage=name, message=message)
        started = time.perf_counter()
        try:
            yield
        finally:
            self.timings[name] = self.timings.get(name, 0) + time.perf_counter() - started
            elapsed=time.perf_counter()-self.started
            save(self.folder / 'timings.json', dict(run_id=self.run_id,elapsed_seconds=round(elapsed, 2),cumulative_seconds=round(self.prior_seconds+elapsed,2),
                  stage_seconds={k: round(v, 2) for k, v in self.timings.items()}))


def identity(project):
    return dict(xml_sha256=digest(project['xml']), duration=project['duration'], audio_track=project['audio_track'],
                primary=project['primary'], media=[dict(path=p, bytes=Path(p).stat().st_size,
                mtime_ns=Path(p).stat().st_mtime_ns) for p in sorted(set(project['paths'].values()))])


def parse_protected(value):
    if not isinstance(value, list):
        raise ValueError('ช่วงที่เก็บต้องเป็นรายการ [เริ่ม,จบ] หน่วยวินาที')
    result = []
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError('ช่วงที่เก็บต้องเป็น [เริ่ม,จบ]')
        result.append(tuple(pair))
    return result


def validate_config(config):
    if config.get('sales_style')=='sales_2_2':raise ValueError('พักโหมด 2.2 แล้ว กรุณาเลือกโหมด 2.1')
    from fast_logic import PROFILES
    config = dict(config)
    if config.get('content_mode')=='single' and config.get('single_style','original') not in ('natural','natural_v2','original'):
        raise ValueError('รูปแบบตัดคลิปเดียวไม่ถูกต้อง')
    if config.get('content_mode','sales')=='sales' and config.get('sales_style','original') not in ('natural','natural_v2','sales_2_1','sales_2_2','original'):
        raise ValueError('รูปแบบตัดคลิปขายของไม่ถูกต้อง')
    if config.get('content_mode','sales') not in ('sales','single'):
        raise ValueError('ประเภทงานไม่ถูกต้อง')
    if config.get('content_mode')=='single' and (config.get('single_method','chronological') not in ('chronological','rearrange') or config.get('editorial_workflow')!='single-story-v1'):
        raise ValueError('ตั้งค่าโหมดคลิปเดียวไม่ถูกต้อง')
    if config.get('content_mode')=='single' and (type(config.get('single_max_seconds',0)) is not int or config.get('single_max_seconds',0) not in (0,180)):
        raise ValueError('ความยาวคลิปเดียวต้องเป็นไม่จำกัดหรือไม่เกิน 3 นาที')
    if config.get('profile', 'compact') not in PROFILES:
        raise ValueError('ระดับความกระชับไม่ถูกต้อง')
    if config.get('mode', 'local') not in ('local', 'ai'):
        raise ValueError('โหมดไม่ถูกต้อง')
    if config.get('effort', 'low') not in ('default', 'low', 'medium', 'high'):
        raise ValueError('ระดับ reasoning ไม่ถูกต้อง')
    if config.get('transcription', 'whisper') not in ('whisper', 'api', 'capcut'):
        raise ValueError('วิธีถอดเสียงไม่ถูกต้อง')
    parse_protected(config.get('protected', []))
    if config.get('mode') == 'ai' and not str(config.get('model', '')).strip():
        raise ValueError('ระบุชื่อโมเดล')
    return config


def prepare(config, folder, progress, key=''):
    with progress.stage('intake', 'ตรวจ XML และสื่อต้นฉบับ'):
        project = intake(config['xml'], config.get('relink') or None, int(config.get('audio_track', 1)))
        from project import configure_export,refresh_source_snapshot
        configure_export(project,config)
        ident = identity(project)
        ready = ('intake.json', 'audio-identity.json', 'transcript.json', 'vad.json', 'source.xml', 'source-sha256.json', 'timeline.wav')
        if all((folder / name).is_file() for name in ready):
            if ident != json.loads((folder / 'intake.json').read_text(encoding='utf8')):
                raise ValueError('สื่อต้นฉบับเปลี่ยน กรุณาสร้างงานใหม่')
            pcm = read_pcm(folder / 'timeline.wav')
            audio_id = json.loads((folder / 'audio-identity.json').read_text(encoding='utf8'))
            if hashlib.sha256(pcm.tobytes()).hexdigest() != audio_id['pcm_sha256'] or len(pcm) != project['duration'] * 320:
                raise ValueError('เสียง cache เปลี่ยน กรุณาสร้างงานใหม่')
            if digest(folder / 'source.xml') != json.loads((folder / 'source-sha256.json').read_text(encoding='utf8')):
                raise ValueError('XML snapshot เปลี่ยน กรุณาสร้างงานใหม่')
            transcript = json.loads((folder / 'transcript.json').read_text(encoding='utf8'))
            validate_transcript(transcript, project['duration'])
            progress(message='ทำต่อจากเสียงและข้อความเดิม ไม่อ่านสื่อหรือถอดเสียงซ้ำ')
            vad = json.loads((folder / 'vad.json').read_text(encoding='utf8'))
            if config.get('transcription', 'whisper') == 'api':
                if not (folder / 'api-transcription.json').is_file():
                    raise ValueError('งานนี้ใช้ข้อความเดิม กรุณาเริ่มงาน API ใหม่แทนการใช้ transcript Whisper')
            if config.get('transcription') == 'capcut':
                import capcut_asr
                capcut_asr.verify_ready(pcm, folder, transcript)
            elif (folder / 'capcut-transcription.json').is_file():
                raise ValueError('งานนี้เป็นข้อความ CapCut กรุณาเริ่มงานใหม่เพื่อเปลี่ยนวิธีถอดเสียง')
            if refresh_source_snapshot(project,folder):progress(message='คืนทุกแทร็กและลิงก์จาก XML ต้นฉบับ · ใช้ข้อความและเสียงวิเคราะห์เดิม')
            return project, pcm, transcript, vad
        save(folder / 'intake.json', ident)
        prepare_source(project, folder)
        save(folder / 'source-sha256.json', digest(folder / 'source.xml'))
        refresh_source_snapshot(project,folder)
    with progress.stage('audio', 'อ่านเสียงจากแทร็กหลัก'):
        pcm = extract_audio(project, folder, progress)
        audio_hash = hashlib.sha256(pcm.tobytes()).hexdigest()
    if config.get('transcription', 'whisper') in ('api', 'capcut'):
        with progress.stage('vad', 'ตรวจช่วงเสียงพูดและจุดแบ่งเสียงสำหรับ API'):
            vad = vad_intervals(pcm)
            save(folder / 'vad.json', vad)
        with progress.stage('transcribe', 'ถอดเสียง แล้วจับเวลาข้อความใหม่บน GPU'):
            if config.get('transcription') == 'capcut':
                import capcut_asr
                transcript = capcut_asr.run(pcm, folder, config, progress)
            else:
                import api_asr
                transcript = api_asr.run(pcm, folder, config, key, progress, vad)
            validate_transcript(transcript, project['duration'])
            save(folder / 'transcript.json', transcript)
            save(folder / 'audio-identity.json', dict(pcm_sha256=audio_hash, samples=len(pcm)))
        return project, pcm, transcript, vad
    # Shared ASR cache is keyed by the actual audio and model, not merely filenames.
    model = config.get('whisper_model', 'large-v3-turbo')
    cache_id = hashlib.sha256((audio_hash + '\n' + model + '\nfast-asr-temp0-v2').encode()).hexdigest()
    cache = bootstrap.ROOT / 'cache' / 'asr'
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / (cache_id + '.json')
    with progress.stage('transcribe', 'ถอดเสียง GPU ครั้งเดียว หรือใช้ cache ที่ตรงเสียง'):
        if config.get('reuse_job'):
            old = Path(config['reuse_job']).resolve()
            previous = read_pcm(old / 'timeline.wav')
            if len(previous) != len(pcm) or hashlib.sha256(previous.tobytes()).hexdigest() != audio_hash:
                raise ValueError('เสียงงานเก่าไม่ตรงกับเสียงที่อ่านใหม่ ห้ามใช้ transcript เก่า')
            transcript = json.loads((old / 'transcript.json').read_text(encoding='utf8'))
            validate_transcript(transcript, project['duration'])
            progress(message='เสียงตรงกันทุก sample ใช้ transcript งานเก่าได้')
        elif cached.is_file():
            record = json.loads(cached.read_text(encoding='utf8'))
            if record['audio_sha256'] != audio_hash or record['model'] != model:
                raise ValueError('ASR cache ไม่ตรง')
            transcript = record['transcript']
            progress(message='ใช้ transcript ที่บันทึกไว้ ไม่ถอดเสียงซ้ำ')
        else:
            location = model
            for home in [bootstrap.ROOT, Path(config.get('shared_home') or bootstrap.ROOT)]:
                if (home / 'models' / model / 'model.bin').is_file():
                    location = str(home / 'models' / model)
                    break
            transcript = transcribe_gpu(pcm, location, progress, config.get('shared_home'))
        validate_transcript(transcript, project['duration'])
        save(cached, dict(audio_sha256=audio_hash, model=model, transcript=transcript))
        save(folder / 'transcript.json', transcript)
        save(folder / 'audio-identity.json', dict(pcm_sha256=audio_hash, samples=len(pcm)))
    with progress.stage('vad', 'ตรวจตำแหน่งเสียงพูดในเครื่อง'):
        vad = vad_intervals(pcm)
        save(folder / 'vad.json', vad)
    return project, pcm, transcript, vad


def cut_map(config, folder, project, pcm, transcript, vad):
    protected = parse_protected(config.get('protected', []))
    detector = detect_silence
    if config.get('silence_policy') == 'speech-v3':
        import silence_v3
        detector = silence_v3.detect
    elif config.get('silence_policy') == 'speech-v4':
        import silence_v4
        detector = silence_v4.detect
    silence = detector(pcm, transcript, config.get('profile', 'compact'), protected, vad)
    catalog = make_catalog(transcript, project['duration'], protected)
    if config.get('editorial_policy') in ('story-v2','story-v3'):
        import editorial
        catalog = editorial.refine_catalog(catalog, transcript)
    if config.get('editorial_policy') == 'story-v4':
        import thai_phrases
        catalog = thai_phrases.refine(catalog, transcript, config.get('phrase_granularity') == 'lexical-review')
        save(folder / 'phrase-policy.json', dict(policy=thai_phrases.POLICY, units=len(catalog),
             multi_phrase_units=sum(len(u['words'])>1 for u in catalog), phrases=sum(max(1,len(u['words'])) for u in catalog)))
    if config.get('content_mode')=='single':
        import single_clip
        catalog=single_clip.prepare_catalog(catalog)
        project['content_mode']='single'
        project['single_max_seconds']=config.get('single_max_seconds',0)
    if ((config.get('content_mode','sales')=='sales' and config.get('sales_style','original') in ('natural','natural_v2','sales_2_1','sales_2_2')) or
        (config.get('content_mode')=='single' and config.get('single_style','original') in ('natural','natural_v2','sales_2_1','sales_2_2'))):
        silence['sales_continuity']='sales-continuity-v1'
        import default_audio
        silence=default_audio.guard_words(silence,transcript)
        project['default_audio']=default_audio.POLICY
        if config.get('single_style' if config.get('content_mode')=='single' else 'sales_style')=='natural_v2':
            import math
            silence['default02']=True
            silence['default02_speech_frames']=[(math.floor(a*50),math.ceil(b*50)) for a,b in vad] if vad is not None else None
    else:
        project.pop('default_audio',None)
    save(folder / 'silence.json', silence)
    save(folder / 'catalog.json', catalog)
    return silence, catalog


def export(folder, project, pcm, plan, catalog, silence, progress):
    if project.get('cut_style')in ('sales_2_1','sales_2_2'):
        import sales_reserve
        sales_reserve.label_plan(plan,catalog)
        sales_reserve.validate_plan(plan,catalog)
    if plan.get('episode_policy') and project.get('default_audio'):
        import episode_continuity
        if episode_continuity.findings(plan,catalog,single=project.get('content_mode')=='single'):
            raise ValueError('แผนสลับเหตุการณ์หรือบริบทไม่ครบ จึงยังไม่ส่งออก XML')
        episode_continuity.record(plan,catalog,folder)
    with progress.stage('export', 'สร้าง XML ทุกเวอร์ชันและ Sequence แสดงการใช้งาน'):
        edits = resolve_plan(plan, catalog, silence, project)
        import hook_library
        coverage_plan=hook_library.extend_export(plan,catalog,silence,project,edits)
        coverage = coverage_report.report(coverage_plan, catalog, edits)
        revision = folder / ('revision-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:4])
        revision.mkdir()
        manifest = build_outputs(project, folder / 'source.xml', edits, revision, pcm.tobytes())
        hook_library.write_report(revision, edits)
        from usage_reasons import annotate
        annotate(revision,coverage,catalog)
        save(revision / 'plan.json', plan)
        save(revision / 'silence.json', silence)
        save(revision / 'source-coverage.json', coverage)
        save(folder / 'latest.json', dict(folder=revision.name))
    return dict(revision=revision.name, versions=len(edits), removed_seconds=silence['removed_seconds'],
                method=silence['method'], manifest=manifest,
                coverage={k: coverage[k] for k in ('total_units', 'used_units', 'unused_units', 'partial_units', 'reviewed_units')},
                short_fragments=len(coverage['short_fragments']), audio_review='pending', premiere_import='pending',
                pending_reserve_warning=plan.get('pending_reserve_warning',''))


def run(config, folder, key, emit=lambda **kw: None, cancelled=lambda: False, prepared=None):
    config = validate_config(config)
    folder.mkdir(parents=True, exist_ok=True)
    save(folder / 'config.json', {k: v for k, v in config.items() if k != 'api_key'})
    progress = Progress(folder, emit, cancelled)
    if prepared is None:
        project, pcm, transcript, vad = prepare(config, folder, progress, key)
    else:
        project, pcm, transcript, vad = prepared
        save(folder / 'source-sha256.json', digest(folder / 'source.xml'))
        from project import configure_export,refresh_source_snapshot
        configure_export(project,config)
        refresh_source_snapshot(project,folder)
    if config.get('stop_after') == 'transcription':
        # Explicit user review checkpoint, before editorial APIs or any export.
        with progress.stage('transcript_review','บันทึกข้อความถอดเสียงให้ตรวจ · หยุดก่อนเรียบเรียง'):
            def stamp(t):return f'{int(t)//60:02d}:{t%60:05.2f}'
            lines=['# ขั้นที่ 2 — ข้อความถอดเสียงสำหรับตรวจ','',
                   'ต้นฉบับ: '+str(config['xml']),
                   'ผู้ถอด: '+config.get('transcription','whisper'),
                   'ข้อความตามผลถอดเดิม ยังไม่เรียบเรียงหรือแก้ตัวสะกด และยังไม่สร้างคัท','']
            for i,row in enumerate(transcript,1):
                lines.append(f"{i:03d} · {stamp(row['start'])}–{stamp(row['end'])} · {row['text']}")
            (folder/'TRANSCRIPT-REVIEW.md').write_text('\n'.join(lines),'utf8')
            result=dict(transcript_review=True,segments=len(transcript),seconds=project['duration']/50)
        progress(stage='done',message=f'ถอดเสียงเสร็จ {len(transcript)} ท่อน · หยุดรอตรวจข้อความก่อนเรียบเรียง',result=result)
        return result
    if config.get('content_mode')=='single':
        import single_clip
        with progress.stage('silence','สร้างขอบตัดช่วงเงียบสำหรับคลิปเดียว'):
            silence,catalog=cut_map(config,folder,project,pcm,transcript,vad)
        plan=single_clip.run(catalog,folder,config,key,progress,project,silence)
        result=export(folder,project,pcm,plan,catalog,silence,progress)
        progress(stage='done',message='สร้าง XML คลิปเดียวเสร็จแล้ว',result=result)
        return result
    if config.get('editorial_workflow')=='sales-v8' and not config.get('approved_story_map_job'):
        import sales_map
        mapped=sales_map.run(folder,folder,config,key,emit,cancelled,progress=progress)
        if not config.get('auto_export'):return mapped
        progress(message='ใช้แผนที่โปรแกรมสร้าง เรียบเรียงและส่งออกต่อโดยไม่หยุดตรวจ')
        config=dict(config,approved_story_map_job=folder.name)
        config.pop('stop_after',None)
        save(folder/'config.json',config)
    with progress.stage('silence', 'สร้างขอบตัดช่วงเงียบร่วมกันทุกเวอร์ชัน'):
        silence, catalog = cut_map(config, folder, project, pcm, transcript, vad)
    if config.get('editorial_workflow')=='sales-v8':
        import sales_draft
        plan=sales_draft.run(catalog,folder,config,key,progress,project,silence)
        if not config.get('auto_export'):return plan
        progress(message='ส่งออก XML จากแผนโปรแกรม ไม่แทรกการแก้ด้วยมือ')
        result=export(folder,project,pcm,plan,catalog,silence,progress)
        message=f'สร้าง XML ครบ {result["versions"]} เวอร์ชันแล้ว'
        if result.get('pending_reserve_warning'):message+=' · '+result['pending_reserve_warning']
        progress(stage='done',message=message,result=result)
        return result
    if config.get('mode') == 'ai' and config.get('editorial_policy') == 'story-v4':
        import story_editorial
        plan=story_editorial.run(catalog,folder,config,key,progress,project,silence)
    elif config.get('mode') == 'ai' and config.get('editorial_policy') == 'story-v3':
        import fast_editorial
        plan=fast_editorial.run(catalog,folder,config,key,progress,project,silence)
    elif config.get('mode') == 'ai':
        story_mode = config.get('editorial_policy') == 'story-v2'
        if story_mode:
            import editorial
            editorial.record_rules(folder)
        payload = request_body(catalog, config['model'], config.get('effort', 'low'), config.get('notes', ''), editorial=story_mode)
        if story_mode and config.get('editorial_seed_job'):
            seed_root=(bootstrap.ROOT/'jobs').resolve()
            seed=(seed_root / config['editorial_seed_job']).resolve()
            if not seed.is_relative_to(seed_root):
                raise ValueError('งานอ้างอิงต้องอยู่ในโฟลเดอร์ jobs')
            seed_plan=json.loads((seed/'plan.json').read_text(encoding='utf8'))
            seed_catalog=json.loads((seed/'catalog.json').read_text(encoding='utf8'))
            add_reference(payload,editorial.assembled_scripts(seed_plan,seed_catalog))
        request_hash = fingerprint(payload)
        existing = folder / 'ai-plan.json'
        with progress.stage('planning', 'วางโครงเรื่องและเลือกเทคจากทั้งคลิป — ขั้น 1/2 ไม่มีตรวจภาพ' if story_mode else 'รอ AI เลือกเนื้อหาและจัดทุกเวอร์ชัน — 1 คำขอ ไม่มีตรวจภาพ'):
            prior = json.loads(existing.read_text(encoding='utf8')) if existing.is_file() else None
            if prior and prior.get('request_hash') == request_hash:
                try:
                    resolve_plan(prior['plan'], catalog, silence, project)
                    coverage_report.validate_source_review(prior['plan'], catalog)
                except (ValueError, KeyError, TypeError):
                    prior = None  # Explicit user retry may replace an invalid saved AI plan.
            if prior and prior.get('request_hash') == request_hash:
                plan = prior['plan']
                progress(message='ใช้แผน AI เดิมที่ตรงคำขอ ไม่เรียก API เพิ่ม')
            else:
                if not key.strip():
                    raise ValueError('ใส่ API key เพื่อส่งคำขอใหม่')
                usage_file = folder / 'api-usage.json'
                ledger = json.loads(usage_file.read_text(encoding='utf8')) if usage_file.exists() else []
                ledger.append(dict(request_hash=request_hash, status='sent', usage=None))
                save(usage_file, ledger)
                plan, record = plan_once(payload, key)
                ledger[-1].update(record, status='completed' if plan is not None else 'failed')
                save(usage_file, ledger)
                if plan is None:
                    raise RuntimeError(record['error'])
                save(existing, dict(request_hash=request_hash, plan=plan))
        if story_mode:
            resolve_plan(plan, catalog, silence, project)
            coverage_report.validate_source_review(plan, catalog)
            save(folder / 'draft-scripts.json', editorial.assembled_scripts(plan, catalog))
            review_payload = review_body(catalog, plan, config['model'], config.get('effort','medium'), config.get('notes',''))
            review_hash = fingerprint(review_payload)
            review_file = folder / 'reviewed-plan.json'
            with progress.stage('editorial_review', 'ตรวจบทที่ประกอบแล้ว: ใจความซ้ำ ประโยคค้าง และลำดับเรื่อง — อีก 1 คำขอข้อความ'):
                cached_review = json.loads(review_file.read_text(encoding='utf8')) if review_file.exists() else None
                if cached_review and cached_review.get('request_hash') == review_hash:
                    plan = cached_review['plan']
                else:
                    usage_file = folder / 'api-usage.json'
                    ledger = json.loads(usage_file.read_text(encoding='utf8'))
                    ledger.append(dict(stage='editorial_review', request_hash=review_hash, status='sent', usage=None))
                    save(usage_file, ledger)
                    reviewed, record = plan_once(review_payload, key)
                    ledger[-1].update(record, status='completed' if reviewed is not None else 'failed')
                    save(usage_file, ledger)
                    if reviewed is None:
                        raise RuntimeError(record['error'])
                    save(review_file, dict(request_hash=review_hash, plan=reviewed))
                    plan = reviewed
            resolve_plan(plan, catalog, silence, project)
            coverage_report.validate_source_review(plan, catalog)
            editorial.validate_hooks(plan, catalog)
            save(folder / 'assembled-scripts.json', editorial.assembled_scripts(plan, catalog))
            editorial.write_script_report(folder, plan, catalog)
    else:
        with progress.stage('planning', 'ทำตัวอย่างตัดช่วงเงียบแบบเรียงต้นฉบับ ไม่เรียก API'):
            plan = local_plan(catalog, silence, project)
    # Keep the raw plan even if semantic validation fails; the user can repair it locally.
    save(folder / 'plan.json', plan)
    if config.get('mode') == 'ai':
        coverage_report.validate_source_review(plan, catalog)
    progress()
    result = export(folder, project, pcm, plan, catalog, silence, progress)
    progress(stage='done', message=f'สร้าง {result["versions"]} เวอร์ชันแล้ว — รอฟังและตรวจใน Premiere', result=result)
    return result


def add_hook_library(folder, key, emit=lambda **kw: None, cancelled=lambda: False):
    """2.2 is paused; reject before any generation or job mutation."""
    raise ValueError('พักโหมด 2.2 และการเพิ่ม Hook สำรองแล้ว')


def rebuild(folder, config_changes, plan, emit=lambda **kw: None, cancelled=lambda: False):
    config = json.loads((folder / 'config.json').read_text(encoding='utf8'))
    config.update({k: v for k, v in config_changes.items() if k in ('profile', 'protected','silence_policy')})
    validate_config(config)
    progress = Progress(folder, emit, cancelled)
    project = intake(config['xml'], config.get('relink') or None, int(config.get('audio_track', 1)))
    if identity(project) != json.loads((folder / 'intake.json').read_text(encoding='utf8')):
        raise ValueError('XML หรือสื่อต้นฉบับเปลี่ยน กรุณาเริ่มงานใหม่')
    if digest(folder / 'source.xml') != json.loads((folder / 'source-sha256.json').read_text(encoding='utf8')):
        raise ValueError('XML snapshot เปลี่ยน กรุณาเริ่มงานใหม่')
    from project import configure_export,refresh_source_snapshot
    configure_export(project,config)
    refresh_source_snapshot(project,folder)
    pcm = read_pcm(folder / 'timeline.wav')
    audio_identity = json.loads((folder / 'audio-identity.json').read_text(encoding='utf8'))
    if len(pcm) != project['duration'] * 320 or hashlib.sha256(pcm.tobytes()).hexdigest() != audio_identity['pcm_sha256']:
        raise ValueError('เสียงที่บันทึกเปลี่ยน กรุณาเริ่มงานใหม่')
    transcript = json.loads((folder / 'transcript.json').read_text(encoding='utf8'))
    vad = json.loads((folder / 'vad.json').read_text(encoding='utf8'))
    silence, catalog = cut_map(config, folder, project, pcm, transcript, vad)
    result = export(folder, project, pcm, plan, catalog, silence, progress)
    save(folder / 'config.json', config)
    save(folder / 'plan.json', plan)
    if config.get('editorial_workflow')=='sales-v8':
        import sales_draft
        sales_draft.refresh_export_review(folder,plan,catalog,resolve_plan(plan,catalog,silence,project),project['duration']/50)
    progress(stage='done', message='สร้าง XML ใหม่แล้ว ไม่ถอดเสียงหรือเรียก AI ซ้ำ', result=result)
    return result
