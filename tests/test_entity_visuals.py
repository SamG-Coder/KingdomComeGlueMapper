"""Checks for parent-local item placement, including nonzero rotations."""
import math
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from entity_visuals import world_transform, initial_layer


class EntityVisualTests(unittest.TestCase):
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
