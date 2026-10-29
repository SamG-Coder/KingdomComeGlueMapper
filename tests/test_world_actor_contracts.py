import os
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as E
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from world_actor_contracts import henry_context, missing_voices, upgrade, TRAVEL
from setup_gameplay import PROJECT

PROJECT_XML = b'<Database><Skald><Project><Nodes><State Name="keep" TypeT="bool"/></Nodes></Project></Skald></Database>'
SOUL = 'Libs/Tables/rpg/soul__import.xml'


def table(tag, rows):
    root = E.Element('database')
    for attrs in rows:
        E.SubElement(root, tag, attrs)
    return E.tostring(root)


def native(root):
    path = root/'Data/Tables.pak'
    path.parent.mkdir(parents=True)
    chars = [dict(skald_character_name='char_GENERIC_MAN_GUARD_02', voice_id='13',
                  age='35', body_type='0', gender='0', unique_assets=''),
             dict(skald_character_name='char_GENERIC_WOMAN_COMMONER_01', voice_id='27',
                  age='35', body_type='0', gender='1', unique_assets=''),
             dict(skald_character_name='char_GENERIC_MAN_COMMONER_01', voice_id='18',
                  age='35', body_type='0', gender='0', unique_assets='')]
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('Libs/Tables/rpg/soul_archetype.xml', table('soul_archetype', [
            dict(soul_archetype_id='51', soul_archetype_name='NPC'),
            dict(soul_archetype_id='52', soul_archetype_name='NPC_Female'),
            dict(soul_archetype_id='53', soul_archetype_name='Horse')]))
        z.writestr('Libs/Tables/skald/skald_character.xml', table('skald_character', chars))
        z.writestr('Libs/Tables/skald/voice.xml', table('voice', [
            dict(voice_id=c['voice_id'], voice_technical_name='voice_'+c['voice_id']) for c in chars]))
        z.writestr('Libs/Tables/rpg/soul__region.xml', table('soul', [
            dict(soul_archetype_id='51', social_class_id='981',
                 skald_character_name=chars[0]['skald_character_name'], voice_group_name='Guards'),
            dict(soul_archetype_id='52', social_class_id='902',
                 skald_character_name=chars[1]['skald_character_name'])]))
        z.writestr('Libs/Tables/rpg/soul__region.bin', b'not XML')


def soul(name, archetype='51', social='981', **attrs):
    return dict(soul_name=name, soul_id='stable-'+name, soul_archetype_id=archetype,
                social_class_id=social, voice_group_name='Merchants', **attrs)


class ActorContractsTests(unittest.TestCase):
    def test_henry_branch_has_an_active_native_context_owner(self):
        repaired = henry_context(PROJECT_XML)
        root = E.fromstring(repaired)
        context = root.find('.//GameContextTrigger')
        self.assertEqual(context.find('Constant').attrib, dict(Name='Context', Value='player_henry'))
        edge = context.find('Edge')
        state = root.find(".//State[@Name='"+edge.get('From').split('.')[0]+"']")
        self.assertEqual(edge.get('To'), 'IsActive')
        self.assertEqual(state.get('TypeT'), 'bool')
        self.assertEqual(state.find('Constant').get('Value'), 'true')
        self.assertIsNotNone(root.find(".//State[@Name='keep']"))
        self.assertEqual(henry_context(repaired), repaired)

    def test_conflicting_context_fails_instead_of_silently_disabling_henry(self):
        repaired = henry_context(PROJECT_XML).replace(b'Value="true"', b'Value="false"')
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            henry_context(repaired)

    def test_generic_voices_follow_native_class_and_gender_not_template(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder); native(target)
            original = {SOUL: table('soul', [soul('watchman'), soul('villager', '52', '902'),
                soul('miller', '51', '777'), soul('horse', '53', '999'),
                soul('voiced', skald_character_name='gm_voice_authored')])}
            changed, report = missing_voices(original, target)
            byname = {s.get('soul_name'): s for s in E.fromstring(changed[SOUL])}
            self.assertEqual(byname['watchman'].get('voice_group_name'), 'Guards')
            self.assertIn('_MAN_GUARD_', byname['watchman'].get('skald_character_name'))
            self.assertIn('_WOMAN_COMMONER_', byname['villager'].get('skald_character_name'))
            self.assertIsNone(byname['villager'].get('voice_group_name'))
            self.assertIn('_MAN_COMMONER_', byname['miller'].get('skald_character_name'))
            self.assertIsNone(byname['horse'].get('skald_character_name'))
            self.assertEqual(byname['voiced'].get('skald_character_name'), 'gm_voice_authored')
            self.assertEqual(len(report), 3)
            self.assertEqual(missing_voices(changed, target), ({}, []))
            self.assertEqual(missing_voices(original, target), (changed, report))
            self.assertEqual(byname['watchman'].get('soul_id'), 'stable-watchman')
            self.assertNotIn(b'char_GENERIC', original[SOUL])

    def test_unknown_archetype_fails_before_any_package_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); native(root/'native')
            package = root/'package'; (package/'Data').mkdir(parents=True)
            with zipfile.ZipFile(package/TRAVEL, 'w') as z:
                z.writestr(PROJECT, PROJECT_XML)
                z.writestr(SOUL, table('soul', [soul('bad', '999')]))
            before = (package/TRAVEL).read_bytes()
            with self.assertRaisesRegex(ValueError, 'Unknown'):
                upgrade(package, root/'native')
            self.assertEqual((package/TRAVEL).read_bytes(), before)

    def test_repair_replaces_hardlink_without_changing_installed_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); native(root/'native')
            package = root/'package'; (package/'Data').mkdir(parents=True)
            original = root/'installed.pak'
            with zipfile.ZipFile(original, 'w') as z:
                z.writestr(PROJECT, PROJECT_XML)
                z.writestr(SOUL, table('soul', [soul('guard')]))
                z.writestr('keep.bin', b'untouched')
            before = original.read_bytes()
            os.link(original, package/TRAVEL)
            files, report = upgrade(package, root/'native')
            self.assertIn(TRAVEL, files)
            self.assertEqual(original.read_bytes(), before)
            with zipfile.ZipFile(package/TRAVEL) as z:
                self.assertEqual(z.read('keep.bin'), b'untouched')
                self.assertIn(b'player_henry', z.read(PROJECT))
            self.assertEqual(upgrade(package, root/'native')[0], {})


if __name__ == '__main__':
    unittest.main()
