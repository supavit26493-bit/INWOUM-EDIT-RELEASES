import copy,tempfile,unittest
from pathlib import Path
import xml.etree.ElementTree as E
import numpy as np
from demo import create
from project import intake,prepare_source,verify_sequence,build_outputs
from default_audio import guard_words,apply_crossfades,render_preview,smooth_groups
from assemble_edits import assemble

class DefaultAudioTests(unittest.TestCase):
    def test_cadence_prioritizes_shortest_island_with_global_budget(self):
        groups=[[(0,20),(27,60)],[(100,106),(118,160)]]
        self.assertEqual(smooth_groups(groups,budget=12),[groups[0],[(100,160)]])
        result=smooth_groups(groups)
        self.assertEqual(result,[[(0,60)],[(100,160)]])
        self.assertEqual(sum(b-a for g in result for a,b in g)-sum(b-a for g in groups for a,b in g),19)
        self.assertEqual(smooth_groups([[(0,5),(30,80)]]),[[(0,5),(30,80)]])

    def test_adjacent_selected_words_avoid_rounding_microcut_default_only(self):
        from fast_logic import resolve_plan
        words=[dict(text='เคส',start=0,end=.51),dict(text='ใส',start=.51,end=1.1)]
        catalog=[dict(id='A',start=0,end=1.1,text='เคสใส',words=words,protected=False)]
        picks=[dict(unit_id='A',first_word=i,last_word=i) for i in (0,1)]
        plan=dict(edits=[dict(name='test',selections=picks)])
        project=dict(duration=100,primary=[dict(start=0,end=100)])
        silence=dict(profile='compact',policy='vad-negative-noise-v4',removed_frames=[])
        original=resolve_plan(plan,catalog,silence,project)
        natural=resolve_plan(plan,catalog,dict(silence,sales_continuity='sales-continuity-v1'),project)
        self.assertEqual(len(original[0]['ranges']),2);self.assertEqual(len(natural[0]['ranges']),1)

    def test_cadence_never_bridges_an_editorially_omitted_word(self):
        from fast_logic import resolve_plan
        words=[dict(text=t,start=a,end=b) for t,a,b in [('เคส',0,.2),('เอ่อ',.2,.24),('ใส',.24,.44)]]
        catalog=[dict(id='A',start=0,end=.44,text='เคสเอ่อใส',words=words,protected=False)]
        picks=[dict(unit_id='A',first_word=i,last_word=i) for i in (0,2)]
        plan=dict(edits=[dict(name='test',selections=picks)])
        project=dict(duration=100,primary=[dict(start=0,end=100)])
        silence=dict(profile='compact',policy='vad-negative-noise-v4',removed_frames=[],sales_continuity='sales-continuity-v1')
        ranges=resolve_plan(plan,catalog,silence,project)[0]['ranges']
        self.assertEqual(len(ranges),2);self.assertLessEqual(ranges[0]['end'],.2);self.assertGreaterEqual(ranges[1]['start'],.24)
    def test_soft_short_word_core_is_never_cut_by_vad_negative_pause(self):
        silence=dict(removed_frames=[(27,80)],removed_seconds=1.06)
        transcript=[dict(words=[dict(start=.5,end=.62,text='ครับ')])]
        fixed=guard_words(silence,transcript)
        self.assertEqual(fixed['removed_frames'],[(32,80)])
        self.assertEqual(silence['removed_frames'],[(27,80)])

    def test_crossfade_is_audio_only_with_real_handles_and_valid_ticks(self):
        with tempfile.TemporaryDirectory() as d:
            source,pcm,_=create(Path(d)/'media')
            seq,cuts=assemble(source,[(1,2),(4,5)],'test','test',primary_audio=None)
            video=E.tostring(seq.find('media/video'))
            joins=apply_crossfades(seq,source,cuts)
            self.assertTrue(joins)
            self.assertEqual(E.tostring(seq.find('media/video')),video)
            self.assertEqual(int(seq.findtext('duration')),100)
            self.assertTrue(all(j['cut_frame']==50 and j['frames']==2 for j in joins))
            verify_sequence(seq)
            for tr in seq.findall('media/audio/track/transitionitem'):
                self.assertEqual((tr.findtext('start'),tr.findtext('end')),('49','51'))

    def test_no_fade_when_incoming_has_no_media_handle_or_source_is_contiguous(self):
        with tempfile.TemporaryDirectory() as d:
            source,_,_=create(Path(d)/'media')
            for ranges in [[(1,2),(0,1)],[(1,2),(2,3)]]:
                seq,cuts=assemble(source,ranges,'test','test',primary_audio=None)
                self.assertFalse(apply_crossfades(seq,source,cuts))
                verify_sequence(seq)

    def test_preview_preserves_duration_and_samples_outside_40ms_overlap(self):
        pcm=np.r_[np.full(16000,1000),np.full(16000,2000),np.full(16000,3000)].astype('<i2')
        ranges=[dict(start=.1,end=.5),dict(start=1.1,end=1.5)]
        joins=[dict(track=1,cut_frame=20,frames=2)]
        mixed=render_preview(pcm,ranges,joins,1)
        raw=np.r_[pcm[1600:8000],pcm[17600:24000]]
        self.assertEqual(len(mixed),len(raw))
        np.testing.assert_array_equal(mixed[:6080],raw[:6080])
        np.testing.assert_array_equal(mixed[6720:],raw[6720:])
        self.assertEqual(mixed[6080],1000);self.assertEqual(mixed[6719],2000)
        self.assertAlmostEqual(int(mixed[6400]),round(3000/np.sqrt(2)),delta=3)

    def test_original_export_stays_hard_cut_and_raw_source_samples(self):
        import wave
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,pcm,_=create(root/'media');project=intake(source,audio_track=1)
            job=root/'job';job.mkdir();prepare_source(project,job)
            out=root/'out';out.mkdir();ranges=[dict(start=1,end=2),dict(start=4,end=5)]
            build_outputs(project,job/'source.xml',[dict(name='original',ranges=ranges)],out,pcm.tobytes())
            self.assertFalse(E.parse(out/'EDITS.xml').findall('.//transitionitem'))
            with wave.open(str(out/'EDIT_01.wav')) as wav:actual=wav.readframes(wav.getnframes())
            self.assertEqual(actual,pcm[16000:32000].tobytes()+pcm[64000:80000].tobytes())
