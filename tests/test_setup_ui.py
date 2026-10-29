"""Exercise the local setup screens without starting a build, Steam, or KCD2.

Optional UI checks: use the development Playwright installation and Windows Edge.
The native API is simulated; its filesystem and launch contracts have separate tests.
"""
import importlib.util
import os
from pathlib import Path
import unittest

HAS_BROWSER = os.name == 'nt' and importlib.util.find_spec('playwright') is not None
FIXTURE = """
window.testCalls=[];window.toolsPresent=true;window.launchFail=false;
window.pywebview={api:{
 state:async()=>({config:{mods:['map'],action:'build_install',baking:'none',cutscenes:'none',
  kcd1:'D:/SteamLibrary/steamapps/common/KingdomComeDeliverance',
  kcd2:'D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2',
  mod_tools:'D:/SteamLibrary/steamapps/common/KCD2Mod',workspace:'D:/BuildKCDMod',package:''},
  installed:[],warnings:[],version:'0.3.4',busy:false,converter:null}),
 poll:async()=>({busy:false,events:[]}),installed:async()=>[],
 check_mod_tools:async()=>window.toolsPresent?
  {ok:true,path:'D:/SteamLibrary/steamapps/common/KCD2Mod'}:
  {ok:false,error:'Install the official KCD2 Modding Tools before continuing.'},
 mod_tools_link:async()=>{window.testCalls.push('tools');return true;},
 find_converter:async()=>{window.testCalls.push('find_converter');return null;},
 locate_converter:async()=>{window.testCalls.push('locate_converter');return null;},
 check:async c=>({ok:true,config:c,messages:['Locations and required files checked.']}),
 launch_mod:async target=>{window.testCalls.push(['launch',target]);
  await new Promise(r=>setTimeout(r,250));return window.launchFail?
  {ok:false,error:'Retail shaders changed'}:{ok:true,pid:42,log:'D:/KCD2/Mods/runtime/kcd.log'};}
}};
"""


