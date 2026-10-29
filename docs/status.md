# Release, source and runtime status

Updated for **Setup v0.3.28 on 26 October 2026**. Its runtime source includes
[`cae0d3e`](https://github.com/SamG-Coder/KingdomComeGlueMapper/commit/cae0d3ea781c15af0184c1821bab53fcfbe7c339);
the release tag also contains the release documentation. The executable passed
its packaged self-test. Dated runtime records below retain their original test
scope; this release preparation is not a new game playtest.

## Choose the right version

| Reference | What it establishes | What it does not establish |
| --- | --- | --- |
| [Published Setup v0.3.28](https://github.com/SamG-Coder/KingdomComeGlueMapper/releases/tag/v0.3.28) | Windows x64 executable, checksum and packaged self-test report; recipe 61 and appearance policy 2. Includes the changes through `cae0d3e`. | Packaging and automated tests do not prove every NPC, quest or retail interaction. |
| Previous published Setup v0.3.16, `9881163`, recipe 40 | The starting point for the [v0.3.28 changelog and credits](releases/v0.3.28.md). | Intermediate locally built Setup versions were not separate published GitHub releases. |
| Source after the v0.3.28 tag | Any subsequent development commits. | Later source changes are not automatically in the downloaded executable. |
| Dated runtime records | Acceptance of the specific build, location and behavior described in each record. | Whole-map, every-NPC, all-quest or save/load acceptance outside the recorded test. |

The [Windows guide](windows-setup.md) is the installation starting point.
Use the executable under the release's **Assets** for the published build, or
follow [Run the current source](../README.md#run-the-current-source) for source
development. Keep the original job folder and its `job.json` for Resume.
Setup version, mod package version and gameplay recipe version are separate;
include the release/commit and package recipe when reporting a problem.

Recipe eligibility is defined by
[`build_recipe.py`](../tools/build_recipe.py) and
[`setup_package_upgrade.py`](../tools/setup_package_upgrade.py).
Setup v0.3.28 accepts saved receipts from recipes 3–61 for checking and upgrades
supported older packages to recipe 61, including current appearance validation.
Valid receipts and all required validators must still pass. Direct installation
requires the recipe expected by the running setup; it does not migrate an older
package or downgrade a newer one.

## Selected feature and acceptance boundaries

| Area | Implemented or packaged | Recorded runtime evidence / remaining work |
| --- | --- | --- |
| NPC appearances | v0.3.28 includes native outfit composition, source detail attachments, equipment preservation, neck/body coverage fixes and integrated saved appearance upgrades. | Six-actor performance equivalence checks preserve 77 mesh/LOD payloads and resolved material surfaces. They do not establish every outfit's in-game correctness. See the [clothing investigation](clothing-native-rendering.md). |
| Quest & NPC Tests | Separate developer source tool emulates declared quest/NPC logic and produces HTML, JSON and JUnit reports; generic importer fixes are included in Setup. | Unsupported operations are failures. This tool does not render the world or emulate the full engine. See [validation](quest-emulator-validation.md). |
| KCD1:MP export | v0.3.16 fixes reserved scenery entity IDs; scene preparation retained 19,043 visual entities. | Export preparation and packaged self-test passed. Player initialization, traversal and two-client synchronization remain unverified. See [release notes](releases/v0.3.16.md) and [multiplayer acceptance](kcd1-mp-setup.md#acceptance-boundary). |
| Imported level startup | Setup's private runtime supplies canonical level paths as well as mod shaders. | User-confirmed NPC behavior recovery on 20 October after launching through setup. Use **Launch mod**, including for diagnostics; see [startup audit](retail-npc-startup-audit-2026-10-20.md). |
| Town benches and NPC sleep | v0.3.28 includes generic schedule/seating and sleep conversion. | Sampled retail behavior was user-confirmed on 22 October. Contention, interruptions, all actors and save/load remain separate checks. See [bench playtest](city-bench-work-playtest.md) and [sleep playtest](npc-sleep-playtest.md). |
| Fence repair and home rest | v0.3.28 converts bounded source-linked activities; targeted packages were installed in retail. | Installation and structural validation do not establish behavior. Their records still require runtime observation: [fence repair](fence-work-playtest.md), [home rest](home-rest-playtest.md). |
| Designer collision and barriers | v0.3.28 includes supported initial-world volume conversion and follow-up repairs. | Fresh-build player/horse traversal, water and map-edge barriers, monologues and normal routes still require the checks recorded in the [conversion review](https://github.com/SamG-Coder/KingdomComeGlueMapper/pull/43#pullrequestreview-5604765913). Source conversion does not close every reported collision problem. |
| Starting torch and training | v0.3.28 includes the torch, invitation/departure adapters and subsequent training handoff/initialization repairs. | The original torch/invitation record covers a limited slice, not full combat-lesson acceptance. See [quest inventory](source-quest-inventory.md#new-game-torch--23-october-2026), [introduction audit](quest-introduction-activities.md) and [release scope](releases/v0.3.28.md). |
| Stale LOD repair and install reuse | v0.3.28 repairs supported stale sections from matching saved evidence and reuses verified installed files during staging. | Tests cover bounded repairs and transactions. This is not arbitrary corrupt-package recovery or a gameplay acceptance claim. Keep the original workspace; see [repair requirements](stale-section-repair.md). |

The [README feature checklist](../README.md#feature-checklist) covers broader
world conversion. Its checkmarks mean the stated implementation exists, not that
every object has passed runtime acceptance. The full KCD1 campaign and KCD1 save
migration remain unfinished; multiplayer scenery export does not convert quests
into cooperative gameplay.

The [quest checklist](quest-checklist.md) is a historical checkpoint. Use the
newer linked audits for later work, retaining their explicit pending checks.
No new runtime acceptance is inferred from this documentation update.

## Performance evidence

[PR #45](https://github.com/SamG-Coder/KingdomComeGlueMapper/pull/45), merged
22 October after v0.3.16, reports a complete 1,220-tile **Buildings** bake on a
24-thread machine with 64 GB RAM and an RTX 5090 32 GB. Its standalone default
two-heavy-slot run took 23.5 minutes, with all tile hashes matching the serial
baseline. A separate cold build reported 94 minutes for map conversion, baking
and finalization, including a 24.1-minute bake with an empty lighting cache.

These are contributor measurements for the stated source/build configuration.
The parallel tile implementation is included in v0.3.28. These are not
fresh-install timings for every later recipe or measurements of **All (Forest)**.
Compare like-for-like hardware, cache state, selected coverage and measured
stages before drawing conclusions about setup duration.

The v0.3.28 fresh-appearance investigation measured six actors at 89.2 s before
and 63.2 s after its first changes (conversion plus packing). A subsequent mask
sharing change removes another 149.36 MiB from Father's generated package while
preserving mesh and material equivalence. A 61-person run before mask sharing
measured 361.29 s conversion and 102.57 s packing. Those figures exclude other
Setup stages and are not a full retail build estimate. Substantial texture data
is still duplicated between actor packages. See the [complete investigation](clothing-native-rendering.md)
for timing boundaries, validation and the remaining shared-asset work.
