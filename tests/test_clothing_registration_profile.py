import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import xml.etree.ElementTree as ET
from build_npc_clothing_probe import registration_profile, native_soul_archetype


class RegistrationProfileTests(unittest.TestCase):
    def test_native_soul_gender_uses_matching_archetype(self):
        table = ET.fromstring('<soul_archetypes><soul_archetype soul_archetype_name="NPC" '
            'gender_id="1" soul_archetype_id="40"/><soul_archetype soul_archetype_name="NPC_Female" '
            'gender_id="2" soul_archetype_id="41"/></soul_archetypes>')
        self.assertEqual(native_soul_archetype(table, 'Female'), '41')
        self.assertEqual(native_soul_archetype(table, 'Male'), '40')
        table[1].set('gender_id', '1')
        with self.assertRaises(ValueError): native_soul_archetype(table, 'Female')

    def test_explicit_profile_accepts_other_person_without_martin_defaults(self):
        report = dict(npc='another_person', parts=[dict(kind='body', gender_id='2'),
                                                 dict(kind='cloth', clothing_name='dress')])
        profile = dict(gender='Female', default_body='authored_body', underwear='authored_underwear',
                       garments={'dress':['Dress', '88', 'torso', '4', False]},
                       assembled_armor_type='Dress', assembled_archetype='88', assembled_layer='4')
        original = copy.deepcopy(profile)
        self.assertEqual(registration_profile(report, profile), original)
        self.assertEqual(profile, original)
        with self.assertRaises(ValueError): registration_profile(report)
        profile['gender'] = 'Male'
        with self.assertRaises(ValueError): registration_profile(report, profile)
        profile['gender'] = 'Female'; profile['garments'] = {}
        with self.assertRaises(ValueError): registration_profile(report, profile)

    def test_existing_martin_mapping_is_preserved(self):
        report = dict(npc='ska_fatherOfHenry', parts=[dict(kind='body', gender_id='1'),
                                                   dict(kind='cloth', clothing_name='shirt_001')])
        result = registration_profile(report)
        self.assertEqual(result['garments']['shirt_001'], ('TunicLong', '27', 'torso', '5', False))
        self.assertEqual(result['underwear'], 'm_underwear01_m01')


if __name__ == '__main__': unittest.main()
