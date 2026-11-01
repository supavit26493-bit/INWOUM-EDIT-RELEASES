import copy,hashlib,io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import engine,runtime_contract,server,silence_v3,silence_v4
from demo import create
from project import intake,prepare_source
from audio_io import write_pcm
from verify_outputs import check

class AutoXMLTests(unittest.TestCase):
    def test_auto_export_with_additional_noise_pause_serializes_and_builds_xml(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,cfg,prepared,story,draft=self.setup_job(Path(tmp))
            project,pcm,transcript,_=prepared
            vad=[(.6,1),(1.8,2.4),(4,5.8),(8,10.5)]
            engine.save(job/'vad.json',vad)
            def respond(req,key):return copy.deepcopy(story if req['text']['format']['name']=='sales_story_map' else draft),dict(usage={})
            with patch('parallel_requests.plan_once',side_effect=respond) as send:
                result=engine.run(cfg,job,'key',prepared=(project,pcm,transcript,vad))
            silence=json.loads((job/'silence.json').read_text('utf8'))
            self.assertGreater(silence['extra_removed_seconds'],0)
            self.assertEqual(send.call_count,2);self.assertEqual(check(job)['revision'],result['revision'])
    def setup_job(self,root):
        source,pcm,transcript=create(root/'media',two_cameras=True)
        job=root/'job';job.mkdir();project=intake(source,audio_track=1);prepare_source(project,job)
        write_pcm(job/'timeline.wav',pcm)
        for name,value in [('transcript.json',transcript),('vad.json',[(s['start'],s['end']) for s in transcript])]:engine.save(job/name,value)
        ids=['T001','T002','T003']
        story=dict(groups=[dict(id='G',title='เรื่อง',segment_ids=ids)],hooks=[dict(id='H',segment_id=ids[0],opening_quote=transcript[0]['text'],trim_note='',sales_angle='ขาย')],full_hook_id='H',full_outline=[dict(role=role,segment_ids=[sid],budget_seconds=3,purpose=role) for role,sid in zip(('hook','features','close'),ids)],versions=[],segment_review=[dict(segment_id=sid,roles=['feature'],disposition='usable',reason='เนื้อหา',excluded_quotes=[]) for sid in ids],continuity_links=[],review_note='แผน')
        draft=dict(edits={'FULL':dict(name='FULL',purpose='ครบเรื่อง',selections=[dict(unit_id=f'U{i:04}',first_word=0,last_word=0) for i in range(1,4)])},unused=[],review_note='บท')
        from test_episode_continuity import fixture_events
        fixture_events(draft)
        config=runtime_contract.configure(dict(xml=str(source),mode='ai',sales_style='natural',model='gpt-6-luna',profile='compact',transcription='whisper'))
        return job,config,(project,pcm,transcript,[(s['start'],s['end']) for s in transcript]),story,draft
    def test_one_start_runs_two_text_calls_and_writes_verified_xml_without_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            job,cfg,prepared,story,draft=self.setup_job(Path(tmp));events=[]
            def respond(req,key):return copy.deepcopy(story if req['text']['format']['name']=='sales_story_map' else draft),dict(usage={})
            with patch('parallel_requests.plan_once',side_effect=respond) as send,patch('engine.prepare',side_effect=AssertionError('prepared fixture; no new ASR')):
                result=engine.run(cfg,job,'test-key',lambda **e:events.append(e),prepared=prepared)
                self.assertEqual(send.call_count,2)
                self.assertTrue((job/result['revision']/'EDITS-WITH-USAGE.xml').exists())
                self.assertEqual(check(job)['versions'],1)
                self.assertEqual(events[-1]['stage'],'done')
                self.assertEqual(set(json.loads((job/'timings.json').read_text('utf8'))['stage_seconds']),{'grouping','silence','draft','export'})
                engine.run(cfg,job,'',prepared=prepared)
                self.assertEqual(send.call_count,2) # cached retries do not send again
    def test_incomplete_map_or_draft_stops_without_export_or_automatic_paid_retry(self):
        for failed_stage in ('sales_story_map','approved_map_draft'):
            with self.subTest(stage=failed_stage),tempfile.TemporaryDirectory() as tmp:
                job,cfg,prepared,story,draft=self.setup_job(Path(tmp))
                def respond(req,key):
                    if req['text']['format']['name']==failed_stage:return None,dict(error='incomplete',incomplete_reason='max_output_tokens')
                    return copy.deepcopy(story),dict(usage={})
                with patch('parallel_requests.plan_once',side_effect=respond) as send,patch('engine.export') as export:
                    with self.assertRaisesRegex(RuntimeError,'incomplete|คำตอบขั้นประกอบบทชนเพดาน'):engine.run(cfg,job,'key',prepared=prepared)
                    self.assertEqual(send.call_count,1 if failed_stage=='sales_story_map' else 2)
                    export.assert_not_called();self.assertFalse((job/'latest.json').exists())
    def test_auto_xml_endpoint_resolves_saved_map_back_to_prepared_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'audio';source.mkdir();mapped=root/'map';mapped.mkdir()
            for name in server.PREPARED_FILES:(source/name).write_text('cached')
            cfg=dict(xml='source.xml',mode='ai',model='gpt-6-luna',stop_after='transcription',approved_story_map_job='old',defer_map_ids={'V':'manual'})
            engine.save(source/'config.json',cfg);engine.save(mapped/'grouping-source.json',dict(source_job='audio'))
            before=(source/'config.json').read_bytes();responses=[];body=json.dumps(dict(id='map',api_key='secret',model='gpt-6.1-sol')).encode()
            handler=SimpleNamespace(auth=lambda:None,path='/api/auto-xml',headers={'Content-Length':str(len(body))},rfile=io.BytesIO(body),send_json=lambda *args:responses.append(args))
            with patch('server.JOB_ROOT',root),patch('server.JOBS',{'audio':{},'map':{}}),patch('server.launch',return_value=dict(id='new')) as launch:
                server.Handler.do_POST(handler)
            self.assertEqual(responses,[(dict(id='new'),)]);self.assertEqual(launch.call_args.kwargs,dict(action='replan',source_job='audio'))
            new=launch.call_args.args[0];self.assertTrue(new['auto_export']);self.assertNotIn('stop_after',new);self.assertNotIn('approved_story_map_job',new);self.assertNotIn('defer_map_ids',new)
            self.assertEqual(new['model'],'gpt-6.1-sol');self.assertEqual((source/'config.json').read_bytes(),before)

class NoisyPauseTests(unittest.TestCase):
    def test_noise_pause_result_uses_builtin_numbers_and_can_be_saved_as_json(self):
        pcm=np.full(4*16000,3000,dtype=np.int16)
        transcript=[dict(start=0,end=4,text='คำ',words=[dict(start=0,end=4,text='คำ')])]
        result=silence_v4.detect(pcm,transcript,'compact',[],[(0,1),(3,4)])
        self.assertGreater(result['extra_removed_seconds'],0)
        self.assertTrue(all(type(a) is int and type(b) is int for a,b in result['removed_frames']))
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'silence.json';engine.save(target,result)
            restored=json.loads(target.read_text('utf8'))
            self.assertEqual(restored['removed_frames'],[list(r) for r in result['removed_frames']])
    def test_audible_noise_in_stretched_confirmed_word_no_longer_vetoes_pause_cut(self):
        pcm=np.full(4*16000,3000,dtype=np.int16)
        transcript=[dict(start=0,end=4,text='พูด',words=[dict(start=0,end=4,text='พูด')])]
        vad=[(0,1),(3,4)]
        before=silence_v3.detect(pcm,transcript,'compact',[],vad)
        after=silence_v4.detect(pcm,transcript,'compact',[],vad)
        self.assertEqual(before['removed_seconds'],0);self.assertGreater(after['removed_seconds'],1.5)
        for a,b in after['removed_frames']:self.assertTrue(a>=50 and b<=150)
    def test_vad_positive_soft_speech_and_protected_silent_action_stay_intact(self):
        pcm=np.full(4*16000,100,dtype=np.int16)
        transcript=[dict(start=0,end=4,text='คำ',words=[dict(start=0,end=4,text='คำ')])]
        result=silence_v4.detect(pcm,transcript,'compact',[(2,2.5)],[(0,1),(3,4)])
        for a,b in result['removed_frames']:
            self.assertFalse(max(a,100)<min(b,125));self.assertTrue(a>=50 and b<=150)
    def test_unconfirmed_or_uncertain_soft_phrase_is_not_erased(self):
        pcm=np.full(4*16000,12,dtype=np.int16)
        transcript=[dict(start=0,end=4,text='เสียงเบา',words=[dict(start=0,end=4,text='เสียงเบา')])]
        self.assertEqual(silence_v4.detect(pcm,transcript,vad_speech=[])['removed_seconds'],0)
        transcript[0]['alignment_uncertain']=True
        self.assertEqual(silence_v4.detect(np.full(4*16000,3000,dtype=np.int16),transcript,vad_speech=[(0,1),(3,4)])['removed_seconds'],0)
    def test_short_noisy_rest_is_not_cut_into_micro_edits(self):
        pcm=np.full(2*16000,3000,dtype=np.int16)
        transcript=[dict(start=0,end=2,text='คำ',words=[dict(start=0,end=2,text='คำ')])]
        self.assertEqual(silence_v4.detect(pcm,transcript,'compact',[],[(0,1),(1.15,2)])['removed_seconds'],0)
