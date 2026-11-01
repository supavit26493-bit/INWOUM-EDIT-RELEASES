import unittest
import subtitle_mode as sub
from test_subtitle_mode import timed

class MaiyamokTests(unittest.TestCase):
    def test_clear_allowlisted_reduplication(self):
        for original,expected in [('จริงจริง','จริง ๆ'),('ค่อยค่อยทำ','ค่อย ๆ ทำ'),
                                  ('ไปเรื่อยเรื่อย','ไปเรื่อย ๆ'),('เด็กเด็ก','เด็ก ๆ'),('สาว สาว','สาว ๆ')]:
            self.assertEqual(sub.maiyamok_text(original),expected,original)

    def test_ambiguous_words_stutters_names_and_triples_stay(self):
        for text in ['คนคนนี้','ผมผมจะบอกว่า','ดีนะดีนะ','ดีนะดีเลย','สาวสาวก','จริงจริงจริง','ที่ที่ทำงาน']:
            self.assertEqual(sub.maiyamok_text(text),text)

    def test_existing_marks_are_idempotent(self):
        for text in ['จริงๆครับ','เด็ก ๆ เล่นกัน','ไปเรื่อยๆ']:
            normal=sub.maiyamok_text(text)
            self.assertEqual(sub.maiyamok_text(normal),normal)
        self.assertEqual(sub.maiyamok_text('จริงๆครับ'),'จริง ๆ ครับ')

    def test_spelling_only_keeps_genuine_source_edges(self):
        source=[timed('จริงจริงวันนี้เราจะค่อยค่อยทำไปเรื่อยเรื่อยครับ')]
        changes=[];result=sub.split(source,changes=changes)
        self.assertTrue(changes)
        self.assertIn('จริง ๆ', ''.join(c['text'] for c in result))
        self.assertEqual(result[0]['start'],source[0]['start'])
        self.assertEqual(result[-1]['end'],source[0]['end'])
        self.assertTrue(all(sub.count(c['text'])<=45 for c in result))
        self.assertEqual(sub.split(source,maiyamok=False)[0]['text'],source[0]['text'])

    def test_long_pause_is_not_rewritten_as_reduplication(self):
        rows=[timed('จริง',0),timed('จริง',3)]
        result=sub.split(rows)
        self.assertEqual([c['text'] for c in result],['จริง','จริง'])

    def test_no_join_without_space_after_mark(self):
        result,_=sub.maiyamok_atoms([dict(start=0,end=1,text='จริงจริง'),dict(start=1,end=2,text='เรามาลอง')])
        self.assertEqual(''.join(c['text'] for c in result),'จริง ๆ เรามาลอง')
