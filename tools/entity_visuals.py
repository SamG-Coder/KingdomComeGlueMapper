"""Extract initial-state furnishings without importing KCD1 gameplay scripts."""
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
import math
from upgrade_map import read

VISUAL_CLASSES = {"GeomEntity", "Bed", "Chair", "Stash", "AnimObject", "AnimDoor",
                  "RigidBodyEx", "Ladder", "Grindstone", "PickableItem", "ItemSlot",
                  "AlchemyItem", "ShootingTarget", "LedgerBook"}


def initial_layer(name):
    name = name.split("{")[0].lower()
    return name.endswith(("_state0", "_state0_prefabs"))


def quaternion_product(a, b):
    w,x,y,z=a;v,i,j,k=b
    return (w*v-x*i-y*j-z*k,w*i+x*v+y*k-z*j,w*j-x*k+y*v+z*i,w*k+x*j-y*i+z*v)


def world_transform(entity, entities, visiting=()):
    """Compose source parent-local TRS; reject cycles and unsupported shear."""
    key=entity.get('EntityId')
    if key in visiting:raise ValueError('Parent cycle')
    pos=tuple(map(float,entity.get('Pos','0,0,0').split(',')))
    rot=tuple(map(float,entity.get('Rotate','1,0,0,0').split(',')))
    scale=tuple(map(float,entity.get('Scale','1,1,1').split(',')))
    if len(pos)!=3 or len(rot)!=4 or len(scale)!=3 or not all(map(math.isfinite,pos+rot+scale)):
        raise ValueError('Invalid entity transform')
    length=math.sqrt(sum(x*x for x in rot))
    if length<1e-8:raise ValueError('Zero quaternion')
    rot=tuple(x/length for x in rot)
    parent_id=entity.get('ParentId')
    if parent_id:
        if parent_id not in entities:raise ValueError('Parent outside selected initial world')
        pp,pq,ps=world_transform(entities[parent_id],entities,visiting+(key,))
        if max(ps)-min(ps)>1e-4 and max(abs(v) for v in rot[1:])>1e-5:
            raise ValueError('Rotated child under nonuniform parent requires affine geometry')
        offset=quaternion_product(quaternion_product(pq,(0,*(pos[i]*ps[i] for i in range(3)))),(pq[0],-pq[1],-pq[2],-pq[3]))[1:]
        pos=tuple(pp[i]+offset[i] for i in range(3))
        rot=quaternion_product(pq,rot)
        scale=tuple(ps[i]*scale[i] for i in range(3))
    elif entity.get('ParentGuid'):
        raise ValueError('Unresolved GUID-only parent')
    return pos,rot,scale


def collect_visuals(source_pak, library):
    """Resolve direct models and pickable-item database IDs; audit exclusions."""
    items = {}
    paths = [library / "KingdomComeDeliverance/Data/Tables.pak"]
    paths += sorted((library / "KingdomComeDeliverance/Data/patch").glob("*.pak"))
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.lower() != "libs/tables/item/pickable_item.xml":
                    continue
                for row in ET.fromstring(read(archive, name)).iter("row"):
                    if row.get("model"):
                        model = row.get("model").replace("\\", "/").lower()
                        if not model.startswith("objects/"):
                            model = "objects/" + model
                        if not Path(model).suffix:
                            model += ".cgf"
                        items[row.get("item_id").lower()] = (model, row.get("material", ""))
    entities = []
    excluded = Counter()
    seen = set()
    for name in source_pak.namelist():
        main = name.lower() == "objects_mission0.xml"
        if not main and (not name.lower().startswith("layers/") or not name.lower().endswith(".xml")):
            continue
        for entity in ET.fromstring(read(source_pak, name)).iter("Entity"):
            if entity.get("EntityClass") not in VISUAL_CLASSES:
                continue
            if not (initial_layer(entity.get("Layer", "")) or (main and not entity.get("Layer"))):
                excluded["non_initial_layer"] += 1
                continue
            if entity.get('EntityId') not in seen:
                entities.append(entity)
                seen.add(entity.get('EntityId'))
    ids = {e.get("EntityId") for e in entities if e.get("EntityClass") != "ItemSlot"}
    entity_map = {e.get('EntityId'):e for e in entities}
    result = []
    for e in entities:
        props = e.find("Properties")
        if props is None:
            props = ET.Element("Properties")
        try:
            position,rotation,scale=world_transform(e,entity_map)
        except ValueError as error:
            excluded['transform:'+str(error)] += 1
            continue
        model = e.get("Geometry") or props.get("object_Model", "")
        material = e.get("Material", "")
        cls = e.get("EntityClass")
        if cls == "ItemSlot":
            if props.get("bSpawnOnStart", "1") != "1":
                excluded["slot_not_spawned"] += 1
                continue
            if any(link.get("TargetId") in ids for link in e.findall("EntityLinks/Link") if link.get("Name") == "SlotLink"):
                excluded["slot_already_has_placed_item"] += 1
                continue
            identity = ("1,0,0", "0,1,0", "0,0,1", "0,0,0")
            if any(props.get(f"vectorPIMatRow{i+1}", value) != value for i, value in enumerate(identity)):
                excluded["unlinked_slot_local_transform"] += 1
                continue
        if cls in ("ItemSlot", "PickableItem"):
            model, material = items.get(props.get("guidItemClassId", "").lower(), ("", ""))
        model = model.replace("\\", "/").lower()
        if not model.endswith((".cgf", ".cga")):
            excluded["unresolved_or_character_model"] += 1
            continue
        target = ET.Element("Entity", Name="glue_item_" + e.get("EntityId", str(len(result))),
                            EntityClass="GeomEntity", Geometry=model, Pos=','.join(map(str,position)),
                            CastShadowMinSpec="1", ViewDistRatio="100")
        target.set('Rotate',','.join(map(str,rotation)))
        target.set('Scale',','.join(map(str,scale)))
        ET.SubElement(target, "Properties", bSaved_by_game="0", bInteractiveCollisionClass="0")
        result.append({"entity": target, "model": model, "material": material,
                       "source_id": e.get("EntityId"), "source_class": cls, "source_name": e.get("Name"),
                       "source_layer": e.get("Layer")})
    return result, dict(excluded)
