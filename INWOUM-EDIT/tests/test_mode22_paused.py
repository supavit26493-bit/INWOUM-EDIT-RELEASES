import unittest
from unittest.mock import patch
from pathlib import Path
import runtime_contract as runtime,engine,server,sales_reserve

class PausedModeTests(unittest.TestCase):
 def test_21_retains_original_reserve_contract(self):
  cfg=runtime.configure(dict(sales_style='sales_2_1'))
  self.assertTrue(sales_reserve.enabled(cfg))
  self.assertIn(sales_reserve.INSTRUCTIONS,runtime.rules_for(cfg))
 def test_22_rejected_before_api(self):
  with patch('parallel_requests.plan_once') as paid:
   with self.assertRaisesRegex(ValueError,'พักโหมด 2.2'):runtime.configure(dict(sales_style='sales_2_2'))
   with self.assertRaisesRegex(ValueError,'พักโหมด 2.2'):engine.validate_config(dict(sales_style='sales_2_2'))
   with self.assertRaisesRegex(ValueError,'พักโหมด 2.2'):server.launch(dict(sales_style='sales_2_2'),'fake')
   with self.assertRaisesRegex(ValueError,'พักโหมด 2.2'):engine.add_hook_library(Path('unused'),'fake')
   paid.assert_not_called()
 def test_22_option_and_hook_button_removed(self):
  html=(runtime.ROOT/'web/index.html').read_text('utf8')
  self.assertNotIn("new Option('Mode สำหรับตัดขายของ 2.2",html)
  self.assertNotIn("addHooks.id='addHookLibrary'",html)

if __name__=='__main__':unittest.main()
