-- Bring the existing owned horse beside the player after KDC1 entry settles.
-- No replacement soul, ownership changes, or equipment reconstruction.
GlueTravelHorseRecovery = GlueTravelHorseRecovery or {generation=0}
local recovery=GlueTravelHorseRecovery
local function log(s) System.LogAlways('GLUE_HORSE_ARRIVAL '..s) end
local function pos(p) return string.format('%.2f,%.2f,%.2f',p.x,p.y,p.z) end
local function inspect(e,owned)
    if not e then return 'missing' end
    local ok,result=pcall(function()
        local props=e.Properties or {}
        return 'class='..tostring(e.class)..' dummy='..tostring(props.bIsDummy)..
            ' profile='..tostring(props.ControlProfile)..' ai='..tostring(CryAction.HasAI(e.id))..
            ' health='..tostring(e.actor and e.actor:GetHealth())..
            ' controller='..tostring(e.ActionController)..
            ' wuid='..tostring(XGenAIModule.GetWuidDebugString(owned))
    end)
    return ok and result or 'inspect_error='..tostring(result)
end
local function dist2(a,b) return (a.x-b.x)^2+(a.y-b.y)^2 end
function recovery.NearbyTarget(p,ground)
    local road=GlueTravelHorseRoadArrivals and GlueTravelHorseRoadArrivals.kcd1_travel
    if not road or dist2(p,road.arrival)>900 then return nil end
    local driver=System.GetEntityByName('gmtravel_return_driver')
    local coach=driver and driver:GetWorldPos()
    -- The importer derives these positions from the local road mesh and checks
    -- a 3 x 1.4 metre footprint. Never substitute an arbitrary off-road offset.
    for _,point in ipairs(road.positions) do
        local q={x=point.x,y=point.y,z=point.z}
        local h=System.GetTerrainElevation(q)
        if math.abs(h-ground)<1.5 and math.abs(h-point.z)<1 and
                dist2(q,p)>=9 and (not coach or dist2(q,coach)>=36) then
            q.z=h+0.25;return q
        end
    end
end
function recovery.InspectDriver(actor)
    local e=System.GetEntityByName('gmtravel_return_driver')
    if not e or not e.actor or not e.soul then return end
    local ok,result=pcall(function()
        return 'can_talk='..tostring(e.actor:CanTalk(actor.id))..
            ' role='..tostring(e.soul:HasRoleByName('GMTRAVEL_RETURN_DRIVER'))..
            ' restricted='..tostring(e.soul:IsDialogRestricted(actor.id))..
            ' health='..tostring(e.actor:GetHealth())..' position='..pos(e:GetWorldPos())
    end)
    log('driver '..(ok and result or 'inspect_error='..tostring(result)))
end
local function currentMap()
    -- TagPoints can be consumed by native systems without a Lua entity.
    local raw=System.GetCVar('sv_map')
    local name=type(raw)=='string' and string.lower(raw):gsub('\\','/'):gsub('/+$','') or ''
    return name:match('([^/]+)$') or '',tostring(raw)
