import copy,hashlib,json,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as E
import demo,project,runtime_contract as runtime,sales_continuity,production_cleanup
import test_sales_continuity as fixtures
from audio_io import write_pcm
from verify_outputs import check

class SourcePreservationTests(unittest.TestCase):
    def fixture(self,root,muted=False):
        source,pcm,_=demo.create(root/'media',two_cameras=True)
        tree=E.parse(source);seq=tree.find('.//sequence')
        a2=seq.findall('media/audio/track')[1];clip=a2.find('clipitem')
        for k,v in {'start':100,'end':500,'in':30,'out':430,'pproTicksIn':30*5080320000,'pproTicksOut':430*5080320000}.items():clip.find(k).text=str(v)
        if muted:a2.find('enabled').text='FALSE';clip.find('enabled').text='FALSE'
        E.SubElement(clip,'filter').text='original-volume-filter'
        for kind in ('video','audio'):
            for ti,track in enumerate(seq.findall('media/'+kind+'/track')):
                c=track.find('clipitem');E.SubElement(c,'marker')
                if ti==0:
                    for ref,media in [('camera-a','video'),('audio-main','audio')]:
                        link=E.SubElement(c,'link')
                        for k,v in [('linkclipref',ref),('mediatype',media),('trackindex',1),('clipindex',1),('groupindex',7)]:E.SubElement(link,k).text=str(v)
        tree.write(source,encoding='utf8');return source,pcm

    def test_default_exports_all_tracks_original_links_mutes_levels_without_fades_markers(self):
        for muted in (False,True):
            with self.subTest(muted=muted),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source,pcm=self.fixture(root,muted)
                p=project.configure_export(project.intake(source,audio_track=1),dict(sales_style='natural'))
                p['default_audio']='default-audio-v2';job=root/'job';job.mkdir();snapshot=project.prepare_source(p,job)
                out=job/'revision';out.mkdir();before=source.read_bytes()
                edits=[dict(name='review',ranges=[dict(start=3,end=4),dict(start=7,end=8)])]
                with patch('default_audio.apply_crossfades',side_effect=AssertionError('No crossfade')):
                    manifest=project.build_outputs(p,snapshot,edits,out,pcm.tobytes())
                seq=E.parse(out/'EDITS.xml').find('.//sequence')
                self.assertEqual(len(seq.findall('media/audio/track')),2)
                for ti,t in enumerate(seq.findall('media/audio/track')):
                    self.assertEqual(len(t.findall('clipitem')),2)
                    if ti==1:
                        self.assertEqual(t.findtext('enabled'),'FALSE' if muted else 'TRUE')
                        self.assertTrue(all(c.findtext('filter')=='original-volume-filter' for c in t.findall('clipitem')))
                    for c in t.findall('clipitem'):
                        if ti==1:self.assertFalse(c.findall('link'))
                        else:self.assertEqual(len(c.findall('link')),2)
                        for l in c.findall('link'):self.assertEqual(l.findtext('groupindex'),'7')
                for c in seq.findall('media/video/track')[1].findall('clipitem'):self.assertFalse(c.findall('link'))
                self.assertFalse(seq.findall('.//marker'));self.assertFalse(seq.findall('.//transitionitem'))
                self.assertEqual(source.read_bytes(),before)
                write_pcm(job/'timeline.wav',pcm);(job/'latest.json').write_text(json.dumps(dict(folder='revision')),'utf8')
                self.assertEqual(check(job)['versions'],1)
                self.assertFalse(manifest['crossfades']);self.assertEqual(manifest['analysis_audio_track'],1)
                a=seq.findall('media/audio/track')[1].find('clipitem');v=seq.findall('media/video/track')[1].find('clipitem')
                for k in ('start','end','in','out'):self.assertEqual(a.findtext(k),v.findtext(k))

    def test_snapshot_recovers_second_audio_without_transcription_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,pcm=self.fixture(root);job=root/'job';job.mkdir()
            p=project.intake(source,audio_track=1);project.prepare_source(p,job)
            self.assertFalse(E.parse(job/'source.xml').findall('.//sequence/media/audio/track')[1].findall('clipitem'))
            transcript=job/'transcript.json';transcript.write_text('[]','utf8');before=transcript.read_bytes()
            project.configure_export(p,dict(sales_style='natural'))
            self.assertTrue(project.refresh_source_snapshot(p,job));self.assertFalse(project.refresh_source_snapshot(p,job))
            self.assertTrue(E.parse(job/'source.xml').findall('.//sequence/media/audio/track')[1].findall('clipitem'))
            self.assertEqual(transcript.read_bytes(),before)
            self.assertEqual(json.loads((job/'source-sha256.json').read_text()),hashlib.sha256((job/'source.xml').read_bytes()).hexdigest())

    def test_original_keeps_legacy_source_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,_=self.fixture(root);job=root/'job';job.mkdir()
            p=project.configure_export(project.intake(source),dict(sales_style='original'))
            self.assertFalse(p['preserve_source_tracks']);project.prepare_source(p,job)
            self.assertFalse(E.parse(job/'source.xml').findall('.//sequence/media/audio/track')[1].findall('clipitem'))

    def test_engine_cached_resume_recovers_tracks_without_reading_or_transcribing_audio(self):
        import engine
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,pcm=self.fixture(root);job=root/'job';job.mkdir()
            p=project.intake(source,audio_track=1);project.prepare_source(p,job)
            _,_,transcript=demo.create(root/'other')
            for name,value in [('intake.json',engine.identity(p)),('source-sha256.json',engine.digest(job/'source.xml')),
                               ('audio-identity.json',dict(pcm_sha256=hashlib.sha256(pcm.tobytes()).hexdigest())),
                               ('transcript.json',transcript),('vad.json',[])]:engine.save(job/name,value)
            write_pcm(job/'timeline.wav',pcm);before=(job/'transcript.json').read_bytes()
            progress=engine.Progress(job,lambda **kw:None,lambda:False)
            with patch('engine.extract_audio',side_effect=AssertionError('Do not re-read audio')):
                result=engine.prepare(dict(xml=str(source),audio_track=1,sales_style='natural',transcription='whisper'),job,progress)
            self.assertTrue(result[0]['preserve_source_tracks'])
            self.assertTrue(E.parse(job/'source.xml').findall('.//sequence/media/audio/track')[1].findall('clipitem'))
            self.assertEqual((job/'transcript.json').read_bytes(),before)

    def test_new_default_analyzes_track_one_original_choice_unchanged(self):
        for mode,field in [('sales','sales_style'),('single','single_style')]:
            self.assertEqual(runtime.configure(dict(content_mode=mode,audio_track=2,**{field:'natural'}))['audio_track'],1)
            self.assertEqual(runtime.configure(dict(content_mode=mode,audio_track=2,**{field:'original'}))['audio_track'],2)

