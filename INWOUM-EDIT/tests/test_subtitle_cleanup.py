import copy,tempfile,unittest
from pathlib import Path
import subtitle_cleanup as cleanup,subtitle_mode as sub

def words(texts,gap=.05):
    return [dict(text=t,start=i*(.3+gap),end=i*(.3+gap)+.3) for i,t in enumerate(texts)]

class SubtitleCleanupTests(unittest.TestCase):
    def test_off_retains_exact_words_and_does_not_mutate(self):
        raw=words(['โอ้โห','สินค้าดี']);before=copy.deepcopy(raw)
        self.assertEqual(cleanup.clean(raw),(raw,[]));self.assertEqual(raw,before)

    def test_light_hesitation_removed_but_meaningful_context_and_grammar_kept(self):
        raw=words(['เอ่อ','แบบ','นี้','คือ','ดี','ก็','ได้','นะ'])
        out,changes=cleanup.clean(raw,'light')
        self.assertEqual([w['text'] for w in out],['แบบ','นี้','คือ','ดี','ก็','ได้','นะ'])
        self.assertEqual(len(changes),1);self.assertEqual(out[0]['start'],raw[1]['start'])
        for texts in [['โอ๊ย','เจ็บ'],['อ๋อ','เข้าใจแล้ว'],['อ้าว','ทำไม']]:
            self.assertEqual(cleanup.clean(words(texts),'strong')[0],words(texts))

    def test_light_isolated_and_strong_prefix_only(self):
        adjacent=words(['โอ้โห','สวยมาก'])
        self.assertEqual(cleanup.clean(adjacent,'light')[0],adjacent)
        self.assertEqual([w['text'] for w in cleanup.clean(adjacent,'strong')[0]],['สวยมาก'])
        isolated=words(['สินค้า','ว้าว','น่าสนใจ'],gap=.6)
        out,_=cleanup.clean(isolated,'light')
        self.assertEqual([w['text'] for w in out],['สินค้า','น่าสนใจ'])

    def test_exceptions_and_mixed_uncertain_atoms_stay(self):
        raw=words(['โอ้โห','สินค้านี้ดี'])
        self.assertEqual(cleanup.clean(raw,'strong','โอ้โห, ว้าว')[0],raw)
        mixed=words(['โอ้โหสินค้านี้ดี','อะไหล่'])
        self.assertEqual(cleanup.clean(mixed,'strong')[0],mixed)

    def test_invalid_settings_and_empty_output_are_explicit(self):
        for mode,keep in [('unknown',''),('light',None),('light','ก'*501)]:
            with self.assertRaises(ValueError):cleanup.validate(mode,keep)
        with self.assertRaisesRegex(ValueError,'ไม่มีข้อความ'):cleanup.clean(words(['เอ่อ']),'light')

    def test_split_uses_retained_word_timing_and_preserves_raw_transcript(self):
        raw=words(['เอ่อ','สินค้า','นี้','ดี','มาก'])
        transcript=[dict(start=0,end=raw[-1]['end'],text=''.join(w['text'] for w in raw),words=raw)]
        before=copy.deepcopy(transcript);changes=[]
        cues=sub.split(transcript,cleanup='light',cleanup_changes=changes)
        self.assertNotIn('เอ่อ',''.join(c['text'] for c in cues))
        self.assertEqual(cues[0]['start'],raw[1]['start']);self.assertEqual(cues[-1]['end'],raw[-1]['end'])
        self.assertEqual(transcript,before);self.assertEqual(len(changes),1)
        self.assertTrue(all(sub.count(c['text'])<=45 for c in cues))
        with tempfile.TemporaryDirectory() as d:
            metadata=dict(cleanup=dict(mode='light',changes=changes,extra_api_calls=0))
            saved=sub.write_result(Path(d),cues,metadata=metadata)
            self.assertEqual(saved['cleanup'],metadata['cleanup']);self.assertEqual(saved['openai_calls'],0)
