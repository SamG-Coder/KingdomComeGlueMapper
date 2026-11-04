"""Register source merchant hours and stock on an already imported work routine."""
import copy
import xml.etree.ElementTree as E
from campaign_entity_links import append_links, guid_value
from character_person_shop import register_shop_activity, register_shop_service
from character_person_activity import validate_scheduler_targets
from region_travel import guid
from upgrade_map import xml


def hours(shop):
    start,end=(float(shop['business_hours_'+key])*60 for key in ('begin','end'))
    if not 0 <= start < 1440 or not 0 <= end <= 1440 or start==end:
        raise ValueError('Unsupported merchant business hours')
    return [(start,end)] if start<end else [(start,1440),(0,end)]


def register(files, resources, entry, merchant, stock, namespace, role='innkeeper'):
    mission=E.fromstring(files['objects_mission0.xml'])
    files,resources,report=register_trees(files,resources,mission,E.fromstring(files['tables/ai/scheduler.xml']),
                                          entry,merchant,stock,namespace,role)
    files['objects_mission0.xml']=xml(mission)
    return files,resources,report


def register_trees(files, resources, mission, scheduler, entry, merchant, stock, namespace, role='innkeeper'):
    """register on an already parsed mission, edited in place.

    The caller serializes the mission. The scheduler is serialized here into
    files, because the native shop lifecycle is added to that copy; do not
    serialize the scheduler tree again.
    """
    files,resources=dict(files),dict(resources)
    rows=scheduler.find('Schedulers');name=merchant['name']
    actors=[e for e in mission if e.get('Name')==name]
    if len(actors)!=1:raise ValueError('Missing imported merchant: '+name)
    npc=actors[0];actor=next(r for r in rows if r.get('EntityGuid')==str(guid_value(npc.get('EntityGuid'))))
    activities=list(actor.findall('Links/S_ActivityLink'))
    if not activities:raise ValueError('Merchant requires a converted work schedule')
    next_id=max(int(e.get('EntityId','0')) for e in mission)+1
    def add(suffix,kind,pos):
        nonlocal next_id
        n=namespace+'_'+suffix
        if any(e.get('Name')==n for e in mission):raise ValueError('Merchant already registered: '+n)
        e=E.SubElement(mission,'Entity',Name=n,EntityClass=kind,EntityGuid=guid(n),EntityId=str(next_id),Pos=pos)
        next_id+=1;return e
    shop=add('shop','Shop',merchant['shop_entity'].get('Pos'))
    E.SubElement(shop,'Properties',sShopName=stock['shop_name'],iShopId=str(stock['shop_id']),bOwnerIsSpawned='1')
    hub=add('work','SchedulerHub',merchant['area'].get('Pos'));E.SubElement(hub,'Properties',bSaved_by_game='0')
    work=E.SubElement(rows,'C_SmartHub',EntityGuid=str(guid_value(hub.get('EntityGuid'))),IgnoreDeadEnds='false',StupidHub='false')
    links=E.SubElement(work,'Links')
    # Keep source work outside business hours; route staffed hours through the
    # shop hub. Each interval keeps its original destination and behavior.
    arrivals=[]
    for activity in activities:
        params=activity.find('Parameters')
        if params.get('BehaviorName') not in ('use', 'seller_market'):
            raise ValueError('Unsupported merchant work leaf')
        for begin,end in hours(merchant['shop']):
            begin=max(begin,float(params.get('TimeOfDayStart')));end=min(end,float(params.get('TimeOfDayEnd')))
            if begin>=end:continue
            leaf=copy.deepcopy(activity);links.append(leaf)
            incoming=copy.deepcopy(activity);incoming.set('TargetGuid',work.get('EntityGuid'))
            p=incoming.find('Parameters');p.attrib.pop('BehaviorName')
            p.set('TimeOfDayStart',str(begin));p.set('TimeOfDayEnd',str(end));p.set('Priority',str(int(p.get('Priority','0'))+1))
            actor.find('Links').append(incoming);arrivals.append(incoming)
    if len(arrivals)!=1:
        raise ValueError('Multiple merchant opening intervals require scheduler lifecycle conversion')
    files['tables/ai/scheduler.xml']=register_shop_activity(xml(scheduler),merchant['target'],npc,hub,shop,role=role)
    resources[entry]=register_shop_service(resources[entry],merchant['soul']['soul_id'],namespace)
    root=E.fromstring(resources[entry]);nodes=root.find('Skald/Level/Nodes')
    nodes.remove(nodes.find("SetEntityContext[@Name='"+namespace+"_ready']"))
    resources[entry]=xml(root)
    holders=[e for e in mission if e.get('EntityClass')=='LevelHolder']
    if len(holders)!=1:raise ValueError('Missing merchant level holder')
    edges=[(npc,shop,'owner'),(npc,shop,'shopKeeper'),(holders[0],shop,"asset['"+namespace+"']")]
    for a,b,label in edges:
        ls=a.find('EntityLinks')
        if ls is None:ls=E.SubElement(a,'EntityLinks')
        E.SubElement(ls,'Link',Name=label,TargetGuid=b.get('EntityGuid'),TargetId=b.get('EntityId'))
    files['waitinglinks.xml']=append_links(files['waitinglinks.xml'],[(a.get('EntityGuid'),b.get('EntityGuid'),label) for a,b,label in edges])
    validate_scheduler_targets(E.fromstring(files['tables/ai/scheduler.xml']))
    return files,resources,dict(person=name,shop=stock['shop_name'],business_hours=hours(merchant['shop']),runtime_verified=False)


def add_trade_dialogue(resources, entry, person, role, namespace):
    """Use native merchant topic/action semantics with a caller-owned identity."""
    import posixpath
    from region_travel_merchant import dialogue, repair_trade_prompts
    resources=dict(resources)
    name=namespace+'_trade'
    path=posixpath.join(posixpath.dirname(entry),name+'.xml')
    if path in resources:
        resources[path] = repair_trade_prompts(resources[path])
        return resources
    root=E.fromstring(resources[entry]);host=root.find('Skald/Level')
    definitions=host.find('Definitions')
    if definitions is None: definitions=E.SubElement(host,'Definitions')
    E.SubElement(definitions,'Definition',File=name+'.xml')
    node=E.SubElement(host.find('Nodes'),name,Name=name)
    # The native Shop sequence checks actual merchant service availability.
    resources[path]=dialogue(person,role=role,name=name,trade_string='ui_gmtravel_rattay_trade',diagnostics=False)
    resources[entry]=xml(root)
    return resources
