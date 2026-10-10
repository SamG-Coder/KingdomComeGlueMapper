-- Temporary, read-only render observations; no spawning, material replacement,
-- graphics-budget changes, or console UI. File logging restores after ten minutes.
GlueRenderDiagnostics = GlueRenderDiagnostics or {}
local diag = GlueRenderDiagnostics
local function log(s) System.LogAlways('GLUE_RENDER '..s) end
local function read(object, method, ...)
    if not object or type(object[method]) ~= 'function' then return 'unavailable' end
    local ok, value = pcall(object[method], object, ...)
    return ok and tostring(value) or 'read_error'
end
local function cvar(name)
    local ok, value = pcall(System.GetCVar, name)
    return ok and value or nil
end
local previous = {}
for name, value in pairs({log_WriteToFileVerbosity=4, log_VerbosityOverridesWriteToFile=0}) do
    previous[name] = cvar(name)
    if previous[name] ~= nil then pcall(System.SetCVar, name, value) end
    log('logging '..name..' previous='..tostring(previous[name])..' current='..tostring(cvar(name)))
end
local settings = {'r_TexturesStreamPoolSize','r_TexturesStreamDynamicPool',
    'r_TexturesStreamDynamicPoolMaxSizeWH','e_StreamCgfPoolSize',
    'ca_AttachmentMergingMemoryBudget','r_buffer_pool_max_allocs',
    'r_MeshPoolSize','e_MergedMeshesPool','e_Vegetation','e_Brushes',
    'e_MergedMeshes','r_ShadersAsyncCompiling','e_ViewDistRatio',
    'e_ViewDistRatioVegetation','ca_DrawCharacters','ca_DrawAttachments',
    'wh_e_HLodStreamBrushInstances','wh_e_HLodStreamingFramesUntilUnload',
    'wh_e_HLodStreamingMaxReleasedPerFrame','wh_e_HlodMaxStreamingTasks',
    'e_StreamCgf','e_StreamInstances','e_MergedMeshesInstanceDist',
    'e_MergedMeshesLodRatio','r_TexturesStreaming'}
local observations = {}
function diag.Population()
    if not player or type(System.GetEntities) ~= 'function' then return end
    local origin = player:GetWorldPos()
    local buckets = {{limit=100,label='0-100m'}, {limit=500,label='100-500m'},
                     {limit=math.huge,label='500m+'}}
    for _,b in ipairs(buckets) do b.total=0; b.slotted=0; b.hidden=0; b.unknown=0 end
    local examples=0
    -- Five read-only censuses in ten minutes, not an update loop or a spawn scan.
    -- Lua entities and slots are observations, not a claim of AI simulation LOD.
    for _,e in pairs(System.GetEntities() or {}) do
        if e.actor and e~=player and e.GetWorldPos then
            local p=e:GetWorldPos()
            local distance=math.sqrt((p.x-origin.x)^2+(p.y-origin.y)^2+(p.z-origin.z)^2)
            local slots=tonumber(read(e,'GetSlotCount'))
            for _,b in ipairs(buckets) do
                if distance<=b.limit then
                    b.total=b.total+1
                    if slots==nil then b.unknown=b.unknown+1
                    elseif slots>0 then b.slotted=b.slotted+1 end
                    if read(e,'IsHidden')=='true' or read(e,'IsHidden')=='1' then b.hidden=b.hidden+1 end
                    break
                end
            end
            if distance>500 and slots and slots>0 and examples<8 then
                log('far_actor name='..read(e,'GetName')..' distance='..math.floor(distance)..
                    ' slots='..slots..' hidden='..read(e,'IsHidden'))
                examples=examples+1
            end
        end
    end
    for _,b in ipairs(buckets) do
        log('population range='..b.label..' entities='..b.total..' with_slots='..b.slotted..
            ' hidden='..b.hidden..' slots_unavailable='..b.unknown)
    end
end
function diag.Entity(entity, phase)
    if not entity then return end
    local identity = tostring(entity.id)
    local count = (observations[identity] or 0) + 1
    if count > 3 then return end
    observations[identity] = count
    local p = entity.GetWorldPos and entity:GetWorldPos() or {}
    log('phase='..phase..' name='..read(entity,'GetName')..' class='..tostring(entity.class)..
        ' position='..tostring(p.x)..','..tostring(p.y)..','..tostring(p.z)..
        ' hidden='..read(entity,'IsHidden')..' slots='..read(entity,'GetSlotCount')..
        ' material0='..read(entity,'GetMaterial',0)..' slot0valid='..read(entity,'IsSlotValid',0))
end
local ticks, generation = 0, 0
local function tick(owner)
    if owner ~= generation then return end
    ticks = ticks + 1
    local map = tostring(cvar('sv_map'))
    if ticks == 1 or ticks % 4 == 0 then
        log('snapshot='..ticks..' map='..map)
        for _,name in ipairs(settings) do log('cvar '..name..'='..tostring(cvar(name))) end
    end
    local ok, err = pcall(function()
        if ticks==1 or ticks==4 or ticks==8 or ticks==12 or ticks==16 then diag.Population() end
        if player and player.GetWorldPos and type(System.GetEntitiesInSphere)=='function' then
            local nearby = System.GetEntitiesInSphere(player:GetWorldPos(),30) or {}
            local count = 0
            for _,entity in pairs(nearby) do
                if entity.actor then
                    diag.Entity(entity,'nearby')
                    count=count+1
                    if count>=16 then break end
                end
            end
        end
    end)
    if not ok then log('snapshot_error='..tostring(err)) end
    if ticks < 20 then Script.SetTimer(30000,function() tick(owner) end)
    else
        for name,value in pairs(previous) do pcall(System.SetCVar,name,value) end
        log('finished; file logging restored')
    end
end
function diag.Start()
    -- Level transitions clear Script timers. Restart from the same Player
    -- lifecycle used by the companion hook, cancelling any surviving callbacks.
    generation=generation+1
    ticks=0
    local owner=generation
    if previous.log_WriteToFileVerbosity ~= nil then pcall(System.SetCVar,'log_WriteToFileVerbosity',4) end
    if previous.log_VerbosityOverridesWriteToFile ~= nil then pcall(System.SetCVar,'log_VerbosityOverridesWriteToFile',0) end
    log('scheduled from player lifecycle generation='..owner)
    Script.SetTimer(30000,function() tick(owner) end)
end
if type(Player)=='table' then
    local originalInit, originalLoad = Player.OnInit, Player.OnLoadAI
    function Player:OnInit(...)
        if originalInit then originalInit(self,...) end
        diag.Start()
    end
    function Player:OnLoadAI(...)
        if originalLoad then originalLoad(self,...) end
        diag.Start()
    end
end
log('installed; bounded observations, rendering unchanged')
diag.Start()
