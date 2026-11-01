import copy,json,tempfile,unittest,io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import subtitle_mode as sub,subtitle_polish as polish,engine,server
from audio_io import write_pcm
from test_subtitle_mode import timed

def row():
 return dict(id='S00000',words=[dict(text=t,start=i*.4,end=(i+1)*.4) for i,t in enumerate(['จริงๆ','ก็','ไม่ได้','กี','นะ','แคปคัต'])])
def correction(**kw):
 return dict(segment_id='S00000',first_phrase=3,last_phrase=3,before='กี',after='ดี',kind='asr_typo',confidence='high',**kw)

class SubtitlePolishTests(unittest.TestCase):
 def test_high_confidence_typo_and_name_keep_exact_span_times(self):
  original=row();answer=dict(corrections=[correction(),dict(segment_id='S00000',first_phrase=5,last_phrase=5,before='แคปคัต',after='CapCut',kind='proper_name',confidence='high')])
  fixed,changes,skipped=polish.apply([original],answer,45)
  self.assertEqual([w['text'] for w in fixed[0]['words']],['จริงๆ','ก็','ไม่ได้','ดี','นะ','CapCut'])
  self.assertEqual([(w['start'],w['end']) for w in fixed[0]['words']],[(w['start'],w['end']) for w in original['words']])
  self.assertEqual(original,row());self.assertEqual((len(changes),skipped),(2,0))
 def test_uncertainty_and_unsafe_corrections_are_skipped_without_repair(self):
  for update in [dict(confidence='uncertain'),dict(before='wrong'),dict(first_phrase=99),dict(after='ไม่ดี'),dict(after='ดี 2'),dict(after='ดี\nนะ'),dict(after=''),dict(after='x'*100)]:
   edit=correction();edit.update(update);fixed,changes,n=polish.apply([row()],dict(corrections=[edit]),45)
   self.assertEqual(fixed,[row()]);self.assertEqual(changes,[]);self.assertEqual(n,1)
 def test_merge_uses_existing_span_not_invented_word_times(self):
  edit=correction();edit.update(first_phrase=3,last_phrase=4,before='กีนะ',after='ดีนะ')
  fixed,changes,_=polish.apply([row()],dict(corrections=[edit]),45)
  self.assertEqual(fixed[0]['words'][3]['start'],row()['words'][3]['start'])
  self.assertEqual(fixed[0]['words'][3]['end'],row()['words'][4]['end'])
  self.assertEqual(len(fixed[0]['words']),5)
 def test_numeric_negation_and_long_pause_protected(self):
  original=row();original['words'][3]['text']='สองร้อย'
  edit=correction();edit.update(before='สองร้อย',after='สามร้อย')
  self.assertFalse(polish.apply([original],dict(corrections=[edit]),45)[1])
  original=row();original['words'][4]['start']+=1;edit.update(first_phrase=3,last_phrase=4,before='กีนะ',after='ดีนะ')
  self.assertFalse(polish.apply([original],dict(corrections=[edit]),45)[1])
 def test_config_defaults_and_invalid_limits(self):
  with tempfile.TemporaryDirectory() as d:
   source=Path(d)/'in.wav';source.touch()
   self.assertEqual(sub.validate_config(dict(subtitle_source=str(source)))['subtitle_chars'],40)
   for value in (0,9,121,True,40.5,'40'):
    with self.assertRaises(ValueError):sub.validate_config(dict(subtitle_source=str(source),subtitle_chars=value))
 def test_custom_limits_preserve_text_and_single_lines(self):
  text='วันนี้เราจะมาเล่าประสบการณ์ไปเที่ยวญี่ปุ่นครั้งแรกครับการเตรียมตัวก่อนเดินทางเป็นเรื่องสำคัญมาก'
  for limit in (15,25,40,60):
   cues=sub.split([timed(text)],soft_limit=limit)
   self.assertEqual(''.join(c['text'] for c in cues),text)
   self.assertTrue(all(sub.count(c['text'])<=limit+5 and '\n' not in c['text'] for c in cues))
  with tempfile.TemporaryDirectory() as d:
   result=sub.write_result(Path(d),[dict(start=0,end=1,text='A'*55)],soft_limit=60)
   self.assertEqual((result['soft_limit'],result['hard_limit']),(60,65))
 def test_polish_request_is_sparse_text_only_and_bounded(self):
  req=polish.request([dict(id='S00000',phrases={'0':'แคปคัต'})],[],dict(subtitle_glossary='แคปคัต = CapCut'))
  self.assertIn('omit the edit entirely',req['instructions']);self.assertNotIn('start',json.loads(req['input'])['segments'][0])
  self.assertEqual(req['text']['format']['schema']['required'],['corrections'])
 def test_full_capcut_polish_cached_retry_keeps_raw_and_no_retranscription(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'in.wav';write_pcm(source,np.zeros(16000*10,dtype='<i2'));folder=root/'job';folder.mkdir()
   transcript=[dict(start=0,end=2.4,text=''.join(w['text'] for w in row()['words']),words=row()['words'])]
   config=dict(subtitle_source=str(source),subtitle_ai_polish=True,subtitle_maiyamok=False,model='gpt-6.1-sol',subtitle_chars=30)
   def capcut(pcm,folder,cfg,progress):
    engine.save(folder/'capcut-transcription.json',dict(contract=sub.capcut_asr.contract(pcm),status='completed',aligned_transcript_sha256=sub.capcut_asr.transcript_hash(transcript)))
    return copy.deepcopy(transcript)
   def answer(req,key):
    data=json.loads(req['input']);segment=data['segments'][0];phrases=segment['phrases'];idx=next(int(i) for i,t in phrases.items() if 'กี' in t)
    before=phrases[str(idx)]
    return dict(corrections=[dict(segment_id=segment['id'],first_phrase=idx,last_phrase=idx,before=before,after=before.replace('กี','ดี'),kind='asr_typo',confidence='high')]),dict(usage=dict(input_tokens=10,output_tokens=10,total_tokens=20))
   with patch('capcut_asr.run',side_effect=capcut) as asr,patch('parallel_requests.plan_once',side_effect=answer) as send:
    result=sub.run(config,folder,key='test');again=sub.run(config,folder,key='')
   self.assertEqual(asr.call_count,1);self.assertEqual(send.call_count,1)
   self.assertEqual(json.loads((folder/'transcript.json').read_text('utf8')),transcript)
   self.assertEqual(result['openai_calls'],1);self.assertEqual(result['soft_limit'],30)
   self.assertTrue(result['ai_polish']['changes']);self.assertEqual(result['cues'],again['cues'])
 def test_endpoint_only_passes_key_when_enabled_and_save_keeps_limits(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'in.wav';source.touch()
   body=json.dumps(dict(subtitle_source=str(source),subtitle_ai_polish=True,subtitle_chars=25,api_key='memory-only')).encode()
   h=SimpleNamespace(auth=lambda:None,path='/api/subtitle',headers={'Content-Length':str(len(body))},rfile=io.BytesIO(body),send_json=lambda *a:None)
   with patch('server.launch',return_value={}) as launch:server.Handler.do_POST(h)
   self.assertNotIn('api_key',launch.call_args.args[0]);self.assertEqual(launch.call_args.args[1],'memory-only')
   self.assertEqual(launch.call_args.args[0]['subtitle_chars'],25)
   job=root/'id';job.mkdir();old=sub.write_result(job,[dict(start=1,end=2,text='A'*55)],soft_limit=60,metadata={'openai_calls':1,'ai_polish':{'changes':[]}})
   body=json.dumps(dict(id='id',texts=['B'*55])).encode();responses=[]
   h=SimpleNamespace(auth=lambda:None,path='/api/subtitle-save',headers={'Content-Length':str(len(body))},rfile=io.BytesIO(body),send_json=lambda *a:responses.append(a))
   with patch('server.JOB_ROOT',root),patch('server.JOBS',{'id':dict(id='id',status='done',result={})}):server.Handler.do_POST(h)
   self.assertEqual(responses[0][0]['hard_limit'],65);self.assertEqual(responses[0][0]['openai_calls'],1)
