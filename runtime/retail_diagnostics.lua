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

if UIAction and UIAction.RegisterElementListener then
    KingdomComeGlueMapper.diagnostics = {}
    KingdomComeGlueMapper.diagnostics.OnMenuEvent = function(...)
        local values = {}
        for _, value in ipairs({...}) do
            values[#values + 1] = tostring(value)
        end
        log("Menu event " .. table.concat(values, " | "))
    end
    local ok, err = pcall(UIAction.RegisterElementListener,
        KingdomComeGlueMapper.diagnostics, "Menu", 0, "", "OnMenuEvent")
    log("Menu listener registered=" .. tostring(ok) .. " result=" .. tostring(err))
end
