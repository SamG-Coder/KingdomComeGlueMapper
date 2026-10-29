# Crime and cinematic conversion

The selected policy is **KCD2 punishments**. The first destination site is the
existing stocks/pillory area in the square, as requested by the user. The
replacement is integrated through the shared importer and normal Setup
finalization, including saved-build upgrades. Runtime playback remains under
test. This uses native crime AI and its controller.

## Findings from the installed games and built mod

The original `kcd1_travel` world had the crime root, a district and its origin,
native authority social classes, and the native combat surrender holder. Its
open-world graph did **not** register `nextnextgenpunishment`, which contains
the native arrest dialogue, civilian self-help resolution, physical punishment
and repeat-arrest logic. Combat mercy and arrest resolution are different
contracts. The district also lacked confiscated-item storage, and the world
lacked native punishment-area and presentation bindings. The current shared
crime-services stage installs these bindings and validates their owned resources.

KCD2's punishment controller needs local world assets. Copying its Trosky links
unchanged would leave teleports, guards and reputation aimed at the original
region. A native pillory sentence plays a rendered movie. Whipping and branding
are in-world scenes, backed by profiles, scene layers, animation curves,
attached cameras, cleanup areas and native assets.

The KCD1 `rataje` archive contains **228 named sequences**, including quest/helper
sequences. The inspected built `kcd1_travel` level contains none of them and has
no `whdata_1` profile manager. This is separate from the optional opening movie
conversion. Sequence counts must not be described as counts of missing story
movies. An initial audit also found unresolved entity references in 56 source
scene dependency closures; these include legacy editor references and actors
outside the serialized level entity set. They need explicit binding or asset
adapters, not guessed GUID truncation.

## Implemented foundation

`world_gameplay_basis.package_dependencies` now handles nested native libraries,
parent-library helpers and relative namespaces. It emits only requested branches
and preserves native instance declarations. Qualified project libraries resolve
even when a module has a local library with the same first namespace component.
The native punishment controller's **71 graph dependencies** resolve against the
installed game's concept definitions. This validates dependency scopes, not
native gameplay execution.

`cinematic_scene.SceneIndex` indexes the world once, discovers layer-owned and
referenced objects, records profiles and reports missing or conflicting bindings.
`convert` copies a closed scene into private identities and remaps entity links,
sequence targets, cleanup areas and parents. Translation preserves animation
times, tangents, rotations and parent-relative transforms. Both supplied games
use sequence version 3 and parameter version 8. KCD1 model, material, clothing
and animation conversion is an explicit adapter boundary; raw KCD1 assets are
never silently passed through as converted KCD2 assets.

`cinematic_scene_profiles.register` produces dormant, navigation-free cinematic
profiles, layer metadata and waiting links. It preserves unrelated destination
metadata. It distinguishes inline `SequenceArea` cleanup geometry from compiled
gameplay trigger volumes. Profiles with navigation, additional layers or mutual
exclusion groups fail explicitly until their full lifecycle can be converted.

The real native whipping and branding scenes have been converted into private
candidates with **123 and 114 entities**, respectively. Their reference frame is
the native scene's stage prop, translated to the source stocks' authored
placement. Native orientation is retained; terrain clearance and camera framing
still need an in-game check. The replacement inventory identifies only the
co-located stocks and log in the selected intact layer; burned-state props and
other towns are not selected for removal.

## Reproduce the audit

Run `tools/audit_world_scenes.py` with `--source`, `--target`, `--mod` and a fresh
`--output` directory. It reads both games and the built mod, then writes a
searchable HTML report and JSON findings. No installed archive is changed.
`source-stocks.json` lists exact authored brush placements. `--site-offset`
selects one of those placements for native scene preparation; it is a source
record selector, not a hard-coded NPC or runtime coordinate.

For the inspected source revision, the selected intact stocks record is
`117012800`, at approximately `(767.641, 3473.154, 53.2655)`. This number is
evidence for that source archive, not a permanent engine identifier. Source
content changes require rediscovery and validation.

The initial scene-audit candidate folder deliberately has no mod manifest and is not an installable
package. The punishment graph there is a dependency probe, not a wired live
controller. That audit is separate from the later complete integration package.
`crime-plan.json` records the audit's remaining work and always marks native
runtime verification false.

