from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_entity_links import native_guid, guid_value, append_links
from build_campaign_runtime_probe import add_concept_preprocess


class EntityLinksTests(unittest.TestCase):
    def test_text_guid_matches_binary_identity_field_order(self):
        self.assertEqual(native_guid(0x123456789abcdef0), '9abcdef0-5678-1234')
        self.assertEqual(guid_value('9abcdef0-5678-1234'), 0x123456789abcdef0)
        for value in (0, -1, 1 << 64):
            with self.assertRaises(ValueError): native_guid(value)
        for value in ('1234', '00000000-0000-0000', '12345678-1234-1234-1234-123456789abc'):
            with self.assertRaises(ValueError): guid_value(value)

    def test_persistent_link_network_preserves_existing_and_deduplicates(self):
        a, b, c = (native_guid(i) for i in (1, 2, 3))
        source = f'<StaticLinksInfo version="1"><WaitingLinks><WaitingLink SourceId="{a}" TargetId="{b}"><LinkDefinition>home</LinkDefinition></WaitingLink></WaitingLinks></StaticLinksInfo>'.encode()
        data = append_links(source, [(a, b, 'home'), (a, b, "asset['door']"), (a, c, "asset['area']")])
        r = E.fromstring(data)
        self.assertEqual(len(r.find('WaitingLinks')), 2)
        self.assertEqual([n.text for n in r.find('WaitingLinks')[0]], ['home', "asset['door']"])
        self.assertEqual(append_links(data, [(a, c, "asset['area']")]), data)
        with self.assertRaises(ValueError): append_links(b'<StaticLinksInfo version="2"/>', [])

    def test_concept_registration_preserves_people_and_binds_real_player(self):
        original = b'<Root version="1"><SoulList><Souls><Soul><Name>existing</Name><Guid>existing</Guid></Soul></Souls></SoulList></Root>'
        player = E.fromstring('<Soul version="8"><Name>Dude</Name><Player>1</Player><SharedSoulGuid>player-id</SharedSoulGuid></Soul>')
        data = add_concept_preprocess(original, 'Quests/Example.xml', 'example_world', player)
        r = E.fromstring(data)
        self.assertEqual([n.findtext('Name') for n in r.findall('./SoulList/Souls/Soul')], ['existing', 'Dude'])
        self.assertEqual(r.findtext('./ConceptManager/ConceptPaths/Path'), 'quests/example.xml')
        self.assertEqual(r.findtext('./AI/LevelPath'), 'data/levels/example_world')
        self.assertEqual(add_concept_preprocess(data, 'Quests/Example.xml', 'example_world', player), data)
        player.find('SharedSoulGuid').text = 'different'
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            add_concept_preprocess(data, 'Quests/Example.xml', 'example_world', player)


if __name__ == '__main__': unittest.main()
