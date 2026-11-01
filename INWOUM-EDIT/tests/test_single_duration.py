import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as E
import numpy as np
import engine,runtime_contract,single_clip
import test_single_clip as fixtures
from project import validate_edits,intake,prepare_source
from demo import create
from audio_io import write_pcm
from verify_outputs import check

class SingleDurationTests(unittest.TestCase):
    def test_explicit_limit_only_and_prompt_has_durations_without_conflicting_rules(self):
        cat=fixtures.SingleClipTests().catalog()
        for method in ('chronological','rearrange'):
            cfg=runtime_contract.configure(dict(content_mode='single',model='gpt-6-luna',single_method=method,single_max_seconds=180))
            req=single_clip.request_body(cat,cfg)
            self.assertIn('<=180 seconds',req['instructions'])
            self.assertNotIn('no duration ceiling',req['instructions'])
            self.assertNotIn('Do not omit an idea just to make the clip shorter',req['instructions'])
            self.assertEqual(json.loads(req['input'])['units'][0]['seconds'],100)
        for bad in (True,'180',181,-1):
            with self.assertRaises(ValueError):runtime_contract.configure(dict(content_mode='single',single_max_seconds=bad))

    def test_actual_frame_ceiling_does_not_truncate_and_unlimited_still_allows_long(self):
        project=dict(duration=12000,primary=[dict(start=0,end=12000)],content_mode='single',single_max_seconds=180)
        edit=[dict(name='one',ranges=[dict(start=0,end=180)])]
        self.assertEqual(validate_edits(copy.deepcopy(edit),project)[0]['ranges'][0]['end'],180)
        edit[0]['ranges'][0]['end']=180.02;before=copy.deepcopy(edit)
        with self.assertRaisesRegex(ValueError,'เกิน3นาที'):validate_edits(edit,project)
        self.assertEqual(edit,before)
        self.assertEqual(len(validate_edits(edit,dict(project,single_max_seconds=0))),1)

    def test_real_limited_export_each_method_one_api_and_retry_uses_cache(self):
        for method in ('chronological','rearrange'):
            with self.subTest(method=method),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source,short,_=create(root/'media')
                tree=E.parse(source)
                for el in tree.iter():
                    if el.tag in ('duration','end','out') and el.text=='600':el.text='12000'
                    if el.tag=='pproTicksOut':el.text=str(12000*5080320000)
                tree.write(source,encoding='utf8',xml_declaration=True)
                pcm=np.tile(short,20);write_pcm(source.parent/'demo-tones.wav',pcm)
                transcript=[dict(start=a,end=b,text=t,words=[dict(start=a,end=b,text=t)]) for a,b,t in [(1,79,'เริ่มเรื่อง'),(81,159,'เรื่องรอง'),(161,239,'จบเรื่อง')]]
                job=root/'job';job.mkdir();project=intake(source,audio_track=1);prepare_source(project,job);write_pcm(job/'timeline.wav',pcm)
                cfg=runtime_contract.configure(dict(xml=str(source),model='gpt-6-luna',content_mode='single',single_method=method,single_max_seconds=180))
                def respond(req,key):
                    units=json.loads(req['input'])['units'];selected=[units[0],units[-1]]
                    if method=='chronological':raw=dict(decisions=[dict(unit_id=u['id'],keep=u in selected,reason='เรื่องรอง') for u in units])
                    else:raw=dict(edits=[dict(name='เรื่องสั้น',purpose='ประเด็นหลักครบ',selections=[dict(unit_id=u['id'],first_word=0,last_word=len(u['phrases'])-1) for u in selected])],unused=[dict(unit_id=u['id'],decision='production',reason='เรื่องรอง') for u in units if u not in selected])
                    if cfg.get('episode_policy'):
                        from test_episode_continuity import fixture_events
                        fixture_events(raw)
                    return raw,dict(usage={})
                silence=dict(profile='compact',removed_frames=[],removed_seconds=0,method='fixture',policy='vad-negative-noise-v4')
                prepared=(project,pcm,transcript,[(s['start'],s['end']) for s in transcript])
                with patch('parallel_requests.plan_once',side_effect=respond) as send,patch('silence_v4.detect',return_value=silence):
                    result=engine.run(cfg,job,'dummy',prepared=prepared)
                    engine.run(cfg,job,'',prepared=prepared)
                    self.assertEqual(send.call_count,1)
                self.assertTrue(check(job)['wav_exact_samples'])
                manifest=json.loads((job/result['revision']/'manifest.json').read_text('utf8'))
                self.assertEqual(len(manifest['edits']),1)
                self.assertLessEqual(manifest['edits'][0]['seconds'],180)
