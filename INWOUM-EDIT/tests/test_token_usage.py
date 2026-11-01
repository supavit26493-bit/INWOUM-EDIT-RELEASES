import json,tempfile,unittest
from pathlib import Path
from token_usage import summarize,read

class TokenUsageTests(unittest.TestCase):
    def test_usd_cache_reads_writes_and_reasoning_are_not_double_billed(self):
        row=dict(model='gpt-6.1-sol',usage=dict(input_tokens=1000000,output_tokens=100000,
                 input_tokens_details=dict(cached_tokens=200000,cache_write_tokens=300000),
                 output_tokens_details=dict(reasoning_tokens=90000)))
        cost=summarize([row])['cost']
        self.assertAlmostEqual(cost['usd'],2.77) # 1 + .02 + .75 + 1
        self.assertFalse(cost['partial'])
        row['_pricing_request']=dict(prompt_cache_options=dict(ttl='12h'))
        self.assertAlmostEqual(summarize([row])['cost']['usd'],3.52)

    def test_usd_unknown_and_failed_usage_and_mixed_models(self):
        usage=dict(input_tokens=1000000,output_tokens=1000000,input_tokens_details=dict(cached_tokens=0,cache_write_tokens=0))
        rows=[dict(model='gpt-6.1-sol',usage=usage,status='failed'),dict(model='gpt-6-luna',usage=usage),dict(usage=usage)]
        cost=summarize(rows)['cost'];self.assertAlmostEqual(cost['usd'],12.6)
        self.assertEqual(cost['unpriced_calls'],1);self.assertTrue(cost['partial'])
        self.assertEqual(summarize([])['cost']['usd'],0)
        self.assertIsNone(summarize([dict(usage=usage)])['cost']['usd'])

    def test_usd_mismatched_request_does_not_guess_from_current_config(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);request=dict(model='gpt-6-luna')
            digest=hashlib.sha256(json.dumps(request,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            rows=[dict(stage='map',request_hash=digest,usage=dict(input_tokens=1000000,output_tokens=1000000))]
            (folder/'api-usage.json').write_text(json.dumps(rows),'utf8')
            (folder/'map-request.json').write_text(json.dumps(request),'utf8')
            self.assertAlmostEqual(read(folder)['cost']['usd'],.6)
            (folder/'map-request.json').write_text(json.dumps(dict(model='gpt-6.1-sol')),'utf8')
            self.assertIsNone(read(folder)['cost']['usd'])

    def test_usd_missing_cache_is_disclosed_and_unsupported_tier_not_guessed(self):
        row=dict(model='gpt-6-luna',usage=dict(input_tokens=1,output_tokens=1))
        self.assertTrue(summarize([row])['cost']['assumptions'])
        row['service_tier']='ultrafast'
        self.assertIsNone(summarize([row])['cost']['usd'])

    def test_totals_count_cached_and_reasoning_as_subsets(self):
        rows=[dict(stage='map',usage=dict(input_tokens=100,output_tokens=30,total_tokens=130,
            input_tokens_details=dict(cached_tokens=80),output_tokens_details=dict(reasoning_tokens=20))),
            dict(stage='draft',usage=dict(input_tokens=200,output_tokens=50,total_tokens=250))]
        result=summarize(rows)
        self.assertEqual(result['total_tokens'],380)
        self.assertEqual(result['input_tokens'],300)
        self.assertEqual(result['cached_input_tokens'],80)
        self.assertEqual(result['reasoning_tokens'],20)
        self.assertFalse(result['partial'])

    def test_failed_or_incomplete_usage_still_counts_unknown_stays_visible(self):
        result=summarize([dict(status='failed',usage=dict(input_tokens=10,output_tokens=5,total_tokens=15)),dict(status='sent',usage=None)])
        self.assertEqual(result['total_tokens'],15)
        self.assertEqual(result['unreported_calls'],1)
        self.assertTrue(result['partial'])
        self.assertIsNone(result['calls'][1]['total_tokens'])

    def test_explicit_retry_same_request_counts_each_attempt(self):
        rows=[dict(attempt_id=i,request_hash='same',usage=dict(input_tokens=10,output_tokens=5)) for i in ['a','b']]
        self.assertEqual(summarize(rows)['total_tokens'],30)
        self.assertEqual(summarize(rows+[rows[0]])['total_tokens'],30)

    def test_zero_local_job_and_missing_legacy_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);self.assertEqual(read(folder)['total_calls'],0)
            self.assertEqual(read(folder)['total_tokens'],0)
            (folder/'api-usage.json').write_text(json.dumps([dict(usage={})]),'utf8')
            self.assertEqual(read(folder)['unreported_calls'],1)

    def test_total_only_is_partial_and_not_silently_lost(self):
        r=summarize([dict(usage=dict(total_tokens=42))])
        self.assertEqual(r['total_tokens'],42);self.assertTrue(r['partial'])
