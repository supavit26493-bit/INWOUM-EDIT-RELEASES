import copy,json,tempfile,unittest,shutil,threading
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as E
import numpy as np
import bootstrap,super_mode as s,super_prompt,server
import super_edit
from super_font import Metrics
from super_graphic import item_text,read_text,replace_text
from demo import create
from engine import Progress

class SuperTests(unittest.TestCase):
    def fixture(self,root,portrait=False,tb=50):
        source,pcm,_=create(root/'media',two_cameras=True)
        tree=E.parse(source);seq=tree.getroot().find('sequence');video=seq.find('media/video')
        chars=E.SubElement(E.SubElement(video,'format'),'samplecharacteristics')
        E.SubElement(chars,'width').text='1080' if portrait else '1920';E.SubElement(chars,'height').text='1920' if portrait else '1080'
        if tb!=50:
            seq.find('rate/timebase').text=str(tb);seq.find('duration').text=str(12*tb)
            for c in seq.iter('clipitem'):
                c.find('rate/timebase').text=str(tb)
                for k in ('start','end','in','out','duration'):
                    c.find(k).text=str(round(int(c.findtext(k))*tb/50))
        tree.write(source,encoding='utf-8',xml_declaration=True)
        cfg=s.validate_config(dict(xml=str(source),model='gpt-6.1-sol',audio_track=1))
        units=[dict(id=1,start=0,end=12,text='ตัวอย่างการสรุปใจความ')]
        plan=dict(items=[dict(start=.6,end=4.6,text='สรุปใจความสั้น ๆ',source_ids=[1]),dict(start=6,end=10,text='อ่านง่ายในบรรทัดเดียว',source_ids=[1])])
        job=root/'job';job.mkdir();return source,cfg,units,plan,job,pcm
    def test_font_metrics_handles_thai_and_supported_symbols(self):
        m=Metrics(bootstrap.ROOT/'web/fonts/Kanit-Bold.ttf')
        self.assertGreater(m.width('ตัวอย่าง ≠ ของจริง?',72),0)
        self.assertAlmostEqual(m.width('AA',72),m.width('A',72)*2)
        with self.assertRaises(ValueError):m.width('🙂',72)
    def test_native_fps_layout_and_preserved_tracks(self):
        for portrait,tb in ((False,50),(True,25)):
            with self.subTest(portrait=portrait),tempfile.TemporaryDirectory() as temp:
                source,cfg,units,plan,job,_=self.fixture(Path(temp),portrait,tb)
                p=s.load_project(cfg);s.validate_plan(plan,units,p);r=s.export(plan,p,job)
                self.assertEqual(p['seconds'],12);self.assertEqual(r['layout'],'portrait' if portrait else 'landscape')
                combined=E.parse(job/r['combined_file']).getroot().find('sequence')
                self.assertEqual(len(combined.findall('media/video/track')),3)
                def canonical(e):
                    e=copy.deepcopy(e)
                    for n in e.iter():
                        if n.text is not None and not n.text.strip():n.text=None
                        n.tail=None
                    return E.tostring(e)
                for kind in ('video','audio'):
                    original=p['seq'].findall('media/'+kind+'/track');actual=combined.findall('media/'+kind+'/track')
                    self.assertEqual([canonical(t) for t in actual[:len(original)]],[canonical(t) for t in original])
                generators=combined.findall('media/video/track')[-1].findall('clipitem')
                self.assertEqual(len(generators),2);self.assertEqual(int(generators[0].findtext('start')),round(.6*tb))
                self.assertFalse(E.parse(job/r['text_file']).findall('.//pathurl'))
                self.assertEqual([e.findtext('effectid') for e in generators[0].findall('filter/effect')],['basic','GraphicAndType'])
    def test_reject_wrong_layout_audio_speed_and_multiline(self):
        with tempfile.TemporaryDirectory() as temp:
            source,cfg,units,plan,job,_=self.fixture(Path(temp))
            with self.assertRaises(ValueError):s.load_project(dict(cfg,super_layout='portrait'))
            p=s.load_project(cfg);plan['items'][0]['text']='สอง\nบรรทัด'
            with self.assertRaises(ValueError):s.validate_plan(plan,units,p)
            tree=E.parse(source);tree.find('.//audio/track/clipitem/out').text='599';tree.write(source)
            with self.assertRaises(ValueError):s.load_project(cfg)
    def test_text_only_anchor_preserves_frame_zero_without_changing_super(self):
        for tb in (25,50):
            with self.subTest(tb=tb),tempfile.TemporaryDirectory() as temp:
                _,cfg,units,plan,job,_=self.fixture(Path(temp),tb=tb)
                p=s.load_project(cfg);before=copy.deepcopy(plan);r=s.export(plan,p,job)
                clips=E.parse(job/r['text_file']).findall('.//video/track/clipitem')
                anchor=clips[0]
                self.assertTrue(anchor.get('id').startswith('inwoum-super-anchor-'))
                self.assertEqual([anchor.findtext(k) for k in ('start','end','in','out','duration')],['0','1','0','1','1'])
                self.assertEqual(item_text(anchor),'')
                self.assertEqual(anchor.findtext("filter/effect[effectid='GraphicAndType']/parameter[parameterid='8']/value").split(',')[1],'0.')
                self.assertEqual([int(c.findtext('start')) for c in clips[1:]],[round(x['start']*tb) for x in plan['items']])
                self.assertEqual(len(E.parse(job/r['combined_file']).findall('.//video/track')[-1].findall('clipitem')),2)
                self.assertEqual(r['count'],2);self.assertEqual(plan,before)
    def test_text_at_frame_zero_needs_no_overlapping_anchor(self):
        with tempfile.TemporaryDirectory() as temp:
            _,cfg,_,plan,_,_=self.fixture(Path(temp));p=s.load_project(cfg)
            plan['items'][0]['start']=0
            clips=s.text_track(plan,p,include_anchor=True).findall('clipitem')
            self.assertEqual(len(clips),2)
            self.assertEqual(clips[0].findtext('start'),'0')
            self.assertFalse(any('anchor' in c.get('id') for c in clips))
    def test_native_graphic_text_round_trip_and_style_tables_unchanged(self):
        import base64,struct
        from super_graphic import text_pointer
        specimen=E.parse(bootstrap.ROOT/'assets/super-style.xml').findtext("clipitem/filter/effect[effectid='GraphicAndType']/parameter[parameterid='1']/value")
        baseline=base64.b64decode(specimen)
        pointer=text_pointer(baseline);old=pointer+struct.unpack_from('<I',baseline,pointer)[0]
        old_end=old+4+struct.unpack_from('<I',baseline,old)[0]
        for text in ('','สั้น','ราคาเข้าถึงได้ แต่ฟังก์ชันจัดเต็ม','คำที่ยาวขึ้นมาก '*40,'ทดสอบ ≠ ของจริง?','😀'):
            with self.subTest(text=text):
                encoded=replace_text(specimen,text);raw=base64.b64decode(encoded)
                self.assertEqual(read_text(encoded),text)
                changed=set(range(4))|set(range(pointer,pointer+4))|set(range(old+4,old_end))
                self.assertTrue(all(a==b for i,(a,b) in enumerate(zip(baseline,raw)) if i not in changed))
                self.assertIn(b'Kanit-Bold',raw)
        self.assertEqual(replace_text(specimen,'SUPER'),specimen)
        for bad in ('bad',base64.b64encode(bytes(64)).decode()):
            with self.assertRaises(ValueError):read_text(bad)
        with self.assertRaises(ValueError):replace_text(specimen,'two\nlines')
    def test_native_template_motion_font_and_local_center(self):
        template=E.parse(bootstrap.ROOT/'assets/super-style.xml').getroot()
        original=template.find("clipitem/filter/effect[effectid='basic']")
        reference=template.findtext("clipitem/filter/effect[effectid='GraphicAndType']/parameter[parameterid='3']/value").split(',')[1]
        rx,ry=map(float,reference.split(':'))
        for portrait in (False,True):
            with tempfile.TemporaryDirectory() as temp:
                _,cfg,_,plan,_,_=self.fixture(Path(temp),portrait=portrait);p=s.load_project(cfg)
                clips=s.text_track(plan,p).findall('clipitem')
                self.assertEqual(p['font_size'],100*p['width']/1920)
                self.assertEqual([item_text(c) for c in clips],[x['text'] for x in plan['items']])
                self.assertEqual(len({c.find('file').get('id') for c in clips}),2)
                self.assertEqual(len({c.findtext("filter/effect[effectid='GraphicAndType']/parameter[parameterid='1']/hash") for c in clips}),2)
                for c in clips:
                    motion=c.find("filter/effect[effectid='basic']")
                    self.assertEqual(E.tostring(motion),E.tostring(original))
                    x,y=map(float,c.findtext("filter/effect[effectid='GraphicAndType']/parameter[parameterid='3']/value").split(',')[1].split(':'))
                    self.assertAlmostEqual((x-.5)*p['width'],(rx-.5)*p['width'])
                    self.assertAlmostEqual((y-.5)*p['height'],(ry-.5)*1080*p['width']/1920)
                    self.assertEqual(c.findtext('file/media/video/samplecharacteristics/width'),str(p['width']))
    def test_reject_time_overlap_unknown_source_and_width(self):
        with tempfile.TemporaryDirectory() as temp:
            _,cfg,units,plan,_,_=self.fixture(Path(temp));p=s.load_project(cfg)
            for key,value in [('source_ids',[999]),('text','ข้อความ'*30),('start',-1)]:
                bad=copy.deepcopy(plan);bad['items'][0][key]=value
                with self.subTest(key=key),self.assertRaises(ValueError):s.validate_plan(bad,units,p)
            bad=copy.deepcopy(plan);bad['items'][1]['start']=4
            with self.assertRaises(ValueError):s.validate_plan(bad,units,p)
    def test_real_audio_extraction_matches_source(self):
        with tempfile.TemporaryDirectory() as temp:
            _,cfg,_,_,job,pcm=self.fixture(Path(temp));p=s.load_project(cfg)
            actual=s.extract(p,job,Progress(job,lambda **e:None,lambda:False))
            self.assertEqual(len(actual),len(pcm));self.assertLess(np.max(np.abs(actual.astype(int)-pcm.astype(int))),2)
    def test_one_api_call_and_safe_cached_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            _,cfg,units,plan,job,_=self.fixture(Path(temp));events=[]
            def respond(payload,key):return copy.deepcopy(plan),dict(usage=dict(input_tokens=10,output_tokens=20,total_tokens=30),response_id='test-response')
            with patch('super_mode.transcribe',return_value=units),patch('parallel_requests.plan_once',side_effect=respond) as api:
                first=s.run(dict(cfg,api_key='DO-NOT-PERSIST'),job,'private-key',lambda **e:events.append(e))
                with patch('super_mode.extract',side_effect=AssertionError('must reuse audio')):second=s.run(cfg,job,'')
            self.assertEqual(api.call_count,1);self.assertNotEqual(first['revision'],second['revision'])
            self.assertEqual(first['items'],second['items'])
            self.assertAlmostEqual(first['items'][0]['start'],.9)
            self.assertAlmostEqual(first['items'][0]['end'],3.6)
            self.assertEqual(json.loads((job/'api-usage.json').read_text('utf8'))[0]['status'],'completed')
            self.assertNotIn('api_key',json.loads((job/'config.json').read_text('utf8')))
            self.assertEqual(events[-1]['result']['super_count'],2)
    def test_unknown_api_outcome_never_resubmitted(self):
        with tempfile.TemporaryDirectory() as temp:
            _,cfg,units,_,job,_=self.fixture(Path(temp))
            with patch('super_mode.transcribe',return_value=units),patch('parallel_requests.plan_once',side_effect=TimeoutError()) as api:
                with self.assertRaises(TimeoutError):s.run(cfg,job,'key')
                with self.assertRaisesRegex(ValueError,'ไม่ทราบผล'):s.run(cfg,job,'key')
            self.assertEqual(api.call_count,1)
    def test_cached_capcut_blocks_do_not_need_gpu_or_network(self):
        import capcut_asr
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);job=root/'job';job.mkdir();pcm=np.zeros(8*16000,dtype=np.int16)
            cache=root/'cache/super-capcut';cache.mkdir(parents=True)
            contract=capcut_asr.contract(pcm)
            (cache/(contract['audio_sha256']+'.json')).write_text(json.dumps(dict(contract=contract,status='completed',utterances=[dict(text='ตัวอย่าง',start_time=0,end_time=6000)])),encoding='utf8')
            with patch.object(bootstrap,'ROOT',root),patch('capcut_asr.make_client',side_effect=AssertionError('no upload')):
                units=s.transcribe(pcm,job,Progress(job,lambda **e:None,lambda:False))
            self.assertEqual(units,[dict(id=1,start=0,end=6,text='ตัวอย่าง')])
    def test_prompt_is_generic_and_uses_accepted_certainty_rule(self):
        request=super_prompt.request([dict(id=1,start=0,end=6,text='ตัวอย่าง')],6,dict(model='gpt-6.1-sol'))
        self.assertNotIn('729.12',request['instructions']);self.assertIn('CERTAINTY PER CLAIM',request['instructions'])
        self.assertEqual(json.loads(request['input'])['duration_seconds'],6);self.assertFalse(request['store'])
    def test_worker_dispatches_super_without_cut_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            job=dict(id='super-test',cancel=threading.Event())
            with patch.object(server,'JOB_ROOT',Path(temp)),patch('super_mode.run') as run,patch('engine.run',side_effect=AssertionError('cut engine')),patch('server.update') as update:
                server.worker(job,{},'key','super')
            run.assert_called_once();self.assertEqual(update.call_args.kwargs,dict(status='done'))
    def test_timing_adjustment_is_once_only_and_keeps_short_items(self):
        from fractions import Fraction
        for fps in (Fraction(25),Fraction(50),Fraction(30000,1001)):
            p=dict(fps=fps)
            raw=dict(items=[dict(start=1,end=6,text='เดิม'),dict(start=8,end=8.2,text='สั้น')])
            result=super_edit.adjust_times(raw,p)
            self.assertAlmostEqual(result['items'][0]['start'],round(1*fps)/fps+round(.3*fps)/fps)
            self.assertAlmostEqual(result['items'][0]['end'],(round(6*fps)-round(1*fps))/fps)
            self.assertLess(result['items'][1]['start'],result['items'][1]['end'])
            self.assertEqual(result,super_edit.adjust_times(result,p))
            self.assertEqual(raw['items'][0]['start'],1)
    def editable_fixture(self,temp):
        _,cfg,_,plan,job,_=self.fixture(Path(temp));p=s.load_project(cfg)
        (job/'config.json').write_text(json.dumps(cfg),'utf8')
        (job/'super-source.json').write_text(json.dumps(p['identity']),'utf8')
        r=s.export(plan,p,job)
        payload=dict(base_revision=r['revision'],items=[dict(index=i,text=x['text'],start=x['start'],end=x['end']) for i,x in enumerate(r['items'])])
        return job,p,r,payload
    def test_manual_edit_rebuild_zero_api_and_preserves_original_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            job,p,old,payload=self.editable_fixture(temp)
            before=(job/old['text_file']).read_bytes()
            (job/'api-usage.json').write_text('[{"status":"completed"}]','utf8')
            payload['items']=payload['items'][:1]
            payload['items'][0].update(text='แก้เองได้',start=1.0,end=1.5)
            payload['timing_policy']=super_edit.TIMING_POLICY
            with patch('parallel_requests.call',side_effect=AssertionError('no paid API')),patch('super_mode.extract',side_effect=AssertionError('no audio')),patch('super_mode.transcribe',side_effect=AssertionError('no ASR')):
                new=super_edit.rebuild(job,payload)
            self.assertEqual(new['count'],1);self.assertTrue(new['edited'])
            self.assertEqual(new['parent_revision'],old['revision'])
            self.assertEqual(new['items'][0]['text'],'แก้เองได้')
            self.assertEqual(new['items'][0]['source_ids'],old['items'][0]['source_ids'])
            self.assertEqual(new['items'][0]['start'],1.0);self.assertEqual(new['items'][0]['end'],1.5)
            self.assertEqual((job/old['text_file']).read_bytes(),before)
            self.assertEqual((job/'api-usage.json').read_text('utf8'),'[{"status":"completed"}]')
            clips=E.parse(job/new['text_file']).findall('.//video/track/clipitem')
            self.assertEqual(clips[0].findtext('start'),'0');self.assertEqual(item_text(clips[1]),'แก้เองได้')
            with self.assertRaisesRegex(ValueError,'revision'):super_edit.rebuild(job,payload)
    def test_manual_edits_validate_input_without_changing_saved_result(self):
        with tempfile.TemporaryDirectory() as temp:
            job,p,old,payload=self.editable_fixture(temp);saved=(job/'super-result.json').read_bytes()
            for update in [dict(text=''),dict(text='สอง\nบรรทัด'),dict(start=-1),dict(end=20),dict(end=.601),dict(start=True),dict(text='ยาว'*100),dict(index=999)]:
                bad=copy.deepcopy(payload);bad['items'][0].update(update)
                with self.subTest(update=update),self.assertRaises(ValueError):super_edit.prepare(job,bad)
            for rows in ([],[payload['items'][0],payload['items'][0]]):
                with self.assertRaises(ValueError):super_edit.prepare(job,dict(payload,items=rows))
            overlap=copy.deepcopy(payload);overlap['items'][1]['start']=2
            with self.assertRaises(ValueError):super_edit.prepare(job,overlap)
            self.assertEqual((job/'super-result.json').read_bytes(),saved)
    def test_manual_edit_rejects_changed_source_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            job,p,_,payload=self.editable_fixture(temp)
            path=Path(p['clips'][0]['path']);path.touch()
            with self.assertRaisesRegex(ValueError,'ต้นฉบับเปลี่ยน'):super_edit.prepare(job,payload)
    def test_manual_rebuild_worker_dispatch_never_runs_model_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            job=dict(id='super-test',cancel=threading.Event())
            with patch.object(server,'JOB_ROOT',Path(temp)),patch('super_edit.rebuild') as rebuild,patch('super_mode.run',side_effect=AssertionError('no AI pipeline')),patch('server.update'):
                server.worker(job,{},'','super_rebuild',plan={'base_revision':'test'})
            self.assertEqual(rebuild.call_args.args[1],{'base_revision':'test'})

if __name__=='__main__':unittest.main()
