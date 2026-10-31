import unittest
import xml.etree.ElementTree as E
from test_campaign_behavior import source
from campaign_work_sources import discover, area_context


class WorkSourcesTests(unittest.TestCase):
    def test_invalid_sibling_is_retained_without_losing_valid_programs(self):
        rows = {
            'sa_behaviour_tag': [dict(sa_behaviour_tag_id=i, sa_behaviour_tag_name=n,
                tree_file=f, tree_name=n) for i, n, f in (
                    ('main', 'work', 'work.xml'), ('bad', 'broken', 'invalidProgram'),
                    ('other', 'cleanup', 'cleanup.xml'))],
            'sa_smart_area2sa_behaviour_tag': [dict(sa_smart_area_id='area', sa_behaviour_tag_id=i)
                for i in ('main', 'bad', 'other')],
            'sa_smart_area': [dict(sa_smart_area_id='area')],
        }
        report = discover(self.trees('work'), lambda name: rows.get(name, []), include_area_context=True)[0]
        self.assertEqual({c['tree'] for c in report['candidates']}, {'work', 'cleanup'})
        invalid = report['area_context']['invalid_programs'][0]
        self.assertEqual(invalid['row'], rows['sa_behaviour_tag'][1])
        self.assertIn(invalid['error'], report['unresolved'])
        with self.assertRaisesRegex(ValueError, 'Invalid behavior include'):
            area_context([dict(table='sa_behaviour_tag', row=rows['sa_behaviour_tag'][0])],
                         lambda name: rows.get(name, []))

    def test_invalid_direct_registration_does_not_hide_valid_variant(self):
        rows = [dict(sa_behaviour_tag_name='work', tree_file=f, tree_name='Work')
                for f in ('invalidProgram', 'valid.xml')]
        report = discover(self.trees('work'), lambda name: rows)[0]
        self.assertEqual([c['file'] for c in report['candidates']], ['Libs/AI/valid.xml'])
        self.assertEqual(report['invalid_programs'][0]['row'], rows[0])
        self.assertTrue(report['unresolved'])

    def test_tool_object_dispatch_retains_use_return_and_brain_callbacks(self):
        rows = {
            'so_behaviour_tag': [dict(so_behaviour_tag_id=i, so_behaviour_tag_name=n,
                tree_file='tools.xml', tree_name=n) for i, n in [('use', 'useItem'), ('return', 'returnItem')]],
            'so_smart_object2so_behaviour_tag': [dict(so_smart_object_id='tool', so_behaviour_tag_id=i)
                for i in ('use', 'return')],
            'so_smart_object': [dict(so_smart_object_id='tool', brain_id='brain')],
            'brain2subbrain': [dict(brain_id='brain', subbrain_id='sub')],
            'subbrain': [dict(subbrain_id='sub', subbrain_type='object')],
            'subbrain_type': [dict(subbrain_type_id='object', subbrain_type_name='SmartObject')],
            'subbrain_smart_object': [dict(subbrain_id='sub', file_name='callbacks.xml',
                on_request_tree='Request', on_release_tree='Release', on_update_tree='Update')],
        }
        result = area_context([dict(table='so_behaviour_tag', row=rows['so_behaviour_tag'][0])], rows.__getitem__)
        self.assertEqual({p['tree'] for p in result['programs']},
                         {'useItem', 'returnItem', 'Request', 'Release', 'Update'})
        self.assertEqual(result['templates'], rows['so_smart_object'])

    def test_area_context_follows_sibling_jobs_and_all_lifecycle_callbacks(self):
        rows={
            'sa_behaviour_tag':[dict(sa_behaviour_tag_id='main',sa_behaviour_tag_name='work',tree_file='work.xml',tree_name='Work'),
                                dict(sa_behaviour_tag_id='secondary',sa_behaviour_tag_name='cleanup',tree_file='clean.xml',tree_name='Clean')],
            'sa_smart_area2sa_behaviour_tag':[dict(sa_smart_area_id='area',sa_behaviour_tag_id=t) for t in ('main','secondary')],
            'sa_smart_area':[dict(sa_smart_area_id='area',brain_id='brain')],
            'brain2subbrain':[dict(brain_id='brain',subbrain_id='sub')],
            'subbrain':[dict(subbrain_id='sub',subbrain_type='99')],
            'subbrain_type':[dict(subbrain_type_id='99',subbrain_type_name='SmartArea')],
            'subbrain_smart_area':[dict(subbrain_id='sub',file_name='lifecycle.xml',on_enter_tree='Enter',on_exit_tree='Exit',
                on_update_tree='Update',on_request_tree='Request',on_release_tree='Release')],
        }
        result=area_context([dict(table='sa_behaviour_tag',row=rows['sa_behaviour_tag'][0])],rows.__getitem__)
        self.assertEqual({p['tree'] for p in result['programs']},{'Work','Clean','Enter','Exit','Update','Request','Release'})
        self.assertEqual(result['templates'],rows['sa_smart_area'])

    def trees(self, expression, only='true'):
        m = source('''<Root><SmartAreaBehaviorTag preferredBehaviors="&quot;''' + expression +
            '''&quot;" preferredBehaviorsOnly="&quot;''' + only + '''&quot;"/></Root>''')
        return m['behavior_documents']['libs/ai/quests/q_test.xml']['trees']

    def test_table_dispatch_finds_all_programs_without_matching_actor_names(self):
        rows = [dict(sa_behaviour_tag_name='AuthoredJob', tree_file='work/tools.xml', tree_name='cycle'),
                dict(sa_behaviour_tag_name='AuthoredJob', tree_file='work/other.xml', tree_name='variant'),
                dict(sa_behaviour_tag_name='Unrelated', tree_file='elsewhere.xml', tree_name='unused')]
        report = discover(self.trees('AuthoredJob'), lambda name: rows)
        self.assertEqual({(c['file'], c['tree']) for c in report[0]['candidates']},
                         {('Libs/AI/work/tools.xml', 'cycle'), ('Libs/AI/work/other.xml', 'variant')})
        self.assertFalse(report[0]['unresolved'])

    def test_dynamic_and_fallback_requests_remain_explicit(self):
        dynamic = discover(self.trees('$nextJob'), lambda name: self.fail('Do not guess a dynamic job'))
        self.assertFalse(dynamic[0]['candidates'])
        self.assertTrue(dynamic[0]['unresolved'])
        fallback = discover(self.trees('Unknown', 'false'), lambda name: [])
        self.assertEqual(len(fallback[0]['unresolved']), 2)

    def test_direct_callback_follows_area_and_object_registrations(self):
        model = source('''<Root><CallBehavior Target="&quot;station&quot;" BehaviorName="&quot;use&quot;"/>
          <SmartObjectBehaviorTag preferredBehaviors="&quot;pickup&quot;" preferredBehaviorsOnly="&quot;true&quot;"/>
          </Root>''')
        tables = {
            'sa_behaviour_tag': [dict(sa_behaviour_tag_name='use', tree_file='work/area.xml', tree_name='areaUse')],
            'so_behaviour_tag': [dict(so_behaviour_tag_name='use', tree_file='work/prop.xml', tree_name='propUse'),
                                 dict(so_behaviour_tag_name='pickup', tree_file='work/prop.xml', tree_name='pickup')],
        }
        calls = discover(model['behavior_documents']['libs/ai/quests/q_test.xml']['trees'], tables.__getitem__)
        self.assertEqual({c['tree'] for c in calls[0]['candidates']}, {'areaUse', 'propUse'})
        self.assertEqual([c['tree'] for c in calls[1]['candidates']], ['pickup'])
        self.assertFalse(any(c['unresolved'] for c in calls))


if __name__ == '__main__':
    unittest.main()
