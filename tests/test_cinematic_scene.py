import sys
from pathlib import Path
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from cinematic_scene import SceneIndex, convert
from region_travel import guid


class CinematicSceneTests(unittest.TestCase):
    def fixture(self):
        roots = {'layers/scene.xml': E.fromstring('''<Objects>
          <Entity EntityId="1" EntityGuid="0000000000000001" Name="scene" EntityClass="SequenceObject" Layer="scene">
            <EntityLinks><Link Name="cleanup" TargetId="3" TargetGuid="0"/></EntityLinks>
            <Sequence Name="example" SequenceVersion="3" StartTime="0" EndTime="10">
              <Nodes><Node Id="1" Type="Entity" EntityGUID="0000000000000002" paramIdVersion="8">
                <Track paramType="Position"><NewSubTrack defaultValue="0,100"><Key time="2" value="2,102" ds="1,0.5" dd="1,0.7" flags="75"/></NewSubTrack><NewSubTrack defaultValue="0,200"/><NewSubTrack defaultValue="0,30"/></Track>
                <Track paramType="Rotation"><NewSubTrack defaultValue="0,0.1"/></Track>
              </Node></Nodes><CleanupAreas><CleanupArea ID="3"/></CleanupAreas>
            </Sequence>
          </Entity>
          <Entity EntityId="3" EntityGuid="0000000000000003" Name="cleanup" EntityClass="SequenceArea" Pos="100,200,30" Layer="scene"><Area><Points><Point Pos="1,2,0"/></Points></Area></Entity>
          <Entity EntityId="4" EntityGuid="0000000000000004" Name="untracked light" EntityClass="Light" Pos="101,201,31" Layer="scene"/>
        </Objects>'''), 'objects_mission0.xml': E.fromstring('''<Objects>
          <Entity EntityId="2" EntityGuid="0000000000000002" Name="actor" EntityClass="AnimChar" Pos="100,200,30" Geometry="objects/model.cdf"/>
          <Entity EntityId="5" EntityGuid="0000000000000005" Name="unrelated" EntityClass="QuestObject"/>
        </Objects>''')}
        profiles = E.fromstring('<Root><GameProfile><Name>scene_profile</Name><GameLayers><GameLayer>scene</GameLayer></GameLayers></GameProfile></Root>')
        return SceneIndex(roots, profiles)

    def emit(self, index, **kwargs):
        return convert(index, index.plan('example'), offset=(10,20,-5), identity=guid,
                       first_id=101, layer='relocated', source_game='kcd2', **kwargs)

    def test_closure_keeps_untracked_lights_external_actor_and_profile(self):
        index = self.fixture(); plan = index.plan('example')
        self.assertEqual(plan['entity_classes'], {'SequenceObject':1,'AnimChar':1,'SequenceArea':1,'Light':1})
        self.assertEqual(plan['external_entities'], ['0000000000000002'])
        self.assertEqual(plan['owner_profiles'], ['scene_profile'])
        self.assertEqual(plan['missing_references'], [])

    def test_translation_preserves_spline_times_tangents_and_local_geometry(self):
        index = self.fixture(); original = E.tostring(index.documents['layers/scene.xml'])
        data, report = self.emit(index); root = E.fromstring(data)
        actor = root.find("Entity[@Name='actor']")
        self.assertEqual(actor.get('Pos'), '110,220,25')
        seq = root.find('.//Sequence')
        track = seq.find('.//Track')
        self.assertEqual([n.get('defaultValue') for n in track], ['0,110','0,220','0,25'])
        self.assertEqual(track.find('.//Key').attrib, dict(time='2',value='2,112',ds='1,0.5',dd='1,0.7',flags='75'))
        self.assertEqual(seq.find(".//Track[@paramType='Rotation']/NewSubTrack").get('defaultValue'), '0,0.1')
        self.assertEqual(root.find('.//Point').get('Pos'), '1,2,0')
        cleanup = root.find("Entity[@Name='cleanup']")
        self.assertEqual(seq.find('CleanupAreas/CleanupArea').get('ID'), cleanup.get('EntityId'))
        link=root.find('.//Link')
        self.assertEqual(link.get('TargetId'), cleanup.get('EntityId'))
        self.assertEqual(link.get('TargetGuid'), cleanup.get('EntityGuid'))
        self.assertEqual(seq.find('.//Node').get('EntityGUID'),actor.get('EntityGuid'))
        self.assertEqual(original, E.tostring(index.documents['layers/scene.xml']))
        self.assertFalse(report['runtime_verified'])

    def test_existing_binding_is_referenced_without_duplicate_or_relocation(self):
        destination = E.Element('Entity', EntityId='88', EntityGuid='88888888-8888-8888', Pos='1,2,3')
        data, report = self.emit(self.fixture(), bindings={'0000000000000002':destination})
        root=E.fromstring(data)
        self.assertIsNone(root.find("Entity[@Name='actor']"))
        self.assertEqual(root.find('.//Node').get('EntityGUID'),destination.get('EntityGuid'))
        self.assertEqual(destination.get('Pos'),'1,2,3')
        self.assertEqual(report['external_bindings'],1)

    def test_permanent_foundation_cannot_be_hidden_by_the_cinematic(self):
        idx=self.fixture();node=idx.entities['0000000000000001'].find('.//Node')
        for track in node.findall('Track'):
            for sub in track:
                for key in list(sub):sub.remove(key)
        visibility=E.SubElement(node,'Track',paramType='Visibility')
        E.SubElement(visibility,'Key',time='0');E.SubElement(visibility,'Key',time='7')
        destination=E.Element('Entity',EntityId='88',EntityGuid='88888888-8888-8888',Pos='1,2,3')
        data,_=self.emit(idx,bindings={'0000000000000002':destination},static_bindings={'0000000000000002'})
        self.assertEqual(E.fromstring(data).find('.//Node').findall('Track'),[])
        self.assertEqual(destination.get('Pos'),'1,2,3')

    def test_static_binding_rejects_real_movement(self):
        destination=E.Element('Entity',EntityId='88',EntityGuid='88888888-8888-8888',Pos='1,2,3')
        with self.assertRaisesRegex(ValueError,'animated behaviour'):
            self.emit(self.fixture(),bindings={'0000000000000002':destination},static_bindings={'0000000000000002'})

    def test_missing_reference_cannot_be_emitted_as_complete(self):
        index=self.fixture();index.entities['0000000000000001'].find('.//Node').set('EntityGUID','0000000000000999')
        self.assertEqual(len(index.plan('example')['missing_references']),1)
        with self.assertRaisesRegex(ValueError,'unresolved entity references'): self.emit(index)

    def test_kcd1_requires_asset_adapter_and_can_use_shared_container_conversion(self):
        index=self.fixture();params=dict(offset=(0,0,0),identity=guid,first_id=100,layer='source_scene',source_game='kcd1')
        with self.assertRaisesRegex(ValueError,'character/material/animation adapter'):convert(index,index.plan('example'),**params)
        def adapter(e):
            if e.get('Geometry'):e.set('Geometry','converted/model.cdf')
            return e
        data,_=convert(index,index.plan('example'),entity_adapter=adapter,**params)
        self.assertEqual(E.fromstring(data).find("Entity[@Name='actor']").get('Geometry'),'converted/model.cdf')

    def test_unsupported_curve_fails_explicitly(self):
        index=self.fixture();track=index.entities['0000000000000001'].find('.//Track');track.remove(track[-1])
        with self.assertRaisesRegex(ValueError,'track encoding'):self.emit(index)

    def test_parent_local_transform_and_curves_are_not_shifted_twice(self):
        index=self.fixture();index.entities['0000000000000002'].set('ParentId','3')
        data,_=self.emit(index);root=E.fromstring(data)
        actor=root.find("Entity[@Name='actor']");parent=root.find("Entity[@Name='cleanup']")
        self.assertEqual(actor.get('ParentId'),parent.get('EntityId'))
        self.assertEqual(actor.get('Pos'),'100,200,30')
        self.assertEqual(parent.get('Pos'),'110,220,25')
        self.assertEqual(root.find('.//Track/NewSubTrack').get('defaultValue'),'0,100')

    def test_parent_cycle_is_rejected(self):
        index=self.fixture();index.entities['0000000000000002'].set('ParentId','3')
        index.entities['0000000000000003'].set('ParentId','2')
        with self.assertRaisesRegex(ValueError,'Cyclic cinematic'):self.emit(index)

    def test_editor_guid_is_reported_missing_without_truncation(self):
        index=self.fixture();value='9c9d68d7-18a7-4cb0-9cee-5100a5ed5e6a'
        index.entities['0000000000000001'].find('.//Node').set('EntityGUID',value)
        plan=index.plan('example')
        self.assertEqual(plan['missing_references'][0]['value'],value)
        with self.assertRaisesRegex(ValueError,'unresolved entity references'):self.emit(index)

    def test_conflicting_link_id_and_guid_cannot_be_silently_repaired(self):
        index=self.fixture();index.entities['0000000000000001'].find('.//Link').set('TargetGuid','0000000000000002')
        self.assertIn('inconsistent_link_identity',[r['kind'] for r in index.plan('example')['missing_references']])
        with self.assertRaisesRegex(ValueError,'unresolved entity references'):self.emit(index)


if __name__=='__main__':unittest.main()
