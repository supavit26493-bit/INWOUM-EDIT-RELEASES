import copy,json,tempfile,unittest
from pathlib import Path
import test_sales_v8 as fixtures
import sales_draft,selection_bounds

class RequiredDraftTests(unittest.TestCase):
    def test_rebuild_reports_follow_selected_words_instead_of_stale_draft(self):
        cat=[dict(id='A',start=0,end=2,text='เรื่องใหม่',protected=False,words=[])]
        plan=dict(edits=[dict(name='คลิป',purpose='เรื่อง',selections=[dict(unit_id='A',first_word=0,last_word=0)])])
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);(folder/'ASSEMBLED-SCRIPTS.md').write_text('บทเก่าที่ไม่ตรงXML','utf8')
            sales_draft.refresh_export_review(folder,plan,cat,[dict(ranges=[dict(start=0,end=2)])],2)
            self.assertIn('เรื่องใหม่',(folder/'ASSEMBLED-SCRIPTS.md').read_text('utf8'))
            self.assertIn('ส่งออกล่าสุด',(folder/'DRAFT-REVIEW.md').read_text('utf8'))
            self.assertEqual(json.loads((folder/'draft-quality.json').read_text('utf8'))['findings'],[])
    def test_every_selected_version_is_a_required_schema_key_and_full_cannot_defer(self):
        _,story=fixtures.V8Tests().example()
        schema=sales_draft.draft_schema(story,{})
        self.assertEqual(schema['properties']['edits']['required'],['FULL','V1'])
        schema=sales_draft.draft_schema(story,{'V1':'ผู้ตรวจพัก'})
        self.assertEqual(schema['properties']['edits']['required'],['FULL'])
        for mid in ('FULL','missing'):
            with self.assertRaises(ValueError):sales_draft.draft_schema(story,{mid:'พัก'})
    def test_cannot_drop_focused_versions_because_full_uses_same_content(self):
        source,story=fixtures.V8Tests().example()
        with self.assertRaisesRegex(ValueError,'ทุกเวอร์ชัน'):
            sales_draft.normalize_draft(dict(edits={'FULL':{}},unused=[]),[],story,source,{})
    def test_unmentioned_unused_reasons_inherited_with_provenance_not_new_ai_claims(self):
        source,story=fixtures.V8Tests().example()
        cat=[dict(id='A',start=0,end=2,text=source[0]['text'],protected=False,words=[]),dict(id='B',start=2,end=4,text=source[1]['text'],protected=False,words=[])]
        raw=dict(edits={mid:dict(name=mid,purpose='เรื่อง',selections=[dict(unit_id='A',first_word=0,last_word=0)]) for mid in ['FULL','V1']},unused=[])
        before=copy.deepcopy(raw);plan,inherited=sales_draft.normalize_draft(raw,cat,story,source,{})
        self.assertEqual(raw,before);self.assertEqual(inherited,{'B':['T002']})
        row=next(r for r in plan['source_review'] if r['unit_id']=='B')
        self.assertEqual(row['reason'],story['segment_review'][1]['reason']);self.assertEqual(row['review_source'],'inherited_story_map')
        self.assertEqual(plan['edits'][0]['map_id'],'FULL')