## Bailiff and silent-guard investigation (27 October 2026)

Live testing confirmed assault, crime recognition and surrender dialogue. Henry
speaks after native dialogue recordings are relocated from the final graph
closure. Accepting the bailiff option still stalled at a black loading screen,
and the guard was silent. The stall is not a passing punishment-playback test.

Two missing native contracts were found in that installed package:

* The native dialogue and punishment graphs check `player_henry`. KCD2's original
  campaign activates this through `GameContextTrigger`; the converted campaign
  contained the checks but no context owner. `world_actor_contracts` now adds
  the native boolean State/trigger connection at the converted Henry project's
  scope. It leaves the native sentence, fade and cutscene branches intact.
* Imported souls without explicit source dubbing bindings had no Skald character
  assignment. Guards retained a merchant voice group from the soul template.
  Retail Storm has no character-assignment task, so its authoring rules cannot
  fill these fields at runtime. Finalization now assigns shipped generic voices
  from native peers with the same archetype and social class. Existing explicit
  dubbing, source identity, appearance and equipment remain authoritative.
  Professions with only named native peers use the same-gender commoner pool;
  animals are excluded. A stable soul hash makes selection repeatable.

The current package audit assigned 48 absent human voices, including the local
guards, in 0.054 seconds. These are metadata corrections, not clothing rebuilds.
Both clean finalization and the completed-world resume path apply this step.
Tests cover the Henry trigger, conflicting ownership, class/gender selection,
existing dubbing, idempotence and safe replacement of hardlinked archives.
The candidate retains diagnostic cutscene traces for the next live test.
Subsequent user testing confirmed guard audio, but reported the wrong voice and
no lip sync. The full bailiff handoff still stalled. Those issues remain open.

## First rendered scene test and destination cast (27 October 2026)

A direct `Movie.PlaySequence` probe successfully rendered the whipping sequence.
The user confirmed playback but reported no sound, poor fit to the Skalitz site,
foreign people and actors left behind. This proves sequence/profile loading and
rendering only; it does not prove the native surrender-to-punishment handoff.
The probe bypassed `CutsceneHandler`, including its normal audio/profile lifecycle.
Its forced profile remained loaded after the sequence. Explicitly unloading it
removed the test actors, and restoring the permanent props made the stand visible.
The probe graphs and loose-file priority were restored before the next install.

Crime-service receipt version 3 now builds the cinematic crowd from registered
residents whose imported faction belongs to the destination location and whose
placement is inside its crime district. The same code runs in fresh Setup and
saved-package migration. Both scenes select `ska_bailiff` for the native bailiff
profession; this name is an audit result, not a selection rule. Fifty eligible
Skalitz residents supply 37 distinct human body identities for whipping and 32
for branding. Scenes can reuse residents, but different source bodies within one
scene cannot silently consume the same resident. Gender and crime role must match;
exact professions take precedence. Visitors and foreign dead-body substitutes
are excluded. Imported inventory references resolve the existing converted outfit.
Animation tracks and timings remain native. The first rendered test of this
cast policy exposed a player-variant classification error, described below.

The imported whipping sequence also had explicit visibility keys on the permanent
stage. Existing static foundations are now protected from cinematic visibility
changes, and unused prefab-local transform defaults no longer override registered
world props. Actual animation of a declared static foundation fails conversion.
The native punishment-location override now declares its string link payload in
the merged AI tag registry instead of relying on an undeclared tag.

These changes have unit tests and real-package migration/fresh-builder checks.
The user subsequently confirmed improved playback through the bailiff choice,
including the local cast and stand. Normal cleanup still needs verification.
Camera/crowd terrain fit, guard voice identity/lip sync and cinematic sound
remain outstanding. Translation alone does not adapt
the native motion-capture paths to the destination terrain, and the raw playback
probe is not evidence that the CutsceneHandler's audio loading has run.

## Player phase identity and crowd elevations (27 October 2026)

