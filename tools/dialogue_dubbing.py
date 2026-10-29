"""Runtime voice identities and the KCD2 English dubbing index contract."""
import copy
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib

from upgrade_map import read, xml

DEPOT = 'dialog/response_depot_wave_file.xml'


def ogg_duration(data, *, allow_missing_eos=False):
    """Read duration from a single Vorbis stream's final sample granule.

    Native archive recordings may omit EOS. Only their relocation adapter opts
    in, and it independently requires matching native dubbing-index duration.
    The source recording importer remains strict by default.
    """
    offset=0
    serial=None
    packet=bytearray()
    rate=None
    last=None
    eos=False
    while offset<len(data):
        if data[offset:offset+4]!=b'OggS' or offset+27>len(data) or data[offset+4]!=0:
            raise ValueError('Invalid Ogg page')
        stream=struct.unpack_from('<I',data,offset+14)[0]
        if serial is None:serial=stream
        if stream!=serial:raise ValueError('Chained or multiplexed Ogg is unsupported')
        segments=data[offset+26]
        if offset+27+segments>len(data):raise ValueError('Truncated Ogg lacing')
        lace=data[offset+27:offset+27+segments]
        start=offset+27+segments
        end=start+sum(lace)
        if end>len(data):raise ValueError('Truncated Ogg payload')
        if rate is None:
            for size in lace:
                packet.extend(data[start:start+size]);start+=size
                if size<255:
                    if len(packet)<16 or packet[:7]!=b'\x01vorbis':
                        raise ValueError('Expected Vorbis identification header')
                    rate=struct.unpack_from('<I',packet,12)[0]
                    break
        granule=struct.unpack_from('<Q',data,offset+6)[0]
        if granule!=0xffffffffffffffff:last=granule
        eos=bool(data[offset+5]&4)
        offset=end
    if (not eos and not allow_missing_eos) or not rate or last is None or last<=0:
        raise ValueError('Incomplete Ogg timing')
    return last/rate


def _native_root(target):
    """The native English dubbing index and up to 32 native recording stems."""
    roots=[]
    samples=[]
    for pak in sorted((Path(target)/'Localization').glob('*.pak')):
        if not (pak.name.lower().startswith('english-') or pak.name.lower() in ('english.pak','ipl_english.pak')):
            continue
        with zipfile.ZipFile(pak) as archive:
            names=archive.namelist()
            if DEPOT in names:roots.append(ET.fromstring(read(archive,DEPOT)))
            if not samples:samples=[n.rsplit('/',1)[-1][:-4] for n in names if n.endswith('.ogg')][:32]
    if len(roots)!=1:raise ValueError('Expected one native English dubbing index')
    return roots[0],samples


# Serialized native index per unchanged set of English archives. A build merges into
# it a dozen times; reopening the 65k-entry archives and copying and serializing the
# whole native tree each time cost about 5 s per merge.
_NATIVE={}


def _native_index(target):
    paks=[p for p in sorted((Path(target)/'Localization').glob('*.pak'))
          if p.name.lower().startswith('english-') or p.name.lower() in ('english.pak','ipl_english.pak')]
    signature=tuple((p.resolve(),s.st_size,s.st_mtime_ns) for p in paks for s in [p.stat()])
    if signature not in _NATIVE:
        root,samples=_native_root(target)
        rows=root.find('DialogueDubbingData')
        durations={r.get('FileHash'):r.get('Duration') for r in rows}
        if len(samples)<16 or not all(str(zlib.crc32(s.encode('utf-8'))) in durations for s in samples):
            raise ValueError('Native dubbing filename hash contract changed')
        # Rows appended to DialogueDubbingData serialize just before its closing tag.
        blob=xml(root);end=blob.rindex(b'</DialogueDubbingData>')
        _NATIVE[signature]=dict(prefix=blob[:end],suffix=blob[end:],durations=durations,count=len(rows),samples=len(samples))
    return _NATIVE[signature]


def _custom_rows(existing, native, target):
    """Rows an earlier merge added after the native baseline."""
    prefix,suffix=native['prefix'],native['suffix']
    if existing.startswith(prefix) and existing.endswith(suffix) and len(existing)>=len(prefix)+len(suffix):
        holder=ET.fromstring(b'<rows>'+existing[len(prefix):len(existing)-len(suffix)]+b'</rows>')
        if not (holder.text or '').strip():return list(holder)
    # Not this build's serialization of the baseline: compare native rows one by one.
    previous=ET.fromstring(existing).find('DialogueDubbingData')
    rows=_native_root(target)[0].find('DialogueDubbingData')
    if previous is None or len(previous)<len(rows) or any(
            a.attrib!=b.attrib for a,b in zip(rows,list(previous)[:len(rows)])):
        raise ValueError('Existing dubbing index does not preserve this native baseline')
    return list(previous)[len(rows):]


