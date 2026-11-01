import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as E
import numpy as np
import dead_air_mode as mode,demo,project
from assemble_edits import assemble,package
from audio_io import extract_audio
from verify_outputs import check

class DeadAirTests(unittest.TestCase):
    def source(self,root,ranges=None):
        source,pcm,_=demo.create(root/'media',two_cameras=True)
        if ranges:
            seq,_=assemble(source,ranges,'edited','edited',primary_audio=None,max_seconds=None,include_markers=False)
            path=root/'edited.xml';path.write_text(package(source,[seq]),'utf8');source=path
        return source,pcm

    def test_config_strips_keys_and_forces_analysis_track_one(self):
        with tempfile.TemporaryDirectory() as d:
            source,_=self.source(Path(d));cfg=mode.validate_config(dict(xml=str(source),audio_track=2,api_key='do-not-store',model='paid-model'))
            self.assertEqual(cfg['audio_track'],1);self.assertNotIn('api_key',cfg);self.assertNotIn('model',cfg)
            for data in [dict(xml=str(source),heal_edges='yes'),dict(xml=str(source),sequence_index=-1),dict(xml=str(source),dead_air_profile='unknown')]:
                with self.assertRaises(ValueError):mode.validate_config(data)

    def test_quiet_removed_but_soft_vad_positive_speech_and_loud_action_retained(self):
        pcm=np.zeros(4*16000,dtype=np.int16);pcm[16000:17600]=30;pcm[40000:42000]=5000
        ranges,report=mode.cut_ranges(pcm,[(1,1.1)],'gentle',[dict(start=0,end=200)])
        self.assertGreater(report['removed_seconds'],1)
        for a,b in [(1,1.1),(2.5,2.625)]:self.assertTrue(any(r['start']<=a and r['end']>=b for r in ranges))

    def test_short_audible_fragment_is_not_deleted_by_duration(self):
        pcm=np.zeros(2*16000,dtype=np.int16);pcm[16000:17920]=6000
        ranges,_=mode.cut_ranges(pcm,[],'tight',[dict(start=0,end=100)])
        self.assertTrue(any(r['start']<=1 and r['end']>=1.12 for r in ranges))

    def test_no_vad_or_all_silence_stops_without_guessing(self):
        for speech in (None,[]):
            with self.assertRaises(ValueError):mode.cut_ranges(np.zeros(16000,dtype=np.int16),speech,'gentle',[dict(start=0,end=50)])

    def test_restore_tiny_source_gap_in_every_track_exactly_once(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,original=self.source(root,[(3,4),(4.08,6)])
            folder=root/'job';folder.mkdir();p=project.intake(source);pcm=extract_audio(p,folder,lambda **kw:None)
            fixed,restores,skipped=mode.heal_tiny_gaps(p,pcm,folder,lambda **kw:None)
            self.assertEqual(restores,[dict(cut_frame=50,gap_frames=4)]);self.assertEqual(skipped,[])
            np.testing.assert_array_equal(fixed,original[3*16000:6*16000])
            self.assertEqual(p['sequence'].findtext('duration'),'150')
            project.verify_sequence(p['sequence'],preserve_disabled=True)
            for track in p['sequence'].findall('media/video/track')+p['sequence'].findall('media/audio/track'):
                a,b=track.findall('clipitem');self.assertEqual(a.findtext('end'),b.findtext('start'));self.assertEqual(a.findtext('out'),b.findtext('in'))

    def test_different_track_gap_does_not_restore_or_move_any_clip(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,_=self.source(root,[(3,4),(4.08,6)]);folder=root/'job';folder.mkdir()
            p=project.intake(source);v=p['sequence'].findall('media/video/track')[1].findall('clipitem')[1]
            v.find('in').text=str(int(v.findtext('in'))+1)
            pcm=extract_audio(p,folder,lambda **kw:None);before=E.tostring(p['sequence'])
            after,restores,skipped=mode.heal_tiny_gaps(p,pcm,folder,lambda **kw:None)
            self.assertFalse(restores);self.assertTrue(skipped);self.assertEqual(E.tostring(p['sequence']),before);np.testing.assert_array_equal(after,pcm)

    def test_long_or_reversed_source_gap_is_not_healed(self):
        for ranges in ([(3,4),(4.3,6)],[(4,5),(3,4)]):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);source,_=self.source(root,ranges);folder=root/'job';folder.mkdir();p=project.intake(source)
                pcm=extract_audio(p,folder,lambda **kw:None);before=E.tostring(p['sequence'])
                self.assertFalse(mode.heal_tiny_gaps(p,pcm,folder,lambda **kw:None)[1]);self.assertEqual(E.tostring(p['sequence']),before)

    def test_merge_only_redundant_unlinked_source_continuity(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,_=self.source(root,[(3,3.2),(3.2,5)]);p=project.intake(source)
            files={f.get('id'):f for f in p['root'].iter('file') if f.find('pathurl') is not None}
            self.assertEqual(mode.merge_contiguous(p['sequence'],files),4)
            for t in p['sequence'].findall('media/video/track')+p['sequence'].findall('media/audio/track'):self.assertEqual(len(t.findall('clipitem')),1)
            project.verify_sequence(p['sequence'])

    def test_merge_does_not_break_asymmetric_incoming_link(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,_=self.source(root,[(3,3.2),(3.2,5)]);p=project.intake(source);seq=p['sequence']
            a=seq.find('media/audio/track/clipitem');v=seq.find('media/video/track/clipitem')
            link=E.SubElement(v,'link')
            for k,val in [('linkclipref',a.get('id')),('mediatype','audio'),('trackindex','1'),('clipindex','1')]:E.SubElement(link,k).text=val
            files={f.get('id'):f for f in p['root'].iter('file') if f.find('pathurl') is not None};mode.merge_contiguous(seq,files)
            self.assertEqual(len(seq.find('media/audio/track').findall('clipitem')),2)
            self.assertEqual(v.findtext('link/linkclipref'),a.get('id'))

    def test_pick_sequence_with_file_definitions_owned_by_another_sequence(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,_=self.source(root);seq,_=assemble(source,[(3,5)],'second','second',primary_audio=None,include_markers=False)
            first=copy.deepcopy(project.intake(source)['sequence']);multi=root/'multi.xml';multi.write_text(package(source,[first,seq]),'utf8')
            folder=root/'job';folder.mkdir();cfg=mode.validate_config(dict(xml=str(multi),sequence_index=1))
            self.assertEqual(len(mode.inspect(cfg)['sequences']),2)
            path,_=mode.normalize(cfg,folder);p=project.intake(path);self.assertEqual(p['duration'],100)
            self.assertEqual(p['sequence'].findtext('name'),'second')

    def test_only_known_crossfade_can_be_normalized_back_to_exact_straight_cuts(self):
        from default_audio import apply_crossfades
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,_=self.source(root);seq,cuts=assemble(source,[(3,4),(5,6)],'old','old',primary_audio=None,include_markers=False)
            before=E.tostring(seq);self.assertTrue(apply_crossfades(seq,source,cuts))
            old=root/'old.xml';old.write_text(package(source,[seq]),'utf8');folder=root/'job';folder.mkdir()
            path,count=mode.normalize(mode.validate_config(dict(xml=str(old))),folder)
            p=project.intake(path);self.assertEqual(count,2)
            self.assertFalse(p['sequence'].findall('.//transitionitem'))
            for track in p['sequence'].findall('media/audio/track'):
                self.assertEqual([(c.findtext('start'),c.findtext('end')) for c in track.findall('clipitem')],[('0','50'),('50','100')])

    def test_full_local_pipeline_zero_api_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,_=self.source(root);before=source.read_bytes();job=root/'job'
            with patch('dead_air_mode.vad_intervals',return_value=[(.6,2.4),(4,5.8),(8,10.5)]),patch('parallel_requests.plan_once',side_effect=AssertionError('No API')):
                result=mode.run(dict(xml=str(source)),job)
            self.assertEqual(source.read_bytes(),before);self.assertEqual(result['api_calls'],0)
            self.assertGreater(result['removed_seconds'],0);self.assertLess(result['output_seconds'],12)
            self.assertEqual(json.loads((job/'api-usage.json').read_text()),[]);self.assertEqual(check(job)['versions'],1)
            seq=E.parse(job/result['xml_file']).find('.//sequence')
            self.assertEqual(len(seq.findall('media/audio/track')),2);self.assertFalse(seq.findall('.//marker'));self.assertFalse(seq.findall('.//transitionitem'))

    def test_cancel_stops_without_export_or_api(self):
        import engine
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,_=self.source(root);job=root/'job'
            with self.assertRaises(engine.Cancelled):mode.run(dict(xml=str(source)),job,cancelled=lambda:True)
            self.assertFalse(list(job.glob('revision-*')));self.assertEqual(json.loads((job/'api-usage.json').read_text()),[])

    def test_remaining_short_report_reads_actual_exported_clips(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,_=self.source(root,[(4,4.12),(5,5.6)]);job=root/'job'
            with patch('dead_air_mode.vad_intervals',return_value=[(0,.72)]):
                result=mode.run(dict(xml=str(source),merge_short=False,heal_edges=False),job)
            self.assertTrue(result['remaining_short_ranges'])
            self.assertEqual(result['remaining_short_ranges'][0]['seconds'],.12)
            self.assertEqual(result['remaining_short_ranges'][0]['coordinate'],'output timeline')

    def test_thai_sequence_name_and_unicode_folder_survive_full_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'งาน ภาษาไทย';root.mkdir();source,_=self.source(root)
            tree=E.parse(source);tree.find('.//sequence/name').text='รีวิวถังน้ำแข็งไดว่า: ฟังก์ชันและการใช้งานจริง'
            tree.write(source,encoding='utf-8',xml_declaration=True);before=source.read_bytes();job=root/'ทดสอบ'
            with patch('dead_air_mode.vad_intervals',return_value=[(.6,2.4),(4,5.8),(8,10.5)]):
                result=mode.run(dict(xml=str(source)),job)
            self.assertEqual(source.read_bytes(),before)
            self.assertIn(b"encoding='utf-8'",(job/'input-normalized.xml').read_bytes().splitlines()[0])
            self.assertTrue((job/result['xml_file']).is_file());self.assertEqual(check(job)['versions'],1)
