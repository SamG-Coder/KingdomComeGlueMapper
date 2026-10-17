"""Wearable source pickup components, with source meshes on the native rig."""
from collections import deque
from concurrent.futures import ProcessPoolExecutor
import copy
import xml.etree.ElementTree as ET
import zipfile
import tempfile
from pathlib import Path
from audit_character_bodies import Assets
from bounded_pool import worker_count
from build_npc_probe import asset_path, bind_material
from campaign_dependencies import ImportPlan
from character_person import native_xml
from character_person_appearance import align_internal_translation
from character_skin_upgrade import upgrade_skin, limit_skin_influences, CompiledSkin, matrix, read_morphs
from clothing_appearance import apply_armor_colorization
from clothing_regions import assign_upper_garment_regions, split_skin_regions, read_chunks
from garment_contracts import clothing_role, source_variant
from character_source_clothing import female_role
from upgrade_map import read, xml
from character_native_materials import bake_float_dds, convert_character_materials


def material_inputs(emitted, files):
    """Stage this item's files and shared texture families, not prior outfits."""
    selected=dict(files)
    textures=set()
    for name,payload in files.items():
        if not name.lower().endswith('.mtl'):continue
        root=ET.fromstring(payload.read_bytes() if isinstance(payload,Path) else payload)
        textures.update(t.get('File','').replace('\\','/').lower() for t in root.iter('Texture') if t.get('File'))
    for name,payload in emitted.items():
        key=name.replace('\\','/').lower()
        if any(key==texture or key.startswith(texture+'.') for texture in textures):
            selected.setdefault(name,payload)
    return selected


def retain_material_file(assets, name, path):
    # DependencyImporter supports Path payloads. Keep native float-DDS outputs
    # on disk just like the source textures, rather than retaining every
    # outfit's expanded textures in memory until the entire loot pass finishes.
    assets.emitted[name] = path.read_bytes()
    return assets.emitted[name]


