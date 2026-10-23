import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as E
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import character_horse_handler_work as stable
from character_schedule_jobs import bind_jobs, population_work_supports, job_resources
import test_character_city_work as fixture
from campaign_entity_links import append_links, guid_value, native_guid
from character_schedule_world import baseline_schedule_names
from character_city_benches import signature
from upgrade_map import xml


PROGRAM = b'''<BehaviorTrees><BehaviorTree name="horseHandler_work"><Root>
<Expression expressions="$behaviors[1] = 'unloadHay'"/>
<GraphSearch Origin="__area.id" depth="1"><HasBehaviorFilter Child="activityTarget"/></GraphSearch>
</Root></BehaviorTree></BehaviorTrees>'''


class HorseHandlerTests(unittest.TestCase):
    def legacy_graph(self, shared=False):
        """An old persisted graph, independent of the current stable binder."""
        identity = lambda value: native_guid(int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], 'little'))
        cart_guid = identity('stable-hay/AB')
        slot_guid = native_guid(255)
        point_guid = identity('job-reservation/' + cart_guid + '/stable_pitchfork/' + slot_guid)
        mission = E.Element('Objects')
        scheduler = E.Element('database'); rows = E.SubElement(scheduler, 'Schedulers')
        cart = E.SubElement(mission, 'Entity', Name='gm_stable_hay_ab', EntityClass='SmartObjectHolder',
            EntityGuid=cart_guid, EntityId='10', Pos='10,20,4', Rotate='0.707106781,0,0,0.707106781')
        E.SubElement(cart, 'Properties', guidSmartObjectType=stable.TEMPLATE, bSaved_by_game='0')
        reservation = E.SubElement(mission, 'Entity', Name='gm_job_reservation_' + point_guid,
            EntityClass='TagPoint', EntityGuid=point_guid, EntityId='11', Pos='10,20,4')
        slot = E.SubElement(mission, 'Entity', Name='gm_job_item_ff', EntityClass='ItemSlot',
            EntityGuid=slot_guid, EntityId='12', Pos='5,7,9')
        E.SubElement(slot, 'Properties', guidItemClassId=stable.FORK, bSaveBorrowedItem='1')
        edges = []
        def connect(a, b, label):
            links = a.find('EntityLinks')
            if links is None: links = E.SubElement(a, 'EntityLinks')
            E.SubElement(links, 'Link', Name=label, TargetId=b.get('EntityId'), TargetGuid=b.get('EntityGuid'))
            edges.append((a.get('EntityGuid'), b.get('EntityGuid'), label))
        connect(cart, reservation, "schedulerReservedItem['use','stable_pitchfork']")
        connect(reservation, slot, '')
        E.SubElement(rows, 'C_SmartHub', EntityGuid=str(guid_value(cart_guid)))
        # Unrelated sleep and quest resources let the test prove their clocks
        # and priorities survive without relying on the sleep implementation.
        for number in (90, 91):
            E.SubElement(mission, 'Entity', Name='unrelated_' + str(number), EntityClass='TagPoint',
                         EntityGuid=native_guid(number), EntityId=str(number), Pos='0,0,0')
            E.SubElement(rows, 'C_SmartHub', EntityGuid=str(number))
        for number in range(1, 3 if shared else 2):
            name = 'worker_' + str(number); actor_guid = native_guid(number)
            actor = E.SubElement(mission, 'Entity', Name=name, EntityClass='NPC', EntityGuid=actor_guid,
                                EntityId=str(number), Pos='0,0,0')
            hub_guid = identity('schedule-arrival/' + actor_guid + '/AB')
            hub = E.SubElement(mission, 'Entity', Name='gm_schedule_' + name + '_123', EntityClass='SchedulerHub',
                EntityGuid=hub_guid, EntityId=str(20 + number), Pos=cart.get('Pos'), Rotate=cart.get('Rotate'))
            E.SubElement(hub, 'Properties', bSaved_by_game='0')
            connect(hub, cart, '_,use'); connect(actor, hub, '_')
            anchor = E.SubElement(mission, 'Entity', Name='gm_person_idle_' + name, EntityClass='TagPoint',
                EntityGuid=native_guid(40 + number), EntityId=str(40 + number), Pos='0,0,0')
            connect(actor, anchor, 'home')
            row = E.SubElement(rows, 'C_SmartHub', EntityGuid=str(number)); links = E.SubElement(row, 'Links')
            for first, last in ((400, 900), (950, 1200)):
                edge = E.SubElement(links, 'S_ActivityLink', TargetGuid=str(guid_value(hub_guid)), PositioningDelegate='0')
                E.SubElement(edge, 'Parameters', TimeOfDayStart=str(first), TimeOfDayEnd=str(last),
                             Priority='0', HaltOnInvalid='true')
            for target, start, end, priority in ((90, '1200', '1440', '2'), (91, '0', '1440', '50')):
                edge = E.SubElement(links, 'S_ActivityLink', TargetGuid=str(target), PositioningDelegate='0')
                E.SubElement(edge, 'Parameters', BehaviorName='unrelated', TimeOfDayStart=start,
                             TimeOfDayEnd=end, Priority=priority)
            row = E.SubElement(rows, 'C_SmartHub', EntityGuid=str(guid_value(hub_guid)), Position=hub.get('Pos'))
            edge = E.SubElement(E.SubElement(row, 'Links'), 'S_ActivityLink',
                                TargetGuid=str(guid_value(cart_guid)), PositioningDelegate='0')
            E.SubElement(edge, 'Parameters', BehaviorName='use', TimeOfDayStart='0', TimeOfDayEnd='1440', Priority='0')
        return {'objects_mission0.xml': xml(mission), 'tables/ai/scheduler.xml': xml(scheduler),
                'waitinglinks.xml': append_links(None, edges, allow_untagged=True)}

    def refresh(self, files):
        with patch.object(stable, 'native_fragments', return_value={stable.FRAGMENTS[0]:
                dict(init_align0='0;3;0;0;0;0;1')}):
            return stable.refresh_existing(files, None)

    def test_old_active_job_needs_world_migration_when_behavior_is_refreshed(self):
        files = self.legacy_graph()
        self.assertEqual(baseline_schedule_names(files, {'worker_1'}), set())
        resources = job_resources(None, None, files, {})
        behavior = E.fromstring(resources['AI/gluemapper/stable_hay.xml'])
        self.assertIsNotNone(behavior.find(".//LinkTagFilter[@tag=\"'stableAnimationStart'\"]"))
        self.assertNotIn(b'stableAnimationStart', files['waitinglinks.xml'])
        self.assertIsNone(E.fromstring(files['objects_mission0.xml']).find("Entity[@Name='gm_stable_hay_ab_start']"))

        repaired, report = self.refresh(files)
        mission = E.fromstring(repaired['objects_mission0.xml'])
        start = mission.find("Entity[@Name='gm_stable_hay_ab_start']")
        self.assertIsNotNone(start)
        for actual, expected in zip(map(float, start.get('Pos').split(',')), (7, 20, 4)):
            self.assertAlmostEqual(actual, expected)
        hub = mission.find("Entity[@Name='gm_schedule_worker_1_123']")
        self.assertEqual(hub.get('Pos'), start.get('Pos'))
        cart = mission.find("Entity[@Name='gm_stable_hay_ab']")
        self.assertEqual(cart.get('Pos'), '10,20,4')
        edge = cart.find("EntityLinks/Link[@Name='stableAnimationStart']")
        self.assertEqual(edge.get('TargetId'), start.get('EntityId'))
        self.assertEqual(edge.get('TargetGuid'), start.get('EntityGuid'))
        waiting = E.fromstring(repaired['waitinglinks.xml'])
        self.assertTrue(any(r.get('SourceId') == cart.get('EntityGuid') and r.get('TargetId') == start.get('EntityGuid')
                            and r.findtext('LinkDefinition') == 'stableAnimationStart' for r in waiting.findall('.//WaitingLink')))
        self.assertTrue(report[0]['migrated'])
        self.assertFalse(report[0]['runtime_verified'])
        self.assertIn('horse assignment and care', report[0]['pending'])

    def test_approach_refresh_preserves_clocks_tools_and_shared_cart_on_repeat(self):
        files = self.legacy_graph(shared=True)
        baseline = copy.deepcopy(files)
        repaired, report = self.refresh(files)
        self.assertEqual(files, baseline)
        before_rows = E.fromstring(files['tables/ai/scheduler.xml']).find('Schedulers')
        after_rows = E.fromstring(repaired['tables/ai/scheduler.xml']).find('Schedulers')
        for number in ('1', '2', '90', '91'):
            self.assertEqual(signature(before_rows.find("C_SmartHub[@EntityGuid='" + number + "']")),
                             signature(after_rows.find("C_SmartHub[@EntityGuid='" + number + "']")))
        mission = E.fromstring(repaired['objects_mission0.xml'])
        self.assertEqual(len(mission.findall("Entity[@Name='gm_stable_hay_ab_start']")), 1)
        self.assertEqual(len([e for e in mission if e.get('EntityClass') == 'SmartObjectHolder']), 1)
        for name in ('gm_job_item_ff', 'unrelated_90', 'unrelated_91'):
            self.assertEqual(signature(E.fromstring(files['objects_mission0.xml']).find("Entity[@Name='" + name + "']")),
                             signature(mission.find("Entity[@Name='" + name + "']")))
        repeated, second_report = self.refresh(repaired)
        self.assertEqual(repeated, repaired)
        self.assertEqual(len(report), 2)
        self.assertTrue(all(not r['migrated'] for r in second_report))

    def test_approach_refresh_rejects_foreign_or_conflicting_graphs_without_mutation(self):
        for corruption in ('cart_identity', 'tool_link', 'unknown_pose', 'hub_activity', 'start_pose', 'start_link'):
            files = self.legacy_graph()
            if corruption.startswith('start_'): files, _ = self.refresh(files)
            mission = E.fromstring(files['objects_mission0.xml'])
            scheduler = E.fromstring(files['tables/ai/scheduler.xml'])
            cart = mission.find("Entity[@Name='gm_stable_hay_ab']")
            hub = mission.find("Entity[@Name='gm_schedule_worker_1_123']")
            if corruption == 'cart_identity': cart.set('Name', 'some_other_mod_cart')
            if corruption == 'tool_link': cart.find('EntityLinks/Link').set('TargetId', 'wrong')
            if corruption == 'unknown_pose':
                hub.set('Pos', '100,200,300')
                scheduler.find("Schedulers/C_SmartHub[@EntityGuid='" + str(guid_value(hub.get('EntityGuid'))) + "']").set('Position', '100,200,300')
            if corruption == 'hub_activity':
                scheduler.find("Schedulers/C_SmartHub[@EntityGuid='" + str(guid_value(hub.get('EntityGuid'))) + "']/Links/S_ActivityLink/Parameters").set('BehaviorName', 'another_job')
            if corruption == 'start_pose': mission.find("Entity[@Name='gm_stable_hay_ab_start']").set('Pos', '100,200,300')
            if corruption == 'start_link': cart.find("EntityLinks/Link[@Name='stableAnimationStart']").set('TargetGuid', native_guid(90))
            files['objects_mission0.xml'] = xml(mission)
            files['tables/ai/scheduler.xml'] = xml(scheduler)
            original = copy.deepcopy(files)
            with self.subTest(corruption=corruption), self.assertRaises(ValueError):
                self.refresh(files)
            self.assertEqual(files, original)

    def test_approach_refresh_without_hay_jobs_leaves_files_and_target_untouched(self):
        files, _ = fixture.CityWorkTests().prepared()
        with patch.object(stable, 'native_fragments', side_effect=AssertionError('not a hay package')):
            result, report = stable.refresh_existing(files, None)
        self.assertEqual(result, files)
        self.assertEqual(report, [])

    def current_entry(self, source_id='AB'):
        alignment = dict(EntityId='123', EntityGuid=source_id, Pos='10,20,4', Rotate='0.707106781,0,0,0.707106781')
        arrival = stable.work_contact(alignment, dict(init_align0='0;3;0;0;0;0;1'))
        return dict(arrival=arrival, activity='work', start_minute=400, end_minute=1200,
            behavior=dict(tree_file=stable.SOURCE[0], tree_name=stable.SOURCE[1]),
            horse_handler_work=dict(cart=dict(EntityGuid=source_id), alignment=alignment,
                fork='<Entity EntityClass="ItemSlot" EntityId="99" EntityGuid="FF" Pos="5,7,9"><Properties guidItemClassId="' + stable.FORK + '"/></Entity>'))

    def test_refresh_accepts_current_binder_output_with_upper_or_lower_source_guids(self):
        from character_schedule_world import bind_arrivals
        for source_id in ('abcdef', 'ABCDEF', 'AbCdEf'):
            files, _, activity = fixture.schedule_fixture.ScheduleWorldTests().fixture()
            mission = E.fromstring(files['objects_mission0.xml'])
            mission.find("Entity[@Name='person']").set('EntityClass', 'NPC')
            files['objects_mission0.xml'] = xml(mission)
            schedules = {'person': [self.current_entry(source_id)]}
            with patch('character_schedule_world.native_wait_activity', return_value=(activity, None)):
                files, _ = bind_arrivals(files, schedules, None)
            current, _ = self.bind(files, schedules)
            with self.subTest(source_id=source_id):
                if source_id == 'AbCdEf':
                    # Historical name lowercasing loses mixed-case hash input;
                    # refuse that unknown identity instead of guessing a match.
                    with self.assertRaisesRegex(ValueError, 'foreign stable cart'):
                        self.refresh(current)
                else:
                    refreshed, report = self.refresh(current)
                    for key in current:
                        self.assertEqual(signature(E.fromstring(refreshed[key])), signature(E.fromstring(current[key])))
                    self.assertFalse(report[0]['migrated'])

    def test_migrate_legacy_cart_before_binding_a_new_worker_who_shares_it(self):
        from character_schedule_world import bind_arrivals
        files = self.legacy_graph()
        mission = E.fromstring(files['objects_mission0.xml'])
        E.SubElement(mission, 'Entity', Name='new_worker', EntityClass='NPC', EntityGuid=native_guid(3), EntityId='3', Pos='0,0,0')
        E.SubElement(mission, 'Entity', Name='gm_person_idle_new_worker', EntityClass='TagPoint',
                     EntityGuid=native_guid(43), EntityId='43', Pos='0,0,0')
        scheduler = E.fromstring(files['tables/ai/scheduler.xml'])
        row = E.SubElement(scheduler.find('Schedulers'), 'C_SmartHub', EntityGuid='3')
        activity = E.SubElement(E.SubElement(row, 'Links'), 'S_ActivityLink', TargetGuid='91', PositioningDelegate='43')
        E.SubElement(activity, 'Parameters', BehaviorName='schedulerWait')
        files['objects_mission0.xml'] = xml(mission)
        files['tables/ai/scheduler.xml'] = xml(scheduler)
        files, _ = self.refresh(files)
        schedules = {'new_worker': [self.current_entry()]}
        native = E.fromstring('<S_ActivityLink PositioningDelegate="0"><Parameters Priority="0" BehaviorOverride="false"/></S_ActivityLink>')
        with patch('character_schedule_world.native_wait_activity', return_value=(native, None)):
            files, _ = bind_arrivals(files, schedules, None)
        files, _ = self.bind(files, schedules)
        checked, report = self.refresh(files)
        self.assertEqual({r['person'] for r in report}, {'worker_1', 'new_worker'})
        self.assertTrue(all(not r['migrated'] for r in report))
        mission = E.fromstring(checked['objects_mission0.xml'])
        self.assertEqual(len(mission.findall("Entity[@Name='gm_stable_hay_ab_start']")), 1)

    def test_population_upgrade_publishes_behavior_only_with_its_migrated_graph(self):
        from build_profile_population_upgrade import upgrade_work
        import seating_animations
        for valid in (True, False):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); source = root/'source'; target = root/'target'; package = root/'package'
                source_level = source/'Data/Levels/rataje/level.pak'
                target_level = target/'Data/Levels/trosecko/level.pak'
                package_level = package/'Data/Levels/world/level.pak'
                for path in (source_level, target_level, package_level): path.parent.mkdir(parents=True)
                for path in (source_level, target_level):
                    with zipfile.ZipFile(path, 'w') as archive: archive.writestr('objects_mission0.xml', '<Objects/>')
                files = self.legacy_graph()
                if not valid:
                    files['objects_mission0.xml'] = files['objects_mission0.xml'].replace(b'gm_stable_hay_ab', b'foreign_stable_cart')
                files.update({'ubernav.tmm': b'unchanged navigation', seating_animations.HELPERS: b'<Helpers/>',
                              seating_animations.ANIMATIONS: b'<Animations/>'})
                with zipfile.ZipFile(package_level, 'w') as archive:
                    for path, blob in files.items(): archive.writestr(path, blob)
                travel = package/'Data/gluemappertravel.pak'
                with zipfile.ZipFile(travel, 'w') as archive: archive.writestr('old-resource', b'unchanged')
                (package/'mod.manifest').write_text('<mod><info><modid>test</modid></info></mod>')
                old_level, old_travel = package_level.read_bytes(), travel.read_bytes()
                def publish(source, target, installed, schedules):
                    self.assertIn(b'stableAnimationStart', installed['waitinglinks.xml'])
                    self.assertIsNotNone(E.fromstring(installed['objects_mission0.xml']).find("Entity[@Name='gm_stable_hay_ab_start']"))
                    return job_resources(source, target, installed, schedules)
                with patch('container_items.item_patches', return_value=[]), \
                        patch('world_item_definitions.native_items', return_value={}), \
                        patch('world_item_definitions.native_item_registry', return_value={}), \
                        patch('character_sleep_work.upgrade', side_effect=lambda files, *args: (files, [])), \
                        patch('seating_animations.install', side_effect=lambda files, *args: (files, {})), \
                        patch.object(stable, 'native_fragments', return_value={stable.FRAGMENTS[0]: dict(init_align0='0;3;0;0;0;0;1')}), \
                        patch('character_schedule_jobs.job_resources', side_effect=publish) as publishing:
                    if valid:
                        upgrade_work(source, target, package, 'rataje', 'world', {}, [], [], None, None)
                        publishing.assert_called_once()
                        with zipfile.ZipFile(travel) as archive:
                            self.assertIn('AI/gluemapper/stable_hay.xml', archive.namelist())
                        with zipfile.ZipFile(package_level) as archive:
                            self.assertIn(b'stableAnimationStart', archive.read('waitinglinks.xml'))
                    else:
                        with self.assertRaisesRegex(ValueError, 'foreign stable cart'):
                            upgrade_work(source, target, package, 'rataje', 'world', {}, [], [], None, None)
                        publishing.assert_not_called()
                        self.assertEqual(package_level.read_bytes(), old_level)
                        self.assertEqual(travel.read_bytes(), old_travel)

    def test_start_pose_composes_native_alignment_with_rotated_cart(self):
        from math import sqrt
        animation=dict(init_align0='1;3;0.2;0;0;0;1')
        result=stable.work_contact(dict(Pos='10,20,4',Rotate=f'{sqrt(.5)},0,0,{sqrt(.5)}'),animation)
        for actual,expected in zip(map(float,result['Pos'].split(',')),(7,21,4.2)):
            self.assertAlmostEqual(actual,expected)
        self.assertEqual(result['Rotate'],f'{sqrt(.5):.9g},0,0,{sqrt(.5):.9g}')
        for bad in ('0;0;0', 'nan;0;0;0;0;0;1', '0;0;0;0;0;0;0'):
            with self.assertRaises(ValueError):
                stable.work_contact(dict(Pos='0,0,0'),dict(init_align0=bad))

    def prepared(self):
        files, schedules = fixture.CityWorkTests().prepared()
        for entry in schedules['person']:
            entry.pop('city_work')
            entry['behavior'] = dict(tree_file=stable.SOURCE[0], tree_name=stable.SOURCE[1])
            entry['horse_handler_work'] = dict(cart=dict(EntityGuid='AB'), alignment=dict(Pos='4,5,6',Rotate='1,0,0,0'), fork=
                '<Entity EntityClass="ItemSlot" EntityId="99" EntityGuid="FF" Pos="1,2,3"><Properties guidItemClassId="' + stable.FORK + '"/></Entity>')
        return files, schedules

    def bind(self, files, schedules, registered=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.pak'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('objects_mission0.xml', '<Objects/>')
            with zipfile.ZipFile(path) as z, patch.object(stable, 'native_fragments'):
                return bind_jobs(files, schedules, z, None,
                                 {stable.FORK: stable.FORK} if registered is None else registered)

    def test_selection_uses_source_behavior(self):
        self.assertTrue(population_work_supports(dict(behavior=dict(tree_file='final\\sa_stable.xml', tree_name='horseHandler_work'))))
        self.assertFalse(stable.supports(dict(behavior=dict(tree_file=stable.SOURCE[0], tree_name='other'))))

    def test_changed_source_search_is_rejected(self):
        stable.source_contract(PROGRAM)
        for blob in (PROGRAM.replace(b'unloadHay', b'other'), PROGRAM.replace(b'depth="1"', b'depth="2"'), PROGRAM.replace(b'__area.id', b'__actor.id')):
            with self.assertRaises(ValueError):
                stable.source_contract(blob)

    def test_same_anchor_multiple_intervals_bind_once(self):
        files, schedules = self.prepared()
        entry = schedules['person'][0]
        schedules['person'].append(copy.deepcopy(entry))
        result, report = self.bind(files, schedules)
        jobs = [r for r in report if r['job'] == 'stable_hay_unloading']
        self.assertEqual(len(jobs), 1)
        self.assertEqual(len(jobs[0]['intervals']), 2)
        self.assertFalse(jobs[0]['runtime_verified'])
        self.assertIn('water refill', jobs[0]['pending'])
        mission = E.fromstring(result['objects_mission0.xml'])
        carts = [e for e in mission if e.get('Name', '').startswith('gm_stable_hay_')]
        self.assertEqual(len(carts), 2)
        carts=[e for e in carts if e.get('EntityClass')=='SmartObjectHolder']
        self.assertEqual(carts[0].get('Pos'),'4,5,6')
        reservations = [l for l in carts[0].findall('EntityLinks/Link') if l.get('Name').startswith('schedulerReservedItem')]
        self.assertEqual(len(reservations), 1)
        self.assertEqual(reservations[0].get('Name'), "schedulerReservedItem['use','stable_pitchfork']")
        resources = job_resources(None, None, result, {})
        self.assertIn('AI/gluemapper/stable_hay.xml', resources)
        labels = E.fromstring(resources['Libs/Tables/ai/DecisionLabelDatabase__gm_stable_hay.xml'])
        self.assertEqual([e.get('Name') for e in labels.iter('DecisionLabel')], ['stable_pitchfork'])

    def test_unregistered_tool_fails(self):
        with self.assertRaisesRegex(ValueError, 'Unregistered job item'):
            self.bind(*self.prepared(), registered={})

    def test_conflicting_intervals_fail(self):
        files, schedules = self.prepared()
        other = copy.deepcopy(schedules['person'][0])
        other['horse_handler_work']['cart']['EntityGuid'] = 'AC'
        schedules['person'].append(other)
        with self.assertRaisesRegex(ValueError, 'Different stables'):
            self.bind(files, schedules)

    def test_native_animation_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp); (target/'Data').mkdir()
            def write(bad=None):
                root=E.Element('AnimDB'); actions=E.Element('database')
                for i,name in enumerate(stable.FRAGMENTS):
                    tags='left+up' if i==1 else ''
                    fragment=E.SubElement(E.SubElement(root,name),'Fragment',FragTags=tags,GUID=str(i))
                    E.SubElement(E.SubElement(fragment,'AnimLayer'),'Animation',name='clip')
                    row=E.SubElement(actions,'actor_anim_action',mn_fragment_id=name,mn_tags=tags,
                        mn_fragment_guid=str(i),player='false',mounted='false',actor_class_hash='native-controller',
                        isfullbody='true',isOneShot='false' if i==1 else 'true',
                        init_align0='0;3;0;0;0;0;1',init_align1='0;3;0;0;0;0;1')
                    if i==0 and bad=='missing':actions.remove(row)
                    if i==0 and bad=='guid':row.set('mn_fragment_guid','unknown')
                    if i==0 and bad=='alignment':row.set('init_align1','1;3;0;0;0;0;1')
                    if i==1 and bad=='loop':row.set('isOneShot','true')
                with zipfile.ZipFile(target/'Data/Animations.pak','w') as z:
                    z.writestr('Animations/Mannequin/ADB/kcd_male_database.adb',E.tostring(root))
                with zipfile.ZipFile(target/'Data/Tables.pak','w') as z:
                    z.writestr('Libs/Tables/action/actor_anim_action.xml',E.tostring(actions))
            write()
            self.assertEqual(stable.native_fragments(target)[stable.FRAGMENTS[0]]['init_align0'],'0;3;0;0;0;0;1')
            for bad in ('missing','guid','alignment','loop'):
                write(bad)
                with self.subTest(bad=bad),self.assertRaises(ValueError):stable.native_fragments(target)

    def test_tool_is_held_during_approach_and_animation(self):
        root = E.fromstring(stable.resources()['AI/gluemapper/stable_hay.xml'])
        hand = root.find('.//HandContentElement')
        self.assertEqual(hand.get('decisionLabel'), 'stable_pitchfork')
        sequence = hand.find('Exec/Sequence')
        self.assertEqual(sequence[0].tag, 'GraphSearch')
        self.assertEqual(sequence[0].find('LinkTagFilter').get('Child'), '$approach')
        self.assertEqual(sequence[1].get('destinationSpecification'), '$approach')
        self.assertEqual(sequence[1].get('directionSpecification'), '$approach')
        actions=list(sequence.iter('AnimationAction'))
        self.assertEqual(actions[1].get('tags'), "'left,up'")
        self.assertTrue(all(a.get('alignObject')=='$__object.id' for a in actions))
        self.assertEqual([a.get('fragment') for a in sequence.iter('AnimationAction')],
                         ["'" + name + "'" for name in stable.FRAGMENTS])


if __name__ == '__main__':
    unittest.main()
