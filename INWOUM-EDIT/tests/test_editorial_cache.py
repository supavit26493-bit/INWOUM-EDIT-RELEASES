import json
from pathlib import Path
import tempfile
import unittest
import editorial_cache


class EditorialCacheTests(unittest.TestCase):
    def setup_folders(self,tmp):
        source=Path(tmp)/'source';folder=Path(tmp)/'target';source.mkdir();folder.mkdir()
        cfg=dict(editorial_workflow='self-edit-v3',model='test',notes='เดิม')
        (source/'config.json').write_text(json.dumps(cfg),encoding='utf8')
        for name in ('audio-identity.json','source-sha256.json'):
            for p in (source,folder):(p/name).write_text('"identity"',encoding='utf8')
        for name in ('self-final-response.json','self-draft-repair-response.json','full-coverage.json'):(source/name).write_text('{}',encoding='utf8')
        (source/'api-usage.json').write_text('[{"status":"sent","stage":"unknown"}]',encoding='utf8')
        return source,folder,dict(cfg,editorial_workflow='self-edit-v4')

    def test_upgrade_preserves_prior_unknown_ledger_and_cached_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,folder,cfg=self.setup_folders(tmp)
            self.assertEqual(editorial_cache.reuse_upgrade(source,folder,cfg),2)
            self.assertEqual((source/'api-usage.json').read_bytes(),(folder/'api-usage.json').read_bytes())
            self.assertEqual(json.loads((folder/'editorial-cache-reuse.json').read_text(encoding='utf8'))['inherited_attempts'],1)

    def test_changed_instructions_or_source_cannot_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,folder,cfg=self.setup_folders(tmp)
            self.assertEqual(editorial_cache.reuse_upgrade(source,folder,dict(cfg,notes='ใหม่')),0)
            (folder/'audio-identity.json').write_text('"changed"',encoding='utf8')
            self.assertEqual(editorial_cache.reuse_upgrade(source,folder,cfg),0)
            self.assertFalse((folder/'api-usage.json').exists())
