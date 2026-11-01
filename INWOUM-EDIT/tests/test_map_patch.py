import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import sales_map
import test_sales_v8 as fixtures

class MapPatchTests(unittest.TestCase):
    def test_explicit_validation_retry_is_bounded_and_never_resends_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'map-validation.json').write_text(json.dumps(dict(valid=False,error='FULLซ้ำ')),'utf8')
            (p/'map-candidate.json').write_text('{}','utf8');ledger=p/'api-usage.json'
            for state in ('sent','failed'):
                ledger.write_text(json.dumps([dict(status=state)]),'utf8')
                self.assertIsNone(sales_map.validation_retry(p,{}))
            ledger.write_text(json.dumps([dict(status='completed')]),'utf8')
            config=sales_map.validation_retry(p,{})
            self.assertTrue(config['blocking_only']);self.assertEqual(config['validation_retry_count'],1)
            self.assertIsNone(sales_map.validation_retry(p,config))
    def blank(self):
        return dict(hook_updates=[],hook_additions=[],remove_hooks=[],version_updates=[],version_additions=[],remove_versions=[],group_updates=[],group_additions=[],remove_groups=[],full_hook_id='',full_outline=[],segment_review_updates=[],continuity_additions=[],review_note='แก้เฉพาะจุด')
    def test_patch_keeps_unmentioned_groups_reviews_and_source_immutable(self):
        source,base=fixtures.V8Tests().example();before=copy.deepcopy(base);change=self.blank()
        change['version_updates']=[dict(base['versions'][0],title='เทคใหม่')]
        out=sales_map.apply_map_patch(base,change);sales_map.validate(out,source)
        self.assertEqual(base,before);self.assertEqual(out['groups'],base['groups'])
        self.assertEqual(out['segment_review'],base['segment_review']);self.assertEqual(out['full_outline'],base['full_outline'])
        self.assertEqual(out['versions'][0]['title'],'เทคใหม่')
    def test_patch_rejects_unknown_duplicate_and_implicit_replacement_ids(self):
        _,base=fixtures.V8Tests().example()
        for change in [dict(remove_versions=['missing']),dict(remove_versions=['V1','V1']),dict(version_additions=[base['versions'][0]]),dict(version_updates=[dict(base['versions'][0],id='missing')]),dict(remove_versions=['V1'],version_updates=[base['versions'][0]])]:
            raw=self.blank();raw.update(change)
            with self.assertRaises(ValueError):sales_map.apply_map_patch(base,raw)
    def test_focus_does_not_redo_all_source_reviews_or_demand_all_roles(self):
        source,base=fixtures.V8Tests().example()
        for i in range(3,20):
            source.append(dict(id=f'T{i:03d}',start=i*2,end=i*2+1,text='เรื่องเดิม'))
            base['segment_review'].append(dict(segment_id=f'T{i:03d}',roles=['chatter'],disposition='pending',reason='พักเดิม',excluded_quotes=[]))
        base['hooks'][0]['opening_quote']='คำผิด'
        request,scope=sales_map.refinement_request(source,dict(model='test',effort='low'),base)
        data=json.loads(request['input']);self.assertEqual(scope['reviewed_segment_ids'],['T001','T002'])
        self.assertEqual(len(data['source_reviews']),2);self.assertNotIn('segment_review',data['previous_map'])
        self.assertEqual(request['instructions'].count('SALES EDITORIAL CONTRACT'),1)
        self.assertNotIn('complete revised map',request['instructions'])
        self.assertEqual(request['text']['format']['schema']['properties']['segment_review_updates'].get('minItems',0),0)
    def test_patch_result_is_merged_then_validated_without_paid_retry_or_export(self):
        source,base=fixtures.V8Tests().example();change=self.blank();change['hook_updates']=[dict(base['hooks'][0],trim_note='')]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=root/'source';src.mkdir();old=root/'prior';old.mkdir();out=root/'new';out.mkdir()
            raw=json.dumps(source).encode();(src/'transcript.json').write_bytes(raw)
            import hashlib
            (old/'grouping-source.json').write_text(json.dumps(dict(transcript_sha256=hashlib.sha256(raw).hexdigest())),'utf8')
            (old/'sales-story-map-response.json').write_text(json.dumps(dict(request_hash='original',response=base)),'utf8')
            with patch('sales_map.call',return_value=change) as send:
                result=sales_map.run(src,out,dict(model='test',effort='low',refine_map_job='prior'),'secret',lambda **e:None,lambda:False)
                self.assertEqual(send.call_count,1)
            self.assertEqual(result['segment_review'],base['segment_review']);self.assertEqual(result['groups'],base['groups'])
            self.assertTrue((out/'map-patch.json').exists());self.assertTrue((out/'content-groups.json').exists())
            self.assertEqual((src/'transcript.json').read_bytes(),raw);self.assertFalse((out/'plan.json').exists())
