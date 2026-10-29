"""Audit KCD1 cinematic containers against a built world and prepare native scenes.

Read-only with respect to the mod and both games. Candidate XML is never an
installable package: profile activation, converted assets and native playback
must be verified before it can become part of Setup's normal build.
"""
import argparse
import html
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zipfile

from cinematic_scene import SceneIndex, convert
from compiled_terrain import parse
from building_brushes import read_brush
from water_volumes import read_water
from upgrade_map import read
from region_travel import guid
from world_gameplay_basis import NativeGraphs, package_dependencies, validate_dependency_scopes


PUNISHMENT = 'quests/final/barbora/open_world/nextnextgenpunishment.xml'
SCENES = ('cin_s9906t_crime__whipping_troskovice', 'cin_s9909t_crime__branding_troskovice')


def compare(source, destination):
    rows = []
    for name in sorted(source.sequences):
        try:
            plan = source.plan(name)
            present = destination.sequences.get(name, [])
            row = {key: plan[key] for key in ('name', 'source_document', 'owner_profiles',
                    'entity_classes', 'missing_references', 'sequence_version', 'parameter_versions')}
            row.update(entities=len(plan['entity_guids']), external_entities=len(plan['external_entities']),
                       present_in_destination=len(present),
                       status='present_unverified' if len(present)==1 else 'missing' if not present else 'ambiguous',
                       runtime_verified=False)
        except ValueError as error:
            row = dict(name=name, status='source_contract_error', error=str(error), runtime_verified=False)
        rows.append(row)
    return rows


def stocks(archive):
    """Authored placement inventory; no guessed town coordinates or NPC names."""
    terrain = parse(read(archive, 'terrain/terrain.dat'))
    meshes, materials = (terrain.tables[k]['paths'] for k in ('meshes', 'materials'))
    selected = []
    def visit(data, offset, size, kind):
        if kind != 1: return
        record = read_brush(data, offset, meshes, materials)
        if '/structures/pillory/' in record['path']:
            record['transform'] = list(struct.unpack_from('<12f', data, offset+40))
            selected.append(record)
    read_water(terrain, visit)
    return selected


def build(source, target, mod, output, source_level='rataje', level='kcd1_travel', site_offset=None):
    source, target, mod, output = map(Path, (source, target, mod, output))
    output.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(mod/'Data/Levels'/level/'level.pak') as z:
        destination = SceneIndex.from_archive(z)
        world_files = {n:read(z,n) for n in ('leveldata.xml','waitinglinks.xml','triggerareas.fubar','whdata_1')
                       if n in z.namelist()}
    print('Reading KCD1 scene containers and dependencies', flush=True)
    with zipfile.ZipFile(source/'Data/Levels'/source_level/'level.pak') as z:
        original = SceneIndex.from_archive(z)
        source_stocks = stocks(z)
    rows = compare(original, destination)
    summary = dict(source_sequences=len(rows),
                   destination_has_profile_metadata=destination.has_profile_metadata,
                   missing_sequences=sum(r['status']=='missing' for r in rows),
                   present_unverified=sum(r['status']=='present_unverified' for r in rows),
                   source_contract_errors=sum(r['status']=='source_contract_error' for r in rows),
                   scenes_with_missing_entity_references=sum(bool(r.get('missing_references')) for r in rows),
                   runtime_verified=False, includes_helper_and_quest_sequences=True)
    (output/'source-scenes.json').write_text(json.dumps(dict(summary=summary, scenes=rows), indent=2), encoding='utf8')
    (output/'source-stocks.json').write_text(json.dumps(source_stocks, indent=2), encoding='utf8')
    # Keep the two full world indexes out of memory while reading the native level.
    del original
    print(f"Audited {len(rows)} source sequences; preparing native punishment dependency closure", flush=True)
    entry = 'Quests/GlueTravel/entry/'+level+'.xml'
    wrapper = 'Quests/GlueTravel/world/open_world.xml'
    graphs = {entry: b'<Database><Skald><Level Name="candidate"><Definitions><Definition File="../world/open_world.xml"/></Definitions><Nodes><open_world Name="open_world"/></Nodes></Level></Skald></Database>',
              wrapper: b'<Database><Skald><Gameplay Name="open_world"><Definitions><Definition File="../../Final/Barbora/open_world/nextnextgenpunishment.xml"/></Definitions><Nodes><nextnextgenpunishment Name="nextnextgenpunishment"/></Nodes></Gameplay></Skald></Database>'}
    with zipfile.ZipFile(target/'Data/Scripts.pak') as z:
        native = NativeGraphs(z)
        _, missing = native.audit(PUNISHMENT, {'player','player_any','openworld'})
        graphs = package_dependencies(graphs, entry, wrapper, z)
    wrapper = 'Quests/GlueTravel/entry/world/open_world.xml'
    with zipfile.ZipFile(target/'Data/IPL_GameData.pak') as z:
        count = validate_dependency_scopes(graphs, wrapper, read(z,'Libs/concept/definitions.xml'))
    for name, blob in graphs.items():
        path=output/'candidate'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(blob)
    crime = dict(native_graphs_validated=count, inherited_assets=sorted(missing),
                 punishment_policy='KCD2', scenes=[], installed=False, runtime_verified=False,
                 ready_to_install=False,
                 remaining=['bind local punishment area, guard, confiscation storage and reputation',
                            'verify cinematic profile activation and playback in the destination world',
                            'remove only the replaced source props and validate collision/navigation',
                            'test witnessed crime, reporting, surrender, fine and physical punishment in game'])
    if site_offset is not None:
        sites = [s for s in source_stocks if s['offset']==site_offset]
        if len(sites)!=1: raise ValueError('Select an exact source stock offset from source-stocks.json')
        site=sites[0]
        with zipfile.ZipFile(target/'Data/Levels/trosecko/level.pak') as z:
            scenes=SceneIndex.from_archive(z)
            native_profiles=ET.fromstring(read(z,'whdata_1'))
            native_areas=read(z,'triggerareas.fubar')
        plans=[scenes.plan(n) for n in SCENES]
        # The native scene owns its stage prop. Its placement is the reference
        # frame; actor pivots are on the platform, not on the terrain below it.
        anchors=[scenes.entities[k] for k in plans[0]['entity_guids'] if
                 scenes.entities[k].get('Geometry','').replace('\\','/').lower()==
                 'objects/manmade/structures/municipal/law_and_order/pillory_b.cgf']
        if len(anchors)!=1: raise ValueError('Ambiguous native punishment stage anchor')
        origin=tuple(map(float,anchors[0].get('Pos').split(',')))
        offset=tuple(a-b for a,b in zip(site['position'],origin))
        next_id=max(int(e.get('EntityId','0')) for e in destination.entities.values())+1
        prepared=[]
        for plan in plans:
            layer='gm_'+plan['name']
            data, receipt=convert(scenes,plan,offset=offset,identity=guid,first_id=next_id,
                                 layer=layer,source_game='kcd2')
            next_id+=receipt['emitted_entities']
            path=output/'candidate/layers'/ (layer+'.xml');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            crime['scenes'].append(dict(plan=plan, conversion=receipt, path=str(path.resolve())))
            if len(plan['owner_profiles'])!=1: raise ValueError('Native scene requires an explicit profile choice')
            prepared.append((data,receipt,plan['owner_profiles'][0]))
        from cinematic_scene_profiles import register
        registered,crime['registered_profiles']=register(world_files,prepared,native_profiles,native_areas)
        for name,blob in registered.items():
            path=output/'candidate/level'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(blob)
        crime['site']=dict(source=site,native_anchor=dict(anchors[0].attrib),translation=list(offset),
                           orientation='native orientation retained; clearance needs in-game verification',
                           replaced_source_props=[s for s in source_stocks if s['layer']==site['layer'] and
                                                  max(abs(a-b) for a,b in zip(s['position'],site['position']))<0.01])
    (output/'crime-plan.json').write_text(json.dumps(crime,indent=2),encoding='utf8')
    write_html(output,summary,rows,crime)
    return dict(**summary,native_punishment_graphs=count,prepared_scenes=len(crime['scenes']),report=str((output/'index.html').resolve()))