class ClothingAdapter:
    # Source SQL armor_type is distinct from KCD2's enum. Resolve an explicit
    # semantic role, then inherit the native role's layer and archetype.
    ROLES = {'7': 'Cap', '11': 'HorseShoe'}

    def __init__(self, tables, assets, target, stack, defer_bakes=False):
        self.tables, self.assets = tables, assets
        self.native = Assets(target, stack)
        with zipfile.ZipFile(target/'Data/Tables.pak') as z:
            self.components = native_xml(read(z, 'Libs/Tables/Character/CharacterComponent.xml'))
        self.parents = {c:p for p in self.components.iter() for c in p}
        self.stack, self.defer, self.pool, self.pending = stack, defer_bakes, None, deque()

    def deferred_bake(self, folder, names):
        """Float macro bakes dominate the loot import and are pure; with
        defer_bakes, run them in worker processes while later items convert.
        The caller must then call finish() before reading any payload."""
        reserve = getattr(self.assets.emitted, 'reserve', None)
        if not self.defer or reserve is None: return bake_float_dds
        def bake(path, image, material, specular=None):
            name = path.relative_to(folder).as_posix(); names.append(name)
            if self.pool is None:
                # A 2048px bake holds several float32 RGBA copies and its mips.
                self.workers = worker_count(16, 768 * 1024**2, 'GLUEMAPPER_BAKE_WORKERS')
                self.pool = self.stack.enter_context(ProcessPoolExecutor(max_workers=self.workers))
            # Bound queued texture copies; arguments are pickled after submit().
            while len(self.pending) >= 2*self.workers: self.wait(*self.pending.popleft())
            args = (image.copy(), copy.deepcopy(material), None if specular is None else specular.copy())
            self.pending.append((name, self.pool.submit(bake_float_dds, reserve(name), *args)))
        return bake

    @staticmethod
    def wait(name, future):
        # Not ValueError: the importer would charge it to the item converting now.
        try: future.result()
        except Exception as error: raise RuntimeError('Wearable texture bake '+name+': '+str(error)) from error

    def finish(self):
        """Wait until every deferred texture has been written to its staged path."""
        while self.pending: self.wait(*self.pending.popleft())

    def plan(self, identity):
        armor = self.tables.row('item/armor', 'item_id', identity)
        part = self.tables.row('item/clothing', 'clothing_id', armor['clothing_id'])
        role = clothing_role(self.tables, armor, part)
        return ImportPlan(source=dict(armor=armor, part=part, role=role))

    def convert(self, identity, plan, registered):
        part, armor, role = (plan.source[k] for k in ('part','armor','role'))
        original = next(n for n in self.components.iter('Clothing') if n.get('Name') == role and n.get('ArmorType') == role)
        ancestor = original
        while not ancestor.get('ArmorArchetypeId'): ancestor = self.parents[ancestor]
        name = 'worlditems_cloth_'+identity.replace('-','_')
        prefix = 'objects/characters/gluetravel/worlditems/'+identity.replace('-','')+'/'
        root = ET.Element('database', name='barbora')
        horse = role in ('HorseShoe','Saddle','Bridle','Caparison')
        sex = 'female' if part.get('gender_id') == '2' else 'male'
        component = ET.SubElement(ET.SubElement(root,'CharacterComponents',version='6'),'Component',
            Name=name+'_root', Race='Horse' if horse else 'Human',
            FilePath=prefix.removeprefix('objects/characters/'))
        if not horse: component.set('Gender',sex.title())
        node = ET.SubElement(ET.SubElement(component,'DerivedComponents'),'Clothing',
            Name=name, ArmorType=role, ArmorArchetypeId=ancestor.get('ArmorArchetypeId'))
        files = {}
        missing_variant = None
        internal_offset = None
        if part.get('model'):
            a = self.assets; before = set(a.emitted)
            source_part = dict(part, kind='cloth')
            def resolve(field, extension):
                if not horse: return asset_path(a.index, source_part, field, extension)
                value = part[field].replace('\\','/').lower().removesuffix(extension)
                matches = [p for p in a.index if p.startswith('objects/characters/animals/horse/') and p.endswith('/'+value+extension)]
                if len(matches) != 1: raise ValueError('Ambiguous source horse equipment '+field+': '+value)
                return matches[0]
            model = resolve('model', '.skin')
            mat = a.material(resolve('material', '.mtl'))
            tinted = prefix+'source'
            a.emitted[tinted+'.mtl'] = xml(apply_armor_colorization(ET.parse(a.emitted[mat+'.mtl']).getroot(),armor))
            blob,mat = bind_material(a.mesh(model), tinted, a.emitted, prefix+'slots')
            # Some shipped gauntlets retain an 11 cm internal-buffer offset.
            # Every mapped vertex must agree on one translation; rendered
            # positions are never moved or approximated.
            blob,internal_offset = align_internal_translation(blob, max_translation=.25)
            skeleton,_ = self.native.get('objects/characters/animals/horse/horse.chr' if horse else f'objects/characters/humans/{sex}/skeleton/{sex}.chr')
            available = [m['name'] for c in read_chunks(blob) if c.kind == 0x2002 for m in read_morphs(c)]
            variant, missing_variant = source_variant(part.get('morph_target'), available)
            blob,detail = upgrade_skin(blob,skeleton,variant=variant,
                discard_unused_variants=True,geometry_mode='preserve',clear_hiding=True)
            blob,_ = limit_skin_influences(blob)
            region = 'head'
            if horse: region = 'horse_back' if role in ('Saddle','Caparison') else 'horse_head'
            elif sex == 'female': region = female_role(part)[1]
            elif role in ('HoseJoined','HoseSeparate','LegsChain','LegsPlate','LegsBrigandine','LegsPadded'): region = 'legs'
            elif role in ('Shoes','BootsAnkle','BootsKnee'): region = 'feet'
            elif role in ('Gauntlets','Gloves'): region = 'hands'
            elif role in ('ArmPlate','ArmBrigandine'): region = 'arms'
            elif role in ('Hood','CollarMail','CollarPadded'): region = 'neck'
            regions = {region:blob}
            if sex == 'male' and role in ('Habit','TunicLong','TunicShort','GambesonLong','GambesonShort','MailShort','Cuirass','Waffenrock','Coat'):
                bones = CompiledSkin(blob).info['bones']
                waist = float(matrix(next(b for b in bones if b['name']=='Hips')['bind'])[2,3])
                regions = split_skin_regions(blob,assign_upper_garment_regions(blob,waist_z=waist))
                if 'arms' in regions:
                    # Native sleeved tunics use this group to hide just the
                    # underlying upper arm, retaining hands and bare forearms.
                    node.set('HidingGroups', 'MaleBodyUpperArms')
            elements = ET.SubElement(node,'Elements')
            # Native layer values are inherited by role; do not reuse KCD1's
            # numerical layers as KCD2 layers.
            for region,skin in regions.items():
                ancestor = original; layer = None
                while ancestor is not None and layer is None:
                    e = ancestor.find("Elements/SkinElement[@EquipmentPart='"+region+"']")
                    if e is not None: layer = e.get('BodyLayerId')
                    ancestor = self.parents.get(ancestor)
                files[prefix+region+'.skin'] = skin
                element = ET.SubElement(elements,'SkinElement',EquipmentPart=region,Model=region+'.skin',
                    Material='source.mtl',KeepBodyLayer=str(region not in ('torso','legs','feet')).lower(),IsFinalLayer='true')
                if layer is not None: element.set('BodyLayerId',layer)
            files.update({n:a.emitted[n] for n in a.emitted if n not in before})
            files[prefix+'source.mtl'] = a.emitted[mat+'.mtl']
            # Wearable items need the same native shader/vertex-feature upgrade
            # as fixed NPC outfits. Legacy Illum permutations are not a native
            # clothing contract, even when the item table imports successfully.
            if not horse:
                with tempfile.TemporaryDirectory(prefix='wearable-materials-') as temporary:
                    folder = Path(temporary)
                    # Material textures can be shared with an earlier item in
                    # this import batch; include their already-emitted files.
                    for path, payload in material_inputs(a.emitted,files).items():
                        dest = folder/path; dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(payload.read_bytes() if isinstance(payload, Path) else payload)
                    (folder/'Libs/Tables/Character').mkdir(parents=True,exist_ok=True)
                    baked = []
                    material_report = convert_character_materials(folder, component, self.native, name, sex,
                                                                  self.deferred_bake(folder, baked))
                    for path in folder.rglob('*'):
                        if path.is_file():
                            relative = path.relative_to(folder).as_posix()
                            if relative.startswith(prefix) or relative.startswith('Libs/Tables/Character/ClothingMaterial__'):
                                files[relative] = retain_material_file(a,relative,path)
                    for relative in baked:
                        if relative.startswith(prefix): files[relative] = a.emitted[relative]
        elif role not in ('HorseShoe','Ring','Necklace','Spurs'): raise ValueError('Source wearable is missing a model')
        files['Libs/Tables/Character/CharacterComponent__'+name+'.xml'] = xml(root)
        return name, files, dict(role=role,source_clothing=part['clothing_id'],source_geometry_preserved=True,
            missing_source_variant=missing_variant, internal_translation=internal_offset, fitting_runtime_verified=False)
