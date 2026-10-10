import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from lupa import LuaRuntime

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from region_travel_ai_trace import event, resources


class AITraceTests(unittest.TestCase):
    def test_host_trace_uses_native_loaded_gate(self):
        gate=event('human_host:begin')
        self.assertEqual(gate.tag,'IsLoadedGate')
        self.assertIn(',true)',gate.find('Then/ExecuteLua').get('code'))
        self.assertIn(',false)',gate.find('Else/ExecuteLua').get('code'))

    def test_startup_hosts_do_not_exhaust_reaction_trace_budget(self):
        specs=[('switch_animal_horseBrain','Function_callInterrupt_animal_moveToPlayer',1),
            ('callInterrupt_animal_moveToPlayer','Function_crime_getRedkev',1),
            ('interrupt_animal_moveToPlayer','Move',3),
            ('switch_handleHitReaction','SendAIConceptSignal_hitReactionNotification',1),
            ('callInterrupt_flee','Function_crime_getMrkev',1),('crime_getMrkev','GraphSearch',1)]
        documents=[('<BehaviorTrees><BehaviorTree name="'+name+'"><Root><Behavior>'+
            ('<'+tag+'/>')*count+'</Behavior></Root></BehaviorTree></BehaviorTrees>').encode()
            for name,tag,count in specs]
        with patch('region_travel_ai_trace.read',side_effect=documents): _,code=resources(None,'kcd1_travel')
        lua=LuaRuntime()
        lua.execute('''
            logs={}
            System={GetCVar=function() return 'kcd1_travel' end,
                LogAlways=function(s) table.insert(logs,s) end}
            e={id=1,GetName=function() return 'test' end,
                GetWorldPos=function() return {x=0,y=0,z=0} end}
        ''')
        lua.execute(code.decode())
        lua.execute('''
            for i=1,1000 do e.id=i;GlueTravelAITrace(e,'human_host:begin',false) end
            GlueTravelAITrace(e,'horse_command:begin')
        ''')
        logs=list(lua.globals().logs.values())
        self.assertEqual(sum('phase=human_host:' in s for s in logs),40)
        self.assertTrue(any('phase=horse_command:' in s for s in logs))
        self.assertTrue(any('engine_loaded=false' in s for s in logs))