def dubbing_index(target, media, existing=None):
    """Merge duration metadata, retaining the entire native English index.

    Native FileHash is CRC32 of the case-sensitive filename stem (no path or
    extension). Verify this against native assets each build before using it.
    """
    # Retail contains repeated hashes with different durations. Preserve every
    # native row and its order instead of inventing a collision-resolution rule.
    native=_native_index(target)
    indexed=dict(native['durations']);rows=[]
    for row in (_custom_rows(existing,native,target) if existing else []):
        key=row.get('FileHash')
        if key in indexed:raise ValueError('Duplicate custom dubbing hash: '+str(key))
        indexed[key]=row.get('Duration');rows.append(row)
    additions=[]
    for name,data in media.items():
        stem=name.rsplit('/',1)[-1][:-4]
        key=str(zlib.crc32(stem.encode('utf-8')))
        duration=ogg_duration(data)
        if key in indexed:
            if abs(float(indexed[key])-duration)>0.002:
                raise ValueError('Dubbing hash collision or changed audio: '+stem)
        else:
            row=ET.Element('DialogueDubbing',FileHash=key,Duration=f'{duration:.6f}')
            indexed[key]=row.get('Duration');rows.append(row)
        additions.append(dict(path=name,file_hash=key,duration=duration))
    blob=native['prefix']+b''.join(ET.tostring(row,encoding='utf-8') for row in rows)+native['suffix']
    return blob,dict(native_entries=native['count'],total_entries=native['count']+len(rows),
                     verified_native_hash_samples=native['samples'],recordings=additions)


def import_source_voices(resources, native, source_rows, prefixes):
    """Register missing recording identities from KCD1's abbreviation table."""
    resources = dict(resources)
    root = ET.fromstring(native)
    rows = root.find('voices')
    patch_path = 'Libs/Tables/skald/voice__gm_source.xml'
    patch = ET.fromstring(resources[patch_path]) if patch_path in resources else ET.Element('database', name='barbora')
    added = patch.find('voices')
    if added is None: added = ET.SubElement(patch, 'voices', version='1')
    for path, data in resources.items():
        if path.startswith('Libs/Tables/skald/voice__'):
            rows.extend(copy.deepcopy(list(ET.fromstring(data).iter('voice'))))
    for prefix in sorted(prefixes):
        existing = [v for v in rows if v.get('abbreviation') == prefix]
        if len(existing) > 1: raise ValueError('Ambiguous voice abbreviation: ' + prefix)
        if existing: continue
        source = [r for r in source_rows if r['abbreviation'] == prefix]
        if len(source) != 1: raise ValueError('Missing or ambiguous source voice: ' + prefix)
        identity = str(100000 + int(source[0]['voice_id']))
        name = 'gm_source_' + prefix
        if any(v.get('voice_id') == identity or v.get('voice_technical_name') == name for v in rows):
            raise ValueError('Imported voice identity collision: ' + prefix)
        voice = ET.SubElement(added, 'voice', abbreviation=prefix, voice_id=identity,
            voice_name=prefix, voice_technical_name=name, is_fake='false', fallback_voice_level='0')
        rows.append(copy.deepcopy(voice))
    if len(added): resources[patch_path] = xml(patch)
    return resources, xml(root)


CHARACTER_REQUIRED = ('age', 'body_type', 'gender', 'unique_assets')


