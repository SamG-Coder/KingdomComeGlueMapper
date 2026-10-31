from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from test_campaign_quest_items import tracked, A, B
from campaign_quest_graph import emit_project
from campaign_quest_items import bind
from quest_lab.graph import Graph
from quest_lab.source import Source
from quest_lab.objects import SOUL, INVENTORY
from quest_lab.inventory import acquire


def bag(count):
    return {INVENTORY: {'queries': [dict(method='Items', arguments={}, result={B:count})]}}


def graph():
    model = tracked()
    files, _ = emit_project([model], 'Example')
    path = 'Example/arbitrary_quest.xml'
    files[path], _ = bind(files[path], model, {A:B}, [B], 'player')
    return Graph(Source.blobs(files), 'arbitrary_quest', assets={'player':'henry'}, objects={
        'henry':{SOUL:{'properties':{'Inventory':'player-bag'}}},
        'player-bag':bag(0), 'other-bag':bag(0)})


class InventoryTests(unittest.TestCase):
    def test_scenario_acquisition_uses_production_bound_graph(self):
        from quest_lab.runner import Runner
        g=graph()
        scenario=dict(schema=1,name='Tracked quantity',entry='arbitrary_quest',
            assets=g.assets,objects=g.objects.records,steps=[
                dict(action='fire',port='start_first'),
                dict(action='item_acquired',inventory='player-bag',item=B,amount=9),
                dict(action='assert',values={'output.first_Completed':False}),
                dict(action='item_acquired',inventory='player-bag',item=B,amount=1),
                dict(action='assert',values={'output.first_Completed':True})])
        result=Runner(scenario,source=g.source).run()
        self.assertEqual(result['status'],'PASS',result['error'])

    def test_quantity_actor_and_class_boundaries_then_save_replay(self):
        g=graph();self.assertEqual(g.unsupported, [])
        g.fire('start_first');g.advance_to(g.now)
        acquire(g,'other-bag',B,10);g.advance_to(g.now)
        acquire(g,'player-bag',A,10);g.advance_to(g.now)
        acquire(g,'player-bag',B,9);g.advance_to(g.now)
        self.assertFalse(g.value('output.first_Completed'))
        saved=g.snapshot()
        acquire(g,'player-bag',B,1);g.advance_to(g.now)
        self.assertTrue(g.value('output.first_Completed'))
        g.restore(saved)
        self.assertFalse(g.value('output.first_Completed'))
        acquire(g,'player-bag',B,1);g.advance_to(g.now)
        self.assertTrue(g.value('output.first_Completed'))

    def test_cancelled_and_inactive_watchers_do_not_complete(self):
        g=graph();acquire(g,'player-bag',B,9);g.advance_to(g.now)
        self.assertFalse(g.value('output.first_Completed'))
        g.fire('start_first');g.fire('cancel_first')
        acquire(g,'player-bag',B,1);g.advance_to(g.now)
        self.assertFalse(g.value('output.first_Completed'))
        g.fire('start_first');g.advance_to(g.now)
        self.assertTrue(g.value('output.first_Completed'))

    def test_snapshot_alone_does_not_invent_events_and_invalid_amount_is_atomic(self):
        g=graph();g.fire('start_first');g.advance_to(g.now)
        g.objects.replace('player-bag',bag(10));g.advance_to(g.now)
        self.assertFalse(g.value('output.first_Completed'))
        for amount in (0,-1,True,0.5):
            before=g.snapshot()
            with self.assertRaises(ValueError): acquire(g,'player-bag',B,amount)
            self.assertEqual(g.snapshot(),before)
