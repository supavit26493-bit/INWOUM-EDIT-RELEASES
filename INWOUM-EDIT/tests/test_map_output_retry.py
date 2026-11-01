import io,json,tempfile,unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import sales_map,model_catalog

class MapOutputRetryTests(unittest.TestCase):
    def test_only_one_app_can_listen_on_one_port(self):
        import server
        first=server.LocalHTTPServer(('127.0.0.1',0),server.Handler)
        try:
            with self.assertRaises(OSError):server.LocalHTTPServer(first.server_address,server.Handler)
        finally:first.server_close()
    def test_retry_endpoint_creates_new_map_job_from_original_transcript_job(self):
        import server
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'original';folder.mkdir()
            (folder/'config.json').write_text(json.dumps(dict(editorial_workflow='sales-v8',model='gpt-6.1-sol',effort='medium',grouping_source='original')))
            (folder/'grouping-source.json').write_text('{}')
            (folder/'api-usage.json').write_text(json.dumps([dict(status='failed',incomplete_reason='max_output_tokens',usage=dict(output_tokens=11000))]))
            body=json.dumps(dict(id='original',api_key='test-key')).encode();responses=[]
            handler=SimpleNamespace(auth=lambda:None,headers={'Content-Length':str(len(body))},rfile=io.BytesIO(body),path='/api/retry',send_json=lambda *args:responses.append(args))
            with patch('server.JOB_ROOT',root),patch('server.JOBS',{'original':{'status':'error'}}),patch('server.launch',return_value=dict(id='new-map')) as launch:
                server.Handler.do_POST(handler)
            self.assertEqual(responses,[(dict(id='new-map'),)])
            self.assertEqual(launch.call_args.kwargs,dict(action='group_preview',source_job='original'))
            self.assertEqual(launch.call_args.args[0]['map_output_tokens'],24000)
            self.assertEqual(launch.call_args.args[0]['effort'],'medium')
    def test_initial_map_budget_24000_and_model_cap_still_applied(self):
        source=[dict(id='T001',start=0,end=1,text='ทดสอบ')]
        req=sales_map.make_request(source,dict(model='gpt-6.1-sol'))
        self.assertEqual(req['max_output_tokens'],24000)
        req['model']='gpt-4o-mini'
        self.assertEqual(model_catalog.prepare_request(req)['max_output_tokens'],16384)
    def test_original_main_run_is_recognized_without_group_preview_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);(folder/'grouping-source.json').write_text('{}')
            self.assertTrue(sales_map.is_map_job(dict(editorial_workflow='sales-v8'),folder))
            self.assertFalse(sales_map.is_map_job(dict(editorial_workflow='sales-v8',approved_story_map_job='map'),folder))
    def test_known_limit_retry_keeps_model_effort_and_does_not_send_or_edit_source(self):
        with tempfile.TemporaryDirectory() as tmp,patch('sales_map.call') as send:
            folder=Path(tmp);rows=[dict(status='failed',incomplete_reason='max_output_tokens',usage=dict(output_tokens=11000))]
            ledger=folder/'api-usage.json';ledger.write_text(json.dumps(rows));original=ledger.read_bytes()
            cfg=dict(model='gpt-6.1-sol',effort='medium',grouping_source='original')
            retry=sales_map.output_limit_retry(folder,cfg)
            self.assertEqual(retry['map_output_tokens'],24000)
            self.assertEqual(retry['model'],cfg['model']);self.assertEqual(retry['effort'],'medium')
            self.assertNotIn('known_output_retry',cfg);self.assertEqual(ledger.read_bytes(),original);send.assert_not_called()
            self.assertIsNone(sales_map.output_limit_retry(folder,retry))
            rows[0]['usage']['output_tokens']=24000;ledger.write_text(json.dumps(rows))
            self.assertEqual(sales_map.output_limit_retry(folder,cfg)['map_output_tokens'],32768)
    def test_unknown_result_and_other_failure_never_authorize_output_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            for row in (dict(status='sent'),dict(status='failed',incomplete_reason='content_filter'),dict(status='completed')):
                (folder/'api-usage.json').write_text(json.dumps([row]))
                self.assertIsNone(sales_map.output_limit_retry(folder,dict(model='gpt-6.1-sol')))
    def test_already_at_model_output_cap_does_not_offer_same_size_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);(folder/'api-usage.json').write_text(json.dumps([dict(status='failed',incomplete_reason='max_output_tokens',usage=dict(output_tokens=16384))]))
            with self.assertRaisesRegex(ValueError,'เพดาน'):
                sales_map.output_limit_retry(folder,dict(model='gpt-4o-mini'))
