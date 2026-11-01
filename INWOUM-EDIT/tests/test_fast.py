import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave
import xml.etree.ElementTree as E

import numpy as np
import bootstrap
import engine
from demo import create
from audio_io import write_pcm, extract_audio
from fast_logic import detect_silence, make_catalog, resolve_plan, full_selection, merge, subtract, timed_words, stitch_fragments
import coverage_report
from project import intake, prepare_source, build_outputs, verify_sequence
import planner


class AudioTests(unittest.TestCase):
    def test_natural_repairs_tiny_piece_inside_clause_but_never_crosses_omitted_words(self):
        project=dict(duration=150,primary=[dict(start=0,end=150)])
        unit=dict(id='A',start=0,end=3,text='คำพูด',words=[],protected=False)
        plan=dict(edits=[dict(name='คลิป',selections=[dict(unit_id='A',first_word=0,last_word=0)])])
        silence=dict(profile='compact',removed_frames=[(50,80),(85,130)])
        compact=resolve_plan(copy.deepcopy(plan),[unit],silence,project)[0]['ranges']
        silence['profile']='natural'
        natural=resolve_plan(copy.deepcopy(plan),[unit],silence,project)[0]['ranges']
        self.assertEqual(len(compact),3);self.assertEqual(len(natural),2)
        self.assertGreaterEqual(min(r['end']-r['start'] for r in natural),.24)
        unit.update(words=[dict(start=0,end=1,text='ก่อน'),dict(start=1,end=1.6,text='ห้ามคืน'),dict(start=1.6,end=3,text='หลัง')])
        plan['edits'][0]['selections']=[dict(unit_id='A',first_word=0,last_word=0),dict(unit_id='A',first_word=2,last_word=2)]
        silence.update(removed_frames=[],policy='vad-negative-adaptive-v3')
        ranges=resolve_plan(plan,[unit],silence,project)[0]['ranges']
        self.assertTrue(all(r['end']<=1 or r['start']>=1.6 for r in ranges))
    def test_short_syllable_is_joined_not_deleted(self):
        self.assertEqual(stitch_fragments([(0, 50), (65, 71), (100, 160)]), [(0, 71), (100, 160)])
        self.assertEqual(stitch_fragments([(0, 50), (80, 86), (92, 130)]), [(0, 50), (80, 130)])
        self.assertEqual(stitch_fragments([(0, 50), (100, 103)]), [(0, 50), (100, 103)])
    def test_internal_pause_inside_thai_phrase_and_stretched_token_is_cut(self):
        pcm = np.zeros(3 * 16000, dtype=np.int16)
        pcm[1600:16000] = 6000
        pcm[25600:36800] = 6000
        transcript = [dict(start=.1, end=2.3, text='พูดแล้ว', words=[
            dict(start=.1, end=1.6, text='พูด'), dict(start=1.6, end=2.3, text='แล้ว')])]
        result = detect_silence(pcm, transcript, vad_speech=[(.1, 1), (1.6, 2.3)])
        internal = [(a, b) for a, b in result['removed_frames'] if a >= 50 and b <= 80]
        self.assertEqual(internal, [(53, 78)])  # 0.6s pause -> 0.10s retained, not 0.6s.
        self.assertEqual(timed_words(transcript[0])[0]['text'], 'พูดแล้ว')

    def test_minimum_gap_is_measured_before_edge_padding(self):
        pcm = np.full(3 * 16000, 6000, dtype=np.int16)
        pcm[16000:20800] = 0  # exactly 0.30s
        transcript = [dict(start=0, end=3, text='ก่อน หลัง', words=[
            dict(start=0, end=1, text='ก่อน'), dict(start=1.3, end=3, text=' หลัง')])]
        result = detect_silence(pcm, transcript, vad_speech=[(0, 1), (1.3, 3)])
        self.assertEqual(result['removed_frames'], [(53, 63)])

    def test_vad_positive_soft_speech_and_protected_internal_gap_are_kept(self):
        pcm = np.zeros(3 * 16000, dtype=np.int16)
        pcm[1600:16000] = 6000
        pcm[25600:36800] = 6000
        transcript = [dict(start=.1, end=2.3, text='พูดแล้ว', words=[
            dict(start=.1, end=2.3, text='พูดแล้ว')])]
        result = detect_silence(pcm, transcript, protected=[(1, 1.6)], vad_speech=[(.1, 1), (1.6, 2.3)])
        for a, b in result['removed_frames']:
            self.assertFalse(max(a, 50) < min(b, 80))
        result = detect_silence(pcm, transcript, vad_speech=[(.1, 2.3)])
        for a, b in result['removed_frames']:
            self.assertFalse(max(a, 5) < min(b, 115))

    def test_entirely_soft_asr_phrase_is_not_erased_when_vad_misses_it(self):
        pcm = np.full(3 * 16000, 12, dtype=np.int16)
        transcript = [dict(start=.5, end=2.5, text='เสียงเบา', words=[
            dict(start=.5, end=2.5, text='เสียงเบา')])]
        result = detect_silence(pcm, transcript, vad_speech=[])
        for a, b in result['removed_frames']:
            self.assertFalse(max(a, 25) < min(b, 125))

    def test_thai_zero_duration_vowels_are_not_dropped_or_cut_separately(self):
        segment = dict(start=0, end=2, text='มีกี่คน ครับ', words=[
            dict(start=0, end=.1, text='ม'), dict(start=.1, end=.1, text='ี'),
            dict(start=.1, end=.2, text='ก'), dict(start=.2, end=.2, text='ี่'),
            dict(start=.2, end=1, text='คน'), dict(start=1.2, end=2, text=' ครับ')])
        groups = timed_words(segment)
        self.assertEqual([w['text'] for w in groups], ['มีกี่คน', ' ครับ'])
        self.assertEqual(groups[0]['end'], 1)
        segment['text'] = 'alignment ไม่ตรง'
        self.assertEqual(timed_words(segment), [])

    def test_quiet_speech_and_protected_action_survive(self):
        pcm = np.zeros(6 * 16000, dtype=np.int16)
        pcm[16000:32000] = 12  # soft voice, below energy threshold
        transcript = [dict(start=1, end=2, text='คำเบา', words=[dict(start=1, end=2, text='คำเบา')])]
        result = detect_silence(pcm, transcript, protected=[(3.0, 4.0)])
        removed = result['removed_frames']
        self.assertTrue(removed)
        for a, b in removed:
            self.assertFalse(max(a, 44) < min(b, 109))
            self.assertFalse(max(a, 150) < min(b, 200))
        self.assertGreater(result['removed_seconds'], 1)

    def test_vad_preserves_speech_missing_from_asr_and_cuts_loud_non_speech(self):
        pcm = np.full(6 * 16000, 2000, dtype=np.int16)
        transcript = [dict(start=1, end=2, text='พูด', words=[])]
        result = detect_silence(pcm, transcript, vad_speech=[(1, 2), (3, 4)])
        self.assertGreater(result['removed_seconds'], 1)
        for a, b in result['removed_frames']:
            self.assertFalse(max(a, 150) < min(b, 200))

    def test_profiles_change_only_local_map(self):
        pcm = np.zeros(4 * 16000, dtype=np.int16)
        transcript = [dict(start=.5, end=1, text='ก', words=[]), dict(start=2, end=3, text='ข', words=[])]
        natural = detect_silence(pcm, transcript, 'natural')
        fast = detect_silence(pcm, transcript, 'fast')
        self.assertGreater(fast['removed_seconds'], natural['removed_seconds'])

    def test_interval_subtraction(self):
        self.assertEqual(merge([(3, 5), (1, 3), (9, 10)]), [(1, 5), (9, 10)])
        self.assertEqual(subtract([(0, 10)], [(2, 4), (6, 8)]), [(0, 2), (4, 6), (8, 10)])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source, self.pcm, self.transcript = create(self.root / 'media', two_cameras=True)
        self.project = intake(self.source)
        self.catalog = make_catalog(self.transcript, self.project['duration'])
        self.silence = detect_silence(self.pcm, self.transcript)

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self):
        return dict(edits=[dict(name='เต็ม', purpose='รวม', selections=[full_selection(u) for u in self.catalog]),
                           dict(name='สลับ Hook', purpose='ทดสอบเรียงใหม่', selections=[full_selection(self.catalog[2]), full_selection(self.catalog[0])])],
                    source_review=[dict(unit_id=u['id'], decision='used', reason='ใช้ในแบบเต็ม') for u in self.catalog])

    def test_adjacent_source_units_do_not_replay_padding_after_silence_subtraction(self):
        catalog = [dict(id='A', start=1, end=2, words=[dict(start=1, end=2, text='ก')], protected=False),
                   dict(id='B', start=2, end=3, words=[dict(start=2, end=3, text='ข')], protected=False)]
        plan = dict(edits=[dict(name='test', selections=[full_selection(u) for u in catalog])])
        silence = dict(profile='fast', removed_frames=[(98, 106)])
        edits = resolve_plan(plan, catalog, silence, self.project)
        spans = [(round(r['start'] * 50), round(r['end'] * 50)) for r in edits[0]['ranges']]
        self.assertEqual(spans, [(45, 98), (106, 157)])

    def test_fragment_healing_does_not_restore_an_editorially_omitted_word(self):
        catalog = [dict(id='A', start=0, end=3, protected=False, words=[dict(start=.1, end=1, text='ก่อน'),
                   dict(start=1.1, end=1.8, text='ไม่เอา'), dict(start=2.2, end=2.3, text='ท้าย')])]
        plan = dict(edits=[dict(name='test', selections=[dict(unit_id='A', first_word=0, last_word=0),
                                                        dict(unit_id='A', first_word=2, last_word=2)])])
        edits = resolve_plan(plan, catalog, dict(profile='fast', removed_frames=[]), self.project)
        self.assertEqual(len(edits[0]['ranges']), 2)
        self.assertLess(edits[0]['ranges'][0]['end'], 1.8)

    def test_coverage_requires_real_decisions_and_identifies_partial_units(self):
        plan = self.plan()
        coverage_report.validate_source_review(plan, self.catalog)
        plan['source_review'].pop()
        with self.assertRaises(ValueError):
            coverage_report.validate_source_review(plan, self.catalog)
        plan = self.plan()
        plan['source_review'][0]['decision'] = 'uncertain'
        with self.assertRaises(ValueError):
            coverage_report.validate_source_review(plan, self.catalog)

    def test_export_camera_offsets_complete_usage_and_pending_review(self):
        out = self.root / 'out'
        out.mkdir()
        source = prepare_source(self.project, out)
        edits = resolve_plan(self.plan(), self.catalog, self.silence, self.project)
        manifest = build_outputs(self.project, source, edits, out, self.pcm.tobytes())
        sequences = E.parse(out / 'EDITS-WITH-USAGE.xml').findall('.//sequence')
        self.assertEqual(len(sequences), 3)
        mapped = sequences[0]
        self.assertEqual(int(mapped.findtext('duration')), 600)
        usage = json.loads((out / 'USAGE.json').read_text(encoding='utf8'))
        self.assertEqual(sum(r['end'] - r['start'] for r in usage), 600)
        self.assertTrue(any(len(r['uses']) == 2 for r in usage))
        self.assertTrue(any(not r['uses'] for r in usage))
        self.assertEqual(len(mapped.findall('marker')), len(usage))
        for seq in sequences:
            verify_sequence(seq)
            for clip in seq.findall('media/video/track')[1].findall('clipitem'):
                # second camera begins at source timeline frame 100, media frame 30
                if seq is mapped:
                    self.assertEqual(int(clip.findtext('in')) - int(clip.findtext('start')), -70)
        self.assertFalse(manifest['final_approved'])
        with wave.open(str(out / 'EDIT_02.wav')) as w:
            actual = w.readframes(w.getnframes())
        expected = b''.join(self.pcm[round(r['start'] * 50) * 320:round(r['end'] * 50) * 320].tobytes() for r in edits[1]['ranges'])
        self.assertEqual(actual, expected)
        review = json.loads((out / 'editorial-review.json').read_text(encoding='utf8'))
        self.assertIn('pending', json.dumps(review))

    def test_invalid_word_ids_duplicate_and_duration_rejected(self):
        for mutate in [lambda p: p['edits'][0]['selections'][0].update(unit_id='FAKE'),
                       lambda p: p['edits'][0]['selections'][0].update(last_word=100),
                       lambda p: p['edits'][0]['selections'][0].update(first_word=True),
                       lambda p: p['edits'][0]['selections'].append(copy.deepcopy(p['edits'][0]['selections'][0]))]:
            plan = self.plan()
            mutate(plan)
            with self.assertRaises(ValueError):
                resolve_plan(plan, self.catalog, self.silence, self.project)

    def test_decoder_uses_selected_source_channel(self):
        try:
            import av
        except ImportError:
            self.skipTest('PyAV not installed')
        stereo = self.root / 'media' / 'stereo.wav'
        values = np.column_stack([self.pcm, -self.pcm]).astype('<i2')
        with wave.open(str(stereo), 'wb') as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(values.tobytes())
        project = copy.deepcopy(self.project)
        project['primary'][0].update(path=str(stereo), source_channel=2)
        out = self.root / 'decoded'
        out.mkdir()
        actual = extract_audio(project, out, lambda **kw: None)
        np.testing.assert_array_equal(actual, -self.pcm)

    def test_ai_one_call_and_rebuild_without_api_or_asr(self):
        out = self.root / 'job'
        out.mkdir()
        prepare_source(self.project, out)
        engine.save(out / 'intake.json', engine.identity(self.project))
        write_pcm(out / 'timeline.wav', self.pcm)
        engine.save(out / 'audio-identity.json', dict(pcm_sha256=hashlib.sha256(self.pcm.tobytes()).hexdigest()))
        engine.save(out / 'transcript.json', self.transcript)
        engine.save(out / 'vad.json', None)
        config = dict(xml=str(self.source), audio_track=1, profile='compact', mode='ai', model='test-model')
        with patch('engine.plan_once', return_value=(self.plan(), dict(usage=dict(input_tokens=10, output_tokens=20)))) as api:
            result = engine.run(config, out, 'secret-not-to-save', prepared=(self.project, self.pcm, self.transcript, None))
            self.assertEqual(api.call_count, 1)
            rebuilt = engine.rebuild(out, dict(profile='fast'), self.plan())
            self.assertEqual(api.call_count, 1)
            self.assertNotEqual(result['revision'], rebuilt['revision'])
            self.assertTrue((out / result['revision'] / 'EDITS-WITH-USAGE.xml').exists())
        for f in out.rglob('*.json'):
            self.assertNotIn('secret-not-to-save', f.read_text(encoding='utf8'))

    def test_resume_and_rebuild_reject_tampered_pcm_and_source(self):
        out = self.root / 'job'
        out.mkdir()
        prepare_source(self.project, out)
        write_pcm(out / 'timeline.wav', self.pcm)
        engine.save(out / 'intake.json', engine.identity(self.project))
        engine.save(out / 'audio-identity.json', dict(pcm_sha256=hashlib.sha256(self.pcm.tobytes()).hexdigest()))
        engine.save(out / 'transcript.json', self.transcript)
        engine.save(out / 'vad.json', None)
        config = dict(xml=str(self.source), mode='local')
        engine.run(config, out, '', prepared=(self.project, self.pcm, self.transcript, None))
        with patch('engine.extract_audio', side_effect=AssertionError('must not extract')):
            engine.run(config, out, '')
        (out / 'source.xml').write_text('changed', encoding='utf8')
        with self.assertRaises(ValueError):
            engine.rebuild(out, {}, self.plan())
        with self.assertRaises(ValueError):
            engine.run(config, out, '')


class APITests(unittest.TestCase):
    def test_text_only_schema_and_no_key_in_request_body(self):
        payload = planner.request_body([], 'chosen-model', 'low', 'งานทดลอง')
        self.assertFalse(payload['store'])
        self.assertEqual(payload['text']['format']['type'], 'json_schema')
        self.assertTrue(payload['text']['format']['strict'])
        self.assertEqual(payload['reasoning']['effort'], 'low')
        self.assertNotIn('input_image', json.dumps(payload))

    def test_no_automatic_network_retry(self):
        import urllib.error
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('offline')) as send:
            with self.assertRaises(RuntimeError):
                planner.plan_once(dict(model='gpt-6-luna'), 'test-key')
            self.assertEqual(send.call_count, 1)


if __name__ == '__main__':
    unittest.main()