def write_html(output, summary, rows, crime):
    esc=lambda x:html.escape(str(x))
    body=''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in
        (r['name'],r['status'],', '.join(r.get('owner_profiles',[])),r.get('entities','—'),
         len(r.get('missing_references',[])),r.get('error','')) )+'</tr>' for r in rows)
    document='''<!doctype html><meta charset="utf-8"><title>Crime and cinematic conversion audit</title>
<style>body{background:#11191d;color:#e5ecec;font:16px system-ui;margin:40px auto;max-width:1500px;padding:0 24px}h1{color:#e6c98b}p{line-height:1.6}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid #35434a;padding:10px;text-align:left}a{color:#a2d8de}input{padding:12px;width:50%;margin:16px 0;background:#213139;color:white;border:1px solid #52656d}</style>
<h1>Crime and cinematic conversion audit</h1><p>Read from the source games and built mod. No game or installed mod files were changed.</p>
<p><b>'''+esc(summary['source_sequences'])+''' source in-world sequences · '''+esc(summary['missing_sequences'])+''' missing · '''+esc(summary['present_unverified'])+''' present, playback unverified.</b><br>Counts include helper and quest sequences; these are not all story cinematics. The movie pipeline is separate.</p>
<p><b>KCD2 punishment:</b> '''+esc(crime['native_graphs_validated'])+''' graph dependencies resolve. '''+esc(len(crime['scenes']))+''' native scenes prepared at the authored stocks placement. Candidates are not yet ready to install or validated in game.</p>
<p>Remaining: '''+esc('; '.join(crime['remaining']))+'''.</p>
<p><a href="crime-plan.json">Punishment/site plan</a> · <a href="source-scenes.json">Full source scene audit</a> · <a href="source-stocks.json">Authored stocks locations</a></p>
<input placeholder="Filter scene or profile" oninput="for(const r of document.querySelectorAll('tbody tr'))r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase())">
<table><thead><tr><th>Scene</th><th>Status</th><th>Source profiles</th><th>Entities in closure</th><th>Missing references</th><th>Contract error</th></tr></thead><tbody>'''+body+'</tbody></table>'
    (output/'index.html').write_text(document,encoding='utf8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','target','mod','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--source-level',default='rataje');parser.add_argument('--level',default='kcd1_travel')
    parser.add_argument('--site-offset',type=int,help='Exact authored stock brush offset from source-stocks.json')
    print(json.dumps(build(**vars(parser.parse_args())),indent=2))
