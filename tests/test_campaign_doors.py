from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
import xml.etree.ElementTree as E
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_dependencies import DependencyImporter
from campaign_dependency_adapters import EntityAdapter, LazyAdapter
from campaign_doors import AnimatedDoorAssetAdapter, DoorEntityConversion, rig_mapping
from campaign_entity_links import native_guid
from campaign_trigger_areas import write_areas
from character_skin_upgrade import CompiledSkin
from clothing_regions import read_chunks, write_chunks
from quest_import import ast
from test_campaign_dependencies import Adapter, Tables
from test_character_upgrade import sample_skin


def renamed_rig(source, index, name):
    chunks = read_chunks(source)
    chunk = next(c for c in chunks if c.kind == 0x2000)
    bones = bytearray(chunk.data)
    offset = 32 + 584 * index + 312
    bones[offset:offset+256] = name.encode().ljust(256, b'\0')
    return write_chunks(chunks, {chunk.id: bytes(bones)})


class CampaignDoorTests(unittest.TestCase):
    def test_animated_source_geometry_uses_native_rig_and_keeps_right_door_filename(self):
        source = sample_skin()
        native = renamed_rig(source, 0, 'NativeRoot')
        model = 'objects/characters/assets/doors/arbitrary_right.cdf'
        rig = 'objects/characters/assets/doors/arbitrary_right.chr'
        skin = 'objects/characters/assets/doors/arbitrary_right.skin'
        material = 'objects/props/doors/arbitrary.mtl'
        with tempfile.TemporaryDirectory() as tmp:
            pak = Path(tmp) / 'source.pak'
            with zipfile.ZipFile(pak, 'w') as z:
                z.writestr(model, f'<CharacterDefinition><Model File="{rig}" Material="{material}"/>'
                    f'<AttachmentList><Attachment Type="CA_SKIN" AName="door" Binding="{skin}"/></AttachmentList></CharacterDefinition>')
                z.writestr(rig, source)
            with zipfile.ZipFile(pak) as z:
                pack = SimpleNamespace(index={n:(z,z.getinfo(n)) for n in z.namelist()},
                    emitted={}, mesh=lambda _:source)
                assets = LazyAdapter(lambda:pack)
                native_assets = SimpleNamespace(get=lambda name:(
                    b'<Params><AnimationList><Animation name="*" path="native/*.caf"/></AnimationList></Params>'
                    if name.endswith('.chrparams') else native, [{'archive':'native-retail'}]))
                adapter = AnimatedDoorAssetAdapter(assets, SimpleNamespace(assets=native_assets), 'AnyCampaign')
                importer = DependencyImporter(dict(animated_props=adapter, assets=Adapter()))
                target = importer.ensure('animated_props', model)
                self.assertIsNotNone(target, importer.report())
                self.assertTrue(target.endswith('/arbitrary_right.cdf'))
                cdf = E.fromstring(importer.files[target])
                self.assertEqual(cdf.find('Model').get('File'), rig)
                self.assertEqual(cdf.find('Model').get('Material'), 'converted-'+material.removesuffix('.mtl'))
                converted = importer.files[cdf.find('AttachmentList/Attachment').get('Binding')]
                self.assertEqual(CompiledSkin(converted).vertices.tolist(), CompiledSkin(source).vertices.tolist())
                self.assertEqual(CompiledSkin(converted).info['bones'][0]['name'], 'NativeRoot')

    def test_unknown_pivot_is_not_guessed(self):
        with self.assertRaisesRegex(ValueError, 'Unconverted animated prop joint'):
            rig_mapping(sample_skin(), renamed_rig(sample_skin(), 1, 'DifferentPivot'))

    def make_entity_importer(self, interior='undefined'):
        key = '12345678-1234-1234-1234-123456789abc'
        door = E.fromstring(f'''<Entity Name="AnyDoor" EntityClass="AnimDoor" EntityId="18"
            EntityGuid="123456789abcdef0" Pos="12,34,56" Rotate="1,0,0,0">
            <Properties object_Model="objects/door_right.cdf" guidSmartObjectType="source-type"
                esInteriorType="{interior}" soclasses_SmartObjectHelpers="DoorRight" fKeepOpenFrom="6" fKeepOpenUntil="22">
                <Lock guidItemClassId="{key}" bLocked="1" bNeverLock="0" bCanLockPick="1"/>
                <Physics bPhysicalize="1"/>
            </Properties></Entity>''')
        tables = Tables({'ai/so_smart_object':[dict(so_smart_object_id='source-type', so_smart_object_name='so_door')]})
        animated = SimpleNamespace(source_definition=lambda _:(None,'native-rig',{}))
        native = SimpleNamespace(resolve=lambda *args:dict(smart_object='native-type',animation_set='NativeSet',helper='DoorRight'))
        converter = DoorEntityConversion(tables, animated, native)
        record = dict(source='objects_mission0.xml',attributes=door.attrib,entity=ast(door))
        adapter = EntityAdapter(dict(entities={'a':record}),E.fromstring('<Root/>'),write_areas([],1),1,converter)
        return DependencyImporter(dict(entities=adapter,animated_props=Adapter(),items=Adapter())), key

    def test_door_registration_imports_key_and_preserves_physics_state_and_identity(self):
        importer,key = self.make_entity_importer()
        guid = native_guid(0x123456789abcdef0)
        self.assertEqual(importer.ensure('entities',guid),guid)
        door = E.fromstring(importer.files['world/entities/'+guid+'.xml'])
        self.assertEqual(door.get('EntityClass'),'AnimDoor')
        self.assertEqual(door.get('Pos'),'12,34,56')
        self.assertEqual(door.find('Properties/Physics').get('bPhysicalize'),'1')
        lock = door.find('Properties/Lock')
        self.assertEqual(lock.get('guidItemClassId'),'converted-'+key)
        self.assertEqual(lock.get('bLocked'),'1')
        self.assertEqual(lock.get('bCanUnlockWithDynamicKey'),'0')
        self.assertEqual(lock.get('fKeepUnlockedFrom'),'6')
        self.assertEqual(door.find('Properties').get('esDoorAnimSet'),'NativeSet')
        self.assertEqual(importer.jobs[('entities',guid)]['evidence']['replaces_visual_entity'],'glue_item_18')

    def test_private_door_policy_is_not_silently_lost(self):
        importer,_ = self.make_entity_importer(interior='home')
        guid = native_guid(0x123456789abcdef0)
        self.assertIsNone(importer.ensure('entities',guid))
        self.assertIn('area-link conversion',importer.jobs[('entities',guid)]['error'])
        self.assertNotIn('world/entities/'+guid+'.xml',importer.files)


if __name__ == '__main__': unittest.main()