@unittest.skipUnless(HAS_BROWSER,'Optional Playwright / Windows Edge UI check')
class SetupUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.runtime=sync_playwright().start()
        try:cls.browser=cls.runtime.chromium.launch(channel='msedge',headless=True)
        except Exception:
            cls.runtime.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop()

    def setUp(self):
        self.page=self.browser.new_page(viewport={'width':1280,'height':900})
        self.errors=[]
        self.page.on('pageerror',lambda e:self.errors.append(str(e)))
        self.page.add_init_script(FIXTURE)
        self.page.goto((Path(__file__).resolve().parents[1]/'setup-ui/index.html').as_uri())
        self.page.wait_for_function("document.querySelector('#mod-tools-badge').textContent==='Detected'")

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors,[])

    def settings(self):
        self.page.locator('button.nav[data-page=settings]').click()

    def test_population_progress_replaces_stale_heading_and_shows_real_count(self):
        page=self.page
        page.evaluate("""() => {
            initializeChecklist(['KCD1 New Game menu']);
            event({kind:'stage',label:'KCD1 New Game menu'});
            event({kind:'progress',label:'Convert source character appearances',current:0,total:8,time:100,detail:'actor: resolving source'});
            event({kind:'progress',label:'Convert source character appearances',current:2,total:8,time:160,detail:'actor: checking clothing coverage'});
        }""")
        self.assertEqual(page.locator('#stage').text_content(),'Convert source character appearances')
        self.assertIn('2 / 8',page.locator('#progress-label').text_content())
        self.assertIn('25%',page.locator('#progress-label').text_content())
        self.assertIn('3 min',page.locator('#eta').text_content())
        self.assertIn('checking clothing coverage',page.locator('#stage-detail').text_content())
        page.evaluate("event({kind:'progress',label:'Convert source jobs and schedules',current:0,total:0,detail:'Binding work'})")
        self.assertIn('working',page.locator('.progress-track').get_attribute('class'))
        self.assertNotIn('%',page.locator('#progress-label').text_content())
        self.assertNotIn('3 min',page.locator('#eta').text_content())

    def test_checklisted_progress_keeps_throughput_sample_for_eta(self):
        self.page.evaluate("""() => {
            initializeChecklist(['Convert appearances']);
            event({kind:'progress',label:'Convert appearances',current:0,total:10,time:100});
            event({kind:'progress',label:'Convert appearances',current:2,total:10,time:160});
        }""")
        self.assertIn('4 min',self.page.locator('#eta').text_content())

    def test_current_character_counts_do_not_replace_batch_progress(self):
        self.page.evaluate("""() => {
            event({kind:'progress',label:'Convert source character appearances',current:3,total:8,detail:'Example character: baking materials'});
            event({kind:'work_progress',label:'Native textures',current:7,total:20,detail:'3 reused from identical source pixels'});
        }""")
        self.assertIn('3 / 8',self.page.locator('#progress-label').text_content())
        self.assertIn('7 / 20 (35%)',self.page.locator('#work-label').text_content())
        self.assertIn('3 reused',self.page.locator('#work-label').text_content())
        self.assertFalse(self.page.locator('#work-progress').get_attribute('hidden'))
        self.page.evaluate("event({kind:'progress',label:'Convert source character appearances',current:4,total:8,detail:'Next character'})")
        self.assertIsNotNone(self.page.locator('#work-progress').get_attribute('hidden'))

    def test_locations_layout_keeps_one_action_and_fixed_navigation(self):
        page=self.page;self.settings()
        for width,height in ((1280,900),(980,720)):
            page.set_viewport_size({'width':width,'height':height})
            self.assertTrue(page.locator('#review').is_hidden())
            self.assertEqual(page.locator('button.primary:visible').count(),1)
            box=page.locator('#wizard-next').bounding_box()
            self.assertLessEqual(box['y']+box['height'],height)
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth===window.innerWidth'))
            self.assertEqual(page.locator('.nav.active').count(),1)
        page.locator('#workspace').scroll_into_view_if_needed()
        before=page.locator('main').evaluate('(e)=>e.scrollTop')
        self.assertGreater(before,0)
        page.locator('#workspace').click()
        self.assertEqual(page.locator('main').evaluate('(e)=>e.scrollTop'),before)
        self.assertEqual(page.locator('.sidebar').bounding_box()['y'],0)

    def test_missing_tools_gate_then_wizard_reaches_review_without_auto_file_picker(self):
        page=self.page;self.settings()
        page.evaluate('window.toolsPresent=false')
        page.locator('#check-mod-tools').click()
        page.wait_for_function("document.querySelector('#mod-tools-badge').textContent==='Not found'")
        page.locator('#wizard-next').click()
        self.assertTrue(page.locator('[data-pane="0"]').is_visible())
        page.locator('#install-mod-tools').click()
        self.assertIn('tools',page.evaluate('window.testCalls'))
        page.evaluate('window.toolsPresent=true')
        page.locator('#wizard-next').click()
        page.wait_for_selector('[data-pane="1"]',state='visible')
        self.assertFalse(page.locator('#rebuild-cache').is_checked())
        page.locator('#rebuild-cache').check()
        page.locator('input[name=baking][value=all]').check()
        page.locator('#wizard-next').click()
        page.wait_for_selector('[data-pane="2"]',state='visible')
        self.assertNotIn('locate_converter',page.evaluate('window.testCalls'))
        page.locator('#wizard-next').click()
        page.wait_for_selector('[data-pane="3"]',state='visible')
        page.locator('#wizard-next').click()
        page.wait_for_selector('#confirm[open]')
        self.assertIn('All (Forest)',page.locator('#review-content').inner_text())
        self.assertIn('Rebuild lighting and shaders',page.locator('#review-content').inner_text())
        self.assertTrue(page.evaluate('collect().rebuild_cache'))
        page.evaluate("api.start=async c=>{window.testCalls.push(c);return {ok:true,job:'test',steps:[]}}")
        page.locator('#start').click()
        page.wait_for_function("window.testCalls.some(c=>c?.rebuild_cache===true)")
        self.assertFalse(page.locator('#rebuild-cache').is_checked())

    def test_installed_mod_launch_handles_success_and_failure(self):
        page=self.page
        self.assertTrue(page.locator('#launch-mod').is_hidden())
        page.evaluate("installedMods=['map'];renderLibrary()")
        page.locator('#launch-mod').click()
        self.assertTrue(page.locator('#launch-mod').is_disabled())
        page.wait_for_function("document.querySelector('#notice').textContent==='KCD2 started with the mod shaders.'")
        page.evaluate('window.launchFail=true')
        page.locator('#launch-mod').click()
        page.wait_for_function("document.querySelector('#notice').textContent==='Retail shaders changed'")
        self.assertTrue(page.locator('#launch-mod').is_enabled())
        self.settings()
        self.assertTrue(page.locator('#notice').is_hidden())

    def test_imported_cache_requires_choice_and_rebuild_clears_selection(self):
        page=self.page
        page.evaluate("api.cache_candidates=async()=>[{id:'a'.repeat(64),source:'D:/BuildKCDMod/old',preset:'all',shaders:true}]")
        page.evaluate("window.choice=undefined;void chooseImportedCache({mods:['map'],workspace:'builds',baking:'all',action:'build_only'}).then(x=>window.choice=x)")
        page.wait_for_selector('#cache-choice[open]')
        self.assertIn('D:/BuildKCDMod/old',page.locator('#cache-source').inner_text())
        page.locator('#cache-use').click()
        page.wait_for_function("window.choice==='a'.repeat(64)")
        page.evaluate("window.choice=undefined;void chooseImportedCache({mods:['map'],workspace:'builds',baking:'all',action:'build_only'}).then(x=>window.choice=x)")
        page.wait_for_selector('#cache-choice[open]')
        page.locator('#cache-fresh').click()
        page.wait_for_function("window.choice===''")

    def test_multiplayer_settings_reuse_existing_map_and_reach_native_api(self):
        page=self.page
        page.locator('.mod-title[data-details="mp"]').click()
        page.locator('#mp-manage').click()
        self.assertEqual(page.evaluate('config.mods'),['mp'])
        self.assertTrue(page.locator('#mod-tools-panel').is_hidden())
        page.evaluate("api.mp_profile_info=async()=>({ok:true,config:{}});api.mp_sources=async()=>[{path:'D:/BuildKCDMod/finished/package/map',label:'Finished build'}];api.multiplayer=async(op,c)=>{window.testCalls.push([op,c]);return {ok:true,message:'Operation requested'}}")
        page.locator('#mp-find-sources').click()
        page.locator('#mp-sources').select_option(label='Finished build')
        self.assertEqual(page.locator('#mp_source').input_value(),'D:/BuildKCDMod/finished/package/map')
        page.locator('#mp_client').fill('C:/Downloads/client')
        page.locator('#mp_server').fill('C:/Downloads/server')
        page.locator('#mp_server_name').fill('KCD1:MP Friends')
        page.locator('#mp_port').fill('8123')
        page.locator('#mp_max_players').fill('8')
        page.locator('#mp_host').fill('friends.test:8123')
        page.locator('#mp_pvp').check()
        page.locator('#mp_profile').fill('D:/BuildKCDMod/mp/profile.json')
        for op in ('save','activate','server','client','offline','stop_server','restore'):
            page.locator('[data-mp="'+op+'"]').click()
            page.wait_for_function('(op)=>window.testCalls.some(x=>x[0]===op)',arg=op)
        calls=page.evaluate('window.testCalls')
        self.assertEqual(calls[-1][1]['mp_port'],8123)
        self.assertTrue(calls[-1][1]['mp_pvp'])
        page.locator('#wizard-next').click()
        self.assertTrue(page.locator('[data-pane="3"]').is_visible())
        self.assertIn('KCD1:MP Friends',page.locator('#wizard-review').inner_text())
        page.locator('#wizard-next').click()
        page.wait_for_selector('#confirm[open]')
        self.assertIn('friends.test:8123',page.locator('#review-content').inner_text())

    def test_multiplayer_layout_and_selection_are_independent(self):
        page=self.page
        page.locator('.mod-title[data-details="mp"]').click();page.locator('#mp-manage').click()
        for width,height in ((1280,900),(980,720)):
            page.set_viewport_size({'width':width,'height':height})
            page.locator('#mp-panel').scroll_into_view_if_needed()
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth===window.innerWidth'))
        page.set_viewport_size({'width':1280,'height':900})
        page.locator('#mp_client').scroll_into_view_if_needed()
        output=Path(__file__).resolve().parents[1]/'outputs/multiplayer-setup'
        output.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(output/'settings.png'))
        page.locator('#mp_profile').scroll_into_view_if_needed()
        page.screenshot(path=str(output/'launch-controls.png'))
        page.evaluate("toggleMod('fishing')")
        self.assertNotIn('mp',page.evaluate('config.mods'))


if __name__=='__main__':unittest.main()
