import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import api_budget,parallel_requests

class BudgetTests(unittest.TestCase):
 def setup(self,folder):
  (folder/'config.json').write_text(json.dumps(dict(sales_style='sales_2_1')),'utf8')
  return dict(model='gpt-6.1-sol',max_output_tokens=10000,input='test',instructions='test',store=False)
 def test_first_reservation_includes_all_output_and_most_expensive_input(self):
  with tempfile.TemporaryDirectory() as tmp,patch('api_budget.count_input',return_value=1000):
   folder=Path(tmp);req=self.setup(folder);row=api_budget.reserve(req,folder,'fake',[])
   self.assertAlmostEqual(row['budget_reservation_usd'],.105)
 def test_unknown_request_consumes_reserved_budget_and_blocks_before_generation(self):
  with tempfile.TemporaryDirectory() as tmp,patch('api_budget.count_input',return_value=1000):
   folder=Path(tmp);req=self.setup(folder)
   with self.assertRaises(ValueError):api_budget.reserve(req,folder,'fake',[dict(stage='pending',budget_reservation_usd=.40)])
 def test_unknown_legacy_request_and_model_and_tools_fail_closed(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);req=self.setup(folder)
   for request,rows in [(req,[dict(stage='old')]),(dict(req,model='unknown'),[]),(dict(req,tools=[{}]),[])]:
    with self.assertRaises(ValueError):api_budget.reserve(request,folder,'fake',rows)
 def test_actual_usage_releases_unspent_reservation(self):
  with tempfile.TemporaryDirectory() as tmp,patch('api_budget.count_input',return_value=1000):
   folder=Path(tmp);req=self.setup(folder);(folder/'old-request.json').write_text(json.dumps(req),'utf8')
   prior=dict(stage='old',usage=dict(input_tokens=1000,output_tokens=100),budget_reservation_usd=.4)
   self.assertIsNotNone(api_budget.reserve(req,folder,'fake',[prior]))
 def test_no_paid_call_when_counting_fails_or_next_reservation_exceeds_limit(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);req=self.setup(folder)
   with patch('api_budget.count_input',side_effect=ValueError('cannot count')),patch('parallel_requests.plan_once') as send:
    with self.assertRaises(ValueError):parallel_requests.call(req,folder,'fake','first')
    send.assert_not_called()
   with patch('api_budget.count_input',return_value=100001),patch('parallel_requests.plan_once') as send:
    with self.assertRaises(ValueError):parallel_requests.call(req,folder,'fake','first')
    send.assert_not_called()
 def test_multiple_reservations_cannot_exceed_cap_even_when_usage_missing(self):
  with tempfile.TemporaryDirectory() as tmp,patch('api_budget.count_input',return_value=1000),patch('parallel_requests.plan_once',return_value=({},dict(usage={}))):
   folder=Path(tmp);req=self.setup(folder)
   for i in range(4):parallel_requests.call(dict(req,input=str(i)),folder,'fake',str(i))
   with self.assertRaises(ValueError):parallel_requests.call(dict(req,input='five'),folder,'fake','five')
   ledger=json.loads((folder/'api-usage.json').read_text('utf8'))
   self.assertLessEqual(sum(r['budget_reservation_usd'] for r in ledger),.50)
 def test_cached_completed_response_costs_nothing_and_reuses_without_counter(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);req=self.setup(folder)
   with patch('api_budget.count_input',return_value=1000),patch('parallel_requests.plan_once',return_value=({'ok':True},dict(usage={}))) as send:
    parallel_requests.call(req,folder,'fake','test')
    parallel_requests.call(req,folder,'fake','test');self.assertEqual(send.call_count,1)
   with patch('api_budget.count_input',side_effect=AssertionError('no count')):
    self.assertEqual(parallel_requests.call(req,folder,'','test'),{'ok':True})
 def test_generation_uses_standard_tier_and_completed_cache_survives(self):
  with tempfile.TemporaryDirectory() as tmp,patch('api_budget.count_input',return_value=1000),patch('parallel_requests.plan_once',return_value=({},dict(usage={}))) as send:
   folder=Path(tmp);req=self.setup(folder);parallel_requests.call(req,folder,'fake','test')
   self.assertEqual(send.call_args.args[0]['service_tier'],'default')

if __name__=='__main__':unittest.main()
