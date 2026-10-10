import sys
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from character_bandit_clone import resources
from character_native_clone import identity


class BanditCloneTests(unittest.TestCase):
    def test_combat_and_equipment_survive_source_appearance_override(self):
        native=ET.fromstring('<soul soul_name="bandit" soul_id="native" brain_id="brain" '
            'combat_level="0.3" social_class_id="38" factionName="enemies" voice_group_name="Bandits" soul_archetype_id="0"/>')
        equipment=ET.fromstring('<rule name="gear"><selectors><hasName name="bandit"/></selectors>'
            '<operations><setInventory preset="native_sword_and_armor"/></operations></rule>')
        ability=ET.fromstring('<rule name="stats"><selectors><hasName name="bandit"/></selectors>'
            '<operations><setStrength value="10"/></operations></rule>')
        files={'Libs/Storm/storm.xml':b'<storm><tasks><task name="appearance"/><task name="names"/>'
            b'<task name="equipment"><source path="existing.xml"/></task><task name="abilities"/></tasks></storm>',
            'Quests/existing.xml':b'unchanged'}
        character=dict(archetype='0',appearance={'body':'source-body','head':'source-head'},inventory='source-clothes')
        person=dict(instance=ET.fromstring('<Soul><StaticData><NameStringId>fritz</NameStringId></StaticData></Soul>'))
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp);(source/'Localization').mkdir()
            with zipfile.ZipFile(source/'Localization/English_xml.pak','w') as z:
                z.writestr('text_ui_soul.xml','<Table><Row><Cell>fritz</Cell><Cell>Fritz</Cell><Cell>Fritz</Cell></Row></Table>')
            result,names=resources(files,source,person,character,
                dict(soul=native,rules={'equipment':[equipment],'abilities':[ability]}),'test_fritz')
        soul=ET.fromstring(result['Libs/Tables/rpg/soul__test_fritz.xml']).find('souls/soul')
        for key,value in native.attrib.items():
            if key not in ('soul_name','soul_id'):self.assertEqual(soul.get(key),value)
        self.assertEqual(soul.get('soul_id'),identity('test_fritz'))
        self.assertEqual(native.get('soul_name'),'bandit')
        self.assertEqual(equipment.find('selectors/hasName').get('name'),'bandit')
        gear=ET.fromstring(result['Libs/Storm/equipment/test_fritz.xml'])
        self.assertEqual(gear.find('rules/rule/operations/setInventory').get('preset'),'native_sword_and_armor')
        self.assertNotIn(b'source-clothes',result['Libs/Storm/equipment/test_fritz.xml'])
        self.assertIn(b'setStrength',result['Libs/Storm/abilities/test_fritz.xml'])
        self.assertEqual(result['Quests/existing.xml'],b'unchanged')
        self.assertIn(b'existing.xml',result['Libs/Storm/storm.xml'])
        self.assertIn(b'source-head',result['Libs/Storm/appearance/test_fritz.xml'])
        self.assertIn(b'Fritz',names)
        with self.assertRaises(ValueError):
            resources(result,Path('.'),person,character,dict(soul=native,rules={}), 'test_fritz')


if __name__=='__main__':unittest.main()
