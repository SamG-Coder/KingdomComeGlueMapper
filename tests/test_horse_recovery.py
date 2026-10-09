"""Executable checks for bounded horse placement without ownership mutation."""
from pathlib import Path
import unittest
from lupa import LuaRuntime

class HorseRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute('''
        moves=0; owned=42; available=true; inMap=true; marker=false; mapOverride=nil; timers={}; logs={}; cliff=false
        Player={OnInit=function() end, OnLoadAI=function() end}
        player={player={},position={x=100,y=200,z=10}}
        entity={id=123,class='Horse',horse={},Properties={bIsDummy=false},position={x=900,y=800,z=-50},hidden=1}
        GlueTravelHorseRoadArrivals={kcd1_travel={arrival={x=100,y=200,z=10},positions={{x=94,y=200,z=10.25},{x=100,y=194,z=10.25}}}}
        function player.player:GetPlayerHorse() return owned end
        function player:GetWorldPos() return self.position end
        function entity:GetWorldPos() return self.position end
        function entity:SetWorldPos(p) moves=moves+1;self.position=p end
        function entity:GetName() return 'original_horse' end
        function entity:IsHidden() return self.hidden end
        function entity:Hide(v) self.hidden=v end
        System={GetEntityByName=function(name) if marker and name=='gmtravel_arrival_kcd1' then return {} end end,
            GetCVar=function(name) assert(name=='sv_map');return mapOverride or (inMap and 'kcd1_travel' or 'trosecko') end,
            GetTerrainElevation=function(p) if cliff and p.x~=100 then return -100 end return 10 end,
            LogAlways=function(s) table.insert(logs,s) end}
        XGenAIModule={GetEntityByWUID=function(id) if available and id==owned then return entity end end}
        XGenAIModule.GetWuidDebugString=function(id) return tostring(id) end
        CryAction={HasAI=function(id) return true end}
        Script={SetTimer=function(ms,f) table.insert(timers,f) end}
        function drain() local count=0;while #timers>0 do count=count+1;assert(count<100);table.remove(timers,1)() end end
        ''')
        self.lua.execute((Path(__file__).resolve().parents[1]/'tools/region_travel_horse_recovery.lua').read_text())
    def run_arrival(self):
        self.lua.execute('GlueTravelHorseRecovery.Start(player);drain()')
    def test_places_original_once_and_logs_verification(self):
        self.run_arrival()
        self.assertEqual(self.lua.eval('moves'),1)
        self.assertEqual(self.lua.eval('owned'),42)
        self.assertEqual(self.lua.eval('entity.position.x'),94)
        self.assertEqual(self.lua.eval('entity.position.z'),10.25)
        self.assertEqual(self.lua.eval('entity.hidden'),0)
        self.assertIn('same_owner=true',self.lua.eval("table.concat(logs,';')"))
        self.assertIn('arrival_marker=false',self.lua.eval('logs[1]'))
        self.assertIn('horse=900.00,800.00,-50.00',self.lua.eval('logs[1]'))
    def test_missing_entity_is_bounded_and_never_replaced(self):
        self.lua.execute('available=false');self.run_arrival()
        self.assertEqual(self.lua.eval('moves'),0)
        self.assertIn('finished status=owned_entity_missing',self.lua.eval('logs[#logs]'))
    def test_other_map_untouched(self):
        self.lua.execute('inMap=false');self.run_arrival()
        self.assertEqual(self.lua.eval('moves'),0)
    def test_normalizes_mod_mount_path(self):
        self.lua.execute("mapOverride='Mods/Gluemappertravel/Data/Levels/KCD1_TRAVEL/'")
        self.run_arrival()
        self.assertEqual(self.lua.eval('moves'),1)
    def test_missing_marker_does_not_disable_arrival(self):
        self.lua.execute('marker=false');self.run_arrival()
        self.assertEqual(self.lua.eval('moves'),1)
    def test_waits_for_level_identity_during_transition(self):
        self.lua.execute('inMap=false;GlueTravelHorseRecovery.Start(player);table.remove(timers,1)();inMap=true;drain()')
        self.assertEqual(self.lua.eval('moves'),1)
    def test_no_owned_horse_untouched(self):
        self.lua.execute('owned=0');self.run_arrival()
        self.assertEqual(self.lua.eval('moves'),0)
    def test_reload_cancels_old_chain(self):
        self.lua.execute('GlueTravelHorseRecovery.Start(player)');self.run_arrival()
        self.assertEqual(self.lua.eval('moves'),1)
    def test_waits_for_late_entity(self):
        self.lua.execute('available=false;GlueTravelHorseRecovery.Start(player);table.remove(timers,1)();available=true;drain()')
        self.assertEqual(self.lua.eval('moves'),1)
    def test_waits_for_ownership_restoration_after_level_load(self):
        self.lua.execute('owned=0;GlueTravelHorseRecovery.Start(player);table.remove(timers,1)();owned=42;drain()')
        self.assertEqual(self.lua.eval('moves'),1)
    def test_revisit_runs_a_new_arrival_and_retains_identity(self):
        self.run_arrival()
        self.lua.execute('inMap=false');self.run_arrival()
        self.lua.execute('inMap=true;entity.position={x=0,y=0,z=-1000};entity.hidden=1');self.run_arrival()
        self.assertEqual(self.lua.eval('moves'),2)
        self.assertEqual(self.lua.eval('owned'),42)
    def test_visible_saved_horse_is_not_teleported_on_reload(self):
        self.lua.execute('entity.hidden=0;entity.position={x=200,y=200,z=10}')
        self.run_arrival()
        self.assertEqual(self.lua.eval('moves'),0)
        self.assertIn('horse_already_present',self.lua.eval('logs[#logs]'))
    def test_no_off_road_fallback_when_imported_anchors_are_missing(self):
        self.lua.execute('GlueTravelHorseRoadArrivals=nil')
        self.run_arrival()
        self.assertEqual(self.lua.eval('moves'),0)
        self.assertIn('road_position_unavailable',self.lua.eval('logs[#logs]'))
    def test_inventory_dummy_is_never_moved(self):
        self.lua.execute('entity.Properties.bIsDummy=true')
        self.run_arrival()
        self.assertEqual(self.lua.eval('moves'),0)
    def test_far_away_save_does_not_use_arrival_anchors(self):
        self.lua.execute('player.position={x=500,y=500,z=10}')
        self.run_arrival()
        self.assertEqual(self.lua.eval('moves'),0)
    def test_placement_keeps_distance_from_coach(self):
        self.lua.execute('''
        local old=System.GetEntityByName
        System.GetEntityByName=function(n)
            if n=='gmtravel_return_driver' then return {GetWorldPos=function() return {x=103,y=200,z=10} end} end
            return old(n)
        end
        ''')
        self.run_arrival()
        self.assertEqual(self.lua.eval('entity.position.x'),94)
    def test_waits_for_grounded_player(self):
        self.lua.execute('player.position.z=-50');self.run_arrival()
        self.assertEqual(self.lua.eval('moves'),0)
    def test_avoids_steep_offset(self):
        self.lua.execute('cliff=true');self.run_arrival()
        self.assertEqual(self.lua.eval('entity.position.x'),100)
        self.assertEqual(self.lua.eval('entity.position.y'),194)
if __name__=='__main__':unittest.main()