class EditorialPreservationTests(unittest.TestCase):
    def test_hook_count_shrinks_when_duplicate_hook_selection_removed(self):
        catalog=[fixtures.unit('A',['ว้าว'],0),fixtures.unit('B',['ว้าว'],5),fixtures.unit('C',['รายละเอียด'],10)]
        plan=fixtures.plan([fixtures.selection(k,0,0) for k in ('A','B','C')]);plan['edits'][0]['hook_count']=2
        fixed,_=sales_continuity.clean(plan,catalog)
        self.assertEqual(fixed['edits'][0]['hook_count'],1);self.assertEqual(plan['edits'][0]['hook_count'],2)

    def test_superlative_and_meaningful_emphasis_survive_local_cleanup(self):
        catalog=[fixtures.unit('A',['ดีที่สุดในโลก','ดี','มาก','มาก'],0)]
        plan=fixtures.plan([fixtures.selection('A',0,3)])
        self.assertEqual(sales_continuity.clean(plan,catalog)[0],plan)
        rules=runtime.rules_for(dict(sales_style='natural'))
        self.assertIn('ดีที่สุดในโลก',rules);self.assertIn('ดีมากมาก',rules)
        self.assertEqual(runtime.rules_for(dict(sales_style='original')),runtime.rules())

    def test_aligned_countdowns_trimmed_without_eating_following_speech(self):
        for tokens in (['3','2','1','เริ่ม','ดีมาก'],['สาม','สอง','หนึ่ง','ของดี'],['โอเค','สามสองหนึ่ง','ดีมาก']):
            with self.subTest(tokens=tokens):
                catalog=[fixtures.unit('A',tokens,0)]
                plan=fixtures.plan([fixtures.selection('A',0,len(tokens)-1)])
                fixed,changes=production_cleanup.clean(plan,catalog)
                self.assertTrue(changes);self.assertEqual(fixed['edits'][0]['selections'][0]['first_word'],len(tokens)-1)
                self.assertEqual(plan['edits'][0]['selections'][0]['first_word'],0)

    def test_no_countdown_guessing_inside_tokens_technical_numbers_or_protected_actions(self):
        for tokens in (['สามสองหนึ่งดีมาก'],['3','ระดับ'],['321','รุ่นใหม่'],['3','2','1','ระดับ']):
            with self.subTest(tokens=tokens):
                catalog=[fixtures.unit('A',tokens,0)];plan=fixtures.plan([fixtures.selection('A',0,len(tokens)-1)])
                self.assertEqual(production_cleanup.clean(plan,catalog)[0],plan)
        catalog=[fixtures.unit('A',['สาม','สอง','หนึ่ง','เริ่ม'],0)];catalog[0]['protected']=True
        plan=fixtures.plan([fixtures.selection('A',0,3)]);self.assertEqual(production_cleanup.clean(plan,catalog)[0],plan)

    def test_pure_countdown_hook_removed_and_hook_metadata_reconciled(self):
        catalog=[fixtures.unit('A',['สาม','สอง','หนึ่ง'],0),fixtures.unit('B',['ดีมาก'],5)]
        plan=fixtures.plan([fixtures.selection('A',0,2),fixtures.selection('B',0,0)]);plan['edits'][0]['hook_count']=1
        fixed,_=production_cleanup.clean(plan,catalog)
        self.assertEqual(fixed['edits'][0]['hook_count'],0);self.assertEqual(fixed['edits'][0]['selections'][0]['unit_id'],'B')

    def test_completed_legacy_reply_reused_only_if_source_model_and_schema_match(self):
        old=dict(model='gpt-6.1-sol',instructions=runtime.rules()+'\nSTEP',input='{"x":1}',text=dict(format=dict(schema={'type':'object'})))
        current=copy.deepcopy(old);current['instructions']=runtime.rules_for(dict(sales_style='natural'))+'\nSTEP'
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);(folder/'stage-request.json').write_text(json.dumps(old),'utf8');(folder/'stage-response.json').write_text('{}','utf8')
            self.assertEqual(runtime.cached_editorial_request(current,folder,'stage'),old)
            for key,value in [('model','other'),('input','{"x":2}'),('text',{})]:
                changed=copy.deepcopy(current);changed[key]=value
                self.assertEqual(runtime.cached_editorial_request(changed,folder,'stage'),changed)

    def test_completed_old_policy_map_runs_new_pipeline_without_paid_retry(self):
        import sales_map,test_sales_v8
        from parallel_requests import call
        source,raw=test_sales_v8.V8Tests().example()
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            transcript=[dict(start=s['start'],end=s['end'],text=s['text']) for s in source]
            (folder/'transcript.json').write_text(json.dumps(transcript,ensure_ascii=False),'utf8')
            cfg=runtime.configure(dict(model='gpt-6-luna',sales_style='natural',auto_export=False))
            _,segments=sales_map.segments_for(folder);req=sales_map.make_request(segments,cfg)
            req['instructions']=req['instructions'].replace(runtime.rules_for(cfg),runtime.rules(),1)
            with patch('parallel_requests.plan_once',return_value=(raw,dict(usage={}))):call(req,folder,'test','sales-story-map',max_calls=1)
            before=(folder/'sales-story-map-response.json').read_bytes()
            with patch('parallel_requests.plan_once',side_effect=AssertionError('No paid retry')):
                sales_map.run(folder,folder,cfg,'',lambda **kw:None,lambda:False)
            self.assertEqual((folder/'sales-story-map-response.json').read_bytes(),before)
