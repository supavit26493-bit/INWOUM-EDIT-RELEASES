import json,tempfile,unittest,copy
from pathlib import Path
from unittest.mock import patch
import default02,runtime_contract as runtime,sales_map,single_clip,sales_continuity,episode_continuity,default_audio
from planner import fingerprint

def catalog():
    return [dict(id='U0001',start=0,end=1,text='สินค้า ดีมาก',words=[dict(text='สินค้า',start=0,end=.5),dict(text='ดีมาก',start=.5,end=1)],protected=False)]

class Default02Tests(unittest.TestCase):
    def test_new_jobs_mode_specific_defaults_explicit_choices_remain(self):
        for mode,field in [('sales','sales_style'),('single','single_style')]:
            self.assertEqual(runtime.configure(dict(content_mode=mode))[field],'sales_2_1' if mode=='sales' else 'natural_v2')
            for style in ('natural','original'):
                self.assertEqual(runtime.configure(dict(content_mode=mode,**{field:style}))[field],style)

    def test_old_default_and_original_request_hashes_match_installed_0139(self):
        expected=json.loads((Path(__file__).parent/'fixtures/cut-styles-0139.json').read_text('utf8'))
        segments=[dict(id='T001',start=0,end=1,text='สินค้า ดีมาก')]
        for style in ('natural','original'):
            sales=runtime.configure(dict(model='gpt-6.1-sol',effort='medium',sales_style=style))
            single=runtime.configure(dict(model='gpt-6.1-sol',effort='medium',content_mode='single',single_style=style,single_method='rearrange'))
            self.assertEqual(fingerprint(sales_map.make_request(segments,sales)),expected[style]['sales_map'])
            self.assertEqual(fingerprint(single_clip.request_body(catalog(),single)),expected[style]['single'])

    def test_single_default02_refines_in_existing_request_without_schema_growth(self):
        old=single_clip.request_body(catalog(),runtime.configure(dict(content_mode='single',single_style='natural',single_method='rearrange',model='gpt-6.1-sol')))
        new=single_clip.request_body(catalog(),runtime.configure(dict(content_mode='single',single_style='natural_v2',single_method='rearrange',model='gpt-6.1-sol')))
        self.assertIn(default02.REFINEMENT,new['instructions']);self.assertNotIn(default02.REFINEMENT,old['instructions'])
        self.assertNotIn('Within a passage keep its original word order and a continuous',new['instructions'])
        self.assertEqual(new['text'],old['text']);self.assertEqual(new['input'],old['input'])
        self.assertNotEqual(fingerprint(new),fingerprint(old))

    def test_sales_has_refinement_but_legacy_instruction_string_unchanged(self):
        self.assertEqual(sales_continuity.instructions_for(dict(sales_style='natural')),sales_continuity.INSTRUCTIONS)
        self.assertIn(default02.REFINEMENT,sales_continuity.instructions_for(dict(sales_style='natural_v2')))
        self.assertIn('Keep enthusiasm',default02.REFINEMENT);self.assertIn('ดีมากมาก',default02.REFINEMENT)

    def test_chronological_mode_still_keeps_full_units(self):
        req=single_clip.request_body(catalog(),runtime.configure(dict(content_mode='single',single_style='natural_v2',single_method='chronological',model='gpt-6.1-sol')))
        self.assertIn('Keep full units',req['instructions']);self.assertNotIn(default02.REFINEMENT,req['instructions'])
        self.assertNotIn('events',req['text']['format']['schema']['properties'])

    def test_new_style_keeps_existing_audio_and_event_guards(self):
        from project import configure_export
        for mode,field in [('sales','sales_style'),('single','single_style')]:
            cfg=runtime.configure(dict(content_mode=mode,single_method='rearrange',**{field:'natural_v2'}))
            self.assertTrue(default_audio.enabled(cfg));self.assertTrue(episode_continuity.enabled(cfg))
            self.assertTrue(configure_export({},cfg)['preserve_source_tracks'])

    def test_prune_only_unsupported_padding_island(self):
        pieces=[(0,2),(10,12),(20,24),(30,60)];before=copy.deepcopy(pieces)
        self.assertEqual(default02.prune_padding(pieces,[(10,12)],[(20,24)]),[(10,12),(20,24),(30,60)])
        self.assertEqual(pieces,before)
        self.assertEqual(default02.prune_padding(pieces,[],None),pieces)
        self.assertEqual(default02.prune_padding(pieces,[],[],protected=True),pieces)

    def test_short_meaningful_word_is_never_removed_by_duration(self):
        self.assertEqual(default02.prune_padding([(0,1),(10,27)],[(0,1),(10,27)],[]),[(0,1),(10,27)])

    def test_default02_does_not_accept_old_assembly_reply_as_new_refinement(self):
        old=dict(model='gpt-6.1-sol',instructions=runtime.rules_for(dict(sales_style='natural'))+sales_continuity.INSTRUCTIONS,input='{}',text={})
        new=copy.deepcopy(old);new['instructions']=runtime.rules_for(dict(sales_style='natural_v2'))+sales_continuity.instructions_for(dict(sales_style='natural_v2'))
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'stage-request.json').write_text(json.dumps(old),'utf8');(folder/'stage-response.json').write_text('{}','utf8')
            self.assertEqual(runtime.cached_editorial_request(new,folder,'stage'),new)

    def test_default02_full_sales_pipeline_uses_same_two_requests_and_exports_style(self):
        import test_auto_xml as auto,engine
        with tempfile.TemporaryDirectory() as d:
            job,cfg,prepared,story,draft=auto.AutoXMLTests().setup_job(Path(d))
            cfg=runtime.configure(dict(cfg,sales_style='natural_v2'))
            def respond(req,key):
                is_map=req['text']['format']['name']=='sales_story_map'
                if not is_map:self.assertIn(default02.REFINEMENT,req['instructions'])
                return copy.deepcopy(story if is_map else draft),dict(usage={})
            with patch('parallel_requests.plan_once',side_effect=respond) as send:
                result=engine.run(cfg,job,'test',prepared=prepared)
            self.assertEqual(send.call_count,2)
            manifest=json.loads((job/result['revision']/'manifest.json').read_text('utf8'))
            self.assertEqual(manifest['cut_style'],'natural_v2');self.assertFalse(manifest['crossfades'])
            self.assertTrue(json.loads((job/'silence.json').read_text('utf8'))['default02'])
