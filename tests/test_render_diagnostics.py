from pathlib import Path
import unittest
from lupa import LuaRuntime


class RenderDiagnosticsTests(unittest.TestCase):
    def test_bounded_observations_restore_logging_without_changing_rendering(self):
        lua = LuaRuntime()
        lua.execute('''
            logs={}; timers={}; settings={log_WriteToFileVerbosity=1,log_VerbosityOverridesWriteToFile=1}
            actor={id=9,actor={},class='NPC',GetName=function() return 'keeper' end,
                GetWorldPos=function() return {x=1,y=2,z=3} end,IsHidden=function() return false end,
                GetSlotCount=function() return 0 end,GetMaterial=function() return nil end}
            player=actor
            Player={OnLoadAI=function() loaded=(loaded or 0)+1 end}
            System={LogAlways=function(s) table.insert(logs,s) end,
                GetCVar=function(n) return settings[n] end,
                SetCVar=function(n,v)
                    assert(n=='log_WriteToFileVerbosity' or n=='log_VerbosityOverridesWriteToFile')
                    settings[n]=v
                end,
                GetEntitiesInSphere=function() return {actor} end}
            Script={SetTimer=function(ms,f) assert(ms==30000);table.insert(timers,f) end}
        ''')
        lua.execute((Path(__file__).resolve().parents[1] / 'runtime/render_diagnostics.lua').read_text())
        lua.execute('for i=1,20 do assert(timers[i]);timers[i]() end;assert(#timers==20)')
        self.assertEqual(lua.globals().settings['log_WriteToFileVerbosity'],1)
        self.assertEqual(lua.globals().settings['log_VerbosityOverridesWriteToFile'],1)
        logs=list(lua.globals().logs.values())
        self.assertEqual(sum('phase=nearby' in s for s in logs),3)
        self.assertTrue(any('slots=0' in s and 'material0=nil' in s for s in logs))
        # Retail discards timers on level transitions. The native player load
        # callback must still run and establish a new bounded observation window.
        lua.execute('timers={};Player:OnLoadAI();assert(loaded==1);assert(#timers==1);timers[1]()')
        self.assertEqual(lua.globals().settings['log_WriteToFileVerbosity'],4)

    def test_population_census_separates_distance_slots_and_unknowns(self):
        lua=LuaRuntime()
        lua.execute('''
            logs={}; scans=0
            player={GetWorldPos=function() return {x=0,y=0,z=0} end}
            local function actor(x,slots,hidden)
                return {actor={},GetWorldPos=function() return {x=x,y=0,z=0} end,
                    GetName=function() return 'test' end,
                    GetSlotCount=slots and function() return slots end or nil,
                    IsHidden=function() return hidden end}
            end
            System={LogAlways=function(s) table.insert(logs,s) end,GetCVar=function() end,
                GetEntities=function() scans=scans+1;return {actor(20,1,false),
                    actor(200,0,true),actor(1000,1,false),actor(1000,nil,false)} end}
            Script={SetTimer=function() end}
        ''')
        lua.execute((Path(__file__).resolve().parents[1]/'runtime/render_diagnostics.lua').read_text())
        lua.execute('GlueRenderDiagnostics.Population()')
        logs=list(lua.globals().logs.values())
        self.assertEqual(lua.globals().scans,1)
        self.assertTrue(any('range=500m+ entities=2 with_slots=1 hidden=0 slots_unavailable=1' in s for s in logs))
        self.assertTrue(any('range=100-500m entities=1 with_slots=0 hidden=1' in s for s in logs))


if __name__ == '__main__': unittest.main()
