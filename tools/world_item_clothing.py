"""Wearable source pickup components, with source meshes on the native rig."""
from collections import deque
from concurrent.futures import ProcessPoolExecutor
import copy
import os
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
from character_layer_contract import region_layers, project_regions, source_mesh_regions, source_mesh_region
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
        # A family member is the texture itself or the texture with a '.N' mip suffix;
        # test each dot-prefix against the set instead of every texture against the key.
        if key in textures or any(key[:i] in textures for i,c in enumerate(key) if c=='.'):
            selected.setdefault(name,payload)
    return selected


def retain_material_file(assets, name, path):
    # DependencyImporter supports Path payloads. Keep conversion outputs on disk
    # just like the source textures, rather than retaining every outfit's
    # expanded textures in memory until the entire loot pass finishes. A staged
    # cache on the same volume takes the file itself; reading it back and
    # writing it again would cost two more scanned opens per file.
    reserve = getattr(assets.emitted, 'reserve', None)
    if reserve is not None:
        staged = reserve(name)
        try:
            os.replace(path, staged)
            return staged
        except OSError:
            pass
    assets.emitted[name] = path.read_bytes()
    return assets.emitted[name]


def converted_files(folder):
    """Every file under folder as (posix path relative to folder, Path), without a stat per file."""
    for root, _, names in os.walk(folder):
        for name in names:
            path = Path(root)/name
            yield path.relative_to(folder).as_posix(), path


