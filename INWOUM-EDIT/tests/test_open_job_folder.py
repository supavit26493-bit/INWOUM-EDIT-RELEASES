import io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import server

class OpenJobFolderTests(unittest.TestCase):
    def test_only_known_existing_job_folder_is_opened(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'valid').mkdir()
            with patch.object(server,'JOB_ROOT',root),patch.object(server,'JOBS',{'valid':{},'missing':{},'..':{}}),patch('server.os.startfile',create=True) as open_folder:
                server.open_job_folder('valid')
                open_folder.assert_called_once_with(str((root/'valid').resolve()))
                for bad in ('unknown','../valid','..','missing',None):
                    with self.subTest(bad=bad),self.assertRaises(ValueError):server.open_job_folder(bad)
                self.assertEqual(open_folder.call_count,1)

    def test_endpoint_requires_auth_and_opens_only_selected_id(self):
        body=json.dumps(dict(id='chosen')).encode();result=[]
        auth=unittest.mock.Mock()
        handler=SimpleNamespace(auth=auth,path='/api/open-job-folder',headers={'Content-Length':str(len(body))},rfile=io.BytesIO(body),send_json=lambda *a:result.append(a))
        with patch('server.open_job_folder') as opener:
            server.Handler.do_POST(handler)
            auth.assert_called_once();opener.assert_called_once_with('chosen')
        self.assertEqual(result[0][0],dict(opened=True))
