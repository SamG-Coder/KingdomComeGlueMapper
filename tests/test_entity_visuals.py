"""Checks for parent-local item placement, including nonzero rotations."""
import math
from pathlib import Path
import sys
import unittest
import tempfile
import zipfile
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from entity_visuals import world_transform, initial_layer, visual_model
from static_assets import character_definition


class EntityVisualTests(unittest.TestCase):
    def test_character_dependencies_and_inline_proxy_are_preserved(self):
        source=b'<CharacterDefinition><Model File="objects/door.chr" Material="objects/door"/><AttachmentList><Attachment Type="CA_SKIN" Binding="objects/door.skin"/><Attachment Type="CA_PROX" BoneName="pivot" Position="0,0,1" ProxyParams="0,0,1.3,0.1"/></AttachmentList></CharacterDefinition>'
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.zip'
            with zipfile.ZipFile(path,'w') as z:z.writestr('objects/door.cdf',source)
            with zipfile.ZipFile(path) as z:
                emitted={};index={'objects/door.cdf':(z,z.getinfo('objects/door.cdf'))}
                character_definition(index,emitted,lambda name:'isolated/material',lambda name:name.encode(),
                                     'objects/door.cdf','isolated/door.cdf')
                doc=ET.fromstring(emitted['isolated/door.cdf'])
                self.assertEqual(doc.find('Model').get('File'),'isolated/door_part0.chr')
                self.assertEqual(doc.find('Model').get('Material'),'isolated/material')
                self.assertEqual(emitted['isolated/door_part1.skin'],b'objects/door.skin')
                self.assertEqual(doc.find("AttachmentList/Attachment[@Type='CA_PROX']").get('Position'),'0,0,1')

    def test_class_model_properties_and_explicit_geometry_precedence(self):
        e=ET.Element('Entity')
        props=ET.Element('Properties',fileModel='objects/ladder.cgf')
        self.assertEqual(visual_model(e,props),'objects/ladder.cgf')
        props.set('object_Model','objects/door.cdf')
        self.assertEqual(visual_model(e,props),'objects/door.cdf')
        e.set('Geometry','objects/override.cgf')
        self.assertEqual(visual_model(e,props),'objects/override.cgf')

    def test_parent_rotation_scale_and_child_offset(self):
        q=math.sqrt(.5)
        parent=ET.Element('Entity',EntityId='1',Pos='10,20,30',Rotate=f'{q},0,0,{q}',Scale='2,2,2')
        child=ET.Element('Entity',EntityId='2',ParentId='1',Pos='3,0,1')
        position,rotation,scale=world_transform(child,{'1':parent,'2':child})
        for actual,expected in zip(position,(10,26,32)):self.assertAlmostEqual(actual,expected)
        self.assertAlmostEqual(rotation[3],q)
        self.assertEqual(scale,(2,2,2))

    def test_cycle_and_missing_parent_fail_closed(self):
        e=ET.Element('Entity',EntityId='1',ParentId='1')
        with self.assertRaises(ValueError):world_transform(e,{'1':e})
        with self.assertRaises(ValueError):world_transform(e,{})

    def test_initial_state_selection(self):
        self.assertTrue(initial_layer('sv_state0_prefabs{GUID}'))
        self.assertFalse(initial_layer('sv_state1{GUID}'))
        self.assertFalse(initial_layer('cin_destroyed_skalitz'))


if __name__=='__main__':unittest.main()
