import sys
from pathlib import Path
import unittest
import struct
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from character_person import register_person
from character_person_appearance import keep_body_under_outfit, align_internal_translation
from character_skin_upgrade import CompiledSkin
from clothing_regions import read_chunks, write_chunks
from test_character_upgrade import sample_skin
from character_person_activity import register_lean_activity, validate_scheduler_targets
from campaign_entity_links import guid_value, native_guid


class PersonImportTests(unittest.TestCase):
    def test_uniform_internal_offset_keeps_render_geometry_and_rejects_deformation(self):
        source = sample_skin()
        chunks = read_chunks(source)
        internal = next(c for c in chunks if c.kind == 0x2005)
        payload = bytearray(internal.data)
        for start in range(32, len(payload), 64):
            x = struct.unpack_from('<f', payload, start+12)[0]
            struct.pack_into('<f', payload, start+12, x+.003)
        shifted = write_chunks(chunks, {internal.id: bytes(payload)})
        result, offset = align_internal_translation(shifted)
        self.assertAlmostEqual(offset[0], -.003, places=6)
        a, b = CompiledSkin(source), CompiledSkin(result)
        for stream in (0, 1, 2, 5, 6, 9):
            self.assertEqual(a.streams[stream].data, b.streams[stream].data)
        struct.pack_into('<f', payload, 44, 4)
        with self.assertRaisesRegex(ValueError, 'uniform translation'):
            align_internal_translation(write_chunks(chunks, {internal.id: bytes(payload)}))

    def test_source_identity_and_pose_are_preserved_for_both_sexes(self):
        for cls in ('NPC', 'NPC_Female'):
            with self.subTest(cls=cls):
                wh = ET.fromstring('<WH><SoulList><Souls/></SoulList></WH>')
                person = dict(name='source-person', soul={'soul_id': 'source-soul'},
                    actor=ET.Element('Entity', EntityClass=cls, EntityGuid='48B19E5B58C45A34', Pos='12,34,56'),
                    instance=ET.fromstring('<Soul><Guid>source-instance</Guid></Soul>'))
                def add(name, cls, pos, guid, rotation):
                    return ET.Element('Entity', Name=name, EntityClass=cls, Pos=pos, EntityGuid=guid)
                entity = register_person(add, wh, person)
                self.assertEqual(entity.get('EntityClass'), cls)
                self.assertEqual(entity.get('Pos'), '12,34,56')
                self.assertEqual(guid_value(entity.get('EntityGuid')), int('48B19E5B58C45A34', 16))
                soul = wh.find('SoulList/Souls/Soul')
                self.assertEqual(soul.findtext('SharedSoulGuid'), 'source-soul')
                self.assertEqual(soul.findtext('Guid'), 'source-instance')
                with self.assertRaises(ValueError): register_person(add, wh, person)

    def test_short_sleeves_cannot_remove_bare_arms_or_hands(self):
        for sex in ('male', 'female'):
            for region in ('arms', 'hands'):
                self.assertTrue(keep_body_under_outfit(sex, region))
        self.assertFalse(keep_body_under_outfit('male', 'torso'))
        self.assertTrue(keep_body_under_outfit('female', 'torso'))

    def test_activity_chain_has_only_resolved_native_targets(self):
        npc = ET.Element('Entity', EntityGuid=native_guid(100))
        hub = ET.Element('Entity', EntityGuid=native_guid(200), Pos='1,2,3')
        point = ET.Element('Entity', Pos='1,2,3', Rotate='0.8,0,0,0.6')
        created = []
        def add(name, cls, pos, rotation):
            entity = ET.Element('Entity', Name=name, EntityClass=cls, Pos=pos, Rotate=rotation,
                                EntityGuid=native_guid(300))
            created.append(entity)
            return entity
        activity = ET.fromstring('<S_ActivityLink PositioningDelegate="0" TargetGuid="999">'
                                 '<Parameters BehaviorName="use" Priority="0"/></S_ActivityLink>')
        terminal = ET.Element('C_SmartHub', EntityGuid='999', IgnoreDeadEnds='false', StupidHub='false')
        with patch('character_person_activity.native_lean_activity', return_value=(ET.Element('Properties'), activity, terminal)):
            result, lean = register_lean_activity(b'<database><Schedulers/></database>', add, npc, hub,
                                                  point, 'leaning_right', '', 'imported_lean')
        rows = ET.fromstring(result).find('Schedulers')
        self.assertEqual([r.get('EntityGuid') for r in rows], ['100', '200', '300'])
        self.assertEqual([l.get('TargetGuid') for l in rows.iter('S_ActivityLink')], ['200', '300'])
        self.assertIsNone(rows[0].find('Links/S_ActivityLink/Parameters').get('BehaviorName'))
        self.assertEqual(rows[1].find('Links/S_ActivityLink/Parameters').get('BehaviorName'), 'use')
        self.assertEqual(lean.get('Rotate'), point.get('Rotate'))
        self.assertEqual(len(rows[2]), 0)
        rows.remove(rows[2])
        # An entity exists, but the scheduler record is absent: the exact
        # v22 regression must be rejected before a package can be installed.
        broken = ET.Element('database'); broken.append(rows)
        with self.assertRaisesRegex(ValueError, 'Unregistered compiled scheduler targets: 300'):
            validate_scheduler_targets(broken)


if __name__ == '__main__': unittest.main()
