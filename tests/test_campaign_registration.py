from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from campaign_bindings import build_bindings
from campaign_dependencies import DependencyImporter
from campaign_dependency_adapters import EntityAdapter
from campaign_entity_links import guid_value, native_guid
from campaign_registration import assemble_registrations
from campaign_trigger_areas import TriggerArea, read_areas, write_areas
from test_campaign_bindings import fixture


class CampaignRegistrationTests(unittest.TestCase):
    def test_imported_area_has_same_identity_in_geometry_entity_and_both_link_formats(self):
        model, world, souls = fixture()
        area = TriggerArea(int('432187659abcdef0',16), 3, ((1,2,3),(4,5,6),(7,8,9)))
        importer = DependencyImporter(dict(entities=EntityAdapter(world, souls, write_areas([area], 9), 10)))
        for identity in ('432187659abcdef0','5555666677778888'):
            self.assertIsNotNone(importer.ensure('entities', native_guid(int(identity,16))))
        importer.registered['items'] = {'source-item':'target-item'}
        plan = build_bindings(model, world, souls)
        original = b'<Database><Skald><Project Name="UnrelatedCampaign"><Definitions/><Nodes/></Project></Skald></Database>'
        quest, files, report = assemble_registrations('UnrelatedCampaign', {'UnrelatedCampaign.xml':original},
            {model['quest']:plan}, importer.registered, importer.files, 10)
        self.assertGreater(len(report['assets']), 0)
        self.assertTrue(report['pending']) # Unconverted actors do not discard successful area/item imports.
        project = E.fromstring(quest['UnrelatedCampaign.xml'])
        self.assertEqual(project.find('.//ItemAsset').get('ItemClassGuids'), 'target-item')
        geometry = read_areas(files['world/registration/triggerareas.fubar'])
        self.assertEqual(geometry['areas'][0].guid, area.guid)
        objects = E.fromstring(files['world/registration/objects_mission0.xml'])
        entities = {e.get('EntityGuid'):e for e in objects}
        waiting = E.fromstring(files['world/registration/waitinglinks.xml'])
        for link in waiting.findall('WaitingLinks/WaitingLink'):
            source, target = link.get('SourceId'), link.get('TargetId')
            self.assertIn(source, entities); self.assertIn(target, entities)
            cry = [l for l in entities[source].findall('EntityLinks/Link') if l.get('TargetGuid')==target]
            self.assertTrue(cry)
            self.assertTrue(all(l.get('TargetId')==entities[target].get('EntityId') for l in cry))
            self.assertTrue(all(l.findtext('LinkDefinition') or len(l.findall('LinkDefinition')) for l in [link]))
        self.assertFalse(report['profile_activation_converted'])

    def test_geometry_cannot_be_registered_without_an_entity(self):
        area = TriggerArea(42, 0, ((0,0,0),(1,1,1)))
        with self.assertRaisesRegex(ValueError, 'no placed entity'):
            assemble_registrations('AnyCampaign', {'AnyCampaign.xml':b'<Database><Skald><Project Name="AnyCampaign"/></Skald></Database>'},
                {}, {}, {'world/triggerareas/a.fubar':write_areas([area], 1)}, 1)


if __name__ == '__main__': unittest.main()
