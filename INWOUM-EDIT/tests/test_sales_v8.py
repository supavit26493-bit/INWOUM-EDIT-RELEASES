import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import sales_map,sales_draft,quality_v8,runtime_contract,engine

class V8Tests(unittest.TestCase):
    def test_map_and_patch_send_one_current_contract_without_legacy_policy_reads(self):
        source,story=self.example()
        with patch('fast_editorial.rules_text',side_effect=AssertionError('legacy rules must not load')):
            requests=[sales_map.make_request(source,dict(model='gpt-6-luna')),
                sales_map.refinement_request(source,dict(model='gpt-6-luna'),story)[0]]
        for request in requests:
            self.assertTrue(request['instructions'].startswith(runtime_contract.rules()))
            self.assertEqual(request['instructions'].count('SALES EDITORIAL CONTRACT'),1)
            self.assertNotIn('EDITORIAL-FAST-v3',request['instructions'])
            self.assertNotIn('require a new factual claim',request['instructions'])
    def example(self):
        source=[dict(id='T001',start=0,end=2,text='ซื้อให้แฟนตกปลา'),dict(id='T002',start=2,end=4,text='ลองดึงแล้วพองตัว')]
        answer=dict(groups=[dict(id='G1',title='ผู้ซื้อ',segment_ids=['T001','T002'])],hooks=[dict(id='H1',segment_id='T001',opening_quote='ซื้อให้แฟน',sales_angle='เป็นห่วงคนใกล้ตัว')],full_hook_id='H1',
            full_outline=[dict(role='hook',segment_ids=['T001'],budget_seconds=2,purpose='เปิด'),dict(role='demo',segment_ids=['T002'],budget_seconds=2,purpose='ผล'),dict(role='close',segment_ids=['T001'],budget_seconds=2,purpose='ปิด')],
            versions=[dict(id='V1',title='อีกเทค',kind='alternate_take',hook_id='H1',group_ids=['G1'],segment_ids=['T001','T002'],payoff='ดูผล',review_condition='เทียบอารมณ์')],
            segment_review=[dict(segment_id=s['id'],roles=['buyer'] if i==0 else ['demo'],disposition='usable',reason='สาระ',excluded_quotes=[]) for i,s in enumerate(source)],continuity_links=[],review_note='รอตรวจ')
        return source,answer
    def test_no_usage_quota_and_no_hidden_exclusion_or_double_count(self):
        r=quality_v8.coverage(100,[(0,30),(10,40)])
        self.assertEqual(r['unique_used_seconds'],40);self.assertIsNone(r['usage_target'])
        pending=dict(start=40,end=100,reason='โมเดลเสนอพัก',confirmed_by_user=False)
        self.assertEqual(quality_v8.coverage(100,[(0,40)],[pending])['eligible_seconds'],100)
        pending['confirmed_by_user']=True
        self.assertEqual(quality_v8.coverage(100,[(0,40)],[pending])['eligible_seconds'],40)
    def test_prices_distinguish_notes_technical_units_and_forbidden_amounts(self):
        for text in ['ราคาไม่แพง','ราคาหนึ่งใบแดง สองใบเทา','ราคา 1 ใบแดง 2 ใบเทา','24 กรัม 200 วัตต์']:
            self.assertFalse(quality_v8.price_findings(text),text)
        for text in ['สองร้อยบาท','หลักหมื่น','ราคา 200']:
            self.assertTrue(quality_v8.price_findings(text),text)
    def test_cut_clusters_are_review_findings_not_automatic_word_deletion(self):
        r=[dict(start=i,end=i+.2) for i in range(4)];before=copy.deepcopy(r)
        self.assertTrue(any(x['issue']=='dense_cut_cluster' for x in quality_v8.cut_findings(r)));self.assertEqual(r,before)
    def test_real_hook_and_plan_ids_and_full_bound_checked(self):
        source,answer=self.example();sales_map.validate(answer,source)
        for change in [lambda a:a['hooks'][0].update(opening_quote='แต่งคำขายใหม่'),lambda a:a['segment_review'].pop(),lambda a:a['full_outline'][1].update(budget_seconds=181),lambda a:a['versions'][0].update(segment_ids=['T999']),lambda a:a['versions'][0].update(review_condition='')]:
            bad=copy.deepcopy(answer);change(bad)
            with self.assertRaises(ValueError):sales_map.validate(bad,source)
    def test_runtime_detects_mixed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'server.py';p.write_text('version8','utf8')
            (root/'INSTALL-MANIFEST.json').write_text(json.dumps(dict(files={'server.py':hashlib.sha256(p.read_bytes()).hexdigest()})),'utf8')
            self.assertEqual(runtime_contract.status(root)['integrity'],'verified')
            p.write_text('restored old file','utf8');self.assertEqual(runtime_contract.status(root)['integrity'],'mismatch')
    def test_filler_is_in_verbatim_quote_and_only_proposed_trim_is_separate(self):
        source,answer=self.example();source[0]['text']='ซื้อให้แฟนแบบตกปลา'
        answer['hooks'][0].update(opening_quote='ซื้อให้แฟนแบบตกปลา',trim_note='พิจารณาตัดแบบเมื่อไม่มีความหมาย')
        sales_map.validate(answer,source)
        bad=copy.deepcopy(answer);bad['hooks'][0]['opening_quote']='ซื้อให้แฟนตกปลา'
        with self.assertRaises(ValueError):sales_map.validate(bad,source)
        self.assertEqual(sales_map.audit(bad,source)[0]['issue'],'quote_not_verbatim')
    def test_known_output_limit_retry_has_explicit_budget_and_no_unknown_retry(self):
        source,_=self.example()
        request=sales_map.make_request(source,dict(model='test',effort='low',map_output_tokens=16000))
        self.assertEqual(request['reasoning']['effort'],'low');self.assertEqual(request['max_output_tokens'],16000)
        with self.assertRaises(ValueError):sales_map.make_request(source,dict(model='test',map_output_tokens=999999))
    def test_duplicate_full_blocked_and_unassigned_opportunities_remain_visible(self):
        source,answer=self.example();answer['versions'][0].update(title='FULL รีวิวครบ')
        with self.assertRaisesRegex(ValueError,'FULL'):sales_map.validate(answer,source)
        source.append(dict(id='T003',start=4,end=6,text='อีกมุมขาย'))
        answer['segment_review'].append(dict(segment_id='T003',roles=['hook'],disposition='mixed',reason='มีคำราคาท้าย',excluded_quotes=[]))
        findings=sales_map.audit(answer,source)
        self.assertTrue(any(r['issue']=='opportunity_without_version' and r['segment_id']=='T003' for r in findings))
    def test_failed_response_kept_readable_without_approved_map_or_retry(self):
        source,answer=self.example();answer['hooks'][0]['opening_quote']='พูดที่ไม่มีในต้นฉบับ'
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'source';p.mkdir();out=Path(tmp)/'out';out.mkdir()
            (p/'transcript.json').write_text(json.dumps(source),'utf8')
            with patch('sales_map.call',return_value=answer) as send:
                with self.assertRaises(ValueError):sales_map.run(p,out,dict(model='test'),'secret',lambda **e:None,lambda:False)
                self.assertEqual(send.call_count,1)
            self.assertTrue((out/'LUNA-CANDIDATE-REVIEW.md').exists())
            self.assertFalse((out/'CONTENT-GROUPS-REVIEW.md').exists());self.assertFalse((out/'content-groups.json').exists())
    def test_api_planning_is_source_complete_no_audio_no_export_and_one_call(self):
        source,answer=self.example()
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'source';p.mkdir();out=Path(tmp)/'out';out.mkdir()
            raw=json.dumps([dict(start=s['start'],end=s['end'],text=s['text']) for s in source],ensure_ascii=False).encode();(p/'transcript.json').write_bytes(raw)
            with patch('sales_map.call',return_value=answer) as call:
                sales_map.run(p,out,dict(model='gpt-6-luna',effort='medium'),'secret',lambda **e:None,lambda:False)
            self.assertEqual(call.call_count,1)
            request=call.call_args.args[0];self.assertEqual(len(json.loads(request['input'])['segments']),2)
            self.assertNotIn('EDITORIAL-LESSONS',request['instructions']);self.assertIn('NO required usage',request['instructions'])
            self.assertEqual((p/'transcript.json').read_bytes(),raw)
            self.assertNotIn('secret',(out/'config.json').read_text('utf8'))
            for file in ('timeline.wav','plan.json','latest.json'):self.assertFalse((out/file).exists())

    def test_approved_map_binding_and_draft_stop_before_export(self):
        source,story=self.example()
        catalog=[dict(id='A',start=0,end=4,text='ซื้อให้แฟนตกปลา ลองดึงแล้วพองตัว',protected=False,
            words=[dict(start=0,end=2,text='ซื้อให้แฟนตกปลา'),dict(start=2,end=4,text='ลองดึงแล้วพองตัว')])]
        raw=dict(edits={mid:dict(name=mid,purpose='ครบเรื่อง',
            selections=[dict(unit_id='A',first_word=0,last_word=1)]) for mid in ['FULL','V1']},unused=[],review_note='ตรวจฟังต่อ')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);parent=root/'map';parent.mkdir();folder=root/'draft';folder.mkdir()
            transcript=json.dumps(source).encode();(folder/'transcript.json').write_bytes(transcript)
            (parent/'grouping-source.json').write_text(json.dumps(dict(transcript_sha256=hashlib.sha256(transcript).hexdigest(),segments=source)),encoding='utf8')
            (parent/'content-groups.json').write_text(json.dumps(story),encoding='utf8')
            (parent/'rules-used.txt').write_text(runtime_contract.rules(),encoding='utf8')
            config=dict(approved_story_map_job='map',model='gpt-6-luna',effort='medium')
            cuts=[dict(ranges=[dict(start=0,end=4)]) for _ in range(2)]
            progress=engine.Progress(folder,lambda **e:None,lambda:False)
            with patch('sales_draft.call',return_value=raw) as send,patch('sales_draft.resolve_plan',return_value=cuts),patch('engine.export') as export,patch('fast_editorial.rules_text',side_effect=AssertionError('legacy rules must not load')):
                plan=sales_draft.run(catalog,folder,config,'secret',progress,dict(duration=200),{})
                self.assertEqual([e['map_id'] for e in plan['edits']],['FULL','V1'])
                self.assertEqual(send.call_count,1);export.assert_not_called()
                request=send.call_args.args[0];data=json.loads(request['input'])
                self.assertEqual(data['approved_story_map']['versions'],story['versions'])
                self.assertNotIn('source_segments',data);self.assertNotIn('segment_review',data['approved_story_map'])
                self.assertEqual(data['source_bindings'],[dict(id=s['id'],unit_ids=['A']) for s in source])
                self.assertIn('NO required usage',request['instructions'])
                self.assertEqual(request['instructions'].count('SALES EDITORIAL CONTRACT'),1)
                self.assertTrue((folder/'DRAFT-REVIEW.md').exists());self.assertFalse((folder/'latest.json').exists())
                (folder/'transcript.json').write_text('changed','utf8');send.reset_mock()
                with self.assertRaisesRegex(ValueError,'ข้อความไม่ตรง'):sales_draft.run(catalog,folder,config,'secret',progress,dict(duration=200),{})
                send.assert_not_called()