end
function recovery.Check(actor,state)
    if not actor or actor~=player or not actor.player then return 'player_pending' end
    local map,raw=currentMap()
    local owned=actor.player:GetPlayerHorse()
    local p=actor:GetWorldPos()
    local e=owned~=nil and owned~=__null and owned~=0 and XGenAIModule.GetEntityByWUID(owned) or nil
    if not state.diagnosed or state.map~=raw or state.observedOwner~=tostring(owned) then
        state.diagnosed=true;state.map=raw;state.observedOwner=tostring(owned)
        log('observe map='..raw..' player='..pos(p)..' player_ground='..tostring(System.GetTerrainElevation(p))..
            ' owned='..tostring(owned)..' entity='..tostring(e and e.id)..
            ' horse='..(e and pos(e:GetWorldPos()) or 'missing')..
            ' horse_ground='..tostring(e and System.GetTerrainElevation(e:GetWorldPos()))..
            ' hidden='..tostring(e and e:IsHidden())..
            ' arrival_marker='..tostring(System.GetEntityByName('gmtravel_arrival_kcd1')~=nil)..
            ' return_driver='..tostring(System.GetEntityByName('gmtravel_return_driver')~=nil)..
            ' details='..inspect(e,owned))
    end
    if map~='kcd1_travel' then return 'destination_pending' end
    if owned==nil or owned==__null or owned==0 then return 'no_owned_horse' end
    if not e or not e.horse then return 'owned_entity_missing' end
    if state.placed then return 'monitoring' end
    local dummy=e.Properties and e.Properties.bIsDummy
    if e.class~='Horse' or dummy==true or dummy==1 then return 'not_world_horse' end
    local horsePos=e:GetWorldPos()
    if (e:IsHidden()==false or e:IsHidden()==0) and horsePos.z>=System.GetTerrainElevation(horsePos)-1 then
        return 'horse_already_present'
    end
    local ground=System.GetTerrainElevation(p)
    if p.z<ground-1 or p.z>ground+4 then return 'player_position_pending' end
    if state.last and math.abs(p.x-state.last.x)+math.abs(p.y-state.last.y)+math.abs(p.z-state.last.z)<1 then
        state.stable=state.stable+1
    else state.stable=0 end
    state.last={x=p.x,y=p.y,z=p.z}
    if state.stable<2 then return 'arrival_settling' end
    local target=recovery.NearbyTarget(p,ground)
    if not target then return 'road_position_unavailable' end
    if actor.player:GetPlayerHorse()~=owned then return 'ownership_changed' end
    log('before owned='..tostring(owned)..' entity='..tostring(e.id)..' name='..e:GetName()..
        ' horse='..pos(e:GetWorldPos())..' player='..pos(p)..' target='..pos(target)..' hidden='..tostring(e:IsHidden()))
    e:SetWorldPos(target)
    e:Hide(0)
    log('placed entity='..tostring(e.id)..' horse='..pos(e:GetWorldPos())..' hidden='..tostring(e:IsHidden()))
    state.owned=owned;state.entity=e.id;state.placed=true
    return 'placed'
end
function recovery.Start(actor)
    recovery.generation=recovery.generation+1
    local generation,attempts,previous=recovery.generation,0,nil
    local state={stable=0}
    local function tick()
        if generation~=recovery.generation then return end
        attempts=attempts+1
        local ok,status=pcall(recovery.Check,actor,state)
        if not ok then log('error='..tostring(status));return end
        if attempts==4 then recovery.InspectDriver(actor) end
        if status~=previous then log('status='..status..' attempt='..attempts);previous=status end
        if status=='placed' then
            Script.SetTimer(2000,function()
                if generation~=recovery.generation then return end
                local ok,err=pcall(function()
                    local e=XGenAIModule.GetEntityByWUID(state.owned)
                    if not e then log('verify entity_missing');return end
                    log('verify entity='..tostring(e.id)..' horse='..pos(e:GetWorldPos())..
                        ' player='..pos(actor:GetWorldPos())..' hidden='..tostring(e:IsHidden())..
                        ' same_entity='..tostring(e.id==state.entity)..
                        ' same_owner='..tostring(actor.player:GetPlayerHorse()==state.owned)..
                        ' details='..inspect(e,state.owned))
                end)
                if not ok then log('verify_error='..tostring(err)) end
            end)
        end
        if status=='ownership_changed' then return end
        -- Ownership may be restored after the player entity has loaded. Keep a
        -- bounded observation window, logging changes rather than every tick.
        if attempts<30 then Script.SetTimer(1000,tick) else log('finished status='..status) end
    end
    Script.SetTimer(2000,tick)
end
local originalInit=Player.OnInit
function Player:OnInit(...)
    originalInit(self,...)
    recovery.Start(self)
end
local originalLoad=Player.OnLoadAI
function Player:OnLoadAI(...)
    if originalLoad then originalLoad(self,...) end
    recovery.Start(self)
end
