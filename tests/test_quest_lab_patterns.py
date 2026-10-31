import json
import unittest

from test_campaign_behavior import source, catalog
from campaign_behavior import convert_behaviors
from test_quest_lab_includes import graph
from test_quest_lab_variables import controller
from test_quest_lab_parallel import wait
from quest_lab import Unsupported
from quest_lab.behavior import Behavior
from quest_lab.patterns import concatenate


class PatternTests(unittest.TestCase):
    def test_native_string_and_integer_patterns(self):
        values={'timeCheck_int':12,'alias':'quest_','dialogSequence':'intro',
                'this':{'name':'guard'},'spawnCounter':3,'name':'_spawn_',
                'greeters':['henry','guard'],'label':'$literal;'}
        for pattern, expected in (
            ("'$timeCheck_int;s'",'12s'),
            ("'$alias$dialogSequence'",'quest_intro'),
            ("'$this.name;$name;$spawnCounter'",'guard_spawn_3'),
            ("'greeting_$greeters[0];_$greeters[1];'",'greeting_henry_guard'),
            ("'$label'",'$literal;'),("''",'')):
            with self.subTest(pattern=pattern):self.assertEqual(concatenate(pattern,values),expected)

    def test_compiled_pattern_writes_declared_output(self):
        model=source('''<Root><Behavior><Concatenation OutString="&quot;alias&quot;"
            Pattern="&quot;$prefix;$index;&quot;"/></Behavior></Root>''')
        model['behavior_documents']['libs/ai/quests/q_test.xml']['trees']['onUpdate']['variables'].extend([
            dict(name='prefix',type='_string',form='single',values="'quest_'"),
            dict(name='index',type='_int',form='single',values='4'),
            dict(name='alias',type='_string',form='single',values='')])
        files,_,report=convert_behaviors([model],'Patterns',catalog())
        self.assertEqual(report['whole_trees_emitted'],1)
        b=Behavior('patterns',next(iter(files.values())),graph())
        task=b.start('onUpdate')
        self.assertEqual(b.tasks[task]['locals']['alias'],'quest_4')
        self.assertFalse(b.unsupported)
        self.assertEqual(b.variables,{})

    def test_waiting_pattern_replays_with_current_values(self):
        b=controller('<Sequence>'+wait(1)+
            '<Concatenation OutString="$out.key" Pattern="\'quest_$counter;\'"/></Sequence>',
            '',{'counter':2,'out':{'key':''}})
        b.start('main'); snapshot=json.loads(json.dumps(b.snapshot())); gs=b.graph.snapshot()
        for i in range(2):
            if i:b.graph.restore(gs);b.restore(snapshot)
            b.set_variables({'counter':5})
            b.graph.advance_to(b.next_deadline());b.pump()
            self.assertEqual(b.variables['out']['key'],'quest_5')

    def test_invalid_patterns_do_not_write_output(self):
        for pattern in ("'$missing'","'$record'","'$float'","'$boolean'",
                        "'$a[-1]'","'$a[0+1]'","'$a()'","'$'","'raw;separator'",'42'):
            b=controller('<Concatenation OutString="$out" Pattern="'+pattern+'"/>','',
                {'out':'unchanged','record':{},'float':1.5,'boolean':True,'a':[1,2]})
            with self.subTest(pattern=pattern),self.assertRaises(Unsupported):b.start('main')
            self.assertEqual(b.variables['out'],'unchanged')


if __name__=='__main__':unittest.main()
