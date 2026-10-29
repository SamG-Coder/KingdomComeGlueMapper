"""Adapt independent cinematic actor placement to the destination heightfield.

Authored scene-wide mocap uses a shared origin, not each actor's feet. Keep
that ensemble rigid; only independent actor position tracks are conformed.
Preserve source ground clearance and animation timing, including hide keys.
This does not certify collisions with destination buildings or furniture.
"""
import copy
import math

from cinematic_scene import guid_key


def pair(raw):
    values=tuple(map(float,raw.split(',')))
    if len(values)!=2 or not all(map(math.isfinite,values)):
        raise ValueError('Invalid scene ground spline value')
    return values


def packed(time,value): return format(time,'.12g')+','+format(value,'.12g')


def conform(track,offset,source_height,target_height):
    """Correct ordinate and tangent slopes without changing XY/time/flags.

    XYZ knots must correspond. Arbitrary independently keyed splines require
    resampling and are rejected rather than pairing unrelated key positions.
    """
    subs=list(track)
    if len(subs)!=3 or any(s.tag!='NewSubTrack' for s in subs):
        raise ValueError('Unsupported terrain-fit position track')
    keys=[s.findall('Key') for s in subs]
    times=[tuple(k.get('time') for k in ks) for ks in keys]
    if len(set(times))!=1: raise ValueError('Terrain fit requires corresponding XYZ knots')
    changes=[];clearance=[]
    def correction(x,y):
        value=float(target_height(x+offset[0],y+offset[1])-source_height(x,y))-offset[2]
        if not math.isfinite(value): raise ValueError('Missing cinematic terrain height')
        return value
    # Defaults are real positions for unkeyed tracks. Keyed tracks may retain
    # the editor's shared origin; evaluate actual knots separately.
    points=[(tuple(subs),'defaultValue')]+[(ks,'value') for ks in zip(*keys)]
    for nodes,attr in points:
        values=[pair(n.get(attr)) for n in nodes]
        x,y,z=[v[1] for v in values]
        gap=z-float(source_height(x,y))
        if attr=='value' or not keys[0]: clearance.append(gap)
        if gap < -2: continue  # authored off-screen/underground body switch
        dz=correction(x,y);changes.append(dz)
        if abs(dz)>1e-9: nodes[2].set(attr,packed(values[2][0],z+dz))
        if attr!='value': continue
        # Apply the local ground-difference gradient to the existing Bezier
        # tangent velocities. Preserve their authored time-handle lengths.
        h=.05
        gradient=((correction(x+h,y)-correction(x-h,y))/(2*h),
                  (correction(x,y+h)-correction(x,y-h))/(2*h))
        for side in ('ds','dd'):
            if any(n.get(side) is None for n in nodes):
                raise ValueError('Missing terrain-fit spline tangent')
            tangents=[pair(n.get(side)) for n in nodes]
            dt,dy=tangents[2]
            adjustment=sum(g*v/t*dt for g,(t,v) in zip(gradient,tangents[:2]) if abs(t)>1e-12)
            if abs(adjustment)>1e-9: nodes[2].set(side,packed(dt,dy+adjustment))
    return dict(correction_range=[min(changes,default=0),max(changes,default=0)],
                source_clearance_range=[min(clearance,default=0),max(clearance,default=0)])


def adapter(index,plan,offset,source_height,target_height,protected_entities=()):
    """Return a converter adapter and an honest, per-actor placement receipt."""
    edits={};report=dict(adjusted=[],preserved=[],runtime_verified=False,
        policy='independent_actor_source_clearance_at_destination_terrain',
        limitations=['synchronized_mocap_not_deformed','destination_prop_collisions_not_verified'])
    for owner in plan['entity_guids']:
        original=index.entities[owner]
        sequence=original.find('Sequence')
        if sequence is None: continue
        changed=copy.deepcopy(original)
        for node in changed.findall('Sequence/Nodes/Node'):
            reference=node.get('EntityGUID')
            if not reference: continue
            key=guid_key(reference);entity=index.entities[key]
            body=entity.find('Properties/Body')
            if entity.get('EntityClass')!='AnimChar' or body is None: continue
            name=entity.get('Name');reason=None
            animations=[k.get('anim','') for k in node.findall("Track[@paramType='Animation']/Key")]
            if key in protected_entities:reason='protected_cinematic_body'
            elif entity.get('ParentId') not in (None,'0'):reason='parent_local_motion'
            elif any(a.lower().startswith(plan['name'].lower()+'_') for a in animations):
                reason='synchronized_scene_mocap'
            tracks=node.findall("Track[@paramType='Position']")
            if reason:
                report['preserved'].append(dict(entity=name,reason=reason));continue
            if len(tracks)!=1:raise ValueError('Expected one independent actor position track: '+str(name))
            evidence=conform(tracks[0],offset,source_height,target_height)
            evidence.update(entity=name,entity_guid=key)
            # A raised source root or seated animation depends on supporting
            # scenery. Record that explicitly rather than certifying a fit.
            evidence['support_review_required']=(evidence['source_clearance_range'][1]>.6 or
                any('bench' in a.lower() or 'sitting' in a.lower() for a in animations))
            report['adjusted'].append(evidence)
            actor=copy.deepcopy(entity)
            pos=tuple(map(float,actor.get('Pos').split(',')))
            dz=float(target_height(pos[0]+offset[0],pos[1]+offset[1])-source_height(*pos[:2]))-offset[2]
            if not math.isfinite(dz):raise ValueError('Missing actor initial ground height')
            if abs(dz)>1e-9: actor.set('Pos',','.join(format(v,'.12g') for v in (pos[0],pos[1],pos[2]+dz)))
            edits[key]=actor
        edits[owner]=changed
    def apply(entity):
        replacement=edits.get(guid_key(entity.get('EntityGuid')))
        if replacement is None:return entity
        if replacement.find('Sequence') is not None:return copy.deepcopy(replacement)
        # Preserve the separate cast adapter's body/outfit modifications.
        entity.set('Pos',replacement.get('Pos'));return entity
    return apply,report


def combine(index,plans,cast_adapters,native_resources,offset,source_height,target_height):
    from cinematic_scene_cast import catalog,player_variants
    data=catalog(native_resources)
    combined={};reports=[]
    def compose(cast,ground):return lambda e:ground(cast(e))
    for plan in plans:
        heroes,variants=player_variants(index,plan,data)
        protected=heroes | set(variants)
        ground,report=adapter(index,plan,offset,source_height,target_height,protected)
        combined[plan['name']]=compose(cast_adapters[plan['name']],ground)
        reports.append(dict(scene=plan['name'],**report))
    return combined,reports
