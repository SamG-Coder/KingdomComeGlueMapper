import copy
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as E

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from cinematic_scene_ground import conform,adapter,pair
from cinematic_scene import SceneIndex


def track(points):
    t=E.Element('Track',paramType='Position',Type='4')
    for axis in range(3):
        s=E.SubElement(t,'NewSubTrack',defaultValue='0,'+str(points[0][axis]))
        for i,p in enumerate(points):
            E.SubElement(s,'Key',time=str(i),value=f'{i},{p[axis]}',flags='73',ds='0.3,0',dd='0.3,0')
    return t


class GroundTests(unittest.TestCase):
    def test_source_clearance_and_xy_timing_preserved(self):
        t=track([(0,0,10.1),(4,2,10.3)])
        before=copy.deepcopy(t)
        report=conform(t,(100,200,40),lambda x,y:10,lambda x,y:50+(x-100)*.5+(y-200)*.2)
        self.assertEqual(E.tostring(t[0]),E.tostring(before[0]))
        self.assertEqual(E.tostring(t[1]),E.tostring(before[1]))
        self.assertAlmostEqual(pair(t[2][1].get('value'))[1],12.7)
        self.assertEqual([k.get('time') for k in t[2]],['0','1'])
        self.assertAlmostEqual(report['source_clearance_range'][1],.3)

    def test_planar_gradient_updates_height_tangents(self):
        t=track([(0,0,10),(1,0,10)])
        for k in t[0]:k.set('dd','0.3,0.3');k.set('ds','0.3,0.3')
        conform(t,(0,0,0),lambda x,y:10,lambda x,y:10+2*x)
        self.assertAlmostEqual(pair(t[2][0].get('dd'))[1],.6)
        self.assertAlmostEqual(pair(t[2][1].get('ds'))[1],.6)

    def test_equivalent_translated_ground_is_byte_identical(self):
        t=track([(2,3,10),(6,7,11)]);old=E.tostring(t)
        conform(t,(100,200,40),lambda x,y:8+x*.1,lambda x,y:48+(x-100)*.1)
        self.assertEqual(E.tostring(t),old)

    def test_hidden_underground_keys_are_not_raised(self):
        t=track([(1,2,100),(1,2,0)])
        before=E.tostring(t[2][1])
        conform(t,(0,0,0),lambda x,y:100,lambda x,y:110)
        self.assertEqual(E.tostring(t[2][1]),before)

    def test_unmatched_xyz_times_are_rejected(self):
        t=track([(0,0,0)]);t[1][0].set('time','1')
        with self.assertRaisesRegex(ValueError,'corresponding XYZ'):
            conform(t,(0,0,0),lambda x,y:0,lambda x,y:1)

    def test_shared_mocap_and_protected_variants_are_not_grounded(self):
        root=E.Element('Objects')
        seq=E.SubElement(E.SubElement(root,'Entity',EntityId='1',EntityGuid='0000000000000001',Layer='scene'),
                         'Sequence',Name='scene',SequenceVersion='3')
        nodes=E.SubElement(seq,'Nodes')
        for i,anim,body in [(2,'idle','resident'),(3,'scene_performer','performer'),(4,'idle','player_variant')]:
            g=f'{i:016x}'
            e=E.SubElement(root,'Entity',EntityId=str(i),EntityGuid=g,Name=f'actor{i}',EntityClass='AnimChar',Layer='scene',Pos='1,2,0')
            E.SubElement(E.SubElement(e,'Properties'),'Body',guidBodyPresetId=body)
            n=E.SubElement(nodes,'Node',EntityGUID=g);n.append(track([(1,2,0)]))
            E.SubElement(E.SubElement(n,'Track',paramType='Animation'),'Key',anim=anim)
        ix=SceneIndex({'layers/scene.xml':root},E.fromstring('<Root/>'))
        apply,r=adapter(ix,ix.plan('scene'),(0,0,0),lambda x,y:0,lambda x,y:2,{'0000000000000004'})
        self.assertEqual([a['entity'] for a in r['adjusted']],['actor2'])
        self.assertEqual(len(r['preserved']),2)
        converted=apply(copy.deepcopy(root[0]))
        self.assertEqual(pair(converted.find('Sequence/Nodes')[0].find("Track[@paramType='Position']")[2][0].get('value'))[1],2)
        for old,new in zip(root[0].find('Sequence/Nodes')[1:],converted.find('Sequence/Nodes')[1:]):
            self.assertEqual(E.tostring(old),E.tostring(new))
        e=copy.deepcopy(root[1]);e.find('Properties/Body').set('guidBodyPresetId','recast')
        self.assertEqual(apply(e).find('Properties/Body').get('guidBodyPresetId'),'recast')


if __name__=='__main__':unittest.main()
