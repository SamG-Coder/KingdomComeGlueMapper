import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_clothing_inventory import fixture
from setup_source_appearances import upgrade, apply, APPEARANCE_POLICY_VERSION
from setup_campaign import RECEIPT, digest
from setup_resume import update_private_package


def package(root, current=False, broken=False):
    (root/'Data').mkdir(parents=True)
    tables = {n:v.encode() for n,v in fixture().items()}
    if broken:
        del tables['Libs/Storm/equipment/person.xml']
    row = dict(person='actor',namespace='person',appearance='character_person_appearance',
               appearance_policy=APPEARANCE_POLICY_VERSION-1)
    if current: row['appearance_policy'] = APPEARANCE_POLICY_VERSION
    tables['Libs/GlueMapper/PersonImport/person.json'] = json.dumps(row).encode()
    tables['Libs/Storm/appearance/person.xml'] = b'<storm><rules><rule><operations><setBody name="body"/></operations></rule></rules></storm>'
    with zipfile.ZipFile(root/'Data/gluemappertravel.pak','w') as z:
        for n,b in tables.items(): z.writestr(n,b)
    with zipfile.ZipFile(root/'Data/person.pak','w') as z:
        z.writestr('objects/characters/gluetravel/person/body.skin',b'old body')
    with zipfile.ZipFile(root/'Data/terrain.pak','w') as z:
        z.writestr('terrain',b'completed lighting')
    receipt = dict(files={p.relative_to(root).as_posix():digest(p) for p in root.rglob('*.pak')},
                   baking={'preset':'all','completed_tiles':1291})
    (root/RECEIPT).write_text(json.dumps(receipt))
    return receipt


def build(source,target,person,namespace,output,**kwargs):
    # Real archive writer, equipment-chain validator and private file swaps.
    model=output/'objects/characters/gluetravel/person/body.skin'
    model.parent.mkdir(parents=True);model.write_bytes(b'new source shape')
    for name,value in fixture().items():
        if name.startswith('Libs/Tables/'):
            path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
    return dict(appearance={'body':'body'},inventory='person_clothes',gender='male',
                appearance_policy=APPEARANCE_POLICY_VERSION)


class SetupSourceAppearancesTests(unittest.TestCase):
    def test_resume_preserves_carried_assets_before_replacing_or_removing_mixed_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'package'; receipt=package(root)
            model='objects/gluecampaign/person_weapons/sword.cgf'
            mip='objects/gluecampaign/person_weapons/sword.dds.1'
            with zipfile.ZipFile(root/'Data/person.pak','a') as z:z.writestr(model,b'sword geometry')
            with zipfile.ZipFile(root/'Data/person_03.pak','w') as z:
                z.writestr('objects/characters/gluetravel/person/old_lod.skin',b'old lod')
                z.writestr(mip,b'original streamed mip')
            for relative in ('Data/person.pak','Data/person_03.pak'):receipt['files'][relative]=digest(root/relative)
            checkpoint=Path(tmp)/'checkpoint.pak';os.link(root/'Data/person_03.pak',checkpoint)
            original=checkpoint.read_bytes()
            with patch('setup_source_appearances.resolve_person',return_value={'soul':{}}), \
                 patch('setup_source_appearances.build_appearance',side_effect=build) as builder:
                apply(root,receipt,Path('source'),Path('target'),lambda *a:None,lambda:False)
                retained=receipt['source_appearance_upgrade']['retained_equipment']
                self.assertEqual(len(retained),1)
                with zipfile.ZipFile(root/retained[0]) as z:
                    self.assertEqual({n:z.read(n) for n in z.namelist()},
                        {model:b'sword geometry',mip:b'original streamed mip'})
                self.assertEqual(receipt['files'][retained[0]],digest(root/retained[0]))
                self.assertNotIn('Data/person_03.pak',receipt['files'])
                self.assertFalse((root/'Data/person_03.pak').exists())
                self.assertEqual(checkpoint.read_bytes(),original)
                changes,removed,report=upgrade(root,receipt,None,None)
                self.assertEqual((changes,removed,report['rebuilt']),({},[],[]))
                self.assertEqual(builder.call_count,1)

    def test_resume_rebuilds_outdated_actor_once_and_preserves_bake_and_equipment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'package';receipt=package(root)
            checkpoint=Path(tmp)/'checkpoint.pak'
            os.link(root/'Data/person.pak',checkpoint)
            original=checkpoint.read_bytes();terrain=(root/'Data/terrain.pak').read_bytes()
            with patch('setup_source_appearances.resolve_person',return_value={'soul':{}}), \
                 patch('setup_source_appearances.build_appearance',side_effect=build) as builder:
                apply(root,receipt,Path('source'),Path('target'),lambda *a:None,lambda:False)
                self.assertEqual(builder.call_count,1)
                self.assertEqual(receipt['source_appearance_upgrade']['actors'],1)
                self.assertEqual(receipt['source_appearance_upgrade']['equipment_checks'][0]['status'],'checked')
                self.assertEqual(receipt['baking']['completed_tiles'],1291)
                self.assertEqual(checkpoint.read_bytes(),original)
                self.assertEqual((root/'Data/terrain.pak').read_bytes(),terrain)
                changes,removed,report=upgrade(root,receipt,None,None)
                self.assertEqual((changes,removed,report['rebuilt']),({},[],[]))
                self.assertEqual(builder.call_count,1)

    def test_fresh_current_actor_still_validates_equipment_without_rebuilding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);receipt=package(root,current=True)
            with patch('setup_source_appearances.build_appearance') as builder:
                changes,removed,report=upgrade(root,receipt,None,None)
                builder.assert_not_called()
                self.assertEqual((changes,removed),({},[]))
                self.assertTrue(report['equipment_validation'])

    def test_current_marker_cannot_hide_broken_equipment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);receipt=package(root,current=True,broken=True)
            with self.assertRaises(ValueError):upgrade(root,receipt,None,None)

    def test_cancelled_private_upgrade_preserves_saved_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'package';receipt=package(root)
            with zipfile.ZipFile(root/'Data/person.pak','a') as z:
                z.writestr('objects/gluecampaign/person_weapons/sword.cgf',b'carried sword')
            receipt['files']['Data/person.pak']=digest(root/'Data/person.pak')
            original={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with patch('setup_source_appearances.resolve_person',return_value={'soul':{}}), \
                 patch('setup_source_appearances.build_appearance',side_effect=build), \
                 patch('setup_campaign.verify_package',return_value=receipt):
                with self.assertRaises(InterruptedError):
                    update_private_package(root,lambda p:apply(p,receipt,Path('source'),Path('target'),lambda *a:None,lambda:False),lambda:True)
            self.assertEqual(original,{p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()})
            self.assertEqual(list(root.parent.glob('package-update-*')),[])


if __name__=='__main__':unittest.main()
