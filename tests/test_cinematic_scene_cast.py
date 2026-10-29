import copy
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from cinematic_scene_cast import bind_cast,catalog,outfit_for
from cinematic_scene import SceneIndex
from campaign_trigger_areas import write_areas


class SceneCastTests(unittest.TestCase):
    def fixture(self):
        shape='<Area Height="50"><Points><Point Pos="0,0,0"/><Point Pos="100,0,0"/><Point Pos="100,100,0"/><Point Pos="0,100,0"/></Points></Area>'
        mission=E.fromstring('<Objects><Entity EntityId="1" EntityGuid="00000001-0000-0000" Pos="0,0,0">'+shape+'</Entity></Objects>')
        wh=E.fromstring('<Root><SoulList><Souls/></SoulList></Root>')
        db=E.Element('root');equipment=E.fromstring('<storm><rules/></storm>')
        for ident,name,gender,profession,faction in [
                ('resident1','actual_bailiff','M','B','7'),
                ('resident2','local_woman','F','C','7'),
                ('visitor','foreign_woman','F','C','8')]:
            count=len(mission)+1;g=f'{count:08x}-0000-0000'
            E.SubElement(mission,'Entity',EntityId=str(count),EntityGuid=g,Name=name,
                         Pos='10,20,1',EntityClass='NPC_Female' if gender=='F' else 'NPC')
            s=E.SubElement(wh.find('SoulList/Souls'),'Soul');E.SubElement(s,'EntityGuid').text=g
            E.SubElement(s,'SharedSoulGuid').text=ident
            E.SubElement(db,'soul',soul_id=ident,soul_name=name,soul_archetype_id=gender,
                         social_class_id=profession,factionName='gluemapper_kcd1_faction_'+faction)
            E.SubElement(db,'clothing_preset',clothing_preset_name=name+'_outfit',
                         clothing_preset_id=ident+'_clothes',gender='Female' if gender=='F' else 'Male')
            p=E.SubElement(db,'InventoryPreset',Name=name+'_inventory',Mode='All')
            E.SubElement(p,'ClothingPresetRef',Name=name+'_outfit')
            E.SubElement(p,'InventoryPresetRef',Name='random_loot')
            rule=E.SubElement(equipment.find('rules'),'rule')
            E.SubElement(E.SubElement(rule,'selectors'),'hasName',name=name)
            E.SubElement(E.SubElement(rule,'operations'),'setInventory',preset=name+'_inventory')
        E.SubElement(db,'InventoryPreset',Name='random_loot',Mode='Random')
        native=E.fromstring('''<root>
        <soul_archetype soul_archetype_id="M" soul_archetype_name="NPC"/>
        <soul_archetype soul_archetype_id="F" soul_archetype_name="NPC_Female"/>
        <soul_archetype soul_archetype_id="P" soul_archetype_name="Hero"/>
        <social_class social_class_id="B" social_class_name="bailiff" soul_crime_role_id="2"/>
        <social_class social_class_id="C" social_class_name="none" soul_crime_role_id="1"/>
        <soul soul_id="native1" soul_name="native_bailiff" social_class_id="B" soul_archetype_id="M"/>
        <soul soul_id="native2" soul_name="native_woman" soul_archetype_id="F"/>
        <soul soul_id="player" soul_name="player_henry" soul_archetype_id="P"/>
        </root>''')
        objects=E.fromstring('<Objects/>')
        for i,identity in enumerate(['native1','native2','player'],10):
            e=E.SubElement(objects,'Entity',Name='cin_'+identity,EntityId=str(i),EntityGuid=f'{i:016x}',EntityClass='AnimChar',Layer='scene')
            E.SubElement(E.SubElement(e,'Properties',bSaved_by_game='0'),'Body',guidBodyPresetId=identity,
                guidClothingPresetId='old_'+identity,guidSubstituteBodyPresetId='foreign_substitute',fBloodOverride='.5')
        seq=E.SubElement(E.SubElement(objects,'Entity',Name='seq',EntityId='30',EntityGuid='0000000000000030',Layer='scene'),
                         'Sequence',Name='scene',SequenceVersion='3')
        idx=SceneIndex({'layers/scene.xml':objects},E.fromstring('<Root/>'))
        local={'defs.xml':E.tostring(db),'Libs/Storm/equipment/local.xml':E.tostring(equipment),
               'source/faction.xml':b'<root><row faction_id="7" location_id="local"/><row faction_id="8" location_id="elsewhere"/></root>'}
        files={'objects_mission0.xml':E.tostring(mission),'whdata_0':E.tostring(wh),'triggerareas.fubar':write_areas([],1)}
        return idx,files,local,{'defs.xml':E.tostring(native)},dict(district='00000001-0000-0000',location='local')

    def test_cast_uses_registered_local_appearance_and_keeps_player(self):
        idx,files,local,native,selection=self.fixture()
        adapters,report=bind_cast(idx,[idx.plan('scene')],files,local,native,selection)
        self.assertEqual(report['residents'],2)
        self.assertEqual({r['name'] for r in report['assignments']},{'actual_bailiff','local_woman'})
        for e in list(idx.entities.values())[:3]:
            old=E.tostring(e);new=adapters['scene'](copy.deepcopy(e));body=new.find('Properties/Body')
            expected={'cin_native1':'resident1','cin_native2':'resident2','cin_player':'player'}[e.get('Name')]
            self.assertEqual(body.get('guidBodyPresetId'),expected)
            self.assertEqual(body.get('fBloodOverride'),'.5')
            if expected!='player':
                self.assertEqual(body.get('guidClothingPresetId'),expected+'_clothes')
                self.assertEqual(body.get('guidSubstituteBodyPresetId'),expected)
            else:self.assertEqual(E.tostring(new),old)
            self.assertEqual(E.tostring(e),old)

    def test_separate_scenes_can_reuse_residents(self):
        idx,files,local,native,selection=self.fixture()
        a=idx.plan('scene');b=dict(a,name='second')
        adapters,r=bind_cast(idx,[a,b],files,local,native,selection)
        self.assertEqual(len(r['assignments']),4)
        self.assertEqual([x['soul'] for x in r['assignments'][:2]],[x['soul'] for x in r['assignments'][2:]])

    def test_npc_archetype_player_damage_variant_is_not_cast_as_a_villager(self):
        idx,files,local,native,selection=self.fixture()
        root=E.fromstring(native['defs.xml'])
        E.SubElement(root,'soul',soul_id='injured_body',soul_name='authored_variant',
                     soul_archetype_id='M',factionName='cutsceneOnly')
        native['defs.xml']=E.tostring(root)
        e=E.SubElement(idx.documents['layers/scene.xml'],'Entity',Name='second_phase_body',
                       EntityId='40',EntityGuid='0000000000000040',EntityClass='AnimChar',Layer='scene')
        E.SubElement(E.SubElement(e,'Properties'),'Body',guidBodyPresetId='injured_body',
                     guidClothingPresetId='old_player',fScratchesOverride='0.5')
        nodes=E.SubElement(idx.documents['layers/scene.xml'].find('Entity/Sequence'),'Nodes')
        for key in ('000000000000000c','0000000000000040'):
            node=E.SubElement(nodes,'Node',EntityGUID=key)
            E.SubElement(E.SubElement(node,'Track',paramType='Animation'),'Key',anim='shared_authored_performance')
        idx=SceneIndex(idx.documents,E.fromstring('<Root/>'))
        adapters,report=bind_cast(idx,[idx.plan('scene')],files,local,native,selection)
        old=E.tostring(e)
        self.assertEqual(E.tostring(adapters['scene'](copy.deepcopy(e))),old)
        self.assertEqual(report['preserved_variants'][0]['soul'],'injured_body')
        self.assertNotIn('injured_body',{a['source_soul'] for a in report['assignments']})

    def test_ordinary_cutscene_only_extras_are_still_recast(self):
        idx,files,local,native,selection=self.fixture()
        root=E.fromstring(native['defs.xml'])
        root.find("soul[@soul_id='native1']").set('factionName','cutsceneOnly')
        native['defs.xml']=E.tostring(root)
        _,report=bind_cast(idx,[idx.plan('scene')],files,local,native,selection)
        self.assertIn('native1',{a['source_soul'] for a in report['assignments']})
        self.assertEqual(report['preserved_variants'],[])

    def test_does_not_silently_use_a_visitor_when_local_cast_is_insufficient(self):
        idx,files,local,native,selection=self.fixture()
        root=E.fromstring(local['defs.xml']);root.find("soul[@soul_id='resident2']").set('factionName','elsewhere')
        local['defs.xml']=E.tostring(root)
        with self.assertRaisesRegex(ValueError,'Not enough distinct local'):
            bind_cast(idx,[idx.plan('scene')],files,local,native,selection)

    def test_random_clothing_is_not_assumed_deterministic(self):
        _,_,local,_,_=self.fixture();data=catalog(local)
        data['inventories']['actual_bailiff_inventory'].set('Mode','Random')
        with self.assertRaisesRegex(ValueError,'deterministic'):
            outfit_for(data['souls']['resident1'],data)


if __name__=='__main__':unittest.main()
