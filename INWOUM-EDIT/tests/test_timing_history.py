import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import engine


class TimingHistoryTests(unittest.TestCase):
    def test_resume_keeps_elapsed_history_without_counting_paused_wall_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            engine.save(folder/'timings.json',dict(elapsed_seconds=120,stage_seconds={'planning':120}))
            with patch('engine.time.perf_counter',side_effect=[1000,1000,1002,1002]):
                progress=engine.Progress(folder,lambda **kw:None,lambda:False)
                with progress.stage('export','export'):pass
            timing=json.loads((folder/'timings.json').read_text('utf8'))
            self.assertEqual(timing['elapsed_seconds'],2)
            self.assertEqual(timing['cumulative_seconds'],122)
            engine.Progress(folder,lambda **kw:None,lambda:False)
            history=json.loads((folder/'timing-history.json').read_text('utf8'))
            self.assertEqual(sum(r['elapsed_seconds'] for r in history),122)
