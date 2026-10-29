'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
let api=null,busy=false,started=0,checking=false,config={mods:['map'],action:'build_install',baking:'none'},logLines=[];
const names={map:'KCD1 Map',mp:'KCD1:MP',fishing:'KCD2 Fishing'};
const versions={map:'0.2.0-alpha.4',mp:'0.1.0',fishing:'0.3.0'},bakeNames={none:'No Baking',buildings:'Buildings',all:'All (Forest)'};
let launching=false;
let installedMods=[],detailKey=null,focusedMod='map';
const detail={mp:{category:'MULTIPLAYER',copy:'Explore your converted KCD1 world using KCD:MP. Reuse a completed map with its lighting and shaders.',list:['Separate scenery export for KCD:MP 0.44.0','Choose client/server folders and configure a private server','Host, join or restore your single-player mod selection'],note:'Experimental. Requires retail Steam KCD2 1.5.6 and a completed KCD1 Map. Campaign quests and NPC schedules are excluded. Doors and furnishings are inert. Every player needs the matching converted map; multiplayer runtime validation is pending.'},map:{category:'WORLD EXPANSION',copy:'The original KCD1 landscape, rebuilt locally for Kingdom Come: Deliverance II.',list:['Rattay, Skalitz and the surrounding countryside','Coach travel from Trosky and imported Rattay services','Built from your installed copies of both games'],note:'Experimental world conversion. The original campaign and KCD1 saves are not converted. Allow at least 200 GiB for the build, plus staging space.'},fishing:{category:'NEW ACTIVITY',copy:'A standalone fishing activity for the rivers and ponds of KCD2. Also supports KCD1 Map when present at build time.',list:['Carry a fishing rod and choose Fish at the water','Native casting, a cork-and-quill float and timed bites','Rods added to general merchants’ stock; existing shops may need to restock'],note:'Experimental standalone build. Requires KCD2 1.5.6. Does not require KCD1 or KCD1 Map. English interface text is included; native-region gameplay validation is still pending.'}};
window.showNotice=(text,type='info')=>{const el=$('#notice');el.textContent=text;el.className=type;el.hidden=false;};
function page(name){
 if(document.body.dataset.view!==name&&!busy)$('#notice').hidden=true;
 document.body.dataset.view=name;
 $$('.page').forEach(e=>e.classList.toggle('active',e.id===name));
 $$('.nav').forEach(e=>e.classList.toggle('active',e.dataset.page===name));
 $('#breadcrumb').textContent={library:'Library',settings:'Install settings',activity:'Activity'}[name];
 $('#review').hidden=name!=='library';
 $('#wizard-back').hidden=name!=='settings';$('#wizard-next').hidden=name!=='settings';
 $('.install-bar').hidden=name==='activity';
 $('main').scrollTop=0;
}
function collect(){collectMP();for(const k of ['kcd1','kcd2','mod_tools','workspace','package'])config[k]=$('#'+k).value.trim();config.action=$('input[name=action]:checked').value;config.baking=$('input[name=baking]:checked').value;config.cutscenes=$('#install-opening').checked?'opening':'none';config.rebuild_cache=$('#rebuild-cache').checked;return JSON.parse(JSON.stringify(config));}
function selection(){
 for(const key of Object.keys(names)){
  const on=config.mods.includes(key),b=$(`[data-select="${key}"]`);if(!b)continue;
  b.setAttribute('aria-pressed',String(on));b.closest('article').classList.toggle('selected',on);
  b.querySelector('.checkbox').textContent=on?'✓':'';b.querySelector('.selection-word').textContent=on?'Selected':'Select';
 }
 $('#selected-count').textContent=`${config.mods.length} MOD${config.mods.length===1?'':'S'} SELECTED`;
 $('#selected-names').textContent=config.mods.map(m=>names[m]).join(' + ')||'Choose a mod from your library';
 const locked=busy||launching||checking;
 $('#review').disabled=locked||!config.mods.length;
 $('#wizard-next').disabled=locked||!config.mods.length;$('#wizard-back').disabled=locked;
 $('#mp-panel').hidden=!config.mods.includes('mp');
 $('#mod-tools-panel').hidden=!config.mods.includes('map');
 const action=$('input[name=action]:checked').value;
 $('#space-note').textContent=action==='install'?'Setup checks available space against the selected package.':config.mods.includes('map')?(action==='build_only'?'Allow at least 200 GiB for the conversion.':'Allow 200 GiB for conversion, plus 64 GiB for installation.'): config.mods.includes('mp')?'Reuse a completed map. Allow space for a separate multiplayer snapshot; no lighting rebake.':'Allow 1 GiB for the fishing package and installation.';
}
function setBusy(value){busy=value;$$('[data-select],[data-browse],#detect,#check-mod-tools,#install-mod-tools,input').forEach(e=>e.disabled=value);$('#cancel').disabled=!value;$('#resume').disabled=value;$('#activity-dot').style.display=value?'block':'none';selection();syncLaunch();$('#inspector-select').disabled=busy||launching;}
function log(message){logLines.push(message);logLines=logLines.slice(-350);$('#log').textContent=logLines.join('\n');$('#log').scrollTop=$('#log').scrollHeight;}
function event(e){if(e.kind==='work_progress'){renderWorkProgress(e);return;}if(['stage','progress','done','error','cancelled'].includes(e.kind))$('#work-progress').hidden=true;if(e.mp_profile){config.mp_profile=e.mp_profile;$('#mp_profile').value=e.mp_profile;}trackEvent(e);if(e.kind==='stage'){$('#stage').textContent=e.label;$('#stage-detail').textContent='Working on this stage. Large conversions can take several minutes.';$('.progress-track').classList.add('working');$('#progress-label').textContent=activeCheck?`Step ${$$('#job-checklist li').indexOf(activeCheck)+1} of ${$$('#job-checklist li').length} · selected installation plan`:'Preparing selected operations';log(e.label);}else if(e.kind==='progress'){$('#stage').textContent=e.label;if(e.total>0){$('.progress-track').classList.remove('working');$('#progress-fill').style.width=Math.min(100,100*e.current/e.total)+'%';$('#progress-label').textContent=`${e.label} · ${e.current} / ${e.total} · ${Math.round(100*e.current/e.total)}%`;}else{$('.progress-track').classList.add('working');$('#progress-fill').style.width='0%';$('#progress-label').textContent=e.label+' · In progress';}$('#stage-detail').textContent=e.detail||e.label;}else if(e.kind==='log'){log(e.message||e.label||'');}else if(['done','error','cancelled'].includes(e.kind)){$('.progress-track').classList.remove('working');$('#job-state').textContent={done:'COMPLETE',error:'NEEDS ATTENTION',cancelled:'CANCELLED'}[e.kind];$('#stage').textContent=e.message;$('#stage-detail').textContent=e.package?`Package folder: ${e.package}`:'Your log and completed output are retained.';$('#progress-fill').style.width=e.kind==='done'?'100%':'0%';$('#progress-label').textContent=e.kind==='done'?'All selected operations completed':'See activity log';log(e.message);if(e.traceback)log(e.traceback);showNotice(e.message,e.kind==='error'?'error':'info');}}
async function guarded(fn){try{return await fn();}catch(e){showNotice(e.message||String(e),'error');}}
function reviewRow(label,value){const row=document.createElement('div');row.className='review-row';const title=document.createElement('span');title.textContent=label;row.append(title,document.createTextNode(value));$('#review-content').append(row);}
async function review(){if(busy||launching||checking||!api)return;checking=true;selection();$('#review').textContent='Checking locations…';try{const c=collect();c.imported_cache=await chooseImportedCache(c);config.imported_cache=c.imported_cache;const result=await api.check(c);if(!result.ok){wizard(0);showNotice(result.error,'error');return;}$('#notice').hidden=true;$('#review-content').replaceChildren();reviewRow('SELECTED MODS',c.mods.map(m=>names[m]+' v'+versions[m]).join(' + '));if(c.mods.includes('map'))reviewRow('LIGHTING BAKE',c.action==='install'?'As recorded in the existing package':bakeNames[c.baking]);if(c.mods.includes('map'))reviewRow('OPENING MOVIE',c.action==='install'?'As recorded in the package':c.cutscenes==='opening'?'Convert and install KCD1 opening (replaces shared New Game intro)':'Not selected');if(c.imported_cache)reviewRow('OLDER BUILD OUTPUTS','Use selected imported lighting/shaders where available; rebuild incompatible shaders');reviewRow('KCD2 INSTALLATION',c.kcd2);reviewRow('BUILD FOLDER',c.workspace);if(c.mods.includes('map')&&c.action!=='install')reviewRow('CACHED OUTPUTS',c.rebuild_cache?'Rebuild lighting and shaders':'Reuse matching lighting and shaders');if(c.action==='install')reviewRow('PACKAGE COLLECTION',c.package);if(c.mods.includes('mp')){reviewRow('MULTIPLAYER SOURCE',c.mp_source||(c.mods.includes('map')?'Map built in this job':'Installed KCD1 Map'));reviewRow('CLIENT / SERVER',c.mp_client+' / '+c.mp_server);reviewRow('SERVER SETTINGS',c.mp_server_name+' · port '+c.mp_port+' · '+c.mp_max_players+' players · PvP '+(c.mp_pvp?'on':'off'));reviewRow('JOIN ADDRESS',c.mp_host);}for(const message of result.messages)reviewRow('LOCATION CHECK',message);$('#start').textContent={build_install:'Build & install',build_only:'Build packages',install:'Install packages'}[c.action];$('#confirm-title').textContent={build_install:'Ready to build & install.',build_only:'Ready to build.',install:'Ready to install.'}[c.action];$('#confirm').showModal();}finally{checking=false;$('#review').textContent='Configure installation →';selection();}}
$$('[data-page]').forEach(b=>b.addEventListener('click',()=>page(b.dataset.page)));
document.addEventListener('click',e=>{const select=e.target.closest('[data-select]'),details=e.target.closest('[data-details]');if(select){toggleMod(select.dataset.select);return;}if(details)inspectMod(details.dataset.details);});
$$('.dialog-close').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
$$('[data-browse]').forEach(b=>b.addEventListener('click',()=>guarded(async()=>{if(!api)return;const p=await api.browse(b.dataset.browse);if(p){$('#'+b.dataset.browse).value=p;if(b.dataset.browse==='mp_profile')await refreshMPProfile();if(b.dataset.browse==='kcd2')await refreshInstalled();if(['kcd2','mod_tools'].includes(b.dataset.browse))await refreshTools();}})));
$$('input[name=action]').forEach(e=>e.addEventListener('change',()=>{$('#package-row').hidden=e.value!=='install';selection();}));
$('#detect').addEventListener('click',()=>guarded(async()=>{if(!api)return;$('#detect').disabled=true;try{const r=await api.detect();for(const k of ['kcd1','kcd2','mod_tools'])if(r.found[k]?.length)$('#'+k).value=r.found[k][0];await refreshInstalled();await refreshTools();showNotice(r.warnings.length?r.warnings.join('\n'):'Steam scan complete. Review the detected folders.');}finally{$('#detect').disabled=false;}}));
$('#review').addEventListener('click',()=>{if($('#settings').classList.contains('active'))$('#wizard-next').click();else wizard(0);});
$('#start').addEventListener('click',()=>guarded(async()=>{if(busy||launching)return;$('#start').disabled=true;try{const r=await api.start(collect());if(!r.ok){$('#confirm').close();showNotice(r.error,'error');return;}$('#confirm').close();$('#rebuild-cache').checked=false;config.rebuild_cache=false;config.imported_cache='';$('#notice').hidden=true;setBusy(true);page('activity');started=Date.now();logLines=[];initializeChecklist(r.steps||[]);progressSample=null;$('#eta').textContent='Estimating after measurable work starts…';log('Build folder: '+r.job);$('#job-state').textContent='IN PROGRESS';$('#open-log').disabled=false;$('#open-folder').disabled=false;}finally{$('#start').disabled=false;}}));
$('#cancel').addEventListener('click',()=>guarded(async()=>{await api.cancel();$('#cancel').disabled=true;$('#stage-detail').textContent='Cancellation requested. The current conversion stage will finish safely first.';}));
$('#open-log').addEventListener('click',()=>guarded(()=>api.open_result('log')));$('#open-folder').addEventListener('click',()=>guarded(()=>api.open_result('folder')));
let polling=false;
setInterval(async()=>{if(!api||polling)return;polling=true;try{const r=await api.poll();r.events.forEach(event);if(busy&&!r.busy){setBusy(false);await refreshInstalled();}if(busy&&started){const s=Math.floor((Date.now()-started)/1000);$('#elapsed').textContent=String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0');}}catch(e){showNotice('Unable to read setup progress: '+String(e),'error');}finally{polling=false;}},500);
async function connect(){api=window.pywebview?.api;if(!api)return;const r=await api.state();config=r.config;fillMP();config.imported_cache='';$('#rebuild-cache').checked=false;$('#install-opening').checked=config.cutscenes==='opening';showConverter(r.converter);for(const k of ['kcd1','kcd2','mod_tools','workspace','package'])$('#'+k).value=config[k]||'';const radio=$(`input[value="${config.action}"]`);if(radio)radio.checked=true;$('#package-row').hidden=config.action!=='install';installedMods=r.installed||[];renderLibrary();$('#version').textContent=r.version;if(r.catalog)for(const item of r.catalog)versions[item.id]=item.version;const bake=$(`input[name=baking][value="${config.baking||'none'}"]`);if(bake)bake.checked=true;setBusy(r.busy);$('#install-hint').textContent='';if(r.warnings.length)showNotice(r.warnings.join('\n'));await refreshTools();}
function syncLaunch(){const button=$('#launch-mod');button.hidden=focusedMod!=='map'||!installedMods.includes('map');button.disabled=!api||busy||launching;button.textContent=launching?'Preparing launch…':'Launch mod';}
async function refreshInstalled(){if(!api)return;installedMods=await api.installed($('#kcd2').value.trim());renderLibrary();}
async function refreshTools(){
 if(!api)return null;
 const badge=$('#mod-tools-badge');badge.textContent='Checking…';badge.className='status-badge';
 const r=await api.check_mod_tools($('#kcd2').value.trim(),$('#mod_tools').value.trim());
 badge.textContent=r.ok?'Detected':'Not found';badge.classList.add(r.ok?'ready':'missing');
 $('#mod-tools-status').textContent=r.ok?'Shader compiler and required files are available.':'Install the official tools through Steam, or choose their folder.';
 $('#mod-tools-status').title=r.ok?r.path:r.error;
 $('#install-mod-tools').hidden=r.ok;
 if(r.ok)$('#mod_tools').value=r.path;
 return r;
}
$('#kcd2').addEventListener('change',()=>guarded(async()=>{await refreshInstalled();await refreshTools();}));
$('#mod_tools').addEventListener('change',()=>guarded(refreshTools));
$('#check-mod-tools').addEventListener('click',()=>guarded(refreshTools));
$('#install-mod-tools').addEventListener('click',()=>guarded(async()=>{if(!api)return;await api.mod_tools_link();showNotice('Complete the Modding Tools installation in Steam, then choose Recheck tools.');}));
$('#launch-mod').addEventListener('click',()=>guarded(async()=>{if(!api||busy||launching)return;launching=true;syncLaunch();selection();showNotice('Preparing KCD2 with the installed mod shaders…');try{const r=await api.launch_mod($('#kcd2').value.trim());if(!r.ok){showNotice(r.error,'error');return;}showNotice('KCD2 started with the mod shaders.');log('Game process: '+r.pid+' · Log: '+r.log);}finally{launching=false;syncLaunch();selection();}}));
window.addEventListener('pywebviewready',()=>guarded(connect));renderLibrary();selection();
if(window.pywebview?.api)guarded(connect);else setTimeout(()=>{if(!api){$('#install-hint').textContent='Preview · open the desktop setup to install';showNotice('Design preview. Run the desktop setup to detect games and install mods.');}},1500);

let wizardStep=0,progressSample=null,activeCheck=null,converterPrompted=false;
function steps(){const route=config.mods.includes('map')&&$('input[name=action]:checked').value!=='install'?[0,1,2]:[0];if(config.mods.includes('mp'))route.push(4);return [...route,3];}
function wizard(step){
 wizardStep=step;if(step===2)guarded(ensureConverter);page('settings');
 $$('[data-pane]').forEach(e=>e.hidden=Number(e.dataset.pane)!==step);
 $$('[data-step]').forEach(e=>{const n=Number(e.dataset.step);e.classList.toggle('current',n===step);e.classList.toggle('complete',steps().indexOf(n)<steps().indexOf(step)&&steps().includes(n));e.classList.toggle('skipped',!steps().includes(n));e.querySelector('b').textContent=steps().indexOf(n)+1;if(n===step)e.setAttribute('aria-current','step');else e.removeAttribute('aria-current');});
 $('#wizard-back').textContent=step===0?'Back to library':'← Back';
 const route=steps(),next=route[route.indexOf(step)+1];
 $('#wizard-next').textContent=step===3?'Review installation →':{1:'Continue to lighting →',2:'Continue to movie →',3:'Continue to review →',4:'Continue to multiplayer →'}[next]||'Continue →';
 if(step===3){
  const c=collect(),box=$('#wizard-review');box.replaceChildren();
  for(const m of c.mods){const h=document.createElement('h2');h.textContent=names[m]+' · v'+versions[m];const p=document.createElement('p');p.className='guide';p.textContent=detail[m].copy;box.append(h,p);}
  const rows=[['Operation',{build_install:'Build & install',build_only:'Build packages only',install:'Install existing packages'}[c.action]],['KCD2 installation',c.kcd2],['Build workspace',c.workspace]];
  if(c.mods.includes('map')){rows.push(['Modding Tools',c.mod_tools||'Automatic detection']);if(c.action!=='install')rows.push(['Cached outputs',c.rebuild_cache?'Rebuild lighting and shaders':'Reuse matching lighting and shaders'],['Lighting',bakeNames[c.baking]],['Opening movie',c.cutscenes==='opening'?'Include KCD1 opening':'Keep the original intro']);}
  if(c.mods.includes('mp'))rows.push(['Multiplayer source',c.mp_source||(c.mods.includes('map')?'Map built in this job; shared cache applies':'Installed KCD1 Map')],['KCD:MP client',c.mp_client],['KCD:MP server',c.mp_server],['Join address',c.mp_host],['Server',c.mp_server_name+' · port '+c.mp_port+' · '+c.mp_max_players+' players'],['Activation',c.action==='build_only'?'Export only':'Enable only KCD1:MP; save single-player mod order']);
  for(const [label,value] of rows){const row=document.createElement('div');row.className='review-row';const title=document.createElement('span');title.textContent=label;row.append(title,document.createTextNode(value||'Not selected'));box.append(row);}
 }
 selection();
}
$('#wizard-next').addEventListener('click',()=>guarded(async()=>{
 if(busy||launching||checking)return;
 if(wizardStep===3){if(!api)return showNotice('Open the desktop setup to check your games and install. This is a design preview.');return review();}
 if(wizardStep===0&&api&&config.mods.includes('map')){checking=true;selection();try{const r=await refreshTools();if(!r.ok){showNotice(r.error,'error');$('#mod-tools-panel').scrollIntoView({block:'center'});return;}}finally{checking=false;selection();}}
 $('#notice').hidden=true;
 const route=steps();wizard(route[route.indexOf(wizardStep)+1]);
}));
$('#wizard-back').addEventListener('click',()=>{if(wizardStep===0)page('library');else{const route=steps();wizard(route[route.indexOf(wizardStep)-1]??0);}});
$$('[data-page=settings]').forEach(b=>b.addEventListener('click',()=>wizard(0)));
$('#converter-link').addEventListener('click',()=>guarded(async()=>{if(api)await api.converter_link();else showNotice('Official download: https://www.radgametools.com/bnkdown.htm');}));
$('#locate-converter').addEventListener('click',()=>guarded(async()=>{if(!api)return showNotice('Locate a converter in the desktop setup.');const r=await api.locate_converter();if(r){showConverter(r);$('#notice').hidden=true;}}));
function initializeChecklist(labels){
 activeCheck=null;$('#job-checklist').replaceChildren();
 for(const label of labels){const li=document.createElement('li');li.dataset.label=label;li.className='pending';li.textContent='○ '+label+' — Pending';$('#job-checklist').append(li);}
}
function activateStep(label){
 const row=$$('#job-checklist li').find(li=>li.dataset.label===label);
 if(!row||row===activeCheck)return;
 if(activeCheck){activeCheck.className='done';activeCheck.textContent='✓ '+activeCheck.dataset.label;}
 activeCheck=row;row.className='active';row.textContent='● '+label;
 $('#stage').textContent=label;
}
function trackEvent(e){
 if(e.kind==='stage'){
  activateStep(e.label);
  progressSample=null;$('#eta').textContent='Stage ETA: measuring… · Total ETA unavailable until all stages have timing data.';
 }else if(e.kind==='progress'){
  activateStep(e.label);
  if(!(e.total>0)){
   progressSample=null;$('#eta').textContent='Current task ETA unavailable until measurable work starts.';
   return;
  }
  const now=e.time?e.time*1000:Date.now();
  if(!progressSample||progressSample.label!==e.label||progressSample.total!==e.total||e.current<progressSample.current){
   progressSample={label:e.label,total:e.total,current:e.current,time:now};
   $('#eta').textContent='Current task ETA: measuring completed work…';
  }
  const count=e.current-progressSample.current,elapsed=(now-progressSample.time)/1000;
  if(count>0&&elapsed>=2){const left=(e.total-e.current)*elapsed/count;$('#eta').textContent='Current task ETA: '+(left<60?'under a minute':Math.ceil(left/60)+' min')+' · Estimate from measured throughput; later stages are additional.';}
 }else if(['done','error','cancelled'].includes(e.kind)){
  if(e.kind==='done')for(const row of $$('#job-checklist li')){row.className='done';row.textContent='✓ '+row.dataset.label;}
  if(activeCheck){activeCheck.className=e.kind==='done'?'done':'failed';activeCheck.textContent=(e.kind==='done'?'✓ ':'! ')+activeCheck.dataset.label;activeCheck=null;}
  $('#eta').textContent=e.kind==='done'?'All selected work completed.':e.kind==='cancelled'?'Stopped safely. Completed work is retained.':'Stopped. See the log for the exact issue.';
 }
}

function toggleMod(key){if(busy||launching)return;if(!config.mods.includes(key)){if(key==='mp')config.mods=config.mods.filter(m=>m!=='fishing');if(key==='fishing')config.mods=config.mods.filter(m=>m!=='mp');}config.mods=config.mods.includes(key)?config.mods.filter(m=>m!==key):[...config.mods,key];config.mods.sort((a,b)=>a==='map'?-1:1);selection();filterLibrary();inspectMod(key);if(detailKey===key)$('#detail-select').textContent=config.mods.includes(key)?'Remove selection':'Select mod';}
function showDetails(key){detailKey=key;const d=detail[key];$('#detail-title').textContent=names[key]+' · v'+versions[key];$('#detail-category').textContent=d.category;$('#detail-copy').textContent=d.copy;$('#detail-note').textContent=d.note;$('#detail-list').replaceChildren(...d.list.map(t=>{const li=document.createElement('li');li.textContent=t;return li;}));$('#detail-select').textContent=config.mods.includes(key)?'Remove selection':'Select mod';$('#detail-select').disabled=busy;$('#details').showModal();}
function renderLibrary(){
 const grid=$('#mod-grid'),side=$('#sidebar-mods');grid.replaceChildren();side.replaceChildren();
 for(const key of Object.keys(names)){
  const article=document.createElement('article');article.className='mod-card';article.dataset.mod=key;
  const cover=document.createElement('button');cover.className='card-art';cover.dataset.details=key;cover.setAttribute('aria-label','Details for '+names[key]);
  const img=document.createElement('img');img.src='assets/'+(key==='fishing'?'fishing':'map')+'-gameplay.png';img.alt=names[key]+' gameplay';const wordmark=document.createElement('span');wordmark.className='cover-wordmark';wordmark.append(document.createTextNode(key==='fishing'?'KCD2':'KCD1'));const word=document.createElement('strong');word.textContent=key==='mp'?'MP':key==='map'?'MAP':'FISHING';wordmark.append(word);cover.append(img,wordmark);
  const body=document.createElement('div');body.className='card-body';const title=document.createElement('button');title.className='mod-title';title.dataset.details=key;title.textContent=names[key];
  const meta=document.createElement('div');meta.className='card-meta';meta.textContent='v'+versions[key]+' · '+(key==='mp'?'Multiplayer':key==='map'?'World':'Activity');
  const requirement=document.createElement('p');requirement.className='card-requirement';requirement.textContent=key==='mp'?'Completed map + KCD:MP 0.44.0':key==='map'?'KCD1 + KCD2 + Modding Tools':'Standalone · KCD2';
  const bottom=document.createElement('div');bottom.className='card-footer';const status=document.createElement('span');status.className='install-status';status.textContent=installedMods.includes(key)?'Installed':'Not installed';
  const select=document.createElement('button');select.className='select-mod';select.dataset.select=key;select.setAttribute('aria-label','Select '+names[key]);select.innerHTML='<span class="checkbox"></span><span class="selection-word">Select</span>';bottom.append(status,select);body.append(title,meta,requirement,bottom);article.append(cover,body);grid.append(article);
  const link=document.createElement('button');link.className='sidebar-mod';link.dataset.details=key;const icon=img.cloneNode();icon.alt='';link.append(icon,document.createTextNode(names[key]));side.append(link);
 }
 $('#library-count').textContent=Object.keys(names).length;selection();filterLibrary();inspectMod(focusedMod);
}
function filterLibrary(){const query=$('#mod-search').value.trim().toLowerCase(),filter=$('#mod-filter').value;let shown=0;const cards=$$('.mod-card');cards.sort((a,b)=>{const x=a.dataset.mod,y=b.dataset.mod;return ($('#mod-sort').value==='type'?detail[x].category.localeCompare(detail[y].category):0)||names[x].localeCompare(names[y]);});for(const card of cards){const key=card.dataset.mod;card.hidden=!(names[key]+' '+detail[key].copy).toLowerCase().includes(query)||(filter==='selected'&&!config.mods.includes(key))||(filter==='installed'&&!installedMods.includes(key));if(!card.hidden)shown++;$('#mod-grid').append(card);}$('#catalog-count').textContent=shown+' of '+cards.length+' mods';$('#empty-library').hidden=shown>0;}
$('#mod-search').addEventListener('input',filterLibrary);$('#mod-filter').addEventListener('change',filterLibrary);$('#mod-sort').addEventListener('change',filterLibrary);$('#detail-select').addEventListener('click',()=>toggleMod(detailKey));

function inspectMod(key){focusedMod=key;$('#mp-manage').hidden=key!=='mp';const d=detail[key];$('#inspector-image').src='assets/'+(key==='mp'?'map':key)+'-gameplay.png';$('#inspector-image').alt=names[key]+' gameplay';$('.mod-inspector').dataset.mod=key;$('#inspector-category').textContent=key==='mp'?'Multiplayer':key==='map'?'World conversion':'Gameplay mod';$('#inspector-title').textContent=names[key];$('#inspector-version').textContent='Version '+versions[key];$('#inspector-description').textContent=d.copy;$('#inspector-requires').textContent=key==='mp'?'Completed KCD1 Map + KCD:MP 0.44.0':key==='map'?'KCD1 + KCD2 1.5.6 + Modding Tools':'KCD2 1.5.6';$('#inspector-status').textContent=installedMods.includes(key)?'Installed':'Not installed';$('#inspector-select').textContent=config.mods.includes(key)?'Remove selection':'Select mod';$('#inspector-select').disabled=busy||launching;syncLaunch();$$('.sidebar-mod').forEach(e=>e.classList.toggle('focused',e.dataset.details===key));$$('.mod-card').forEach(e=>e.classList.toggle('focused',e.dataset.mod===key));}
$('#inspector-select').addEventListener('click',()=>toggleMod(focusedMod));$('#inspector-details').addEventListener('click',()=>showDetails(focusedMod));
function showConverter(info){if(!info)return;config.converter=info.path;$('#converter-result').textContent=info.message+'\n'+info.path;$('#converter-instructions').hidden=true;$('#converter-link').hidden=true;$('#locate-converter').textContent='Change converter';}

async function ensureConverter(){
 if(!api||converterPrompted)return;converterPrompted=true;
 const found=await api.find_converter();if(found){showConverter(found);return;}
 $('#converter-result').textContent='Optional · locate RAD Video Tools to include the opening movie, or continue without it.';
}

$('#resume').addEventListener('click',()=>guarded(async()=>{
 if(!api||busy)return;
 $('#resume').disabled=true;
 try{
  const r=await api.resume();if(r.cancelled)return;if(!r.ok)return showNotice(r.error,'error');
  page('activity');logLines=[];initializeChecklist(r.steps||[]);log('Resuming '+r.source);log('Attempt logs: '+r.job);
  $('#notice').hidden=true;setBusy(true);started=Date.now();progressSample=null;
  $('#job-state').textContent='IN PROGRESS';$('#stage').textContent='Verify saved work';
  $('#stage-detail').textContent='Checking saved files before reusing completed packages.';
  $('#progress-fill').style.width='0%';$('#open-log').disabled=false;$('#open-folder').disabled=false;
 }finally{$('#resume').disabled=busy;}
}));

async function chooseImportedCache(c){
  if(c.rebuild_cache||c.action==='install'||!c.mods.includes('map')||!api.cache_candidates)return '';
  const items=await api.cache_candidates(c.workspace,c.baking);
  if(!items.length)return '';
  const select=$('#cache-source');select.replaceChildren();
  for(const item of items){const option=document.createElement('option');option.value=item.id;option.textContent=item.source;select.append(option);}
  const detail=()=>{const item=items.find(x=>x.id===select.value);$('#cache-source-details').textContent=[item.preset===c.baking&&c.baking!=='none'?bakeNames[item.preset]+' lighting':'',item.shaders?'Native shaders':''].filter(Boolean).join(' + ');};
  select.onchange=detail;detail();
  const dialog=$('#cache-choice');
  return new Promise(resolve=>{dialog.addEventListener('close',()=>resolve(dialog.returnValue==='use'?select.value:''),{once:true});dialog.showModal();});
}
$('#cache-use').addEventListener('click',()=>$('#cache-choice').close('use'));
$('#cache-fresh').addEventListener('click',()=>$('#cache-choice').close('fresh'));
$('#import-cache').addEventListener('click',()=>guarded(async()=>{
  if(busy||launching||checking)return;checking=true;$('#import-cache').disabled=true;
  $('#import-cache-status').textContent='Choose an older build. Verifying and copying completed outputs may take a few minutes.';
  try{const r=await api.import_old_cache($('#workspace').value.trim());
    $('#import-cache-status').textContent=r.ok?'Imported from '+r.cache.source+'. Review the build to choose whether to use these outputs.':r.cancelled?'Import cancelled.':r.error;
  }finally{checking=false;$('#import-cache').disabled=false;}
}));

function collectMP(){
 for(const key of ['mp_source','mp_client','mp_server','mp_profile','mp_host','mp_player','mp_server_name','mp_password','mp_time'])config[key]=$('#'+key).value.trim();
 for(const key of ['mp_port','mp_max_players','mp_time_ratio'])config[key]=Number($('#'+key).value);
 config.mp_pvp=$('#mp_pvp').checked;
}
function fillMP(){
 const defaults={mp_host:'127.0.0.1:7777',mp_player:'Player',mp_server_name:'KCD1:MP',mp_port:7777,mp_max_players:4,mp_time:'12:00',mp_time_ratio:0};
 for(const key of ['mp_source','mp_client','mp_server','mp_profile','mp_host','mp_player','mp_server_name','mp_password','mp_time','mp_port','mp_max_players','mp_time_ratio'])$('#'+key).value=config[key]??defaults[key]??'';
 $('#mp_pvp').checked=!!config.mp_pvp;
}
$('#mp-manage').addEventListener('click',()=>{if(!config.mods.includes('mp')){config.mods=['mp'];selection();}wizard(4);$('#mp-panel').scrollIntoView({block:'start'});});
$('#mp-find-sources').addEventListener('click',()=>guarded(async()=>{
 if(!api||busy)return;const items=await api.mp_sources($('#workspace').value.trim(),$('#kcd2').value.trim());
 const list=$('#mp-sources');list.replaceChildren(new Option('Choose a completed map…',''));
 for(const item of items)list.append(new Option(item.label,item.path));
 $('#mp-status').textContent=items.length?'Choose a completed map below. Its lighting and shaders will be reused.':'No finished builds found. Browse for a package or select KCD1 Map too to build one.';
}));
$('#mp-sources').addEventListener('change',()=>{if($('#mp-sources').value)$('#mp_source').value=$('#mp-sources').value;});
$$('[data-mp]').forEach(button=>button.addEventListener('click',()=>guarded(async()=>{
 if(!api||busy||launching||checking)return;launching=true;selection();$$('[data-mp]').forEach(b=>b.disabled=true);
 $('#mp-status').textContent='Preparing '+button.textContent+'…';
 try{const r=await api.multiplayer(button.dataset.mp,collect());$('#mp-status').textContent=r.ok?r.message:r.error;if(!r.ok)showNotice(r.error,'error');await refreshInstalled();}
 finally{launching=false;selection();$$('[data-mp]').forEach(b=>b.disabled=false);}
})));

async function refreshMPProfile(){
 if(!api||!$('#mp_profile').value.trim())return;
 const r=await api.mp_profile_info($('#mp_profile').value.trim(),$('#kcd2').value.trim());
 if(!r.ok){showNotice(r.error,'error');return;}
 collectMP();Object.assign(config,r.config);fillMP();$('#mp-status').textContent='Loaded the saved multiplayer profile settings.';
}
$('#mp_profile').addEventListener('change',()=>guarded(refreshMPProfile));

function renderWorkProgress(e){
 $('#work-progress').hidden=false;
 const known=e.total>0;
 $('#work-label').textContent=known?`${e.label}: ${e.current} / ${e.total} (${Math.round(100*e.current/e.total)}%)`:`${e.label}: in progress`;
 if(e.detail)$('#work-label').textContent+=' · '+e.detail;
 $('.work-track').classList.toggle('working',!known);
 $('#work-fill').style.width=known?Math.min(100,100*e.current/e.total)+'%':'0%';
}
