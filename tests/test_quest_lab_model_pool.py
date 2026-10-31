import copy
import unittest

from test_campaign_behavior import source, catalog
from campaign_behavior import convert_behaviors
from quest_lab.model_pool import ModelPool


class ModelPoolTests(unittest.TestCase):
    def test_only_identical_documents_share_storage(self):
        pool=ModelPool()
        document={'trees':{'work':{'variables':[],'root':{'op':'Success'}}},'provenance':['original']}
        def model(name, doc):return {'quest':name,'behavior_documents':{'shared.xml':copy.deepcopy(doc)},'bindings':[name]}
        a=pool.add(model('a',document));b=pool.add(model('b',document))
        self.assertIs(a['behavior_documents']['shared.xml'],b['behavior_documents']['shared.xml'])
        altered=copy.deepcopy(document);altered['trees']['work']['root']['op']='Fail'
        c=pool.add(model('c',altered))
        self.assertIsNot(c['behavior_documents']['shared.xml'],a['behavior_documents']['shared.xml'])
        altered=copy.deepcopy(document);altered['provenance']=['patch']
        d=pool.add(model('d',altered))
        self.assertIsNot(d['behavior_documents']['shared.xml'],a['behavior_documents']['shared.xml'])
        self.assertEqual(a['bindings'],['a']);self.assertEqual(b['bindings'],['b'])
        self.assertEqual(pool.reused,1)

    def test_conversion_keeps_shared_source_immutable_and_matches_unpooled(self):
        models=[source('<Root><Behavior><IncludeTree File="shared.xml" Name="work"/></Behavior></Root>',
                       {'libs/ai/shared.xml':{'trees':{'work':{'variables':[],'root':{
                           'op':'Success','attributes':{},'children':[],'text':''}}}}},name=name)
                for name in ('q_alpha','q_beta')]
        expected=convert_behaviors(copy.deepcopy(models),'Pooled',catalog())
        pool=ModelPool();pooled=[pool.add(m) for m in models];before=copy.deepcopy(pooled)
        self.assertEqual(convert_behaviors(pooled,'Pooled',catalog()),expected)
        self.assertEqual(pooled,before)
        self.assertEqual(pool.reused,1)


if __name__ == '__main__':unittest.main()
