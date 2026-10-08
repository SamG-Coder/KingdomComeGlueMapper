from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_character_dependencies import CharacterPartAdapter
from campaign_dependencies import DependencyImporter
from campaign_dependency_adapters import LazyAdapter
from character_skin_upgrade import CompiledSkin
from test_campaign_dependencies import Adapter, Tables
from test_character_upgrade import sample_skin, target_skin


class CampaignCharacterTests(unittest.TestCase):
    def test_both_genders_are_imported_from_source_rows_with_preserved_geometry(self):
        for gender, sex in [('1','male'),('2','female')]:
            with self.subTest(gender=gender), tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                root=Path(tmp);material=root/'source.mtl';material.write_bytes(b'<Material Shader="HumanSkin"/>')
                source=sample_skin();native=target_skin(source, rotate=True, child_translation=2)
                mesh='objects/characters/humans/body/arbitrary.skin'
                mat='objects/characters/humans/body/arbitrary.mtl'
                native_material='converted-'+mat
                pack=SimpleNamespace(index={mesh:(SimpleNamespace(filename='retail.pak'),SimpleNamespace(filename=mesh)), mat:None},
                    emitted={native_material:material},mesh=lambda name:source)
                assets=LazyAdapter(lambda:pack)
                identity='12345678-1234-1234-1234-123456789abc'
                tables=Tables({'character_body':[dict(character_body_id=identity,model='arbitrary',material='arbitrary',gender_id=gender,race_id='0')]})
                adapter=CharacterPartAdapter(tables,root,stack,assets,'AnyStory')
                importer=DependencyImporter(dict(character_parts=adapter,assets=Adapter()))
                # Only the installed asset reader is replaced. The actual skin
                # upgrade and male region split execute on a compiled mesh.
                with patch('campaign_character_dependencies.Assets') as reader:
                    reader.return_value.get.return_value=(native,[dict(archive='native-retail')])
                    result=importer.ensure('character_parts','body:'+identity)
                    self.assertIsNotNone(result, importer.report())
                    reader.return_value.get.assert_called_once_with('objects/characters/humans/'+sex+'/skeleton/'+sex+'.chr')
                evidence=importer.jobs[('character_parts','body:'+identity)]['evidence']
                self.assertEqual(evidence['geometry']['moved_vertices'],0)
                self.assertEqual(evidence['gender'],sex)
                self.assertTrue(evidence['body_from_source'])
                emitted=next(v for k,v in importer.files.items() if k.startswith('Libs/Tables/Character/'))
                component=E.fromstring(emitted).find('CharacterComponents/Component')
                self.assertEqual(component.get('Gender'),sex.title())
                skins=[v for k,v in importer.files.items() if k.endswith('.skin')]
                self.assertEqual(sum(len(CompiledSkin(v).faces) for v in skins),len(CompiledSkin(source).faces))
                self.assertEqual(E.fromstring(material.read_bytes()).get('Shader'),'HumanSkin')


if __name__ == '__main__': unittest.main()