def generic_character_template(archive, characters, soul):
    """Use shipped generic voice metadata when KCD1 has no Skald biography.

    This supplies the database reader's required classification, not an actor
    appearance or an invented source biography. The converted soul still owns
    its body, head and equipment. Reject unknown archetypes instead of silently
    assigning a male identity to a female, child or animal.
    """
    archetypes = ET.fromstring(read(archive, 'Libs/Tables/rpg/soul_archetype.xml'))
    matches = [a for a in archetypes.iter('soul_archetype')
               if a.get('soul_archetype_id') == soul.get('soul_archetype_id')]
    if len(matches) != 1 or matches[0].get('soul_archetype_name') not in ('NPC', 'NPC_Female'):
        raise ValueError('No generic voice character contract for imported soul archetype')
    genders = ET.fromstring(read(archive, 'Libs/Tables/rpg/gender.xml'))
    names = [g.get('gender_name') for g in genders.iter('gender')
             if g.get('gender_id') == matches[0].get('gender_id')]
    if len(names) != 1 or names[0] not in ('male', 'female'):
        raise ValueError('No generic voice character contract for imported soul gender')
    prefix = 'char_GENERIC_' + {'male': 'MAN', 'female': 'WOMAN'}[names[0]] + '_COMMONER_'
    candidates = sorted((c for c in characters.iter('skald_character')
                         if c.get('skald_character_name', '').startswith(prefix)
                         and all(k in c.attrib for k in CHARACTER_REQUIRED)
                         and not c.get('unique_assets')), key=lambda c: c.get('skald_character_name'))
    if not candidates:
        raise ValueError('Missing complete native generic voice character template: ' + prefix)
    return candidates[0]


def bind_soul_voice(resources,target,modid,soul_name,voice_name,role_name,ui_name):
    """Give an imported soul its own Skald identity, without reusing native NPC IDs."""
    resources=dict(resources)
    matches=[]
    for path,blob in resources.items():
        if path.startswith('Libs/Tables/rpg/soul__'):
            root=ET.fromstring(blob)
            matches.extend((path,root,soul) for soul in root.iter('soul') if soul.get('soul_name')==soul_name)
    if len(matches)!=1:raise ValueError('Expected one imported soul: '+soul_name)
    with zipfile.ZipFile(Path(target)/'Data/Tables.pak') as archive:
        voices=ET.fromstring(read(archive,'Libs/Tables/skald/voice.xml'))
        for resource_path, data in resources.items():
            if resource_path.startswith('Libs/Tables/skald/voice__'):
                voices.extend(list(ET.fromstring(data).iter('voice')))
        matches_voice=[v for v in voices.iter('voice') if v.get('voice_technical_name')==voice_name]
        if len(matches_voice)!=1:raise ValueError('Native voice missing or ambiguous')
        voice_id=matches_voice[0].get('voice_id')
        native=ET.fromstring(read(archive,'Libs/Tables/skald/skald_character.xml'))
        template=next((c for c in native.iter('skald_character') if c.get('voice_id')==voice_id),None)
        policy = 'native_voice_character'
        if template is None:
            current_name = matches[0][2].get('skald_character_name')
            existing = [c for n,b in resources.items() if n.startswith('Libs/Tables/skald/skald_character__')
                        for c in ET.fromstring(b).iter('skald_character') if c.get('skald_character_name') == current_name]
            if len(existing) > 1: raise ValueError('Ambiguous imported character identity')
            template = existing[0] if existing else None
            policy = 'retained_imported_character'
            if template is None or not all(k in template.attrib for k in CHARACTER_REQUIRED):
                template = generic_character_template(archive, native, matches[0][2])
                policy = 'native_generic_classification_no_source_biography'
        if not all(k in template.attrib for k in CHARACTER_REQUIRED):
            raise ValueError('Incomplete native voice character template')
    path,root,soul=matches[0]
    identity='gm_voice_'+soul_name
    soul.set('skald_character_name',identity)
    resources[path]=xml(root)
    table_path='Libs/Tables/skald/skald_character__'+modid+'.xml'
    chars=ET.fromstring(resources[table_path]) if table_path in resources else ET.Element('database',name='barbora')
    container=chars.find('skald_characters')
    if container is None:container=ET.SubElement(chars,'skald_characters',version='1')
    old=container.find("skald_character[@skald_character_name='"+identity+"']")
    if old is not None:container.remove(old)
    # Keep native reader-required fields; omit another character's biography.
    attrs={k:v for k,v in template.attrib.items() if k in
           ('age','body_type','gender','image1','image2','image3','image4','mortality_id','voice_categories','unique_assets')}
    attrs.update(skald_character_name=identity,voice_id=voice_id,master_role_name=role_name,
                 ui_name_string_name=ui_name,skald_character_full_name_string_name=ui_name)
    ET.SubElement(container,'skald_character',attrs)
    resources[table_path]=xml(chars)
    return resources,dict(soul=soul_name,character=identity,voice_id=int(voice_id),voice=voice_name,
                          character_template=template.get('skald_character_name'),classification_policy=policy)
