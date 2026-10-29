"""Native table checks and conservative geometry diagnostics, never engine certification."""
from itertools import combinations, product
import math

import numpy as np
from clothing_coverage import normals, alpha_intersections, exterior_coverage
from .geometry import Surface


def finding(code, message, components=(), severity='review', **evidence):
    return dict(code=code, severity=severity, message=message, components=list(components), **evidence)


def material_alpha(catalog, materials):
    surfaces = []
    for mat in materials:
        if mat.get('shader', '').lower() == 'nodraw':
            surfaces.append((0, [np.zeros((1, 1))]))
            continue
        if mat.get('opacity', 1) != 1:
            raise ValueError('Translucent material cannot prove opaque skin coverage')
        if not mat['texture']:
            raise ValueError('No alpha texture; geometry coverage is unverified')
        pixels, _ = catalog.assets.pixels(mat['texture'])
        surfaces.append((mat['threshold'], [pixels[:, :, 3]]))
    return surfaces


def active_masks(catalog, selected):
    masks = {}
    for identity in selected:
        part = catalog.parts[identity]
        for name in part['attributes'].get('HidingGroups','').split():
            g = catalog.groups.get(name)
            if g and 0 <= g['index'] < 8:
                masks[g['region']] = masks.get(g['region'],0) | (1 << g['index'])
    return masks


def element_replaced(catalog, selected, part, element):
    """KCD2 has one clothing attachment per region, plus the retained body.

    BodyLayerId is selection priority, not a request to draw every layer.
    Hiding groups of equipped but unselected items still apply to that region.
    Keep skin and VCloth selection separate; neither replaces the other.
    """
    if part['kind'] not in ('Body', 'Clothing'): return False
    region = element.get('EquipmentPart')
    kind = element.get('element_type', 'SkinElement')
    candidates = [(catalog.layer_order.get(e.get('BodyLayerId')), e)
        for identity in selected if catalog.parts[identity]['kind'] == 'Clothing'
        for e in catalog.parts[identity]['elements']
        if e.get('EquipmentPart') == region and e.get('element_type', 'SkinElement') == kind]
    candidates = [(order, e) for order, e in candidates if order is not None]
    layer = catalog.layer_order.get(element.get('BodyLayerId'))
    if not candidates or layer is None: return False
    highest = max(order for order, _ in candidates)
    if highest <= layer: return False
    if part['kind'] == 'Clothing': return True
    return any(e.get('KeepBodyLayer', 'false') != 'true'
               for order, e in candidates if order == highest)


def body_replaced(catalog, selected, part, element):
    return part['kind'] == 'Body' and element_replaced(catalog, selected, part, element)


def rendered_vertices(mesh, hidden):
    """Match the viewer's triangle culling, not just individual vertex bits."""
    visible = np.zeros(mesh.n, bool)
    visible[np.unique(mesh.faces[~hidden[mesh.faces].any(axis=1)])] = True
    return visible


