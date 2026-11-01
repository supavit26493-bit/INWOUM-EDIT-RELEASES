import copy,io,json,tempfile,unittest,wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import subtitle_mode as sub,engine,server
from audio_io import write_pcm,read_pcm
from demo import create
from project import intake,prepare_source


def timed(text,start=0,step=.12):
    words=[dict(start=start+i*step,end=start+(i+1)*step,text=c) for i,c in enumerate(text)]
    return dict(start=start,end=start+len(text)*step,text=text,words=words)


class SubtitleTests(unittest.TestCase):
    def test_thai_count_preserves_written_text(self):
        self.assertEqual(sub.count('เก่งมาก'),4) # ก ง ม ก
        self.assertEqual(sub.count('กินข้าว'),4) # ก น ข ว
        self.assertEqual(sub.count('ABC 12!'),7)
        self.assertEqual(sub.count('น้ำ'),1)

    def test_long_thai_split_preserves_speech_and_never_exceeds_45(self):
        text='วันนี้เราจะมาเล่าประสบการณ์เดินทางไปเที่ยวญี่ปุ่นครั้งแรกครับการเตรียมตัวก่อนเดินทางเป็นเรื่องสำคัญมากเพราะเราต้องตรวจสอบเอกสารและวางแผนการเดินทางให้เรียบร้อยครับ'
        result=sub.split([timed(text)])
        self.assertGreater(len(result),1)
        self.assertEqual(''.join(r['text'] for r in result),text)
        self.assertTrue(all(sub.count(r['text'])<=45 for r in result))
        self.assertTrue(all(a['end']<=b['start'] for a,b in zip(result,result[1:])))

    def test_short_sentence_and_long_gap_stay_separate(self):
        result=sub.split([timed('สวัสดีครับ',0),timed('วันนี้อากาศดีครับ',5)])
        self.assertEqual([c['text'] for c in result],['สวัสดีครับ','วันนี้อากาศดีครับ'])
        self.assertLess(result[0]['end'],5)

    def test_no_fake_times_for_oversized_unaligned_token(self):
        text='hello'*12
        transcript=[dict(start=0,end=5,text=text,words=[])]
        with self.assertRaisesRegex(ValueError,'45'):sub.split(transcript)

    def test_uncertain_segment_can_use_existing_usable_word_edges(self):
        row=timed('วันนี้เราจะมาเล่าเรื่องการเดินทางของเราครับทุกคนต้องเตรียมตัวให้พร้อมก่อนออกเดินทางนะครับ')
        row['alignment_uncertain']=True
        result=sub.split([row]);edges={w['start'] for w in row['words']}|{w['end'] for w in row['words']}
        self.assertTrue(all(c['start'] in edges and c['end'] in edges for c in result))
        self.assertEqual(''.join(c['text'] for c in result),row['text'])

    def test_compound_number_and_reduplication_are_not_cut_in_half(self):
        row=timed('ฉีดครั้งเดียวแล้วแข็งสี่สิบแปดชั่วโมงครับจริงจริงเราควรลองก่อนนะครับ')
        items=sub.atoms([row]);boundaries=[];pos=0
        for item in items:
            pos+=len(item['text']);boundaries.append(pos)
        for phrase in ['สี่สิบแปด','จริงจริง','นะครับ']:
            start=row['text'].index(phrase)
            self.assertFalse(any(start<b<start+len(phrase) for b in boundaries),phrase)

    def test_srt_utf8_and_timestamp_rollover_and_no_overlaps(self):
        self.assertEqual(sub.stamp(59.9996),'00:01:00,000')
        cues=[dict(start=1.25,end=2.5,text='สวัสดีครับ')]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r=sub.write_result(root,cues)
            text=(root/r['file']).read_text('utf-8-sig')
            self.assertIn('1\n00:00:01,250 --> 00:00:02,500\nสวัสดีครับ',text)
            self.assertEqual(r['openai_calls'],0)
        with self.assertRaises(ValueError):sub.validate_cues(cues+[dict(start=2,end=3,text='ทับ')])
        with self.assertRaises(ValueError):sub.validate_cues([dict(start=0,end=1,text='a'*46)])

    def test_media_extract_and_cached_retry_never_calls_openai(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'input.wav';pcm=np.zeros(4*16000,dtype=np.int16);write_pcm(source,pcm)
            folder=root/'job';folder.mkdir();transcript=[timed('สวัสดีครับ')]
            def capcut(pcm,folder,config,progress):
                engine.save(folder/'capcut-transcription.json',dict(contract=sub.capcut_asr.contract(pcm),status='completed',aligned_transcript_sha256=sub.capcut_asr.transcript_hash(transcript)))
                return copy.deepcopy(transcript)
            with patch('capcut_asr.run',side_effect=capcut) as asr,patch('parallel_requests.plan_once',side_effect=AssertionError('No paid API')):
                result=sub.run(dict(subtitle_source=str(source)),folder)
                self.assertEqual(len(result['cues']),1)
                np.testing.assert_array_equal(read_pcm(folder/'timeline.wav'),pcm)
                sub.run(dict(subtitle_source=str(source)),folder)
                self.assertEqual(asr.call_count,1)
            self.assertFalse((folder/'api-usage.json').exists())

    def test_xml_uses_prepared_capcut_transcript_without_new_asr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,pcm,transcript=create(root/'media')
            job=root/'job';job.mkdir();project=intake(source);prepare_source(project,job);write_pcm(job/'timeline.wav',pcm)
            engine.save(job/'intake.json',engine.identity(project))
            engine.save(job/'source-sha256.json',sub.digest(job/'source.xml'))
            engine.save(job/'audio-identity.json',dict(pcm_sha256=sub.hashlib.sha256(pcm.tobytes()).hexdigest(),samples=len(pcm)))
            engine.save(job/'transcript.json',transcript);engine.save(job/'vad.json',None)
            engine.save(job/'capcut-transcription.json',dict(contract=sub.capcut_asr.contract(pcm),status='completed',aligned_transcript_sha256=sub.capcut_asr.transcript_hash(transcript)))
            with patch('capcut_asr.run',side_effect=AssertionError('cached')),patch('planner.plan_once',side_effect=AssertionError('No OpenAI')):
                result=sub.run(dict(subtitle_source=str(source)),job)
            self.assertTrue(result['file'].endswith('.srt'))

    def test_subtitle_endpoint_drops_supplied_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'audio.wav';source.write_bytes(b'fixture')
            body=json.dumps(dict(subtitle_source=str(source),api_key='do-not-use')).encode();responses=[]
            h=SimpleNamespace(auth=lambda:None,path='/api/subtitle',headers={'Content-Length':str(len(body))},rfile=io.BytesIO(body),send_json=lambda *a:responses.append(a))
            with patch('server.launch',return_value=dict(id='new')) as launch:server.Handler.do_POST(h)
            self.assertNotIn('api_key',launch.call_args.args[0])
            self.assertEqual(launch.call_args.kwargs['action'],'subtitle')

    def test_edit_save_keeps_times_and_creates_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);job=root/'id';job.mkdir();old=sub.write_result(job,[dict(start=1,end=2,text='เดิม')])
            body=json.dumps(dict(id='id',texts=['ข้อความใหม่'])).encode();responses=[]
            h=SimpleNamespace(auth=lambda:None,path='/api/subtitle-save',headers={'Content-Length':str(len(body))},rfile=io.BytesIO(body),send_json=lambda *a:responses.append(a))
            with patch('server.JOB_ROOT',root),patch('server.JOBS',{'id':dict(id='id',status='done',result={})}):server.Handler.do_POST(h)
            result=responses[0][0]
            self.assertEqual(result['cues'][0],dict(start=1,end=2,text='ข้อความใหม่'))
            self.assertNotEqual(result['file'],old['file']);self.assertTrue((job/old['file']).exists())

    def test_srt_download_serves_exact_file_as_attachment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);result=sub.write_result(root,[dict(start=0,end=1,text='ทดสอบ')])
            headers={};status=[];body=io.BytesIO()
            handler=SimpleNamespace(headers={},send_response=status.append,send_header=lambda k,v:headers.update({k:v}),end_headers=lambda:None,wfile=body)
            server.Handler.send_file(handler,root/result['file'])
            self.assertEqual(status,[200])
            self.assertEqual(headers['Content-Disposition'],'attachment; filename="SUBTITLES.srt"')
            self.assertEqual(body.getvalue(),(root/result['file']).read_bytes())
