"""Relocated loft beds must not retain their native town's scheduler position."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import forge_loft
from campaign_entity_links import guid_value, native_guid
from character_sleep_work import bind
from character_city_benches import signature
from test_character_sleep import fixture


def baseline():
    mission, scheduler, activity, choices, clock = fixture()
    mission[0].set('Name', 'ska_motherOfHenry')
    father = copy.deepcopy(mission[0])
    father.set('Name', 'ska_fatherOfHenry')
    father.set('EntityGuid', native_guid(4)); father.set('EntityId', '4')
    mission.append(father)
    row = copy.deepcopy(scheduler.find('Schedulers')[0]); row.set('EntityGuid', '4')
    scheduler.find('Schedulers').append(row)
    for name in ('ska_motherOfHenry', 'ska_fatherOfHenry'):
        bind(mission, scheduler, name, clock, choices, 'bed', activity)
    for index, name, cls in ((20, 'gm_ladder_32403', 'Ladder'),
                             (21, 'Ska_SA_HomeForge', 'SmartAreaShape'),
                             (22, 'gm_player_chest_32407', 'Chest')):
        entity = E.SubElement(mission, 'Entity', Name=name, EntityClass=cls,
                              EntityId=str(index), EntityGuid=native_guid(index), Pos='0,0,0')
        E.SubElement(entity, 'Properties', bUsable='true')
        if cls == 'Chest':
            E.SubElement(E.SubElement(entity, 'EntityLinks'), 'Link', Name='masterStash',
                         TargetGuid=native_guid(23), TargetId='23')
    # The correction must not remove legitimate positions from unrelated hubs.
    E.SubElement(scheduler.find('Schedulers'), 'C_SmartHub', EntityGuid='99', Position='1,2,3')
    return {
        'objects_mission0.xml': E.tostring(mission),
        'tables/ai/scheduler.xml': E.tostring(scheduler),
        'waitinglinks.xml': b'<StaticLinksInfo version="1"><WaitingLinks/></StaticLinksInfo>',
        'whdata_0': f'<Root><SoulList><Souls><Soul><Player>1</Player><EntityGuid>{native_guid(5)}</EntityGuid></Soul></Souls></SoulList></Root>'.encode(),
        forge_loft.ANIMATIONS: b'<Root><SmartObjectAnimationss/></Root>',
        forge_loft.HELPERS: b'<Helpers/>',
        'terrain/hlods.dat': b'untouched geometry',
        'terrain/hlods.xml': b'<Root/>',
    }


def donor():
    bed = E.Element('Entity', EntityClass='StanceSmartObject', EntityId='100',
                    EntityGuid=native_guid(100), Pos='100,200,300', Rotate='1,0,0,0')
    E.SubElement(bed, 'Properties', guidSmartObjectType='bed', soclass_SmartObjectHelpers='Bed_1Place_High')
    E.SubElement(E.SubElement(bed, 'EntityLinks'), 'Link', Name='mTrigger',
                 TargetId='101', TargetGuid=native_guid(101))
    trigger = E.Element('Entity', EntityClass='BedTrigger', EntityId='101',
                        EntityGuid=native_guid(101), Pos='100,200,300', Rotate='1,0,0,0')
    props = E.SubElement(trigger, 'Properties')
    E.SubElement(props, 'Click'); E.SubElement(props, 'Hold', sAction='lying')
    E.SubElement(E.SubElement(trigger, 'EntityLinks'), 'Link', Name='bed',
                 TargetId='100', TargetGuid=native_guid(100))
    hub = E.Element('C_SmartHub', EntityGuid='100', Position='100,200,300')
    E.SubElement(E.SubElement(hub, 'Effects'), 'EffectHeal', Role='sleeper', HealRate='25')
    return {'Bed_1Place_High': (bed, trigger, hub, {'100': bed, '101': trigger})}


class ForgeLoftSchedulerTests(unittest.TestCase):
    def convert(self, files):
        # Native animation archives and HLOD meshes are separate game-dependent
        # contracts. Exercise the real loft placement, assignment and upgrade.
        with patch('world_beds.donors', return_value=donor()), \
             patch('forge_loft.install', side_effect=lambda files, *a, **k: (files, [])), \
             patch('world_brush_edits.edit', side_effect=lambda data, metadata, edits: (data, metadata)), \
             patch('forge_loft.validate'):
            return forge_loft.convert(files, None)[0]

    def hubs(self, files):
        entities = E.fromstring(files['objects_mission0.xml'])
        rows = E.fromstring(files['tables/ai/scheduler.xml']).find('Schedulers')
        return rows, [next(r for r in rows if r.get('EntityGuid') == str(guid_value(e.get('EntityGuid'))))
                      for e in entities if e.get('Name') in
                      {forge_loft.PREFIX + role + '_bed' for role in ('player', 'mother', 'father')}]

    def test_new_beds_drop_native_position_and_preserve_healing(self):
        result = self.convert(baseline())
        rows, beds = self.hubs(result)
        self.assertEqual(len(beds), 3)
        for row in beds:
            self.assertNotIn('Position', row.attrib)
            self.assertEqual(row.find('.//EffectHeal').attrib, {'Role': 'sleeper', 'HealRate': '25'})
        self.assertEqual(next(r for r in rows if r.get('EntityGuid') == '99').get('Position'), '1,2,3')

    def test_existing_loft_repairs_only_bed_positions_and_is_idempotent(self):
        files = self.convert(baseline())
        rows, beds = self.hubs(files)
        for row in beds:
            row.set('Position', '100,200,300')
        expected = copy.deepcopy(rows)
        ids = {r.get('EntityGuid') for r in beds}
        for row in expected:
            if row.get('EntityGuid') in ids:
                row.attrib.pop('Position')
        root = E.Element('Root'); root.append(rows)
        files['tables/ai/scheduler.xml'] = E.tostring(root)
        result = self.convert(files)
        repaired, _ = self.hubs(result)
        self.assertEqual(signature(repaired), signature(expected))
        self.assertEqual(result, self.convert(result))


if __name__ == '__main__':
    unittest.main()
