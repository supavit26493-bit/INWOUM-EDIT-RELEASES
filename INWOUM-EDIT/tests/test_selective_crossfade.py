import copy,tempfile,unittest,wave,json
from pathlib import Path
import xml.etree.ElementTree as E
import numpy as np
import bootstrap
from demo import create
from assemble_edits import assemble
from default_audio import apply_crossfades,discontinuous_joins
from project import intake,prepare_source,configure_export,build_outputs,verify_sequence

class SelectiveCrossfadeTests(unittest.TestCase):
 def test_click_detection_skips_silence_and_continuous_audio(self):
  pcm=np.zeros(16000*6,dtype='<i2');ranges=[dict(start=1,end=2),dict(start=4,end=5)]
  self.assertEqual(discontinuous_joins(pcm,ranges),set())
  pcm[32000-160:32000]=8000;pcm[64000:64000+160]=-8000
  self.assertEqual(discontinuous_joins(pcm,ranges),{50})
  self.assertEqual(discontinuous_joins(pcm,[dict(start=1,end=2),dict(start=2,end=3)]),set())
 def test_only_selected_join_a1_changes_links_and_other_tracks_exact(self):
  with tempfile.TemporaryDirectory() as d:
   source,_,_=create(Path(d)/'media',two_cameras=True)
   seq,cuts=assemble(source,[(1,2),(4,5),(8,9)],'test','test',primary_audio=None)
   before=copy.deepcopy(seq);links=[E.tostring(l) for c in seq.iter('clipitem') for l in c.findall('link')]
   untouched=[E.tostring(t) for t in seq.findall('media/audio/track')[1:]]
   joins=apply_crossfades(seq,source,cuts,track_number=1,candidate_frames={50})
   self.assertEqual(joins,[dict(track=1,cut_frame=50,frames=2)])
   self.assertEqual(E.tostring(seq.find('media/video')),E.tostring(before.find('media/video')))
   self.assertEqual(untouched,[E.tostring(t) for t in seq.findall('media/audio/track')[1:]])
   self.assertEqual(links,[E.tostring(l) for c in seq.iter('clipitem') for l in c.findall('link')])
   self.assertEqual(seq.findtext('duration'),before.findtext('duration'));verify_sequence(seq,preserve_disabled=True)
   # Undo transition representation: every original clip field/filter/link must match.
   tr=seq.find('media/audio/track/transitionitem');track=seq.find('media/audio/track');track.remove(tr)
   left,right=track.findall('clipitem')[:2]
   for clip,old in zip((left,right),before.findall('media/audio/track/clipitem')[:2]):
    for name in ('start','end','in','out','pproTicksIn','pproTicksOut'):
     if old.find(name) is not None:clip.find(name).text=old.findtext(name)
   self.assertEqual(E.tostring(seq),E.tostring(before))
 def test_option_off_or_original_unchanged(self):
  for style in ('natural','natural_v2','original'):
   p=configure_export({},dict(sales_style=style,audio_crossfade=True))
   self.assertEqual(p['selective_crossfade'],style!='original')
   self.assertFalse(configure_export({},dict(sales_style=style))['selective_crossfade'])
 def test_export_writes_audio_only_transition_and_manifest_no_markers(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source,pcm,_=create(root/'media',two_cameras=True)
   p=intake(source,audio_track=1);configure_export(p,dict(sales_style='natural',audio_crossfade=True))
   job=root/'job';job.mkdir();prepare_source(p,job);out=root/'out';out.mkdir()
   pcm=pcm.copy();pcm[32000-160:32000]=8000;pcm[64000:64000+160]=-8000
   m=build_outputs(p,job/'source.xml',[dict(name='test',ranges=[dict(start=1,end=2),dict(start=4,end=5)])],out,pcm.tobytes())
   self.assertTrue(m['crossfades']);self.assertFalse(m['markers'])
   seq=E.parse(out/'EDIT_01.xml').find('.//sequence');self.assertFalse(seq.findall('.//marker'))
   self.assertEqual(len(seq.findall('media/audio/track/transitionitem')),1)
   self.assertFalse(seq.findall('media/video/track/transitionitem'))
   with wave.open(str(out/'EDIT_01.wav')) as w:self.assertEqual(w.getnframes(),32000)
