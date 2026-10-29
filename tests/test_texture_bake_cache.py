from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as E
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import bake_pool
import setup_progress


class TextureBakeCacheTests(unittest.TestCase):
    def test_bake_key_ignores_only_parameters_the_pixel_baker_does_not_read(self):
        from npc_effects import bake_macro_diffuse
        import numpy as np
        image = Image.new('RGBA', (8, 8), (180, 90, 60, 200))
        first = E.fromstring('<Material StringGenMask="%NORMAL_MAP"><PublicParams Roughness=".2" MaskHue1="3"/></Material>')
        second = E.fromstring('<Material StringGenMask="%SPECULAR_MAP"><PublicParams Roughness=".9" MaskHue1="90"/></Material>')
        spec = Image.new('RGBA', (8,8), (20,100,60,255))
        self.assertEqual(bake_pool.TextureBakeCache.key(image, first, None), bake_pool.TextureBakeCache.key(image, second, spec))
        np.testing.assert_array_equal(bake_macro_diffuse(image, first), bake_macro_diffuse(image, second, spec))
        second.set('StringGenMask', '%COLORIZING_WITH_MASK')
        self.assertNotEqual(bake_pool.TextureBakeCache.key(image, first, None), bake_pool.TextureBakeCache.key(image, second, spec))

    def test_reference_shares_one_texture_but_keeps_tint_and_alpha_distinct(self):
        image = Image.new('RGBA', (4, 4), (180, 90, 60, 255))
        with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(1) as executor, \
             patch.object(bake_pool, 'pool', return_value=executor):
            with bake_pool.Bakes() as batch:
                batch.plan(4)
                first = batch.reference(Path(tmp)/'first.dds', image, E.Element('Material'))
                duplicate = batch.reference(Path(tmp)/'duplicate.dds', image.copy(), E.Element('Material'))
                tinted = batch.reference(Path(tmp)/'tinted.dds', image, E.Element('Material', Diffuse='.5,1,1'))
                alpha = batch.reference(Path(tmp)/'alpha.dds', Image.new('RGBA', (4,4),(180,90,60,0)), E.Element('Material'))
            self.assertEqual(first, duplicate)
            self.assertFalse((Path(tmp)/'duplicate.dds').exists())
            self.assertEqual(len({p.read_bytes() for p in (first, tinted, alpha)}), 3)
            self.assertEqual(batch.stats, dict(completed=4, total=4, reused=1))

    def test_identical_inflight_bakes_are_submitted_only_once(self):
        cache=bake_pool.TextureBakeCache();image=Image.new('RGBA',(4,4),(100,200,80,255))
        try:
            with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(1) as executor, \
                 patch.object(bake_pool,'pool',return_value=executor), \
                 patch.object(bake_pool,'shared_cache',return_value=cache), \
                 patch.object(bake_pool,'bake_float_dds',wraps=bake_pool.bake_float_dds) as bake:
                with bake_pool.Bakes(reuse=True) as batch:
                    batch.plan(3)
                    for i in range(3):batch(Path(tmp)/f'{i}.dds',image,E.Element('Material'))
                self.assertEqual(bake.call_count,1)
                self.assertEqual(batch.stats,dict(completed=3,total=3,reused=2))
                self.assertEqual(len({p.read_bytes() for p in Path(tmp).iterdir()}),1)
        finally:cache.close()

    def test_reuses_completed_pixels_across_people_without_sharing_mutable_files(self):
        image=Image.new('RGBA',(4,4),(180,90,60,255))
        cache=bake_pool.TextureBakeCache()
        try:
            with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(1) as executor, \
                 patch.object(bake_pool,'pool',return_value=executor), \
                 patch.object(bake_pool,'shared_cache',return_value=cache), \
                 patch.object(bake_pool,'bake_float_dds',wraps=bake_pool.bake_float_dds) as bake:
                first=Path(tmp)/'first.dds'; second=Path(tmp)/'second.dds'
                for path,name in ((first,'person_one'),(second,'person_two')):
                    with bake_pool.Bakes(reuse=True) as batch:
                        batch.plan(1)
                        batch(path,image,E.Element('Material',Name=name,Diffuse='1,1,1'))
                self.assertEqual(bake.call_count,1)
                self.assertEqual(batch.stats,dict(completed=1,total=1,reused=1))
                self.assertEqual(first.read_bytes(),second.read_bytes())
                original=first.read_bytes(); second.write_bytes(b'edited output')
                restored=Path(tmp)/'restored.dds'
                with bake_pool.Bakes(reuse=True) as batch:
                    batch(restored,image,E.Element('Material',Diffuse='1,1,1'))
                self.assertEqual(restored.read_bytes(),original)
        finally:cache.close()

    def test_keys_keep_tint_mask_alpha_and_dimensions_distinct(self):
        image=Image.new('RGBA',(4,4),(255,200,100,255))
        material=E.fromstring('<Material Diffuse="1,1,1" StringGenMask="%COLORIZING_WITH_MASK"><PublicParams MaskHue1=".1"/></Material>')
        spec=Image.new('RGBA',(4,4),(1,2,3,255))
        key=bake_pool.TextureBakeCache.key
        original=key(image,material,spec)
        for attribute,value in (('Diffuse','.5,1,1'),('StringGenMask','%COLORIZING')):
            changed=E.fromstring(E.tostring(material)); changed.set(attribute,value)
            self.assertNotEqual(key(image,changed,spec),original)
        changed=E.fromstring(E.tostring(material));changed.find('PublicParams').set('MaskHue1','.2')
        self.assertNotEqual(key(image,changed,spec),original)
        for changed_image in (Image.new('RGBA',(4,4),(255,200,100,0)), image.resize((2,8))):
            self.assertNotEqual(key(changed_image,material,spec),original)
        self.assertNotEqual(key(image,material,Image.new('RGBA',(4,4),(1,40,3,255))),original)

    def test_bounded_cache_evicts_old_entries(self):
        cache=bake_pool.TextureBakeCache(limit=8)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source=Path(tmp)/'source';source.write_bytes(b'12345')
                cache.put('a',source);cache.put('b',source)
                self.assertEqual(list(cache.entries),['b'])
                self.assertEqual(cache.size,5)
                self.assertFalse((Path(cache.folder.name)/'a').exists())
        finally:cache.close()

    def test_failed_bake_is_not_cached_or_reported_complete(self):
        cache=bake_pool.TextureBakeCache();events=[]
        try:
            with ThreadPoolExecutor(1) as executor, patch.object(bake_pool,'pool',return_value=executor), \
                 patch.object(bake_pool,'shared_cache',return_value=cache), \
                 patch.object(bake_pool,'bake_float_dds',side_effect=ValueError('failed pixels')), \
                 patch.object(setup_progress,'work_sink',lambda *args:events.append(args)):
                with self.assertRaisesRegex(RuntimeError,'failed pixels'):
                    with bake_pool.Bakes(reuse=True) as batch:
                        batch.plan(1);batch(Path('unused.dds'),Image.new('RGBA',(4,4)),E.Element('Material'))
                self.assertFalse(cache.entries);self.assertEqual(batch.completed,0)
                self.assertEqual(events[0][1:3],(0,1))
        finally:cache.close()


if __name__=='__main__':unittest.main()
