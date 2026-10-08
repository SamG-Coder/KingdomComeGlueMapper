"""Translate legacy NPC effect masks into native KCD2 material inputs."""
import copy
import io
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image
import numpy as np

from clothing_regions import Skin, Chunk, write_chunks


def read_dds_family(path):
    """Decode a CryEngine DDS with its numbered high-resolution mip payloads."""
    path = Path(path)
    data = bytearray(path.read_bytes())
    if data[:4] != b'DDS ' or len(data) < 128:
        raise ValueError('Invalid DDS')
    header_size = 148 if data[84:88] == b'DX10' else 128
    if len(data) < header_size:
        raise ValueError('Truncated DDS header')
    members = sorted((p for p in path.parent.glob(path.name+'.*') if p.suffix[1:].isdigit()),
                     key=lambda p: int(p.suffix[1:]), reverse=True)
    if members:
        numbers = sorted(int(p.suffix[1:]) for p in members)
        if numbers != list(range(1, max(numbers)+1)):
            raise ValueError('Incomplete split DDS mip family')
        data = data[:header_size]+b''.join(p.read_bytes() for p in members)+data[header_size:]
    if header_size == 148:
        # Pillow supports the identical block layout under the UNORM enum. This
        # preserves encoded channel bytes; mask channels are not colour-managed.
        dxgi = struct.unpack_from('<I', data, 128)[0]
        struct.pack_into('<I', data, 128, {72:71, 75:74, 78:77, 99:98}.get(dxgi, dxgi))
    image = Image.open(io.BytesIO(data))
    image.load()
    return image.convert('RGBA')


def legacy_bg_mask(image):
    """KCD1 HumanSkin: alpha=blood, green=dirt; native BG: R=blood,G=grime."""
    _, green, _, alpha = image.convert('RGBA').split()
    return Image.merge('RGBA', (alpha, green, Image.new('L', image.size, 0), Image.new('L', image.size, 255)))


def native_skin_material(source, mask_path, native_reference):
    """Retain source surface maps and add native blood/grime parameters."""
    result = copy.deepcopy(source)
    reference = native_reference.find('PublicParams')
    if reference is None:
        raise ValueError('Native skin reference lacks effect parameters')
    params = {k:v for k,v in reference.attrib.items() if k.startswith(('Blood','Grime'))}
    if not params or 'BloodTintCenter' not in params or 'GrimeDiffuse' not in params:
        raise ValueError('Incomplete native skin effect parameters')
    for material in result.iter('Material'):
        if material.get('Shader', '').lower() != 'humanskin': continue
        textures = material.find('Textures')
        if textures is None: textures = ET.SubElement(material, 'Textures')
        for texture in list(textures):
            if texture.get('Map') == 'Emittance': textures.remove(texture)
        ET.SubElement(textures, 'Texture', Map='Emittance', File=mask_path)
        public = material.find('PublicParams')
        if public is None: public = ET.SubElement(material, 'PublicParams')
        public.attrib.update(params)
    return result


def native_vertex_features(blob, feature=0):
    """Use per-mesh macro textures and a native feature; keep skinning untouched.

    KCD2 encodes macro index in blue's upper three bits. Index 7 selects the
    material's own maps. Alpha 255 disables hiding groups for this fixed outfit.
    """
    if not 0 <= feature < 27:
        raise ValueError('Native feature must be in 0..26')
    skin = Skin(blob)
    colors = skin.streams.get(3)
    payload = struct.pack('<6I', 0, 3, skin.vertex_count, 4, 0, 0)
    payload += bytes((255, 255, 224 + feature, 255)) * skin.vertex_count
    if colors:
        if struct.unpack_from('<I', colors.data, 12)[0] != 4:
            raise ValueError('Expected four-byte vertex colours')
        return write_chunks(skin.chunks, {colors.id: payload})
    ident = max(c.id for c in skin.chunks) + 1
    mesh = bytearray(skin.mesh.data)
    struct.pack_into('<I', mesh, 28 + 3 * 4, ident)
    return write_chunks(skin.chunks + [Chunk(0x1016, 0x800, ident, payload)],
                        {skin.mesh.id: bytes(mesh)})


def transform_hsv(rgb, hue=0, saturation=1, brightness=1):
    """Vectorized equivalent of the engine's linear-colour HSV transform."""
    v = brightness
    u = v*saturation*np.cos(np.deg2rad(hue))
    w = v*saturation*np.sin(np.deg2rad(hue))
    matrix = np.array([
        [.299*v+.701*u+.168*w, .587*v-.587*u+.330*w, .114*v-.114*u-.497*w],
        [.299*v-.299*u-.328*w, .587*v+.413*u+.035*w, .114*v-.114*u+.292*w],
        [.299*v-.300*u+1.25*w, .587*v-.588*u-1.05*w, .114*v+.886*u-.203*w]])
    return np.clip(rgb @ matrix.T, 0, 1)


