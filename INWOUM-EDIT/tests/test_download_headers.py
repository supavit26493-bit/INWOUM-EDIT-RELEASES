import io,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
import server

class DownloadHeaderTests(unittest.TestCase):
    def test_xml_csv_force_download_with_unicode_safe_filename_and_exact_bytes(self):
        for suffix in ('.xml','.csv','.srt','.wav'):
            with self.subTest(suffix=suffix),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/('ทดสอบ'+suffix);data=b'export-bytes';path.write_bytes(data);headers={};codes=[];body=io.BytesIO()
                handler=SimpleNamespace(headers={},send_response=codes.append,send_header=lambda k,v:headers.update({k:v}),end_headers=lambda:None,wfile=body)
                server.Handler.send_file(handler,path)
                self.assertEqual(codes,[200]);self.assertEqual(body.getvalue(),data)
                if suffix=='.wav':self.assertNotIn('Content-Disposition',headers)
                else:
                    self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
                    headers['Content-Disposition'].encode('ascii')
    def test_download_keeps_audio_range_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'preview.wav';path.write_bytes(b'0123456789');headers={};codes=[];body=io.BytesIO()
            handler=SimpleNamespace(headers={'Range':'bytes=2-5'},send_response=codes.append,send_header=lambda k,v:headers.update({k:v}),end_headers=lambda:None,wfile=body)
            server.Handler.send_file(handler,path)
            self.assertEqual(codes,[206]);self.assertEqual(body.getvalue(),b'2345');self.assertEqual(headers['Content-Range'],'bytes 2-5/10')
