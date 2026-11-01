import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import runtime_contract as runtime,sales_reserve as reserve,sales_map,sales_draft,engine
import episode_continuity,default_audio
from project import configure_export
from test_sales_continuity import unit,selection

class ReserveTests(unittest.TestCase):
    def setUp(self):
        p=patch('api_budget.count_input',return_value=1000);p.start();self.addCleanup(p.stop)
    def test_opt_in_sales_only_and_existing_defaults(self):
        cfg=runtime.configure(dict(sales_style=reserve.STYLE))
        self.assertTrue(reserve.enabled(cfg));self.assertTrue(episode_continuity.enabled(cfg))
        self.assertTrue(default_audio.enabled(cfg));self.assertEqual(cfg['audio_track'],1)
        self.assertTrue(configure_export({},cfg)['preserve_source_tracks'])
        self.assertEqual(runtime.configure({})['sales_style'],'sales_2_1')
        self.assertFalse(reserve.enabled(dict(content_mode='single',sales_style=reserve.STYLE)))
        with self.assertRaises(ValueError):runtime.configure(dict(content_mode='single',single_style=reserve.STYLE))

    def test_extra_inventory_only_in_new_request_no_additional_call(self):
        segments=[dict(id='T001',start=0,end=2,text='สินค้านี้ดี')]
        old=sales_map.make_request(segments,runtime.configure(dict(model='gpt-6.1-sol',sales_style='natural')))
        new=sales_map.make_request(segments,runtime.configure(dict(model='gpt-6.1-sol',sales_style=reserve.STYLE)))
        self.assertNotIn('reserve_takes',old['text']['format']['schema']['properties'])
        self.assertIn('reserve_takes',new['text']['format']['schema']['required'])
        self.assertEqual(json.loads(new['input']),json.loads(old['input']))
        for term in ('bullying','humor','explicit prohibited prices','No usage quota'):
            self.assertIn(term,new['instructions'])

    def test_map_invalid_references_and_collision_rejected(self):
        story=dict(versions=[],reserve_takes=[dict(id='R1',title='มุก',segment_ids=['T'],reason='เทคต่างอารมณ์')])
        reserve.validate_map(story,[dict(id='T',start=0)])
        for rid,refs in [('FULL',['T']),('R1',['missing']),('R1',['T','T'])]:
            bad=copy.deepcopy(story);bad['reserve_takes'][0].update(id=rid,segment_ids=refs)
            with self.assertRaises(ValueError):reserve.validate_map(bad,[dict(id='T',start=0)])

    def test_order_and_titles_do_not_touch_main_selections(self):
        cat=[unit('A',['สินค้านี้','ดีมาก'],0),unit('B',['ดีมาก','ขำมาก'],4),unit('C',['อีกเทค','น่าสนใจ'],8)]
        main=dict(name='FULL',edit_role='FULL',selections=[selection('A',0,1)])
        plan=dict(edits=[main,dict(name='ท้าย',reserve_title='ท้าย',edit_role='reserve',hook_count=0,selections=[selection('C',0,1)]),dict(name='ก่อน',reserve_title='ก่อน',edit_role='reserve',hook_count=0,selections=[selection('B',0,1)])])
        before=copy.deepcopy(main);reserve.label_plan(plan,cat);reserve.validate_plan(plan,cat)
        self.assertEqual(plan['edits'][0],before)
        self.assertEqual([e['name'] for e in plan['edits'][1:]],['เทคสำรอง 01 — ก่อน','เทคสำรอง 02 — ท้าย'])
        reserve.label_plan(plan,cat);self.assertEqual(plan['edits'][1]['name'],'เทคสำรอง 01 — ก่อน')

    def test_reserve_prices_reversed_order_and_all_main_duplicate_rejected(self):
        cat=[unit('A',['ดีมาก'],0),unit('B',['ราคา','สองร้อยบาท'],4)]
        main=dict(edit_role='FULL',selections=[selection('A',0,0)])
        for picks in [[selection('B',0,1)],[selection('B',0,0),selection('A',0,0)],[selection('A',0,0)]]:
            plan=dict(edits=[main,dict(edit_role='reserve',hook_count=0,selections=picks)])
            with self.assertRaises(ValueError):reserve.validate_plan(plan,cat)

    def test_pipeline_exports_extra_sequence_preserves_main_and_caches_each_clip(self):
        from test_auto_xml import AutoXMLTests
        from verify_outputs import check
        with tempfile.TemporaryDirectory() as tmp:
            job,cfg,prepared,story,draft=AutoXMLTests().setup_job(Path(tmp))
            cfg=runtime.configure(dict(cfg,sales_style=reserve.STYLE))
            story['reserve_takes']=[dict(id='R1',title='อีกเทค',segment_ids=['T003'],reason='เก็บเนื้อหาเพิ่มเติม')]
            saved_main=copy.deepcopy(draft['edits']['FULL'])
            last=draft['edits']['FULL']['selections'].pop()
            draft['edits']['R1']=dict(name='อีกเทค',purpose='ทางเลือกให้คนตัด',selections=[last],hook_count=0)
            expected=copy.deepcopy(draft['edits']['FULL']['selections'])
            def respond(req,key):
                if req['text']['format']['name']=='sales_story_map':return copy.deepcopy(story),dict(usage={})
                answer=copy.deepcopy(draft)
                if 'assembly_scope' in json.loads(req['input']):
                    mids=json.loads(req['input'])['assembly_scope']['map_ids']
                    answer['edits']={mid:answer['edits'][mid] for mid in mids};answer['unused']=[]
                return answer,dict(usage={})
            with patch('parallel_requests.plan_once',side_effect=respond) as send:
                result=engine.run(cfg,job,'fake-key',prepared=prepared)
                self.assertEqual(send.call_count,2)
                plan=json.loads((job/'plan.json').read_text('utf8'))
                self.assertEqual([{k:v for k,v in s.items() if k in ('unit_id','first_word','last_word')} for s in plan['edits'][0]['selections']],[{k:v for k,v in s.items() if k in ('unit_id','first_word','last_word')} for s in expected])
                self.assertEqual(plan['edits'][1]['name'],'เทคสำรอง 01 — อีกเทค')
                self.assertEqual(plan['edits'][1]['edit_role'],'reserve')
                self.assertEqual(check(job)['versions'],2)
                manifest=json.loads((job/result['revision']/'manifest.json').read_text('utf8'))
                self.assertEqual(manifest['cut_style'],reserve.STYLE)
                engine.run(cfg,job,'',prepared=prepared)
                self.assertEqual(send.call_count,2)

