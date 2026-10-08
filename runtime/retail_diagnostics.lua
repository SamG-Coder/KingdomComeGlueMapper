-- Read-only capability probe. Included only by setup --diagnostics.
local function log(message)
    System.LogAlways("[GlueMapper:diagnostics] " .. tostring(message))
end

local function inspect(name)
    local value = _G[name]
    log(name .. "=" .. type(value))
    if type(value) == "table" then
        local names = {}
        for key, member in pairs(value) do
            if type(member) == "function" then names[#names + 1] = tostring(key) end
        end
        table.sort(names)
        log(name .. " methods=" .. table.concat(names, ","))
    end
end

for _, name in ipairs({"UIAction", "Game", "System", "Script", "Quest", "Database", "Skald"}) do
    inspect(name)
end

for _, name in ipairs({"wh_sys_BaseLevelId", "wh_sys_NoPlaylineDeleting", "wh_sys_DebugPlayline"}) do
    local ok, value = pcall(System.GetCVar, name)
    log("CVar " .. name .. " readable=" .. tostring(ok) .. " value=" .. tostring(value))
end

local function describe(value, depth)
    if type(value) ~= "table" or depth == 0 then return tostring(value) end
    local fields = {}
    for key, item in pairs(value) do
        fields[#fields + 1] = tostring(key) .. "=" .. describe(item, depth - 1)
        if #fields >= 20 then break end
    end
    table.sort(fields)
    return "{" .. table.concat(fields, ",") .. "}"
end

if UIAction and UIAction.RegisterElementListener then
    KingdomComeGlueMapper.diagnostics = {}
    KingdomComeGlueMapper.diagnostics.OnMenuEvent = function(...)
        local values = {}
        for _, value in ipairs({...}) do
            values[#values + 1] = describe(value, 2)
        end
        log("Menu event " .. table.concat(values, " | "))
    end
    local ok, err = pcall(UIAction.RegisterElementListener,
        KingdomComeGlueMapper.diagnostics, "Menu", 0, "", "OnMenuEvent")
    log("Menu listener registered=" .. tostring(ok) .. " result=" .. tostring(err))
end
