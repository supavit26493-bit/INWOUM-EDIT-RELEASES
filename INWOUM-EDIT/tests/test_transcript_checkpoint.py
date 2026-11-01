import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
import engine

class TranscriptCheckpointTests(unittest.TestCase):
    def test_atomic_save_retries_only_transient_windows_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'timings.json';original=Path.replace;attempts=[]
            def locked(source,target):
                attempts.append(1)
                if len(attempts)<3:raise PermissionError('reader lock')
                return original(source,target)
            with patch.object(Path,'replace',locked),patch('engine.time.sleep'):
                engine.save(p,dict(value='kept'))
            self.assertEqual(len(attempts),3)
            self.assertEqual(json.loads(p.read_text('utf8')),dict(value='kept'))
    def test_review_checkpoint_never_plans_or_exports_and_retains_asr_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);events=[]
            transcript=[dict(start=0,end=1,text='แบบ คำถอดเพี้ยน ไม่กันน้ำ',words=[])]
            cfg=dict(xml='source.xml',mode='ai',model='test',transcription='capcut',stop_after='transcription')
            with patch('engine.prepare',return_value=(dict(duration=100),None,transcript,None)), \
                 patch('engine.cut_map',side_effect=AssertionError('Must stop before cut map')), \
                 patch('engine.export',side_effect=AssertionError('Must not export')), \
                 patch('engine.plan_once',side_effect=AssertionError('Must not plan')):
                result=engine.run(cfg,p,'secret',lambda **e:events.append(e))
            self.assertTrue(result['transcript_review'])
            self.assertIn(transcript[0]['text'],(p/'TRANSCRIPT-REVIEW.md').read_text('utf8'))
            self.assertNotIn('secret',(p/'config.json').read_text('utf8'))
            self.assertFalse((p/'plan.json').exists())
            self.assertEqual(events[-1]['stage'],'done')
