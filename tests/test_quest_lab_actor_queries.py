import unittest
from test_campaign_behavior import source
from quest_lab.actor_queries import inspect


class ActorQueryTests(unittest.TestCase):
    def test_records_filters_without_guessing_default_origin(self):
        model=source('''<Root><GraphSearch Origin="&quot;&quot;" depth="&quot;1&quot;">
            <LinkDataFilter tag="&quot;QuestActor&quot;" condition="&quot;$QuestActor == 'worker'&quot;"
                Child="&quot;worker&quot;" negprune="&quot;unknown&quot;"/>
            </GraphSearch></Root>''')
        query,=inspect(model)
        self.assertTrue(query['origin_requires_engine_context'])
        self.assertEqual(query['filters'][0]['attributes']['Child'],'worker')
        self.assertEqual(query['filters'][0]['attributes']['negprune'],'unknown')
        self.assertFalse(query['executed'])

    def test_nested_search_filters_are_not_assigned_to_parent_origin(self):
        model=source('''<Root><GraphSearch Origin="&quot;__object.id&quot;">
            <LinkTagFilter tag="&quot;outer&quot;"/><Sequence>
            <GraphSearch Origin="&quot;target&quot;"><LinkTagFilter tag="&quot;inner&quot;"/></GraphSearch>
            </Sequence></GraphSearch></Root>''')
        outer,inner=inspect(model)
        self.assertEqual([f['attributes']['tag'] for f in outer['filters']],['outer'])
        self.assertEqual([f['attributes']['tag'] for f in inner['filters']],['inner'])
        self.assertEqual(inner['search']['Origin'],'target')
        self.assertFalse(outer['origin_requires_engine_context'])


if __name__=='__main__':unittest.main()
