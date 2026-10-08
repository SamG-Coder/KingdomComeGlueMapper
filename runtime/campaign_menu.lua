-- Menu lifecycle comes from a narrowly patched local copy of the retail UI.
-- New Game remains unavailable until the campaign/persistence gates pass.
local mod = KingdomComeGlueMapper

function mod:OnCampaignMenuEvent(element, instance, event, args)
    if event ~= "GlueMapperRootMenu" then return end
    System.LogAlways("[GlueMapper] Root menu event received")
    -- Script timers do not advance at the title screen. The root lifecycle
    -- event is emitted after its native function, so update directly.
    UIAction.CallFunction("Menu", 0, "GlueMapperRemoveButton", "GlueMapperPlayKDC1", 0)
    UIAction.CallFunction("Menu", 0, "AddBasicButton", "GlueMapperPlayKDC1", 0,
        "Play KDC1", "The KDC1 campaign is not ready to play yet.", true)
    System.LogAlways("[GlueMapper] Play KDC1 menu entry added; disabled until campaign is ready")
end

if UIAction and UIAction.RegisterElementListener then
    UIAction.RegisterElementListener(mod, "Menu", 0, "GlueMapperRootMenu", "OnCampaignMenuEvent")
else
    System.LogAlways("[GlueMapper] Required retail menu API unavailable")
end