def bake_macro_diffuse(diffuse, material, specular=None):
    """Bake source tint and linear surface colour for KCD2 macro overlay input."""
    pixels = np.asarray(diffuse.convert('RGBA')).astype(np.float32)/255
    rgb = pixels[..., :3]
    rgb = np.where(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)**2.4)
    flags = material.get('StringGenMask', '').split('%')
    params = material.find('PublicParams')
    params = params.attrib if params is not None else {}
    if 'COLORIZING_WITH_MASK' in flags:
        # Specular sampling is linear; its green channel stores the zone index.
        if specular is None:
            zones = np.zeros(rgb.shape[:2], dtype=np.uint8)
        else:
            mask = np.asarray(specular.resize(diffuse.size).convert('RGB'))[..., 1]/255
            zones = np.floor(mask*3.9999).astype(np.uint8)
        for zone in range(4):
            keys = ('ColorizingHue','ColorizingSaturation','ColorizingBrightness') if zone == 0 else (
                f'MaskHue{zone}', f'MaskSaturation{zone}', f'MaskBrightness{zone}')
            selected = zones == zone
            rgb[selected] = transform_hsv(rgb[selected], *(float(params.get(k,d)) for k,d in zip(keys,(0,1,1))))
    elif 'COLORIZING' in flags:
        rgb = transform_hsv(rgb, *(float(params.get(k,d)) for k,d in zip(
            ('ColorizingHue','ColorizingSaturation','ColorizingBrightness'), (0,1,1))))
    rgb *= np.array([float(v) for v in material.get('Diffuse','1,1,1').split(',')])
    # Float DDS is sampled linearly. Invert the native macro fallback's 1/2.2
    # exponent directly. Eight-bit output crushes dark channels after this
    # conversion, visibly turning brown leather red; retain half-float precision.
    pixels[..., :3] = np.clip(rgb,0,1)**2.2
    return pixels.astype(np.float16)


def write_float_dds(path, pixels):
    """Write a standard DX10 RGBA16_FLOAT DDS with a complete mip chain."""
    pixels = np.asarray(pixels, dtype=np.float16)
    if pixels.ndim != 3 or pixels.shape[2] != 4 or not np.isfinite(pixels).all():
        raise ValueError('Expected finite H x W x RGBA pixels')
    height, width, _ = pixels.shape
    if not width or not height: raise ValueError('Empty texture')
    levels = [pixels]
    while min(levels[-1].shape[:2]) > 1 or max(levels[-1].shape[:2]) > 1:
        previous = levels[-1].astype(np.float32)
        h,w = max(1,previous.shape[0]//2),max(1,previous.shape[1]//2)
        channels = [np.asarray(Image.fromarray(previous[..., c]).resize((w,h),Image.Resampling.BOX)) for c in range(4)]
        levels.append(np.stack(channels,axis=-1).astype(np.float16))
    header = bytearray(148)
    header[:4] = b'DDS '
    struct.pack_into('<7I',header,4,124,0x2100F,height,width,width*8,0,len(levels))
    struct.pack_into('<2I4s',header,76,32,4,b'DX10')
    struct.pack_into('<I',header,108,0x401008)
    struct.pack_into('<5I',header,128,10,3,0,1,0)
    Path(path).write_bytes(header+b''.join(level.astype('<f2').tobytes() for level in levels))


def native_clothing_material(source, diffuse_path, mask_path, reference, feature_name):
    """Bind one legacy surface to native Illum features and effects."""
    if source.get('Shader','').lower() != 'illum':
        raise ValueError('Only Illum clothing is supported')
    result = copy.deepcopy(source)
    original = set(source.get('StringGenMask','').split('%'))
    flags = ['CLOTHING_SYSTEM'] + [f for f in ('NORMAL_MAP','SPECULAR_MAP','SUBSURFACE_SCATTERING') if f in original]
    result.set('StringGenMask', ''.join('%'+f for f in flags))
    result.attrib.pop('GenMask',None)
    result.set('Diffuse','1,1,1')
    textures = result.find('Textures')
    if textures is None: textures = ET.SubElement(result,'Textures')
    for t in list(textures):
        if t.get('Map') not in ('Bumpmap','Specular'): textures.remove(t)
    ET.SubElement(textures,'Texture',Map='Diffuse',File=diffuse_path)
    ET.SubElement(textures,'Texture',Map='Custom',File=mask_path)
    old = result.find('PublicParams')
    if old is not None: result.remove(old)
    result.append(copy.deepcopy(reference.find('PublicParams')))
    for tag in ('FeatureSlots','Decals'):
        for node in list(result.findall(tag)): result.remove(node)
    ET.SubElement(result,'FeatureSlots',Slot00=feature_name)
    return result