def validate(catalog, selected, geometry=False, lod=0, inspect_assets=True):
    selected = list(dict.fromkeys(selected))
    findings, meshes, slots, roles, families = [], [], {}, {}, set()
    for check in getattr(catalog, 'inventory_checks', []):
        preset = catalog.presets.get(check['namespace']+'_root')
        if preset and set(preset['parts']) == set(selected) and check['status'] == 'error':
            findings.append(finding('equipped_outfit_unreachable',
                'The NPC equipment references do not resolve to a valid outfit.', selected, 'error',
                person=check['person'], equipment_issues=check['issues']))
    if not catalog.native_roles or not catalog.layer_order:
        findings.append(finding('native_rules_unavailable','Select a KCD2 installation to validate native clothing roles.',selected))
    genders = {catalog.parts[i]['gender'] for i in selected}
    if len(genders) > 1: findings.append(finding('gender_conflict','The selection mixes male and female components.',selected,'error'))
    if not any(catalog.parts[i]['kind']=='Body' for i in selected):
        findings.append(finding('missing_body','No body component is selected.', selected, 'error'))
    masks = active_masks(catalog,selected)
    equipment = getattr(catalog, 'equipment', None)
    lod_selections = []
    def loaded_model(element):
        name = element['ModelResolved']
        if hasattr(catalog, 'model_at_lod'):
            return catalog.model_at_lod(name, lod)
        return (name.removesuffix('.skin')+'_lod'+str(lod)+'.skin' if lod else name), lod
    for identity in selected:
        part = catalog.parts[identity]; attrs = part['attributes']
        families.add(part['family'])
        role = attrs.get('ArmorType')
        if role:
            roles.setdefault(attrs.get('ArmorArchetypeId', role), []).append(identity)
            native = catalog.native_roles.get(role)
            if catalog.native_roles and native is None:
                findings.append(finding('unknown_native_role','Clothing role is absent from the selected KCD2 tables.',[identity],'error',role=role))
            elif native:
                archetype = attrs.get('ArmorArchetypeId')
                valid_archetype = archetype in equipment.types if equipment else native['archetype'] == archetype
                if not valid_archetype or native['gender'] != part['gender']:
                    findings.append(finding('native_role_mismatch','Equipment archetype is invalid or gender differs from the inherited KCD2 visual role.',[identity],'error',expected=native))
        for group in attrs.get('HidingGroups','').split():
            if group not in catalog.groups:
                findings.append(finding('missing_hiding_group','Referenced hiding group is not defined.',[identity],'error',group=group))
            elif not 0 <= catalog.groups[group]['index'] < 8:
                findings.append(finding('invalid_hiding_bit','Hiding-group index cannot be represented by the vertex alpha byte.',[identity],'error',group=group))
        for element in part['elements']:
            region, layer = element.get('EquipmentPart'),element.get('BodyLayerId')
            replaced = element_replaced(catalog, selected, part, element)
            if replaced and part['kind'] == 'Clothing':
                findings.append(finding('native_region_selection',
                    'A higher layer supplies the single native clothing attachment. This mesh is not drawn; its equipped hiding groups still apply.',
                    [identity], region=region, layer=layer))
            slots.setdefault((region,layer),[]).append(identity)
            native=catalog.native_roles.get(role)
            native_default=layer is None and native and (region,None) in native['layers']
            if (layer is None and not native_default) or (layer is not None and not str(layer).isdigit()):
                findings.append(finding('unresolved_layer','Body layer did not resolve through component inheritance.',[identity],'error',region=region))
            elif layer is not None and catalog.layer_order and layer not in catalog.layer_order:
                findings.append(finding('unknown_native_layer','Body layer is absent from the native order table.',[identity],'error',region=region,layer=layer))
            if native_default and geometry:
                findings.append(finding('native_default_layer','The native template leaves this part unnumbered; numeric layer-order geometry tests are not applied.',[identity],region=region))
            native_layers = (sorted(equipment.layers.get(attrs.get('ArmorArchetypeId'), set()))
                             if equipment else native['layers'] if native else [])
            if native and not native_default and (region,layer) not in native_layers:
                findings.append(finding('native_layer_difference','The converted part/layer differs from its native equipment occupancy; inspect replacement behaviour.',[identity],region=region,layer=layer,native_layers=native_layers))
            if part['kind']=='Clothing' and element.get('KeepBodyLayer','false') != 'true':
                findings.append(finding('body_layer_replacement','This element can replace the body region beneath it; check exposed skin.',[identity],region=region))
            if not inspect_assets:
                continue
            name = element.get('ModelResolved','')
            try:
                name, effective_lod = loaded_model(element)
                lod_selections.append(dict(component=identity, model=name, requested=lod, effective=effective_lod))
                mesh = catalog.mesh(name)
                if hasattr(mesh, 'native_colour_mismatches'):
                    mismatches = mesh.native_colour_mismatches()
                    if len(mismatches):
                        findings.append(finding('native_skinning_colour_mismatch',
                            'Render and internal skinning buffers disagree on native material features or hiding bits.',
                            [identity], 'error', model=name, vertices=len(mismatches),
                            sample_vertices=mismatches[:32].tolist()))
                if not np.isfinite(mesh.vertices).all() or np.any(mesh.faces < 0) or np.any(mesh.faces >= mesh.n):
                    raise ValueError('Non-finite vertices or indices outside mesh')
                alpha = np.frombuffer(mesh.streams[3].data[24:],np.uint8).reshape(-1,4)[:,3]
                hidden = ((255 ^ alpha) & masks.get(region,0)) != 0
                if replaced:hidden[:]=True
                materials = catalog.materials(element.get('MaterialResolved'))
                for subset in mesh.info['subset_records']:
                    if subset['material_id'] >= len(materials): raise ValueError('Material slot outside material table')
                for material in materials:
                    if material['texture'] and material['texture'].lower().replace('\\','/') not in catalog.assets.index:
                        raise ValueError('Missing diffuse texture: '+material['texture'])
                meshes.append((identity,element,mesh,hidden,materials))
            except (OSError,ValueError,KeyError) as exc:
                findings.append(finding('asset_error',str(exc),[identity],'error',asset=name,lod=lod))
    for (region,layer), identities in slots.items():
        if len(set(identities)) > 1:
            findings.append(finding('same_layer_overlap','Multiple components occupy the same equipment part and layer.',identities,'error',region=region,layer=layer))
    for role, identities in roles.items():
        if len(identities)>1:
            findings.append(finding('shared_armor_archetype','Multiple garments use one native equipment archetype. Check native equip replacement.',identities,archetype=role))
    if equipment:
        for a, b in combinations(roles, 2):
            conflict = equipment.exclusive.get(a, set()) & equipment.exclusive.get(b, set())
            if conflict:
                findings.append(finding('exclusive_equipment_overlap', 'Different archetypes occupy the same exclusive native equipment slots.',
                    roles[a]+roles[b], 'error', slots=sorted(conflict)))
    private = getattr(catalog, 'private_groups', set(catalog.groups))
    if len(families)>1 and any(private.intersection(catalog.parts[i]['attributes'].get('HidingGroups','').split()) for i in selected):
        findings.append(finding('cross_outfit_masks','This combination mixes outfit-specific hiding masks; matching bit numbers do not prove compatible coverage.',selected))
    stats = [dict(component=i, model=e.get('ModelResolved'), vertices=m.n, referenced_vertices=len(np.unique(m.faces)), hidden_vertices=int(h[np.unique(m.faces)].sum()),
                  visible_triangles=int((~h[m.faces].any(axis=1)).sum())) for i,e,m,h,_ in meshes]
    if geometry:
        covers = []
        for identity,element,mesh,hidden,materials in meshes:
            if catalog.parts[identity]['kind'] != 'Clothing': continue
            if element_replaced(catalog, selected, catalog.parts[identity], element): continue
            try:
                if hasattr(catalog,'coverage_surface'):
                    model,_=loaded_model(element)
                    mask=masks.get(element.get('EquipmentPart'),0)
                    surface=catalog.coverage_surface(model,element['MaterialResolved'],mask)
                    covers.append((identity,element,(surface,model,element['MaterialResolved'],mask)))
                    continue
                surfaces = material_alpha(catalog, materials)
                visible = ~hidden[mesh.faces].any(axis=1)
                accept = alpha_intersections(mesh,surfaces,visible)
                covers.append((identity,element,Surface(mesh.vertices[mesh.faces[visible]],accept)))
            except (ValueError,OSError,KeyError) as exc:
                findings.append(finding('opacity_unverified',str(exc),[identity],'error'))
        def hits(mesh,element,surface,inward=.06,outward=.12):
            if isinstance(surface,tuple):
                _,cover,material,mask=surface
                source,_=loaded_model(element)
                return catalog.coverage_hits(source,cover,material,mask,inward,outward)
            return surface.covers(mesh.vertices,normals(mesh),inward,outward)
        # A composed outfit can contain both the hidden inner surface and its
        # outer cover. Sampling only Body misses holes in those garments. Use
        # removed face centroids, not shared vertices touching a surviving face.
        for identity, element, mesh, hidden, materials in meshes:
            kind = catalog.parts[identity]['kind']
            if kind == 'Head' and hidden.any():
                def outward(points, directions):
                    covered = np.zeros(len(points), bool)
                    for _, _, surface in covers:
                        surface = surface[0] if isinstance(surface, tuple) else surface
                        covered |= surface.covers(points, directions, inward=0, outward=.12)
                    return covered
                supported = exterior_coverage(mesh.vertices, normals(mesh), outward, hidden)
                unsupported = hidden & ~supported
                if unsupported.any():
                    findings.append(finding('hidden_head_without_exterior_cover',
                        'Head/neck hiding vertices lack opaque outward coverage around their surface normal.',
                        [identity], vertices=int(unsupported.sum()),
                        sample_vertices=np.flatnonzero(unsupported)[:32].tolist(),
                        model=element.get('ModelResolved'), inward_metres=0, outward_metres=.12))
            if kind == 'Clothing' and not element_replaced(catalog, selected, catalog.parts[identity], element):
                removed = np.flatnonzero(hidden[mesh.faces].any(axis=1))
                if len(removed):
                    try:
                        selected_faces = np.zeros(len(mesh.faces), bool)
                        selected_faces[removed] = True
                        accept = alpha_intersections(mesh, material_alpha(catalog, materials), selected_faces)
                        barycentric = np.full(len(removed), 1/3)
                        removed = removed[accept(np.arange(len(removed)), barycentric, barycentric)]
                    except (ValueError, OSError, KeyError) as exc:
                        findings.append(finding('opacity_unverified', str(exc), [identity], 'error'))
                        continue
                    triangles = mesh.vertices[mesh.faces[removed]]
                    directions = np.cross(triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0])
                    lengths = np.linalg.norm(directions,axis=1)
                    valid = lengths > 1e-12
                    removed, triangles, directions = removed[valid], triangles[valid], directions[valid]
                    directions /= lengths[valid,None]
                    covered = np.zeros(len(removed),bool)
                    for _, _, surface in covers:
                        surface = surface[0] if isinstance(surface,tuple) else surface
                        covered |= surface.covers(triangles.mean(axis=1),directions,inward=.02,outward=.12)
                    if (~covered).any():
                        findings.append(finding('hidden_cloth_without_cover',
                            'Culled garment triangles have no surviving opaque cover along their centroid normal.',
                            [identity],triangles=int((~covered).sum()),sample_triangles=removed[~covered][:32].tolist(),
                            model=element.get('ModelResolved'),inward_metres=.02,outward_metres=.12))
            if kind in ('Hair','Beard'):
                visible = rendered_vertices(mesh,hidden)
                for other, cover_element, surface in covers:
                    if cover_element.get('EquipmentPart') not in ('head','face','beard','neck'):
                        continue
                    intersects = hits(mesh,element,surface,inward=.025,outward=.06) & visible
                    if intersects.any():
                        findings.append(finding('unmasked_hair_under_headwear',
                            'Visible hair or beard intersects the headwear coverage band; inspect native masking.',
                            [identity,other],vertices=int(intersects.sum()),sample_vertices=np.flatnonzero(intersects)[:32].tolist()))
        for identity,element,mesh,hidden,_ in meshes:
            if catalog.parts[identity]['kind'] != 'Body': continue
            region = element.get('EquipmentPart'); covered = np.zeros(mesh.n,bool)
            referenced = np.zeros(mesh.n,bool); referenced[np.unique(mesh.faces)] = True
            # An inner shirt behind skin is not a visible defect when an
            # opaque outer garment covers that skin. Test the exposed surface,
            # retaining alpha cutouts and the same bounded normal segments.
            occluded = np.zeros(mesh.n,bool)
            for _,_,surface in covers:
                occluded |= hits(mesh,element,surface,inward=0,outward=.12)
            for other,cover_element,surface in covers:
                # Female torso meshes can span arms and legs; spatial rays decide coverage.
                covered |= hits(mesh,element,surface)
                inside=np.zeros(mesh.n,bool)
                visible_vertices = rendered_vertices(mesh, hidden) & ~occluded
                if visible_vertices.any():inside=hits(mesh,element,surface,inward=.025,outward=0) & visible_vertices
                if inside.any(): findings.append(finding('cloth_under_visible_skin','Garment intersects inward normal segments of skin with no opaque outward cover.',[identity,other],vertices=int(inside.sum()),sample_vertices=np.flatnonzero(inside)[:32].tolist(),inward_metres=.025))
            exposed = ~rendered_vertices(mesh, hidden) & ~covered & referenced
            if exposed.any():
                findings.append(finding('hidden_skin_without_cover','Body vertices are hidden without an opaque garment surface along their normal.',[identity],vertices=int(exposed.sum()),sample_vertices=np.flatnonzero(exposed)[:32].tolist(),inward_metres=.06,outward_metres=.12))
        # Layer-on-layer rays expose inverted layering without claiming a cloth simulation.
        for inner,ie,im,ih,_ in meshes:
            if catalog.parts[inner]['kind'] != 'Clothing': continue
            for outer,oe,surface in covers:
                il=catalog.layer_order.get(ie.get('BodyLayerId'))
                ol=catalog.layer_order.get(oe.get('BodyLayerId'))
                if il is None or ol is None:continue
                if ie.get('EquipmentPart') != oe.get('EquipmentPart') or il >= ol: continue
                referenced = np.zeros(im.n,bool); referenced[np.unique(im.faces)] = True
                inverted = hits(im,ie,surface,inward=.015,outward=0) & rendered_vertices(im, ih)
                if inverted.any(): findings.append(finding('outer_layer_under_inner','Higher layer intersects inward normal segments of the lower garment.',[inner,outer],vertices=int(inverted.sum()),sample_vertices=np.flatnonzero(inverted)[:32].tolist(),inward_metres=.015))
    status = 'error' if any(f['severity']=='error' for f in findings) else 'review' if findings else 'checked'
    return dict(status=status,selected=selected,lod=lod,lod_selections=lod_selections,geometry_checked=geometry,findings=findings,meshes=stats,
                native_rules_checked=bool(catalog.native_roles and catalog.layer_order),assets_checked=inspect_assets,runtime_verified=False)


