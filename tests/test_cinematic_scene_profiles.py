import sys
from pathlib import Path
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from cinematic_scene_profiles import register
from campaign_entity_links import guid_value
from campaign_trigger_areas import TriggerArea,read_areas,write_areas


class SceneProfileTests(unittest.TestCase):
    def fixture(self):
        files={'leveldata.xml':b'<LevelData><Layers><Layer Name="existing" Id="7"/></Layers><Other value="keep"/></LevelData>',
               'whdata_1':b'<Root><LedgeManager keep="true"/><GameProfileManager><GameProfiles><GameProfile><Name>existing</Name><Id>9</Id></GameProfile></GameProfiles><GameProfileGroups/></GameProfileManager></Root>',
               'waitinglinks.xml':b'<StaticLinksInfo version="1"><WaitingLinks/></StaticLinksInfo>',
               'triggerareas.fubar':write_areas([TriggerArea(10,5,((1,2,3),(2,3,4)))],123)}
        profiles=E.fromstring('<Root><GameProfileManager><GameProfiles><GameProfile><Name>example</Name><Id>43</Id><HasNavigation>0</HasNavigation><NeedsSVORebuild>0</NeedsSVORebuild><GameLayers><GameLayer>old</GameLayer></GameLayers></GameProfile></GameProfiles><GameProfileGroups/></GameProfileManager></Root>')
        data=b'<Objects><Entity Name="area" EntityId="100" EntityGuid="11111111-2222-3333" Layer="new"/><Entity Name="sequence" EntityId="101" EntityGuid="11111111-2222-3334" Layer="new"><EntityLinks><Link Name="cleanup" TargetId="100" TargetGuid="11111111-2222-3333"/></EntityLinks></Entity></Objects>'
        receipt=dict(layer='new',offset=[100,200,0],source_game='kcd2',areas=['11111111-2222-3333'],
                     entity_guid_map={'12345678abcd4321':'11111111-2222-3333','12345678abcd4322':'11111111-2222-3334'})
        native=write_areas([TriggerArea(guid_value('12345678-abcd-4321'),2,((1,2,3),(4,5,6)))],456)
        return files,[(data,receipt,'example')],profiles,native

    def test_profiles_layers_links_and_compiled_areas_agree(self):
        inputs=self.fixture();files,report=register(*inputs)
        self.assertEqual(report[0]['profile'],'gm_example')
        self.assertEqual(report[0]['profile_id'],10)
        self.assertEqual(report[0]['layer_id'],8)
        wh=E.fromstring(files['whdata_1'])
        self.assertIsNotNone(wh.find('LedgeManager'))
        p=wh.find(".//GameProfile[Name='gm_example']")
        self.assertEqual(p.findtext('GameLayers/GameLayer'),'new')
        self.assertEqual(p.findtext('HasNavigation'),'0')
        self.assertIn('layers/new.xml',files)
        areas=read_areas(files['triggerareas.fubar']);self.assertEqual(areas['export_version'],123)
        self.assertEqual(areas['areas'][0].points,((1,2,3),(2,3,4)))
        self.assertEqual(areas['areas'][1].points,((101,202,3),(104,205,6)))
        self.assertEqual(areas['areas'][1].guid,guid_value('11111111-2222-3333'))
        self.assertEqual([x.text for x in E.fromstring(files['waitinglinks.xml']).iter('LinkDefinition')],['cleanup'])
        self.assertNotIn('layers/new.xml',inputs[0])
        self.assertNotIn(b'gm_example',inputs[0]['whdata_1'])

    def test_navigation_and_duplicate_registration_fail(self):
        args=list(self.fixture());args[2].find('.//HasNavigation').text='1'
        with self.assertRaisesRegex(ValueError,'navigation or additional layer'):register(*args)
        args=list(self.fixture());files,_=register(*args);args[0]=files
        with self.assertRaisesRegex(ValueError,'already exists'):register(*args)

    def test_missing_area_does_not_produce_half_registered_profile(self):
        args=list(self.fixture());args[3]=write_areas([],456)
        with self.assertRaisesRegex(ValueError,'Missing compiled cinematic area'):register(*args)
        self.assertNotIn('layers/new.xml',args[0])

    def test_kcd1_areas_use_source_numeric_guid_instead_of_native_word_order(self):
        args=list(self.fixture());args[1][0][1]['source_game']='kcd1'
        args[3]=write_areas([TriggerArea(int('12345678abcd4321',16),2,((1,2,3),(4,5,6)))],456)
        _,report=register(*args)
        self.assertEqual(report[0]['registered_areas'],1)


if __name__=='__main__':unittest.main()
