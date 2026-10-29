import copy
import struct
import sys
import unittest
import xml.etree.ElementTree as E
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from crime_site_geometry import remove_stocks
from spatial_hlod import Group,serialize,records
from world_crime_graphs import location_adapter, location_link_type
from world_crime_services import area_geometry,choose_site
from campaign_trigger_areas import write_areas
from campaign_entity_links import guid_value


def brush(mesh,x=10):
    r=bytearray(104);struct.pack_into('<I',r,0,1);struct.pack_into('<H',r,40,mesh)
    struct.pack_into('<12f',r,44,1,0,0,x,0,1,0,20,0,0,1,30)
    return bytes(r)


class CrimeGeometryTests(unittest.TestCase):
    def fixture(self):
        a,b,other=brush(0),brush(1),brush(2)
        group=Group('world','Cluster',(10,20,30),10,records=[a,b,other],
                    children=[Group('detail','Cluster',(10,20,30),1,proxy=brush(3))])
        doc,data,_=serialize(group)
        plans=[dict(path=n,transform=struct.unpack_from('<12f',r,44)) for n,r in [('stocks',a),('log',b)]]
        return data,E.tostring(doc),plans,['stocks','log','house','proxy']

    def test_removes_only_verified_models_and_reindexes_proxy(self):
        args=self.fixture();data,doc,removed=remove_stocks(*args,lambda a,b:a==b)
        root=E.fromstring(doc);self.assertEqual(len(removed),2)
        self.assertEqual(root.find('HLod').get('ProxyIndex'),'1')
        size=int(root.get('DataSize'));items=records(data[8:4+size])
        self.assertEqual(items,[brush(2),brush(3)])
        for n in root.iter('HLod'):
            o,s=int(n.get('DataOffset')),int(n.get('DataSize'))
            self.assertEqual(struct.unpack_from('<I',data,o)[0],s-4)

    def test_same_position_is_not_enough(self):
        with self.assertRaisesRegex(ValueError,'Missing verified'):
            remove_stocks(*self.fixture(),lambda a,b:False)

    def test_refuses_to_remove_a_proxy(self):
        data,doc,plans,meshes=self.fixture()
        plans.append(dict(path='proxy',transform=plans[0]['transform']))
        with self.assertRaisesRegex(ValueError,'orphan'):
            remove_stocks(data,doc,plans,meshes,lambda a,b:a==b)

    def test_refuses_changed_source_transform(self):
        data,doc,plans,meshes=self.fixture();plans[0]['transform']=tuple(x+1 for x in plans[0]['transform'])
        with self.assertRaisesRegex(ValueError,'Missing verified'):
            remove_stocks(data,doc,plans,meshes,lambda a,b:a==b)


class CrimeRegionalTests(unittest.TestCase):
    def test_location_data_has_a_registered_native_string_type(self):
        original=b'<LinkTagDefinitions version="1"><LinkTag name="other_import" type="bool"/></LinkTagDefinitions>'
        result=location_link_type(original)
        root=E.fromstring(result)
        self.assertEqual(root.find("LinkTag[@name='gm_punishment_location']").attrib,
                         dict(name='gm_punishment_location',type='string',persistent='0'))
        self.assertEqual(root.find("LinkTag[@name='other_import']").get('type'),'bool')
        self.assertEqual(location_link_type(result),result)
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            location_link_type(result.replace(b'type="string"',b'type="int"'))

    def test_location_override_keeps_native_switch_fallback(self):
        original=b'''<BehaviorTrees><BehaviorTree name="crime_getPunishmentLocationGUID"><Variables/>
        <Parameters><Variable name="locationGUID" type="_string"/></Parameters><Root><Behavior>
        <Switch><DefaultBranch><Expression expressions="original"/></DefaultBranch></Switch>
        </Behavior></Root><EditorData/></BehaviorTree></BehaviorTrees>'''
        result=E.fromstring(location_adapter(original))
        selector=result.find('.//Root/Behavior/Selector')
        self.assertEqual(selector[1].tag,'Switch')
        self.assertEqual(selector[1].find('.//Expression').get('expressions'),'original')
        self.assertEqual(selector.find('.//LinkTagFilter').get('Data'),'$locationGUID')
        self.assertIsNone(result.find('.//EditorData'))
        with self.assertRaisesRegex(ValueError,'already present'): location_adapter(E.tostring(result))

    def test_inline_native_smart_area_does_not_require_duplicate_fubar(self):
        entity=E.fromstring('''<Entity EntityGuid="12345678-abcd-4321" Pos="100,200,10">
        <Area Height="5"><Points><Point Pos="0,0,0"/><Point Pos="10,0,0"/><Point Pos="0,10,0"/></Points></Area></Entity>''')
        area=area_geometry(entity,{})
        self.assertEqual(area.guid,guid_value(entity.get('EntityGuid')))
        self.assertEqual(area.points[1],(110,200,10))

    def test_selects_source_location_authority_not_visiting_faction(self):
        shape='<Area Height="100"><Points><Point Pos="0,0,0"/><Point Pos="100,0,0"/><Point Pos="100,100,0"/><Point Pos="0,100,0"/></Points></Area>'
        mission=E.fromstring('<Objects/>')
        district=E.SubElement(mission,'Entity',EntityGuid='00000001-0000-0000',EntityId='1',Pos='0,0,0')
        E.SubElement(district,'Properties',Label='crimeDistrict');district.append(E.fromstring(shape))
        location=E.SubElement(mission,'Entity',EntityGuid='00000002-0000-0000',EntityId='2',Pos='0,0,0')
        E.SubElement(location,'Properties',guidLocationId='location1');location.append(E.fromstring(shape))
        for i,pos in [(3,'20,20,1'),(4,'10,10,1')]:
            E.SubElement(mission,'Entity',EntityGuid=f'0000000{i}-0000-0000',EntityId=str(i),Pos=pos,EntityClass='NPC')
        files={'objects_mission0.xml':E.tostring(mission),'triggerareas.fubar':write_areas([],1),
               'whdata_0':b'<Root><SoulList><Souls><Soul><EntityGuid>00000003-0000-0000</EntityGuid><SharedSoulGuid>a</SharedSoulGuid></Soul><Soul><EntityGuid>00000004-0000-0000</EntityGuid><SharedSoulGuid>b</SharedSoulGuid></Soul></Souls></SoulList></Root>'}
        resources={'Libs/soul__test.xml':b'<root><soul soul_id="a" social_class_id="7" factionName="gluemapper_kcd1_faction_8"/><soul soul_id="b" social_class_id="7" factionName="gluemapper_kcd1_faction_9"/></root>',
                   'Libs/social_class.xml':b'<root><row social_class_id="7" soul_crime_role_id="2"/></root>',
                   'Libs/location__test.xml':b'<root><location location_id="location1" ui_type="Common"/></root>',
                   'source/faction.xml':b'<root><row faction_id="8" location_id="location1"/><row faction_id="9" location_id="another"/></root>'}
        records=[dict(path='objects/structures/pillory/'+n+'.cgf',position=[10,10,1],transform=list(range(12)),layer=1) for n in ('pillory','pillory_log')]
        result=choose_site(files,records,resources)
        self.assertEqual(result['guard'],'00000003-0000-0000')
        self.assertEqual(result['faction'],'gluemapper_kcd1_faction_8')
        self.assertEqual(result['location'],'location1')
        self.assertEqual(len(result['replaced']),2)


if __name__=='__main__':unittest.main()
