import copy,tempfile,unittest
from pathlib import Path
import xml.etree.ElementTree as E
from demo import create
from assemble_edits import package

class ReimportDefinitionsTests(unittest.TestCase):
    def test_id_only_selected_clips_keep_canonical_source_file_definitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,_,_=create(Path(tmp)/'media')
            seq=copy.deepcopy(E.parse(source).find('.//sequence'))
            expected={f.get('id') for f in E.parse(source).iter('file') if f.find('pathurl') is not None}
            for parent in seq.iter():
                for file in list(parent.findall('file')):
                    ref=E.Element('file',id=file.get('id'));parent.remove(file);parent.append(ref)
            result=E.fromstring(package(source,[seq]))
            actual={f.get('id') for f in result.iter('file') if f.find('pathurl') is not None}
            self.assertEqual(expected,actual)