The next rendered test showed Henry at the beginning, followed by a clothed
resident when the whipping started. The native scene swaps between normal,
mildly injured and severely injured presentation bodies. The two injured bodies
use the **NPC** archetype, so version 3's archetype-only crowd classification
incorrectly replaced them. They are now recognized by their shared primary
animation, outfit and model with the scene's Hero, together with cinematic-only
faction metadata. No Henry name, body GUID or local resident ID is a selection
rule. The ordinary extras in that same cinematic-only faction remain eligible
for local casting. Fresh Setup and saved-package migration share this fix.

Crime-service receipt version 5 preserves both injured bodies' complete native
Properties in the generated archive, including their outfit and wounds. The
whipping and branding scenes now have 35 and 32 distinct local cast assignments.
An exported-package comparison checks the protected Properties against the
native source; regression tests cover an NPC-archetype player variant and an
ordinary cinematic-only extra separately.

`cinematic_scene_ground` also adjusts independent actors' absolute position
tracks using the difference between the native and destination heightfields.
It retains original ground clearance, XY curves, timing, flags and intentional
underground hide keys. Height tangent velocities incorporate the local terrain
difference gradient. The real candidate adjusts 42 whipping and 33 branding
actors (including body-based animals). Shared-origin, synchronized scene mocap
and protected player bodies remain rigid with the stand. Parent-local motion is
not mistaken for an absolute world-space foot position.

This is not a complete scene-fit certificate: correction is evaluated at the
authored spline knots, destination building/furniture collisions are unverified,
and six actors in each scene are flagged for seated or elevated support review.
The receipt lists adjusted/preserved actors and these limitations. Ground
contact between knots, the synchronized spectators, supporting props, camera
framing and final cleanup still require rendered checks. The existing camera
tracks and the stand have not been independently warped to the terrain.

Verification for this revision: 68 focused scene/cast/terrain, crime-service,
package-upgrade and recipe tests passed; the real saved-package upgrade took
11.5 seconds; a separate fresh-builder run took 7.1 seconds and produced matching
cast and terrain-fit reports. A second upgrade is a no-op. These are build/data
checks, not a claim that the revised punishment has already passed in game.

## Civilian reporting and native authority predicates (27 October 2026)

The user reported that guards reacted when attacked themselves but ignored
assaults on civilians. Inspection found two independent native contracts absent
from the imported population:

- `Libs/Storm/common/base.xml` requires both an authority profession **and**
  membership in a native regional faction for `isGuard` and `isBailiff`.
  Preserving `soul_crime_role_id` alone therefore did not enable
  `crime_isAuthority` or the district's dynamic `crime_authority` registration.
- `contexts_disabledInformationBroadcast` assigns
  `crime_skipInformationBroadcast` to factions without the `settlement` label.
  The converted faction trees had no such label.

`world_crime_factions` now resolves registered residents from their source
faction location, placed Common locations, soul registrations and native crime
roles. It labels the corresponding imported faction leaves as settlements,
registers their source LocationId, and adds their registered faction names to
the native guard/bailiff **regional** predicates. The native
profession checks, other predicates, original faction identities and directional
relationships remain intact. Every duplicate faction-table definition is
updated. Unplaced cinematic actors, unregistered locations and factions
represented only by renegade-role actors do not qualify.

Live testing rejected version 6: guards and the bailiff still had
`crime_isAuthority=false`, civilians had `crime_skipInformationBroadcast=true`,
and the district had zero `crime_authority` links. That version incorrectly
joined faction labels with semicolons and used an undeclared custom label.
Retail `FactionTree` uses whitespace-separated enum names (for example,
`city settlement`); `faction_label.xml` already declares `settlement`.

