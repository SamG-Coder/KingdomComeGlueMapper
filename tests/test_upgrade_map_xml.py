"""upgrade_map.xml produces ElementTree's bytes, document for document."""
import os
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from upgrade_map import read, xml

STEAM = Path(r'C:\Program Files (x86)\Steam\steamapps\common')
KCD1 = Path(os.environ.get('GLUEMAPPER_KCD1', STEAM / 'KingdomComeDeliverance'))
KCD2 = Path(os.environ.get('GLUEMAPPER_KCD2', STEAM / 'KingdomComeDeliverance2'))


def reference(element):
    return ET.tostring(element, encoding='utf-8', xml_declaration=True)


class SerializerTests(unittest.TestCase):
    def check(self, element):
        self.assertEqual(xml(element), reference(element))

    def test_plain_documents_match_elementtree(self):
        root = ET.Element('Objects', Version='2')
        entity = ET.SubElement(root, 'Entity', Name='a & b', Pos='1,2,3', Note='tab\there "quoted" <in> \r\n')
        entity.text = 'text with & < > " \' and \n newlines'
        entity.tail = '\n  tail & more'
        ET.SubElement(entity, 'Properties', value='')  # empty element with attributes
        ET.SubElement(entity, 'EntityLinks')  # empty element without attributes
        unicode_node = ET.SubElement(root, 'Name', text='Rataj\u00e9 \u0161 \U0001f3f0')
        unicode_node.text = '\u010de\u0161tina \U0001f3f0'
        root.append(ET.Comment(' a comment & more '))
        root.append(ET.ProcessingInstruction('target', 'data="x"'))
        wrapper = ET.Element(None)
        wrapper.text = 'loose'
        wrapper.append(ET.Element('inner', a='1'))
        root.append(wrapper)
        self.check(root)
        self.check(ET.fromstring(xml(root)))

    def test_attribute_order_and_lone_surrogates_follow_elementtree(self):
        root = ET.Element('r')
        root.set('z', '1'); root.set('a', '2'); root.set('m', '\ud800')
        root.text = 'bad \udfff surrogate'
        self.check(root)

    def test_namespaced_documents_use_the_stdlib_path(self):
        root = ET.fromstring('<Schedulers xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                             'xsi:noNamespaceSchemaLocation="s.xsd"><Row xml:lang="cs"/></Schedulers>')
        self.check(root)
        qname = ET.Element(ET.QName('http://example/ns', 'tag'))
        qname.set(ET.QName('http://example/ns', 'attr'), ET.QName('http://example/ns', 'value'))
        self.check(qname)

    def test_bad_values_raise_like_elementtree(self):
        root = ET.Element('r')
        root.text = 7
        with self.assertRaises(TypeError):
            reference(root)
        with self.assertRaises(TypeError):
            xml(root)

    @unittest.skipUnless((KCD1 / 'Data/Levels/rataje/level.pak').is_file() and (KCD2 / 'Data/Tables.pak').is_file(),
                         'KCD1 and KCD2 retail data not installed')
    def test_retail_documents_match_elementtree(self):
        checked = 0
        for archive, limit in ((KCD1 / 'Data/Levels/rataje/level.pak', 300), (KCD2 / 'Data/Levels/trosecko/level.pak', 300),
                               (KCD2 / 'Data/Tables.pak', 400)):
            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    if not info.filename.lower().endswith(('.xml', '.mtl', '.cdf', '.lyr')): continue
                    try: root = ET.fromstring(read(z, info.filename))
                    except ET.ParseError: continue
                    with self.subTest(archive=archive.name, entry=info.filename):
                        self.check(root)
                    checked += 1
                    if checked % limit == 0: break
        self.assertGreater(checked, 500)


if __name__ == '__main__':
    unittest.main()
