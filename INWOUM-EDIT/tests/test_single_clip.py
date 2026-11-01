import copy, io, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import engine, runtime_contract as runtime, server, single_clip
from fast_logic import resolve_plan
from project import validate_edits,intake,prepare_source
from demo import create
from audio_io import write_pcm
from verify_outputs import check


class SingleClipTests(unittest.TestCase):
    def test_single_styles_default_legacy_and_validation(self):
        self.assertEqual(runtime.configure(dict(content_mode='single'))['single_style'],'natural_v2')
        self.assertEqual(runtime.configure(dict(content_mode='single',single_style='original'))['single_style'],'original')
        with self.assertRaises(ValueError):runtime.configure(dict(content_mode='single',single_style='wrong'))
        for maximum in (0,180):
            cfg=runtime.configure(dict(content_mode='single',single_method='rearrange',model='gpt-6.1-sol',single_max_seconds=maximum))
            req=single_clip.request_body(self.catalog(),cfg)
            self.assertIn('SINGLE STYLE: DEFAULT',req['instructions'])
            self.assertNotIn('FULL + focused',req['instructions'])
            self.assertNotIn('SALES EDITORIAL CONTRACT',req['instructions'])
            self.assertIn('Prices and numbers are allowed',req['instructions'])
        legacy=single_clip.request_body(self.catalog(),dict(model='gpt-6.1-sol',single_method='rearrange'))
        original=single_clip.request_body(self.catalog(),dict(model='gpt-6.1-sol',single_method='rearrange',single_style='original'))
        self.assertEqual(legacy,original)

    def test_natural_boundary_join_keeps_continuous_tea_phrase(self):
        from test_sales_continuity import unit,selection
        catalog=[unit('A',['ชาเขียว','นี่','มัน','อร่อย','ดี'],0),unit('B',['ดี','นะ','ผม','ชอบ','ซื้อ','กิน','ประจำ'],4)]
        raw=dict(edits=[dict(name='เรื่อง',purpose='เล่าเรื่อง',selections=[selection('A',0,4),selection('B',0,6)])],unused=[])
        result=single_clip.normalize(raw,catalog,'rearrange','natural')
        self.assertEqual([{k:s[k] for k in ('unit_id','first_word','last_word')} for s in result['edits'][0]['selections']],[selection('A',0,4),selection('B',1,6)])

    def test_single_targeted_repair_allows_prices_and_preserves_protected_units(self):
        import sales_continuity as flow
        from test_sales_continuity import unit,selection,plan
        catalog=[unit('A',['นะ'],10),unit('B',['ราคา','สองร้อยบาท'],30)]
        p=plan([selection('A',0,0),selection('B',0,0)])
        answer={'e0':dict(selections=[selection('B',0,1)],reason='เก็บวลีครบ')}
        with tempfile.TemporaryDirectory() as d,patch('parallel_requests.call',return_value=answer) as send:
            result,_=flow.repair(p,catalog,Path(d),'dummy',dict(instructions='SINGLE ONLY',text=dict(format={})),lambda **kw:None,content_mode='single')
            self.assertEqual(result['edits'][0]['selections'],[selection('B',0,1)])
            self.assertEqual(send.call_args.args[3],'single-continuity-repair-v1')
            self.assertNotIn('SALES EDITORIAL CONTRACT',send.call_args.args[0]['instructions'])
            self.assertTrue((Path(d)/'single-continuity-repair.json').exists())
        catalog[1]['protected']=True
        answer={'e0':dict(selections=[selection('A',0,0)],reason='ตัดราคา')}
        with tempfile.TemporaryDirectory() as d,patch('parallel_requests.call',return_value=answer):
            with self.assertRaisesRegex(ValueError,'ระบุให้เก็บ'):
                flow.repair(p,catalog,Path(d),'dummy',dict(instructions='SINGLE ONLY',text=dict(format={})),lambda **kw:None,content_mode='single')
    def catalog(self):
        return [dict(id=f'U{i}',start=a,end=b,text=t,words=[dict(start=a,end=b,text=t)],protected=False)
                for i,a,b,t in [(1,0,100,'เริ่มเรื่อง'),(2,100,200,'ขยายเรื่อง'),(3,200,240,'จบเรื่อง')]]

    def test_separate_rules_and_sales_contract_unchanged(self):
        sales=runtime.configure(dict(mode='ai'))
        self.assertEqual(sales['editorial_workflow'],'sales-v8')
        single=runtime.configure(dict(content_mode='single',model='gpt-6.1-sol',approved_story_map_job='old'))
        self.assertNotIn('approved_story_map_job',single)
        self.assertEqual(single['ruleset'],'single-story-v1')
        request=single_clip.request_body(self.catalog(),single)
        self.assertNotIn('SALES EDITORIAL CONTRACT',request['instructions'])
        self.assertIn('no duration ceiling',request['instructions'])

    def test_long_single_allowed_but_sales_stays_limited(self):
        project=dict(duration=12000,primary=[dict(start=0,end=12000)])
        edit=[dict(name='คลิปเดียว',ranges=[dict(start=0,end=240)])]
        with self.assertRaisesRegex(ValueError,'3นาที'):validate_edits(copy.deepcopy(edit),project)
        self.assertEqual(len(validate_edits(copy.deepcopy(edit),dict(project,content_mode='single'))),1)
        with self.assertRaises(ValueError):validate_edits(edit*2,dict(project,content_mode='single'))

    def test_chronological_keeps_whole_units_sorted_and_rejects_missing(self):
        catalog=self.catalog()
        raw=dict(decisions=[dict(unit_id=u['id'],keep=True,reason='') for u in reversed(catalog)])
        plan=single_clip.normalize(raw,catalog,'chronological')
        self.assertEqual([s['unit_id'] for s in plan['edits'][0]['selections']],['U1','U2','U3'])
        raw['decisions'].pop()
        with self.assertRaises(ValueError):single_clip.normalize(raw,catalog,'chronological')

    def test_rearrange_preserves_model_order_and_rejects_bad_indices(self):
        catalog=self.catalog()
        raw=dict(edits=[dict(name='เรื่อง',purpose='เล่าเรื่อง',selections=[dict(unit_id=i,first_word=0,last_word=0) for i in ['U2','U1','U3']])],unused=[])
        plan=single_clip.normalize(raw,catalog,'rearrange')
        self.assertEqual(plan['edits'][0]['selections'][0]['unit_id'],'U2')
        raw['edits'][0]['selections'][0]['last_word']=2
        with self.assertRaises(ValueError):single_clip.normalize(raw,catalog,'rearrange')

    def test_protected_spans_kept_without_overlap(self):
        catalog=[self.catalog()[0],dict(id='Px',start=90,end=110,text='demo',words=[],protected=True)]
        catalog=single_clip.prepare_catalog(catalog)
        self.assertEqual([(u['start'],u['end']) for u in catalog],[(0,100),(100,110)])
        raw=dict(decisions=[dict(unit_id=u['id'],keep=False,reason='ไม่ใช้') for u in catalog])
        plan=single_clip.normalize(raw,catalog,'chronological')
        self.assertEqual(len(plan['edits'][0]['selections']),2)

    def test_endpoint_routes_single_without_sales(self):
        body=json.dumps(dict(content_mode='single',single_method='rearrange',mode='ai',api_key='test',model='gpt-6-luna')).encode()
        result=[]
        handler=SimpleNamespace(auth=lambda:None,path='/api/run',headers={'Content-Length':str(len(body))},rfile=io.BytesIO(body),send_json=lambda *a:result.append(a))
        with patch('server.launch',return_value=dict(id='new')) as launch:server.Handler.do_POST(handler)
        self.assertEqual(launch.call_args.args[0]['editorial_workflow'],'single-story-v1')
        self.assertNotIn('api_key',launch.call_args.args[0])

    def test_real_export_each_method_cached_retry_and_source_samples(self):
        for method,style in [(m,s) for m in ['chronological','rearrange'] for s in ['natural','original']]:
            with self.subTest(method=method,style=style),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source,pcm,transcript=create(root/'media',True)
                job=root/'job';job.mkdir();project=intake(source,audio_track=1);prepare_source(project,job)
                write_pcm(job/'timeline.wav',pcm)
                cfg=runtime.configure(dict(xml=str(source),mode='ai',model='gpt-6-luna',profile='compact',content_mode='single',single_method=method,single_style=style))
                def respond(request,key):
                    units=json.loads(request['input'])['units']
                    if method=='chronological':raw=dict(decisions=[dict(unit_id=u['id'],keep=True,reason='') for u in units])
                    else:raw=dict(edits=[dict(name='เรื่อง',purpose='ครบเรื่อง',selections=[dict(unit_id=u['id'],first_word=0,last_word=len(u['phrases'])-1) for u in reversed(units)])],unused=[])
                    if cfg.get('episode_policy'):
                        from test_episode_continuity import fixture_events
                        fixture_events(raw)
                    return raw,dict(usage={})
                prepared=(project,pcm,transcript,[(s['start'],s['end']) for s in transcript])
                with patch('parallel_requests.plan_once',side_effect=respond) as send:
                    result=engine.run(cfg,job,'dummy',prepared=prepared)
                    self.assertEqual(result['versions'],1)
                    self.assertTrue(check(job)['wav_exact_samples'])
                    engine.run(cfg,job,'',prepared=prepared)
                    self.assertEqual(send.call_count,1)
                self.assertNotIn('dummy',(job/'config.json').read_text('utf8'))

    def test_unknown_response_never_resends(self):
        with tempfile.TemporaryDirectory() as tmp:
            from parallel_requests import call
            req=single_clip.request_body(self.catalog(),dict(model='gpt-6-luna'))
            with patch('parallel_requests.plan_once',side_effect=TimeoutError('timeout')) as send:
                with self.assertRaises(TimeoutError):call(req,Path(tmp),'dummy','single-story')
                with self.assertRaisesRegex(ValueError,'ไม่ทราบผล'):call(req,Path(tmp),'dummy','single-story')
                self.assertEqual(send.call_count,1)

    def test_actual_xml_over_three_minutes_keeps_exact_audio(self):
        import xml.etree.ElementTree as E
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,short,_=create(root/'media')
            tree=E.parse(source)
            for el in tree.iter():
                if el.tag in ('duration','end','out') and el.text=='600':el.text='12000'
                if el.tag=='pproTicksOut':el.text=str(12000*5080320000)
            tree.write(source,encoding='utf8',xml_declaration=True)
            pcm=np.tile(short,20);write_pcm(source.parent/'demo-tones.wav',pcm)
            transcript=[dict(start=.6,end=239,text='เรื่องยาวครบ',words=[dict(start=.6,end=239,text='เรื่องยาวครบ')])]
            job=root/'job';job.mkdir();project=intake(source,audio_track=1);prepare_source(project,job);write_pcm(job/'timeline.wav',pcm)
            cfg=runtime.configure(dict(xml=str(source),mode='ai',model='gpt-6-luna',profile='compact',content_mode='single'))
            def respond(req,key):
                return dict(decisions=[dict(unit_id=u['id'],keep=True,reason='') for u in json.loads(req['input'])['units']]),dict(usage={})
            # Isolate long-duration export from the independent acoustic pause detector.
            silence=dict(profile='compact',removed_frames=[],removed_seconds=0,method='fixture',policy='vad-negative-noise-v4')
            with patch('parallel_requests.plan_once',side_effect=respond),patch('silence_v4.detect',return_value=silence):
                result=engine.run(cfg,job,'dummy',prepared=(project,pcm,transcript,[(.6,239)]))
            self.assertTrue(check(job)['wav_exact_samples'])
            manifest=json.loads((job/result['revision']/'manifest.json').read_text('utf8'))
            duration=sum(r['end']-r['start'] for r in manifest['edits'][0]['ranges'])
            self.assertGreater(duration,180)
