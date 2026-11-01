import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import sales_map,runtime_contract
from parallel_requests import call
import test_sales_v8 as fixtures

class MapReferenceTests(unittest.TestCase):
    def fixture(self):
        source,answer=fixtures.V8Tests().example()
        for section in ('groups','versions','full_outline'):
            row=answer[section][0];row['segment_ids']+=row['segment_ids'][:1]
        return source,answer

    def test_default_reference_catalogs_unique_without_changing_story_or_raw_reply(self):
        source,raw=self.fixture();before=copy.deepcopy(raw)
        fixed,changes=sales_map.canonicalize_reference_catalogs(raw,source,dict(sales_style='natural'))
        sales_map.validate(fixed,source)
        self.assertEqual(raw,before);self.assertEqual(len(changes),3)
        self.assertEqual(fixed['hooks'],raw['hooks']);self.assertEqual(fixed['continuity_links'],raw['continuity_links'])
        self.assertEqual([b['role'] for b in fixed['full_outline']],[b['role'] for b in raw['full_outline']])
        for section in ('groups','versions','full_outline'):
            self.assertEqual(fixed[section][0]['segment_ids'],list(dict.fromkeys(raw[section][0]['segment_ids'])))
        again,audit=sales_map.canonicalize_reference_catalogs(fixed,source,dict(sales_style='natural'))
        self.assertEqual(again,fixed);self.assertEqual(audit,[])

    def test_original_keeps_prior_validation_and_response(self):
        source,raw=self.fixture()
        fixed,changes=sales_map.canonicalize_reference_catalogs(raw,source,dict(sales_style='original'))
        self.assertIs(fixed,raw);self.assertEqual(changes,[])
        with self.assertRaises(ValueError):sales_map.validate(fixed,source)

    def test_empty_unknown_and_ordered_dependency_duplicates_stay_invalid(self):
        source,base=fixtures.V8Tests().example()
        for bad_refs in ([],['missing'],['T001','missing','T001']):
            raw=copy.deepcopy(base);raw['versions'][0]['segment_ids']=bad_refs
            fixed,_=sales_map.canonicalize_reference_catalogs(raw,source,dict(sales_style='natural'))
            self.assertEqual(fixed['versions'][0]['segment_ids'],bad_refs)
            with self.assertRaises(ValueError):sales_map.validate(fixed,source)
        raw=copy.deepcopy(base);raw['continuity_links']=[dict(segment_ids=['T001','T002','T001'],reason='ordered')]
        fixed,_=sales_map.canonicalize_reference_catalogs(raw,source,dict(sales_style='natural'))
        self.assertEqual(fixed,raw)
        with self.assertRaisesRegex(ValueError,'เหตุการณ์ต่อเนื่อง'):sales_map.validate(fixed,source)

    def test_completed_duplicate_catalog_avoids_paid_validation_patch(self):
        source,raw=self.fixture()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            for name,data in [('map-candidate.json',raw),('grouping-source.json',dict(segments=source)),('map-validation.json',dict(valid=False,error='duplicate')),('api-usage.json',[dict(status='completed')])]:
                (folder/name).write_text(json.dumps(data,ensure_ascii=False),'utf8')
            before=(folder/'api-usage.json').read_bytes()
            self.assertIsNone(sales_map.validation_retry(folder,dict(sales_style='natural')))
            self.assertEqual((folder/'api-usage.json').read_bytes(),before)

    def test_cached_map_pipeline_completes_planning_without_extra_api_or_xml(self):
        source,raw=self.fixture()
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            transcript=[dict(start=s['start'],end=s['end'],text=s['text']) for s in source]
            (folder/'transcript.json').write_text(json.dumps(transcript,ensure_ascii=False),'utf8')
            cfg=runtime_contract.configure(dict(model='gpt-6-luna',sales_style='natural',auto_export=False))
            _,segments=sales_map.segments_for(folder);request=sales_map.make_request(segments,cfg)
            with patch('parallel_requests.plan_once',return_value=(raw,dict(usage={}))) as send:
                call(request,folder,'test','sales-story-map',max_calls=1)
                self.assertEqual(send.call_count,1)
            reply=(folder/'sales-story-map-response.json').read_bytes()
            with patch('parallel_requests.plan_once',side_effect=AssertionError('No paid retry')):
                result=sales_map.run(folder,folder,cfg,'',lambda **kw:None,lambda:False)
            self.assertEqual(result['hooks'],raw['hooks']);self.assertEqual((folder/'sales-story-map-response.json').read_bytes(),reply)
            self.assertTrue((folder/'map-reference-normalization.json').exists());self.assertFalse(list(folder.glob('*.xml')))
