import io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import av,numpy as np
import project_cover,server

class ProjectCoverTests(unittest.TestCase):
    def project(self,path):
        clip=dict(path=str(path),start=100,end=1100,in_frame=500,enabled=True)
        return dict(duration=1500,tracks={('video',1):[clip],('video',2):[dict(clip,in_frame=1000)]})
    def test_sequence_time_maps_to_top_enabled_source_clip(self):
        p=self.project('media.mp4');r=project_cover.choose_frame(p)
        self.assertEqual(r['track'],2);self.assertEqual(r['timeline_seconds'],5);self.assertEqual(r['source_seconds'],23)
        p['tracks'][('video',2)][0]['enabled']=False
        self.assertEqual(project_cover.choose_frame(p)['source_seconds'],13)
    def test_gap_fallback_and_audio_only(self):
        p=self.project('media.mp4')
        for rows in p['tracks'].values():rows[0].update(start=500,end=600)
        self.assertEqual(project_cover.choose_frame(p)['timeline_seconds'],11)
        with self.assertRaises(ValueError):project_cover.choose_frame(dict(duration=100,tracks={}))
    def test_cache_reuses_frame_and_invalidates_changed_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);media=root/'media.mp4';media.write_bytes(b'video');xml=root/'source.xml';xml.write_text('<xml/>');p=self.project(media);p['xml']=str(xml)
            with patch('project_cover.intake',return_value=p),patch('project_cover.render_png',return_value=b'\x89PNG\r\n\x1a\nimage') as render:
                first=project_cover.create(dict(xml=str(xml)),root/'cache')
                self.assertEqual(first,project_cover.create(dict(xml=str(xml)),root/'cache'));self.assertEqual(render.call_count,1)
                media.write_bytes(b'changed video');project_cover.create(dict(xml=str(xml)),root/'cache');self.assertEqual(render.call_count,2)
    def test_real_local_video_decodes_to_png_without_pillow(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'sample.mkv'
            with av.open(str(path),'w') as out:
                stream=out.add_stream('ffv1',rate=10);stream.width=32;stream.height=24;stream.pix_fmt='bgr0'
                for i in range(20):
                    image=av.VideoFrame.from_ndarray(np.full((24,32,3),i*8,dtype=np.uint8),format='rgb24');image.pts=i
                    for packet in stream.encode(image):out.mux(packet)
                for packet in stream.encode():out.mux(packet)
            self.assertTrue(project_cover.render_png(path,.5).startswith(b'\x89PNG\r\n\x1a\n'))
    def test_endpoint_is_authenticated_and_does_not_launch_work(self):
        body=json.dumps(dict(xml='source.xml')).encode();answers=[];auth=unittest.mock.Mock()
        handler=SimpleNamespace(auth=auth,path='/api/project-cover',headers={'Content-Length':str(len(body))},rfile=io.BytesIO(body),send_json=lambda *a:answers.append(a))
        with patch('project_cover.create',return_value=dict(image='data:image/png;base64,test')) as cover,patch('server.launch') as launch:
            server.Handler.do_POST(handler);auth.assert_called_once();cover.assert_called_once();launch.assert_not_called()