def plan(catalog, mode='presets', body=None, gender=None, limit=250, offset=0):
    """Enumerate deterministically; explicit cursor bounds do not imply complete coverage."""
    if not 1 <= limit <= 1000 or offset < 0: raise ValueError('Invalid batch size or cursor')
    if mode=='presets':
        cases = [dict(id=p['id'],label=p['label'],selected=p['parts']) for p in catalog.presets.values() if gender in (None,'All',p['gender'])]
        return dict(total=len(cases),offset=offset,cases=cases[offset:offset+limit],next_offset=min(len(cases),offset+limit))
    if body not in catalog.presets: raise ValueError('Select a character for combination tests')
    preset = catalog.presets[body]
    base = [p for p in preset['parts'] if catalog.parts[p]['kind']!='Clothing']
    garments = sorted(p['id'] for p in catalog.parts.values() if p['kind']=='Clothing' and p['gender']==preset['gender'] and p['attributes'].get('ArmorType'))
    if mode=='singles': total=len(garments); stream=((g,) for g in garments)
    elif mode=='pairs': total=math.comb(len(garments),2); stream=combinations(garments,2)
    elif mode=='layers':
        slots={}
        for g in garments:
            slot=tuple(sorted((e.get('EquipmentPart',''),e.get('BodyLayerId','')) for e in catalog.parts[g]['elements']))
            slots.setdefault(slot,[None]).append(g)
        total=math.prod(map(len,slots.values())); stream=product(*slots.values())
    else: raise ValueError('Unknown combination mode')
    from itertools import islice
    rows=[dict(id=f'{mode}:{offset+i}',label=' + '.join(g for g in group if g) or 'Body only',selected=base+[g for g in group if g])
          for i,group in enumerate(islice(stream,offset,offset+limit))]
    return dict(total=total,offset=offset,cases=rows,next_offset=min(total,offset+len(rows)))
