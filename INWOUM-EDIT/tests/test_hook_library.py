import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as E
import hook_library as hooks, runtime_contract as runtime, project, demo
from test_sales_continuity import unit,selection


class HookLibraryTests(unittest.TestCase):
 def fixture(self):
  catalog=[unit('A',['สินค้า','ใช้ดี'],.6),unit('B',['ไปเที่ยว','หยิบก้อนนี้จบ','สบายใจ'],4),
           unit('C',['จริง','จริง','ดีมาก'],8),unit('D',['คุยหลังกล้อง'],10)]
  plan=dict(edits=[dict(name='FULL',selections=[selection('A',0,1)])],source_review=[
   dict(unit_id=u['id'],decision=d,reason='เลือกอีกเทค' if d=='alternative' else d)
   for u,d in zip(catalog,['used','alternative','alternative','production'])])
  raw=dict(hooks=[dict(selection('B',0,2),reason='Hook ท่องเที่ยว'),dict(selection('C',0,2),reason='คำขายเน้นอารมณ์')],note='ทางเลือก')
  return catalog,plan,raw

 def test_only_new_mode_and_same_main_rules(self):
  for style in ['original','natural','natural_v2','sales_2_1']:
   self.assertFalse(hooks.enabled(dict(sales_style=style)))
  with self.assertRaisesRegex(ValueError,'พักโหมด 2.2'):runtime.configure(dict(sales_style='sales_2_2'))
  c=runtime.configure(dict(sales_style='sales_2_1'))
  self.assertFalse(hooks.enabled(c));self.assertEqual(c['audio_track'],1)
  with self.assertRaises(ValueError):runtime.configure(dict(content_mode='single',single_style='sales_2_2'))

 def test_forbidden_unreviewed_and_used_words_are_not_candidates(self):
  catalog,plan,raw=self.fixture()
  rows=hooks.candidate_units(plan,catalog)
  self.assertEqual([r['id'] for r in rows],['B','C'])
  plan['edits'][0]['selections'].append(selection('B',0,0))
  self.assertEqual(hooks.candidate_units(plan,catalog)[0]['available'],[1,2])

 def test_valid_hooks_preserve_main_and_meaningful_reduplication(self):
  catalog,plan,raw=self.fixture();before=copy.deepcopy(plan)
  out=hooks.validate(raw,plan,catalog)
  self.assertEqual(plan,before);self.assertEqual(out['sequence_count'],1)
  self.assertEqual(out['hooks'][1]['text'],'จริงจริงดีมาก')

 def test_reject_used_duplicate_bad_bounds_and_production(self):
  catalog,plan,raw=self.fixture()
  for bad in [dict(selection('A',0,1),reason='ใช้แล้ว'),dict(selection('D',0,0),reason='คุย'),
              dict(selection('B',0,9),reason='ขอบผิด'),dict(selection('B',True,2),reason='ชนิดผิด')]:
   with self.assertRaises(ValueError):hooks.validate(dict(hooks=[bad]),plan,catalog)
  with self.assertRaises(ValueError):hooks.validate(dict(hooks=[raw['hooks'][0]]*2),plan,catalog)

 def test_prices_and_countdowns_rejected_banknotes_allowed(self):
  catalog,plan,raw=self.fixture()
  for text in ['สองร้อยบาท','3 2 1 เริ่ม','แต่ อันนี้ดี']:
   catalog[1]=unit('B',[text],4)
   with self.assertRaises(ValueError):hooks.validate(dict(hooks=[dict(selection('B',0,0),reason='ทดสอบ')]),plan,catalog)
  catalog[1]=unit('B',['หนึ่งใบแดงก็ใช้ได้'],4)
  self.assertEqual(hooks.validate(dict(hooks=[dict(selection('B',0,0),reason='คุ้ม')]),plan,catalog)['sequence_count'],1)

 def test_one_sequence_with_color_boundaries_and_full_coverage(self):
  catalog,plan,raw=self.fixture();plan['hook_library']=hooks.validate(raw,plan,catalog)
  p=dict(duration=600,primary=[dict(start=0,end=600)],cut_style='sales_2_2')
  silence=dict(profile='compact',removed_frames=[],sales_continuity='sales-continuity-v1')
  edits=[];coverage=hooks.extend_export(plan,catalog,silence,p,edits)
  self.assertEqual(len(edits),1);self.assertEqual(edits[0]['sequence_role'],'hook_library')
  self.assertEqual([r['clip_color'] for r in edits[0]['ranges']],list(hooks.COLORS[:2]))
  self.assertEqual(len(coverage['edits']),2);self.assertEqual(len(plan['edits']),1)
  old=dict(p,cut_style='sales_2_1');self.assertEqual(hooks.resolve(plan,catalog,silence,old),(None,None))

 def test_only_library_can_exceed_three_minutes(self):
  p=dict(duration=20000,primary=[dict(start=0,end=20000)],cut_style='sales_2_2')
  lib=dict(name='Hook',sequence_role='hook_library',ranges=[dict(start=0,end=200)])
  project.validate_edits([copy.deepcopy(lib)],p)
  with self.assertRaises(ValueError):project.validate_edits([dict(lib,sequence_role='main')],p)
  with self.assertRaises(ValueError):project.validate_edits([lib,copy.deepcopy(lib)],p)
  with self.assertRaises(ValueError):project.validate_edits([lib],dict(p,cut_style='sales_2_1'))

 def test_local_stutter_cleanup_never_changes_main_or_joins_different_hooks(self):
  catalog,plan,raw=self.fixture();catalog[1]=unit('B',['เป็น','เป็น','พาวเวอร์แบงค์','ที่จบเลย'],4)
  raw['hooks'][0]['last_word']=3
  plan['hook_library']=hooks.validate(raw,plan,catalog);before=copy.deepcopy(plan)
  p=dict(duration=600,primary=[dict(start=0,end=600)],cut_style='sales_2_2')
  edit,_=hooks.resolve(plan,catalog,dict(profile='compact',removed_frames=[],sales_continuity='sales-continuity-v1'),p)
  self.assertEqual(plan,before);self.assertEqual(edit['hooks'][0]['text'],'เป็นพาวเวอร์แบงค์ที่จบเลย')
  self.assertEqual(edit['hooks'][1]['text'],'จริงจริงดีมาก')
  catalog[2]=unit('C',['พลังงาน','ล้วน','ล้วน'],8)
  plan['hook_library']=hooks.validate(raw,plan,catalog)
  edit,_=hooks.resolve(plan,catalog,dict(profile='compact',removed_frames=[]),p)
  self.assertEqual(edit['hooks'][1]['text'],'พลังงานล้วนล้วน')

 def test_actual_xml_one_library_colors_all_tracks_no_markers(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);source,pcm,_=demo.create(root/'media',two_cameras=True)
   p=project.configure_export(project.intake(source),dict(sales_style='sales_2_2'))
   main=dict(name='FULL',ranges=[dict(start=.6,end=1.2)])
   lib=dict(name='รวม Hook สำรอง',sequence_role='hook_library',hooks=[],ranges=[
    dict(start=4,end=4.6,clip_color=hooks.COLORS[0]),dict(start=8,end=8.6,clip_color=hooks.COLORS[1])])
   out=root/'out';out.mkdir();manifest=project.build_outputs(p,source,[main,lib],out,pcm.tobytes())
   seqs=E.parse(out/'EDITS-WITH-USAGE.xml').findall('.//sequence')
   self.assertEqual(len(seqs),3);library=seqs[-1]
   self.assertEqual(len(library.findall('media/audio/track')),2)
   self.assertEqual(len(library.findall('media/video/track')),2)
   self.assertEqual([c.findtext('labels/label2') for c in library.findall('media/audio/track')[0].findall('clipitem')],list(hooks.COLORS[:2]))
   self.assertFalse(library.findall('.//marker'));self.assertFalse(library.findall('.//transitionitem'))
   self.assertEqual(manifest['edits'][-1]['sequence_role'],'hook_library')

 def test_budget_and_batch_guard_cover_22(self):
  import api_budget,batched_sales_draft
  with tempfile.TemporaryDirectory() as tmp:
   f=Path(tmp);(f/'config.json').write_text(json.dumps(dict(sales_style='sales_2_2')))
   self.assertTrue(api_budget.enabled(f));self.assertTrue(batched_sales_draft.enabled(dict(sales_style='sales_2_2')))

 def test_no_extra_call_if_no_candidates_and_old_modes(self):
  import engine
  catalog,plan,raw=self.fixture()
  with tempfile.TemporaryDirectory() as tmp,patch('parallel_requests.call') as call:
   f=Path(tmp);p=engine.Progress(f,lambda **kw:None,lambda:False)
   self.assertIs(hooks.run(plan,catalog,f,dict(sales_style='sales_2_1'),'unused',p),plan)
   result=hooks.run(plan,[],f,dict(sales_style='sales_2_2'),'unused',p)
   self.assertFalse(result['hook_library']['hooks']);call.assert_not_called()


if __name__=='__main__':unittest.main()
