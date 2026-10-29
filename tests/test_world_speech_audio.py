import sys
from pathlib import Path
import tempfile
import unittest
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from world_speech_audio import relocation, relocations, native_voice_roots, select, upgrade
from dialogue_dubbing import DEPOT


def fixture():
    return {
        'Quests/demo.xml': b'<Database><Skald><Project Name="demo"><Definitions><Definition File="entry/test.xml"/></Definitions><Nodes><test Name="test"/></Nodes></Project></Skald></Database>',
        'Quests/entry/test.xml': b'<Database><Skald><Level Name="test"><Definitions><Definition File="world/open_world.xml"/></Definitions><Nodes><open_world Name="open_world"/></Nodes></Level></Skald></Database>',
        'Quests/entry/world/open_world.xml': b'<Database><Skald><Gameplay Name="open_world" VoiceNamespace="true"><Definitions><Definition File="native.xml"/></Definitions><Nodes><speech Name="renamed_but_native"/></Nodes></Gameplay></Skald></Database>',
        'Quests/entry/world/native.xml': b'<Database><Skald><Dialog Name="speech"><Dialogue><Response><Text StringName="greet_Test"/></Response></Dialogue></Dialog></Skald></Database>'}


def shared_fixture():
    graphs = fixture()
    wrapper = 'Quests/entry/world/open_world.xml'
    graphs[wrapper] = graphs[wrapper].replace(b'</Definitions>', b'<Definition File="shared.xml"/></Definitions>').replace(
        b'</Nodes>', b'<helpers Name="helpers"/></Nodes>')
    graphs['Quests/entry/world/shared.xml'] = b'<Database><Skald><Module Name="helpers" VoiceNamespace="true"><Definitions><Definition File="answer.xml"/></Definitions><Nodes><answers Name="answers"/></Nodes></Module></Skald></Database>'
    graphs['Quests/entry/world/answer.xml'] = b'<Database><Skald><Library Name="answers" VoiceNamespace="true"><Dialogue><Response Role="PLAYER"><Text StringName="reply_Accept"/></Response><Response Role="CIVILIAN"><Text StringName="reply_Thanks"/></Response></Dialogue></Library></Skald></Database>'
    return graphs