Crime-service receipt version 8 and Setup recipe 65 repair those contracts through
both fresh finalization and saved-package migration. Versions 5/6/7 upgrade without
rebuilding punishment scenes or clothing. The repair removes its old custom
label and preserves existing native labels. Validation checks the selector contract,
faction labels, locations and recorded resource hashes. Ten regression tests
cover native/imported authority classification, ordinary civilians, two towns,
visitors, renegades, conflicting locations, repeated upgrades and hardlink-safe
migration without rebuilding scenes, including malformed version-6 labels.
Both male and female native NPC classes participate in resident discovery. A
female-only settlement regression prevents the previous male-only enumeration
from silently skipping its faction. The final candidate upgraded in 5.5 seconds,
covering five faction leaves and 54 registered residents (eight authorities).
Its labels were also checked against the installed native label registry.
After installing version 7 and restarting, live probes confirmed authority contexts on
all eight imported guards/bailiffs and eight dynamic `crime_authority` links
in the Skalitz district. The sampled settlement civilians now have
`crime_skipInformationBroadcast=false`; civilians remain non-authorities.
The evidence is in `outputs/crime-witness-runtime-20261027/live-initialization.json`
and `live-all-actors.json`. Three actors in non-settlement source factions
(unpopular, best friends, millers) still retain the native broadcast restriction;
this migration does not invent a regional affiliation for those factions.
The user then confirmed guards approached when witnessing a civilian assault,
and some civilians reported while others fled. These are user-observed results,
not a captured report-delivery trace. The user also observed guards losing Henry
after he ran to the other end of the village. The native reporting, estimated
tracking, escape and search-origin scripts are not overridden by the package;
their presence does not prove search/navigation works correctly on this map.
Investigation duration, reacquisition, save/load and off-duty behavior remain
unverified. The female-only settlement extension in version 8 has package/test
coverage but has not yet received a separate in-game trial.

In the latest installed-build retest the user identified missing speech as the
remaining issue: surrender, punishment choices, replies and other dialogue lines
are silent. This is broader than a single punishment scene. The witness/authority
repair does not repair or verify dialogue recording resolution; that audio issue
remains open pending a shared voice-binding and localization audit.

### Shared-library reply audio (27 October 2026)

That audit found the relocated world contains two native audio roots. Retail
declares `open_world` and `utils` as siblings; dependency packaging places the
private `utils` library inside the converted world. The previous speech pass
only selected files below `dialog/open_world/`. It therefore omitted all 935
recorded response IDs under the shared library, including guard arrest dialogue,
Henry's replies, punishment choices and payment replies. The native English
archives contain 7,715 corresponding voice variants; none existed at the
imported library's destination paths in the installed package.

`world_speech_audio` now discovers audio roots from native project declarations
and partitions the final dependency closure at those roots. It computes each
destination through the imported instance hierarchy. The resulting shared-library
mapping is `dialog/utils/` to `dialog/kcd1_travel/open_world/utils/`; names and
response IDs are discovered from the graphs rather than an arrest-only list.
Original filenames, all available speaker variants and the native dubbing index
are retained. Speaker identities, text and punishment logic are unchanged.
The same pass already runs after crime integration in both fresh finalization
and saved-package migration. Setup recipe 66 ensures older saved builds receive it.

Seven audio tests cover native source roots, imported scopes, both sides of a reply,
multiple civilian voices, filename case, preserved custom audio and dubbing data,
hardlink safety, repeated upgrades, later-added controller dependencies and a
retained world scope with a relocated shared library.
The related 74-test suite passed. A private real-package upgrade added all 7,715
missing recordings, verified their CRC/size and existing dubbing-index entries,
and left the previously packaged 56,955 world recordings intact. Build plus
idempotence verification took 12.5 seconds. Evidence is in
`outputs/dialogue-shared-voices-20261027/build-result.json`.
This establishes package coverage; audible playback and lip sync still require
an in-game retest and are not asserted by these checks.

## Integration acceptance boundary

1. Replace the selected source props through the normal world builder, retaining
   a precise replacement receipt and validating collision, navigation and fit.
2. Bind the native controller to local districts, confiscation storage, guards,
   scheduler services, punishment presentation and return locations. Adapt
   regional faction/location semantics; retain native witness and guard AI.
3. Integrate scene metadata and playback with both fresh Setup and transactional
   resume. Do not flatten temporary cinematic actors into ordinary NPCs.
4. Convert missing KCD1 scene assets and resolve actor bindings; connect each
   required quest trigger to the converted scene and preserve completion events.
5. Verify in game: unwitnessed crime, civilian witness reporting, an unavailable
   guard, pursuit, surrender, fine, refusal, pillory, whipping, branding,
   repeat arrest and save/reload. Check cameras, animations and return positions.

An emulator can test graph/event contracts but cannot certify scene rendering,
navigation, native arrest UI or cinematic playback. None of those runtime
acceptance checks has been claimed by this audit.
