import copy,unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
from group_preview import validate,run

class GroupPreviewTests(unittest.TestCase):
    def test_group_checkpoint_uses_text_only_and_never_creates_cut_or_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';source.mkdir();out=Path(tmp)/'out';out.mkdir()
            raw=json.dumps([dict(start=0,end=1,text='คำถอดเพี้ยน ไม่กันน้ำ')],ensure_ascii=False).encode()
            (source/'transcript.json').write_bytes(raw)
            idea=dict(title='เรื่อง',segment_ids=['T001'],group_ids=['G1'],reason='ประเด็น')
            answer=dict(groups=[dict(id='G1',title='สินค้า',summary='คำกล่าว',segment_ids=['T001'],alternative_segment_ids=[],caveats=[])],hooks=[],full_outline=[idea],focused_concepts=[],full_target_seconds=60,review_note='แผนเท่านั้น',segment_review=[dict(segment_id='T001',classification='useful',group_ids=['G1'],reason='สาระ')])
            with patch('group_preview.call',return_value=answer) as api:
                run(source,out,dict(model='test',effort='low',hook_policy='hook-sales-v1'),'secret',lambda **e:None,lambda:False)
            self.assertEqual(api.call_count,1)
            self.assertIn('sales Hooks',api.call_args.args[0]['instructions'])
            self.assertIn('[ทางเลือก]',api.call_args.args[0]['instructions'])
            self.assertIn('คำถอดเพี้ยน ไม่กันน้ำ',api.call_args.args[0]['input'])
            self.assertEqual((source/'transcript.json').read_bytes(),raw)
            self.assertTrue((out/'CONTENT-GROUPS-REVIEW.md').is_file())
            for name in ('plan.json','latest.json','timeline.wav','transcript.json'):self.assertFalse((out/name).exists())
            self.assertNotIn('secret',(out/'config.json').read_text('utf8'))
    def test_every_segment_and_group_link_checked(self):
        segments=[dict(id='T001'),dict(id='T002')]
        idea=dict(title='เรื่อง',segment_ids=['T001'],group_ids=['G1'],reason='ประเด็น')
        good=dict(groups=[dict(id='G1',title='กลุ่ม',summary='ใจความ',segment_ids=['T001'],alternative_segment_ids=[])],
            hooks=[idea],full_outline=[idea],focused_concepts=[],segment_review=[dict(segment_id='T001',classification='useful',group_ids=['G1'],reason='สาระ'),dict(segment_id='T002',classification='production',group_ids=[],reason='นับเทค')])
        self.assertIs(validate(good,segments),good)
        for mutate in [lambda a:a['segment_review'].pop(),lambda a:a['groups'][0].update(segment_ids=['T999']),lambda a:a['segment_review'][1].update(classification='useful'),lambda a:a['segment_review'][1].update(group_ids=['G1'])]:
            bad=copy.deepcopy(good);mutate(bad)
            with self.assertRaises(ValueError):validate(bad,segments)
