-- Menu lifecycle comes from a narrowly patched local copy of the retail UI.
-- The experimental New Game route is opt-in while quests/persistence are pending.
local mod = KingdomComeGlueMapper

function mod:OnCampaignMenuEvent(element, instance, event, args)
    if event == "OnButton" and args and args[0] == "GlueMapperPlayKDC1" then
        if not self.startProbe then return end
        local previous = System.GetCVar("wh_sys_BaseLevelId")
        if previous == nil then
            System.LogAlways("[GlueMapper] Native initial-level hook is unavailable; start aborted")
            return
        end
        self.previousBaseLevel = previous
        System.SetCVar("wh_sys_BaseLevelId", 1000)
        if tonumber(System.GetCVar("wh_sys_BaseLevelId")) ~= 1000 then
            System.LogAlways("[GlueMapper] Native initial-level hook rejected the campaign; start aborted")
            return
        end
        self.pendingNewGame = true
        System.LogAlways("[GlueMapper] Requesting native New Game for level 1000 (kcd1_rataje); startup probe")
        UIAction.CallFunction("Menu", 0, "GlueMapperNativeNewGame")
        return
    end
    if event ~= "GlueMapperRootMenu" then return end
    if self.pendingNewGame then
        System.SetCVar("wh_sys_BaseLevelId", self.previousBaseLevel)
        self.pendingNewGame = false
        System.LogAlways("[GlueMapper] Restored native initial level on return to root menu")
    end
    System.LogAlways("[GlueMapper] Root menu event received")
    -- Script timers do not advance at the title screen. The root lifecycle
    -- event is emitted after its native function, so update directly.
    UIAction.CallFunction("Menu", 0, "GlueMapperRemoveButton", "GlueMapperPlayKDC1", 0)
    UIAction.CallFunction("Menu", 0, "AddBasicButton", "GlueMapperPlayKDC1", 0,
        "Play KDC1", self.startProbe and "Test converted world startup. Original quests are not running yet." or "The KDC1 campaign is not ready to play yet.", not self.startProbe)
    System.LogAlways("[GlueMapper] Play KDC1 menu entry added; startProbe=" .. tostring(self.startProbe == true))
end

if UIAction and UIAction.RegisterElementListener then
    UIAction.RegisterElementListener(mod, "Menu", 0, "", "OnCampaignMenuEvent")
else
    System.LogAlways("[GlueMapper] Required retail menu API unavailable")
end
