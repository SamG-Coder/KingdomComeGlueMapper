import sys
from pathlib import Path
import struct
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from building_brushes import is_uberlod_proxy
from remove_uberlod_proxies import filter_hlods


class ProxyTests(unittest.TestCase):
    def test_source_classification_does_not_discard_individual_lods(self):
        self.assertTrue(is_uberlod_proxy(r'Objects\Uber\forest.cgf'))
        self.assertFalse(is_uberlod_proxy('objects/vegetation/trees/tree_lod3.cgf'))
        self.assertFalse(is_uberlod_proxy('objects/props/uber_table.cgf'))

    def test_nested_offsets_and_retained_instances_survive_removal(self):
        proxy=struct.pack('<I',1)+bytes(100)
        retained=bytearray(proxy);retained[4]=1;retained=bytes(retained)
        tree=struct.pack('<I',2)+bytes(range(60))
        blocks=[b'',proxy,retained+tree]
        data=bytearray(struct.pack('<I',2));doc=ET.Element('HLods');parent=doc
        for block in blocks:
            parent=ET.SubElement(parent,'HLod',DataOffset=str(len(data)),DataSize=str(len(block)+4))
            data.extend(struct.pack('<I',len(block))+block)
        result,removed,before,after=filter_hlods(bytes(data),doc,{(proxy[4:28],proxy[44:92]):'objects/uber/forest.cgf'})
        self.assertEqual(removed,{'objects/uber/forest.cgf':1})
        self.assertEqual(before,{1:2,2:1});self.assertEqual(after,{1:1,2:1})
        self.assertEqual(result[16:],retained+tree)
        self.assertEqual([(n.get('DataOffset'),n.get('DataSize')) for n in doc.iter('HLod')],[('4','4'),('8','4'),('12','172')])


if __name__=='__main__':unittest.main()