def write_pak(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as z:
        for name, blob in entries.items(): z.writestr(name, blob)


def native_scripts(target):
    # Both modules are siblings in retail; the converted package nests helpers
    # under the world. Names intentionally do not resemble crime-specific data.
    write_pak(target/'Data/Scripts.pak', {
        'Quests/Final/Barbora.xml': b'<Database><Skald><Project Name="Native"><Definitions><Definition File="Barbora/world.xml"/><Definition File="Barbora/shared.xml"/></Definitions><Nodes><open_world Name="open_world"/><helpers Name="helpers"/></Nodes></Project></Skald></Database>',
        'Quests/Final/Barbora/world.xml': b'<Database><Skald><Gameplay Name="open_world" VoiceNamespace="true"/></Skald></Database>',
        'Quests/Final/Barbora/shared.xml': b'<Database><Skald><Library Name="helpers" VoiceNamespace="true"/></Skald></Database>'})


class SpeechTests(unittest.TestCase):
    def test_scope_comes_from_level_not_archive_path(self):
        native, dest, keys = relocation(fixture(),'Quests/entry/world/open_world.xml')
        self.assertEqual((native,dest),('dialog/open_world/','dialog/test/open_world/'))
        selected = select(['dialog/open_world/hello/abc_greet_Test.ogg',
                           'dialog/open_world/hello/xyz_greet_Test.ogg',
                           'dialog/open_world/hello/abc_unused.ogg',
                           'dialog/another/abc_greet_Test.ogg'],native,dest,keys)
        self.assertEqual(len(selected),2)
        self.assertIn('dialog/test/open_world/hello/abc_greet_test.ogg',selected)

    def test_atomic_upgrade_preserves_recording_case_index_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); package=root/'mod'; target=root/'game'
            native_scripts(target)
            def pak(path,entries):
                path.parent.mkdir(parents=True,exist_ok=True)
                with zipfile.ZipFile(path,'w') as z:
                    for n,b in entries.items():z.writestr(n,b)
            stem='abc_greet_Test'; key=zlib.crc32(stem.encode())
            late_stem='xyz_sentence_Later'
            late_key=zlib.crc32(late_stem.encode())
            index=f'<Root><DialogueDubbingData><DialogueDubbing FileHash="{key}" Duration="1"/><DialogueDubbing FileHash="{late_key}" Duration="2"/></DialogueDubbingData></Root>'.encode()
            pak(package/'Data/gluemappertravel.pak',fixture())
            pak(package/'Localization/English.pak',{DEPOT:index,'dialog/custom/keep.ogg':b'OggSkeep'})
            source='dialog/open_world/hello/'+stem+'.ogg'
            late_source='dialog/open_world/new_controller/'+late_stem+'.ogg'
            pak(target/'Localization/English.pak',{source:b'OggSoriginal',late_source:b'OggSlater'})
            progress=[]
            def notify(label, current, total, detail=''):
                progress.append((label,current,total,detail))
            changed,report=upgrade(package,target,notify)
            self.assertEqual([(p[1],p[2]) for p in progress],[(0,1),(1,1)])
            self.assertEqual(report['changed'],1)
            self.assertIn('Localization/English.pak',changed)
            with zipfile.ZipFile(package/'Localization/English.pak') as z:
                self.assertEqual(z.read('dialog/test/open_world/hello/'+stem+'.ogg'),b'OggSoriginal')
                self.assertEqual(z.read(DEPOT),index)
                self.assertEqual(z.read('dialog/custom/keep.ogg'),b'OggSkeep')
            before=(package/'Localization/English.pak').read_bytes()
            changed,report=upgrade(package,target)
            self.assertFalse(changed)
            self.assertEqual(before,(package/'Localization/English.pak').read_bytes())
            self.assertEqual(report['recordings'],1)
            # A later setup stage adds a controller after the first audio pass.
            # Re-running against the final closure must add its recordings too.
            graphs=fixture()
            graphs['Quests/entry/world/native.xml']=graphs['Quests/entry/world/native.xml'].replace(
                b'</Dialogue>',b'<Response><Text StringName="sentence_Later"/></Response></Dialogue>')
            pak(package/'Data/gluemappertravel.pak',graphs)
            changed,report=upgrade(package,target)
            self.assertEqual(report['changed'],1)
            self.assertEqual(report['recordings'],2)
            with zipfile.ZipFile(package/'Localization/English.pak') as z:
                self.assertEqual(z.read('dialog/test/open_world/new_controller/'+late_stem+'.ogg'),b'OggSlater')
                self.assertEqual(z.read('dialog/test/open_world/hello/'+stem+'.ogg'),b'OggSoriginal')

    def test_missing_dependency_fails(self):
        graphs=fixture();del graphs['Quests/entry/world/native.xml']
        with self.assertRaisesRegex(ValueError,'Missing speech dependency'):
            relocation(graphs,'Quests/entry/world/open_world.xml')

    def test_shared_library_uses_its_own_native_root_and_imported_ancestors(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            native_scripts(target)
            with zipfile.ZipFile(target/'Data/Scripts.pak') as z:
                roots = native_voice_roots(z)
        self.assertEqual(roots, {'open_world':'dialog/open_world/', 'helpers':'dialog/helpers/'})
        scopes = relocations(shared_fixture(), 'Quests/entry/world/open_world.xml', roots)
        self.assertEqual(scopes, [
            ('dialog/open_world/', 'dialog/test/open_world/', {'greet_test'}),
            ('dialog/helpers/', 'dialog/test/open_world/helpers/', {'reply_accept','reply_thanks'})])

    def test_shared_responses_keep_every_voice_and_unrelated_audio_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); package=root/'mod'; target=root/'game'
            native_scripts(target)
            write_pak(package/'Data/gluemappertravel.pak', shared_fixture())
            audio = {'dialog/open_world/hello/one_greet_Test.ogg':b'OggSworld',
                     'dialog/helpers/answers/hero_reply_Accept.ogg':b'OggSplayer',
                     'dialog/helpers/answers/man_reply_Thanks.ogg':b'OggScivilian',
                     'dialog/helpers/answers/woman_reply_Thanks.ogg':b'OggScivilian2',
                     'dialog/helpers/unused/one_unrelated.ogg':b'OggSunrelated'}
            rows = ''.join(f'<DialogueDubbing FileHash="{zlib.crc32(n.rsplit("/",1)[-1][:-4].encode())}" Duration="1"/>' for n in audio)
            index = ('<Root><DialogueDubbingData>'+rows+'</DialogueDubbingData></Root>').encode()
            write_pak(target/'Localization/English.pak',audio)
            write_pak(package/'Localization/English.pak',{DEPOT:index,'dialog/custom/keep.ogg':b'OggSkeep'})
            # Simulate the old installer: world audio exists, library replies do not.
            with zipfile.ZipFile(package/'Localization/English.pak','a') as z:
                z.writestr('dialog/test/open_world/hello/one_greet_Test.ogg',b'OggSworld')
            original = package/'Localization/English.pak'
            linked = root/'previous.pak'
            linked.hardlink_to(original)
            previous = linked.read_bytes()
            changed,report=upgrade(package,target)
            self.assertEqual(report['changed'],3)
            self.assertEqual(report['recordings'],4)
            self.assertEqual(report['version'],2)
            self.assertEqual(linked.read_bytes(),previous)
            with zipfile.ZipFile(original) as z:
                for name,blob in audio.items():
                    if '/unused/' in name: continue
                    destination = 'dialog/test/open_world/'+name.removeprefix('dialog/') if name.startswith('dialog/helpers/') else 'dialog/test/'+name.removeprefix('dialog/')
                    self.assertEqual(z.read(destination),blob)
                self.assertEqual(z.read(DEPOT),index)
                self.assertEqual(z.read('dialog/custom/keep.ogg'),b'OggSkeep')
                self.assertFalse(any('unrelated' in n for n in z.namelist()))
            self.assertFalse(upgrade(package,target)[0])

    def test_unregistered_world_root_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'audio root is not registered'):
            relocations(shared_fixture(),'Quests/entry/world/open_world.xml',{'helpers':'dialog/helpers/'})

    def test_retained_world_scope_still_repairs_relocated_shared_library(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); package=root/'mod'; target=root/'game'
            native_scripts(target)
            graphs=shared_fixture()
            graphs['Quests/entry/test.xml']=graphs['Quests/entry/test.xml'].replace(
                b'<Level Name="test">',b'<Level Name="test" VoiceNamespace="false">')
            write_pak(package/'Data/gluemappertravel.pak',graphs)
            stem='hero_reply_Accept'
            index=f'<Root><DialogueDubbingData><DialogueDubbing FileHash="{zlib.crc32(stem.encode())}" Duration="1"/></DialogueDubbingData></Root>'.encode()
            write_pak(package/'Localization/English.pak',{DEPOT:index})
            write_pak(target/'Localization/English.pak',{'dialog/helpers/answers/'+stem+'.ogg':b'OggSreply'})
            changed,report=upgrade(package,target)
            self.assertEqual(report['recordings'],1)
            self.assertEqual(report['scopes'][0]['destination_scope'],'dialog/open_world/helpers/')
            with zipfile.ZipFile(package/'Localization/English.pak') as z:
                self.assertEqual(z.read('dialog/open_world/helpers/answers/'+stem+'.ogg'),b'OggSreply')
