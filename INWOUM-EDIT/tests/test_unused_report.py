import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import unused_report as report

class UnusedReportTests(unittest.TestCase):
 def unit(self,review=None):
  cat=dict(id='U1',start=0,end=3,text='ดีมากราคาแพง',words=[dict(start=0,end=1,text='ดีมาก'),dict(start=1,end=2,text='ราคา'),dict(start=2,end=3,text='แพง')])
  cov=dict(unit_id='U1',start=0,end=3,text=cat['text'],status='unused',review=review)
  return cat,cov
 def build(self,usage,review=None):
  cat,cov=self.unit(review);return report.build(usage,dict(units=[cov]),[cat])
 def usage(self,a,b,ids=()):return dict(start=a*50,end=b*50,uses=[dict(id=i) for i in ids])
 def test_reserve_usage_counts_even_if_coverage_calls_unit_unused(self):
  usage=[self.usage(0,1,['MAIN']),self.usage(1,3,['RESERVE'])];r=self.build(usage,dict(reason='ซ้ำ',decision='duplicate'))
  self.assertEqual(r['rows'],[]);self.assertEqual(r['exported_sequences'],2)
 def test_whole_unit_reason_is_preserved_and_readable(self):
  r=self.build([self.usage(0,3)],dict(reason='เล่าเรื่องเดียวกับเทคที่เลือก แต่พูดติดขัดกว่า',decision='duplicate'))
  p=r['rows'][0]['parts'][0];self.assertEqual(p['evidence'],'saved_review');self.assertEqual(p['category'],'เนื้อหาซ้ำ');self.assertIn('พูดติดขัดกว่า',p['reason'])
 def test_grouping_note_never_becomes_a_rejection_reason(self):
  r=self.build([self.usage(0,3)],dict(reason='แสดงข้อดีสินค้า',decision='alternative',review_source='inherited_story_map'))
  p=r['rows'][0]['parts'][0];self.assertEqual(p['evidence'],'unknown');self.assertIn('ไม่มีเหตุผลเฉพาะ',p['reason']);self.assertIn('แสดงข้อดีสินค้า',p['note'])
 def test_explicit_rejection_in_story_map_can_be_shown_as_saved_reason(self):
  r=self.build([self.usage(0,3)],dict(reason='คุยสินค้าอีกตัว ไม่เกี่ยวกับเรื่องนี้',decision='production',review_source='inherited_story_map'))
  p=r['rows'][0]['parts'][0];self.assertEqual(p['evidence'],'saved_plan_review');self.assertIn('สินค้าอีกตัว',p['reason']);self.assertIn('จัดกลุ่ม',p['note'])
 def test_whole_unit_duplicate_reason_is_not_applied_to_partial_gap(self):
  r=self.build([self.usage(0,1,['MAIN']),self.usage(1,3)],dict(reason='ซ้ำกับอีกเทค',decision='duplicate'))
  p=r['rows'][0]['parts'][0];self.assertEqual(p['evidence'],'unknown');self.assertEqual(p['text'],'ราคาแพง');self.assertNotIn('ซ้ำกับอีกเทค',p['reason'])
 def test_partial_review_applies_only_to_documented_phrase_indices(self):
  review=dict(decision='used',reason='ใช้แล้ว',partial_omission_review=dict(reason='พูดราคาที่กฎไม่อนุญาต',decision='money',omitted_phrase_indices=[1]))
  r=self.build([self.usage(0,1,['MAIN']),self.usage(1,2),self.usage(2,3,['OTHER'])],review)
  p=r['rows'][0]['parts'][0];self.assertEqual(p['evidence'],'saved_partial_review');self.assertEqual(p['text'],'ราคา')
  r=self.build([self.usage(0,2,['MAIN']),self.usage(2,3)],review);self.assertEqual(r['rows'][0]['parts'][0]['evidence'],'unknown')
 def test_cut_into_word_is_disclosed_not_claimed_entire_word_removed(self):
  r=self.build([self.usage(0,.5),self.usage(.5,3,['MAIN'])]);p=r['rows'][0]['parts'][0]
  self.assertIn('บางส่วน',p['note']);self.assertEqual(p['evidence'],'unknown')
 def test_missing_words_or_changed_catalog_shows_full_unit_warning(self):
  cat,cov=self.unit();cat['text']='changed'
  r=report.build([self.usage(0,3)],dict(units=[cov]),[cat]);p=r['rows'][0]['parts'][0]
  self.assertEqual(p['text_scope'],'whole_unit');self.assertIn('ทั้งท่อน',p['note'])
 def test_gap_does_not_claim_silence_and_adjacent_unused_spans_merge(self):
  r=report.build([self.usage(0,1),self.usage(1,2)],dict(units=[]),[])
  self.assertEqual(r['unused_ranges'],1);self.assertEqual(r['unused_seconds'],2);self.assertIn('ไม่ได้ยืนยัน',r['rows'][0]['parts'][0]['note']);self.assertFalse(r['rows'][0]['has_speech'])
 def test_missing_usage_interval_is_not_invented_as_unused(self):
  r=report.build([self.usage(0,1),self.usage(2,3)],{},[]);self.assertEqual(r['unused_seconds'],2);self.assertEqual(r['unused_ranges'],2)
 def test_invalid_ranges_block_report_without_guessing(self):
  for usage in [[self.usage(2,1)],[self.usage(0,2),self.usage(1,3)],[dict(start=0,end=50)]]:
   with self.assertRaises(ValueError):report.build(usage,{},[])
 def test_read_is_local_readonly_and_uses_latest_revision(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);rev=folder/'revision-1';rev.mkdir();cat,cov=self.unit(dict(reason='คุยเตรียมถ่าย',decision='production'))
   files={folder/'latest.json':dict(folder=rev.name),folder/'catalog.json':[cat],rev/'source-coverage.json':dict(units=[cov]),rev/'USAGE.json':[self.usage(0,3)]}
   for p,data in files.items():p.write_text(json.dumps(data),'utf8')
   before={p:p.read_bytes() for p in files}
   with patch('urllib.request.urlopen',side_effect=AssertionError('No network')):
    r=report.read(folder)
   self.assertTrue(r['available']);self.assertEqual(r['revision'],rev.name);self.assertEqual(r['api_calls_added'],0);self.assertEqual(before,{p:p.read_bytes() for p in files})
 def test_revision_escape_is_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);(folder/'latest.json').write_text(json.dumps(dict(folder='../other')))
   with self.assertRaises(ValueError):report.read(folder)

if __name__=='__main__':unittest.main()
