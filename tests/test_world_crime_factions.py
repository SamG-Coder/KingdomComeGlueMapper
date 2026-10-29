import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as E
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from world_crime_factions import BASE, LEGACY_LABEL, authority_selectors, prepare

FACTIONS='Libs/Tables/rpg/FactionTree__test.xml'
SOULS='Libs/Tables/rpg/soul__test.xml'
LOCATIONS='Libs/Tables/rpg/location__test.xml'
IMPORTED='gluemapper_kcd1_faction_1'


def table(tag, rows):
    root=E.Element('database')
    for attrs in rows: E.SubElement(root,tag,attrs)
    return E.tostring(root)


def base():
    return b'''<storm><customSelectors>
      <customSelector name="isGuard" mode="and"><or><hasSocialClass name="soldier_crimeAuthority"/></or>
        <or><hasFaction name="native_region"/></or><isAlive/></customSelector>
      <customSelector name="isBailiff" mode="and"><or><hasSocialClass name="bailiff"/></or>
        <or><hasFaction name="native_region"/></or></customSelector>
      <customSelector name="unrelated"><isHuman/></customSelector>
    </customSelectors></storm>'''


def evaluate(node, social, faction, labels, alive=True):
    if node.tag=='hasSocialClass': return node.get('name')==social
    if node.tag=='hasFaction': return node.get('name')==faction
    if node.tag=='hasFactionLabel': return node.get('name') in labels
    if node.tag=='isAlive': return alive
    if node.tag=='or': return any(evaluate(c,social,faction,labels,alive) for c in node)
    return all(evaluate(c,social,faction,labels,alive) for c in node)


def fixture():
    faction=lambda n:'gluemapper_kcd1_faction_'+str(n)
    resources={
        FACTIONS:table('Faction',[dict(Name=faction(i),Labels='city camp',Comment='unchanged') for i in range(1,6)]),
        LOCATIONS:table('location',[dict(location_id='town-a',ui_type='Common'),dict(location_id='town-b',ui_type='Common')]),
        SOULS:table('soul',[dict(soul_id=str(i),soul_name='person-'+str(i),factionName=faction(i),social_class_id=role)
                           for i,role in [(1,'90'),(2,'91'),(3,'92'),(4,'91'),(5,'91')]])}
    mission=E.Element('Objects');metadata=E.fromstring('<Root><SoulList><Souls/></SoulList></Root>')
    for i in range(1,5):
        E.SubElement(mission,'Entity',EntityClass='NPC',EntityGuid=str(i),Name='person-'+str(i))
        s=E.SubElement(metadata.find('.//Souls'),'Soul')
        E.SubElement(s,'EntityGuid').text=str(i);E.SubElement(s,'SharedSoulGuid').text=str(i)
    for location in ('town-a','town-b'):
        e=E.SubElement(mission,'Entity',EntityClass='Location')
        E.SubElement(e,'Properties',guidLocationId=location)
    source=table('row',[dict(faction_id=str(i),location_id=loc)
                        for i,loc in [(1,'town-a'),(2,'town-b'),(3,'town-a'),(4,'foreign'),(5,'town-a')]])
    classes=table('social_class',[dict(social_class_id=i,soul_crime_role_id=role)
                                 for i,role in [('90','1'),('91','2'),('92','3')]])
    return resources,E.tostring(mission),E.tostring(metadata),source,classes,base()