class ClothingAdapter:
    # Source SQL armor_type is distinct from KCD2's enum. Resolve an explicit
    # semantic role, then inherit the native role's layer and archetype.
    ROLES = {'7': 'Cap', '11': 'HorseShoe'}

    def __init__(self, tables, assets, target, stack, defer_bakes=False):
        self.tables, self.assets = tables, assets
        self.native = Assets(target, stack)
        with zipfile.ZipFile(target/'Data/Tables.pak') as z:
            self.components = native_xml(read(z, 'Libs/Tables/Character/CharacterComponent.xml'))
            from clothing_equipment import NativeEquipment
            self.equipment = NativeEquipment.from_archive(z)
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
        native_layers = region_layers(original, self.parents)
        ancestor = original
        while not ancestor.get('ArmorArchetypeId'): ancestor = self.parents[ancestor]
        name = 'worlditems_cloth_'+identity.replace('-','_')
        prefix = 'objects/characters/gluetravel/worlditems/'+identity.replace('-','')+'/'
        root = ET.Element('database', name='barbora')
        horse = role in ('HorseShoe','Saddle','Bridle','Caparison')
        sex = 'female' if part.get('gender_id') == '2' else 'male'
        equipment_contract = None
        armor_archetype = ancestor.get('ArmorArchetypeId')
        if sex == 'female':
            region = female_role(part, self.tables)[1]
            equipment_contract = self.equipment.source_contract(part, self.tables, armor_archetype, region)
            armor_archetype = equipment_contract['archetype']
            native_layers = {region: equipment_contract['layer']}
        component = ET.SubElement(ET.SubElement(root,'CharacterComponents',version='6'),'Component',
            Name=name+'_root', Race='Horse' if horse else 'Human',
            FilePath=prefix.removeprefix('objects/characters/'))
        if not horse: component.set('Gender',sex.title())
        node = ET.SubElement(ET.SubElement(component,'DerivedComponents'),'Clothing',
            Name=name, ArmorType=role, ArmorArchetypeId=armor_archetype)
        files = {}
        missing_variant = None
        internal_offset = None
        if part.get('model'):
            if sex != 'female':
                native_layers = source_mesh_regions(original, self.parents, self.equipment, armor_archetype)
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
            elif sex == 'female': region = female_role(part, self.tables)[1]
            elif role in ('HoseJoined','HoseSeparate','LegsChain','LegsPlate','LegsBrigandine','LegsPadded'): region = 'legs'
            elif role in ('Shoes','BootsAnkle','BootsKnee'): region = 'feet'
            elif role in ('Gauntlets','Gloves'): region = 'hands'
            elif role in ('ArmPlate','ArmBrigandine'): region = 'arms'
            elif role in ('Hood','CollarMail','CollarPadded'): region = 'neck'
            regions = {region:blob}
            if sex == 'male' and role in ('Habit','TunicLong','TunicShort','GambesonLong','GambesonShort','MailShort','Cuirass','Waffenrock','Coat'):
                bones = CompiledSkin(blob).info['bones']
                waist = float(matrix(next(b for b in bones if b['name']=='Hips')['bind'])[2,3])
                regions = split_skin_regions(blob, project_regions(
                    assign_upper_garment_regions(blob, waist_z=waist), native_layers))
                if 'arms' in regions:
                    # Native sleeved tunics use this group to hide just the
                    # underlying upper arm, retaining hands and bare forearms.
                    node.set('HidingGroups', 'MaleBodyUpperArms')
            else:
                regions = {source_mesh_region(region, native_layers): blob}
            elements = ET.SubElement(node,'Elements')
            # Native layer values are inherited by role; do not reuse KCD1's
            # numerical layers as KCD2 layers.
            for region,skin in regions.items():
                if region not in native_layers:
                    raise ValueError('Source garment region is absent from native role: '+role+'/'+region)
                layer = native_layers[region]
                files[prefix+region+'.skin'] = skin
                element = ET.SubElement(elements,'SkinElement',EquipmentPart=region,Model=region+'.skin',
                    # Female bodies contain arms, hands and legs in their torso
                    # element. A dress cannot replace that whole body layer:
                    # unlike the split male body, it also removes exposed limbs.
                    Material='source.mtl',KeepBodyLayer=str(sex == 'female' or region not in ('torso','legs','feet')).lower(),IsFinalLayer='true')
                if layer is not None: element.set('BodyLayerId',layer)
            files.update({n:a.emitted[n] for n in a.emitted if n not in before})
            files[prefix+'source.mtl'] = a.emitted[mat+'.mtl']
            # Wearable items need the same native shader/vertex-feature upgrade
            # as fixed NPC outfits. Legacy Illum permutations are not a native
            # clothing contract, even when the item table imports successfully.
            if not horse:
                # Work beside the staging cache: staged textures are then hard
                # links and outputs move into the cache, instead of every file
                # being written, read back and written again (~1 ms per open
                # under on-access scanning, a few hundred files per item).
                staging = getattr(a.emitted, 'directory', None)
                with tempfile.TemporaryDirectory(prefix='wearable-materials-',
                                                 dir=staging.parent if staging is not None else None) as temporary:
                    folder = Path(temporary)
                    linked = {}
                    # Material textures can be shared with an earlier item in
                    # this import batch; include their already-emitted files.
                    for path, payload in material_inputs(a.emitted,files).items():
                        dest = folder/path; dest.parent.mkdir(parents=True, exist_ok=True)
                        # The conversion only reads textures; it rewrites materials and meshes in place.
                        if staging is not None and isinstance(payload, Path) and '.dds' in path.lower():
                            try:
                                os.link(payload, dest); linked[path] = dest.stat()
                                continue
                            except OSError:
                                pass
                        dest.write_bytes(payload.read_bytes() if isinstance(payload, Path) else payload)
                    (folder/'Libs/Tables/Character').mkdir(parents=True,exist_ok=True)
                    baked = []
                    material_report = convert_character_materials(folder, component, self.native, name, sex,
                                                                  self.deferred_bake(folder, baked))
                    for relative, path in converted_files(folder):
                        if not (relative.startswith(prefix) or relative.startswith('Libs/Tables/Character/ClothingMaterial__')):
                            continue
                        if relative in linked:
                            after = path.stat()
                            if (after.st_size, after.st_mtime_ns) != (linked[relative].st_size, linked[relative].st_mtime_ns):
                                raise RuntimeError('Wearable texture was rewritten in place: '+relative)
                            files[relative] = a.emitted[relative]
                        else:
                            files[relative] = retain_material_file(a,relative,path)
                    for relative in baked:
                        if relative.startswith(prefix): files[relative] = a.emitted[relative]
        elif role not in ('HorseShoe','Ring','Necklace','Spurs'): raise ValueError('Source wearable is missing a model')
        files['Libs/Tables/Character/CharacterComponent__'+name+'.xml'] = xml(root)
        return name, files, dict(role=role,source_clothing=part['clothing_id'],source_geometry_preserved=True,
            missing_source_variant=missing_variant, internal_translation=internal_offset,
            equipment=equipment_contract, fitting_runtime_verified=False)
