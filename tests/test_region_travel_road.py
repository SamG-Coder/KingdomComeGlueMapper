import math
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from region_travel_road import road_spawn_points, inside_mesh


def road(width=4., rendered_width=None):
    actual=width if rendered_width is None else rendered_width
    vertices=[(-20,-actual/2,0),(20,-actual/2,0),(20,actual/2,0),(-20,actual/2,0)]
    indices=[0,1,2,0,2,3]
    header=bytearray(112)
    struct.pack_into('<I6f',header,0,11,-20,-width/2,0,20,width/2,0)
    struct.pack_into('<5I',header,84,4,6,4,0,4)
    payload=header+b''.join(struct.pack('<6e',*p,0,0,0) for p in vertices)
    payload+=struct.pack('<6H',*indices)+bytes(64)
    payload+=b''.join(struct.pack('<3f',*p) for p in [(-20,-width/2,0),(-20,width/2,0),(20,-width/2,0),(20,width/2,0)])
    data=struct.pack('<HBB6fI',8,0,0,-20,-width/2,0,20,width/2,0,len(payload))+payload
    return SimpleNamespace(version=29,data=data,tree_offset=0,tables={'materials':{'paths':['road']}})


class RoadArrivalTests(unittest.TestCase):
    def test_uses_road_centre_with_coach_clearance(self):
        source=road();before=source.data
        result=road_spawn_points(source,(0,0,0),[((3,-1,0),6)],lambda x,y:0)
        first=result['candidates'][0]['position']
        self.assertAlmostEqual(first[1],0)
        self.assertLess(first[0],-4.9)
        self.assertGreaterEqual(math.dist(first[:2],(3,-1)),6)
        self.assertEqual(before,source.data)
        for item in result['candidates']:
            self.assertLessEqual(math.dist(item['position'][:2],(0,0)),12)
            self.assertEqual(item['footprint_size'],[3,1.4])
    def test_rejects_narrow_tracks_and_incomplete_footprints(self):
        for source in (road(width=1),road(rendered_width=1)):
            with self.assertRaisesRegex(ValueError,'No road footprint'):
                road_spawn_points(source,(0,0,0),[],lambda x,y:0)
    def test_rejects_steep_ground_and_blocked_road(self):
        for obstacles,height in (([],lambda x,y:x),([((0,0,0),30)],lambda x,y:0)):
            with self.assertRaisesRegex(ValueError,'No road footprint'):
                road_spawn_points(road(),(0,0,0),obstacles,height)
    def test_bounds_do_not_count_as_mesh_coverage(self):
        triangles=np.array([[[0,0,0],[2,0,0],[0,2,0]]],dtype=float)
        self.assertTrue(inside_mesh([(0.5,0.5)],triangles))
        self.assertFalse(inside_mesh([(1.5,1.5)],triangles))
        self.assertFalse(inside_mesh([(0,0)],np.zeros((1,3,3))))


if __name__=='__main__':unittest.main()
