import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import story_editorial as story
import engine


class AuthorizedResumeTests(unittest.TestCase):
    def test_unknown_attempt_requires_one_time_approval_and_keeps_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);payload={'test':'saved-request'}
            ledger=folder/'api-usage.json'
            engine.save(ledger,[dict(stage='self-recovery',request_hash=story.fingerprint(payload),status='sent',usage=None)])
            with patch('story_editorial.plan_once',side_effect=RuntimeError('unknown')) as send:
                with self.assertRaises(ValueError):story.call(payload,folder,'key','self-recovery',max_calls=6)
                self.assertEqual(send.call_count,0)
                story.authorize_unknown_retry(folder)
                with self.assertRaises(RuntimeError):story.call(payload,folder,'key','self-recovery',max_calls=6)
                self.assertEqual(send.call_count,1)
                with self.assertRaises(ValueError):story.call(payload,folder,'key','self-recovery',max_calls=6)
                self.assertEqual(send.call_count,1)
            rows=json.loads(ledger.read_text(encoding='utf8'))
            self.assertEqual(len(rows),2)
            self.assertIn('retry_authorized_at',rows[0])
            self.assertNotIn('retry_authorized_at',rows[1])

    def test_total_cap_includes_unknown_requests_but_allows_cached_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);payload={'test':1}
            engine.save(folder/'api-usage.json',[dict(stage='x',request_hash=str(i),status='sent',usage=None) for i in range(6)])
            with patch('story_editorial.plan_once') as send:
                with self.assertRaises(ValueError):story.call(payload,folder,'key','self-final',max_calls=6)
                send.assert_not_called()
            engine.save(folder/'self-final-response.json',dict(request_hash=story.fingerprint(payload),response={'saved':True}))
            self.assertEqual(story.call(payload,folder,'','self-final',max_calls=6),{'saved':True})