class CrimeFactionTests(unittest.TestCase):
    def test_native_and_imported_guard_and_bailiff_keep_all_profession_gates(self):
        patched=authority_selectors(base(),[IMPORTED]);root=E.fromstring(patched)
        for name,social in [('isGuard','soldier_crimeAuthority'),('isBailiff','bailiff')]:
            node=root.find(".//customSelector[@name='"+name+"']")
            self.assertTrue(evaluate(node,social,'native_region',set()))
            self.assertTrue(evaluate(node,social,IMPORTED,set()))
            self.assertFalse(evaluate(node,'villager',IMPORTED,{'settlement'}))
            self.assertFalse(evaluate(node,social,'foreign',set()))
            self.assertFalse(evaluate(node,social,'foreign',{'settlement',LEGACY_LABEL}))
        guard=root.find(".//customSelector[@name='isGuard']")
        self.assertFalse(evaluate(guard,'soldier_crimeAuthority',IMPORTED,set(),False))
        self.assertEqual(authority_selectors(patched,[IMPORTED]),patched)
        self.assertEqual(E.tostring(root.find(".//customSelector[@name='unrelated']")),
                         E.tostring(E.fromstring(base()).find(".//customSelector[@name='unrelated']")))

    def test_two_towns_only_registered_residents_and_all_faction_copies(self):
        args=list(fixture());resources=args[0]
        other=FACTIONS.replace('test','other');resources[other]=resources[FACTIONS]
        originals=copy.deepcopy(resources)
        changes,report=prepare(*args)
        self.assertEqual(set(report['factions']),{'gluemapper_kcd1_faction_1','gluemapper_kcd1_faction_2'})
        self.assertEqual(len(report['actors']),2)
        for path in (FACTIONS,other):
            rows=list(E.fromstring(changes[path]))
            for row in rows[:2]:
                # Native retail FactionTree stores enum names separated by spaces.
                self.assertEqual(set(row.get('Labels').split()),{'city','camp','settlement'})
                self.assertNotIn(';',row.get('Labels'))
                self.assertEqual(row.get('Comment'),'unchanged')
            self.assertEqual(rows[0].get('LocationId'),'town-a')
            self.assertEqual(rows[1].get('LocationId'),'town-b')
            for row in rows[2:]:
                self.assertEqual(row.get('Labels'),'city camp')
                self.assertIsNone(row.get('LocationId'))
        self.assertEqual(resources,originals)

    def test_reapplication_is_stable(self):
        args=list(fixture());changes,report=prepare(*args)
        args[0].update(changes);args[-1]=changes[BASE]
        second,again=prepare(*args)
        self.assertEqual(second,{BASE:changes[BASE]})
        self.assertEqual(report,again)

    def test_female_only_settlement_gets_the_same_reporting_contract(self):
        args=list(fixture());mission=E.fromstring(args[1])
        mission.find("Entity[@EntityGuid='1']").set('EntityClass','NPC_Female')
        args[1]=E.tostring(mission)
        changes,report=prepare(*args)
        self.assertIn(IMPORTED,report['factions'])
        self.assertIn('person-1',[a['name'] for a in report['actors']])
        faction=E.fromstring(changes[FACTIONS]).find("Faction[@Name='"+IMPORTED+"']")
        self.assertIn('settlement',faction.get('Labels').split())

    def test_repairs_version_six_unregistered_label_and_wrong_separator(self):
        args=list(fixture())
        args[0][FACTIONS]=args[0][FACTIONS].replace(b'city camp',
            ('camp;city;'+LEGACY_LABEL+';settlement').encode())
        args[-1]=args[-1].replace(b'<hasFaction name="native_region"/>',
            ('<hasFaction name="native_region"/><hasFactionLabel name="'+LEGACY_LABEL+'"/>').encode())
        changes,report=prepare(*args)
        root=E.fromstring(changes[BASE])
        self.assertFalse(root.findall('.//hasFactionLabel'))
        for name in ('isGuard','isBailiff'):
            node=root.find(".//customSelector[@name='"+name+"']")
            self.assertEqual({c.get('name') for c in node.findall('.//hasFaction')},
                             {'native_region',*report['factions']})
        for row in list(E.fromstring(changes[FACTIONS]))[:2]:
            self.assertEqual(row.get('Labels'),'camp city settlement')

    def test_rejects_non_imported_authority_regions(self):
        for factions in ([],['native_region']):
            with self.assertRaisesRegex(ValueError,'imported faction identities'):
                authority_selectors(base(),factions)

    def test_does_not_invent_a_missing_faction(self):
        args=list(fixture());args[0][FACTIONS]=b'<database/>'
        with self.assertRaisesRegex(ValueError,'Missing imported settlement faction'):prepare(*args)

    def test_rejects_conflicting_location_without_mutating_input(self):
        args=list(fixture());args[0][FACTIONS]=args[0][FACTIONS].replace(b'Labels="city camp"',b'Labels="city camp" LocationId="foreign"',1)
        before=copy.deepcopy(args[0])
        with self.assertRaisesRegex(ValueError,'conflicts'):prepare(*args)
        self.assertEqual(args[0],before)

    def test_requires_native_selector_contract_instead_of_broadening_it(self):
        with self.assertRaisesRegex(ValueError,'region predicate'):
            authority_selectors(base().replace(b'<hasFaction name="native_region"/>',b'<isHuman/>'),[IMPORTED])
        with self.assertRaisesRegex(ValueError,'Unsupported'):
            authority_selectors(base().replace(b'mode="and"',b'mode="or"'),[IMPORTED])

    def test_saved_version_five_upgrades_without_rebuilding_scenes_or_touching_hardlinks(self):
        from setup_crime_services import upgrade, TRAVEL, RECORD, VERSION
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);package=root/'package';(package/'Data').mkdir(parents=True)
            installed=root/'installed.pak'
            with zipfile.ZipFile(installed,'w') as z: z.writestr('keep.bin',b'unchanged')
            before=installed.read_bytes();os.link(installed,package/TRAVEL)
            report=dict(version=5,level_pak='Data/level.pak',resources={},graphs={})
            (package/'Data/level.pak').write_bytes(b'large verified level left alone')
            (package/RECORD).write_text(json.dumps(report))
            changes,witness=prepare(*fixture())
            with patch('setup_crime_services.validate') as validate, patch(
                    'setup_crime_services.refresh_scene_cast',side_effect=AssertionError('unnecessary scene rebuild')), patch(
                    'setup_crime_services.location_types',return_value=('AI/types.xml',b'<types/>')), patch(
                    'world_crime_factions.package_resources',return_value=(changes,witness)):
                files,result=upgrade(package,root,root)
                self.assertEqual(validate.call_count,2)
                self.assertEqual(result['version'],VERSION)
                self.assertEqual(result['witness_reporting'],witness)
                self.assertNotIn('Data/level.pak',files)
                self.assertEqual(installed.read_bytes(),before)
                self.assertEqual((package/'Data/level.pak').read_bytes(),b'large verified level left alone')
                with zipfile.ZipFile(package/TRAVEL) as z:
                    self.assertEqual(z.read(BASE),changes[BASE])
                    self.assertEqual(z.read('keep.bin'),b'unchanged')
                self.assertEqual(upgrade(package,None,None)[0],{})


if __name__=='__main__':unittest.main()
