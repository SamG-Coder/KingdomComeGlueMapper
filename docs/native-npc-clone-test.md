# Native NPC clone test

The optional region-travel build argument `--test-person ska_matus` creates
Matthew beside the **return coachman on the KCD1 map**. Normal setup builds do
not include this test NPC. Use another KCD1 soul name to test another source
person through the same helper.

```powershell
.venv/Scripts/python.exe tools/region_travel.py `
  --kcd1 D:/SteamLibrary/steamapps/common/KingdomComeDeliverance `
  --kcd2 D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2 `
  --world D:/SteamLibrary/steamapps/common/KingdomComeDeliverance2/Mods/kingdomcomegluemapper/Data/Levels/kcd1_rataje `
  --output D:/KingdomComeGlueMapper/outputs/BuildWork/new-clone-test `
  --modid kingdomcomegluemapper --test-person ska_matus
```

This command creates the bridge payload. Combine it with the existing world
using `travel_bridge_package.combine`, validate, and install the resulting
single mod using `desktop_setup.install_transaction`, as for normal builds.
Do not install the bridge payload by itself over the full mod.

`character_native_clone.clone_world` copies the registered template actor and
its home, assigns deterministic independent identities, moves both together,
and copies/remaps owned scheduler and waiting-link records when present.
External links continue to reference shared services. The current return
coachman has no compiled scheduler or waiting-link rows to copy; the helper
does not invent them. Unit tests cover templates that do have such rows.

`clone_resources` copies the template soul and role and preserves its native
behavior fields. Source head/body/hair/clothing and localized name are applied
through separate Storm rules. It clones the template dialogue with a separate
role and graph node, connecting its travel output to the existing travel
action. Matthew therefore has the same test Talk/Travel interaction as the
coachman; this does not import Matthew's original quests or voiced dialogue.
Conditional source-layer actors are resolved by the person helper.

The 2026-10-10 build places Matthew at approximately
`3831.0762,1784.6631,127.2162`, 3.6 metres horizontally from the coachman,
with height sampled from the destination terrain. Its source identity is
`ska_matus`; its test runtime name is `gmtravel_test_ska_matus`.

Validation: 300 unit tests passed; all 15 combined database tables passed the
official mod-tools GeneratedDatabase reader. Packaged soul comparison found
only `soul_id` and `soul_name` differed from the return coachman. Existing
world asset packages are retained by the single-mod combine step.

Initial retail tests below confirm spawning and partial combat response;
complete behavior validation remains pending. Enter through ordinary Trosky travel,
compare both actors at the return station, and check appearance, Talk/Travel,
response to a drawn weapon or a hit, and return/re-entry/save-load persistence.
Use a disposable save for combat checks. Record which actor reacts and whether
it moves; a valid package does not establish that its runtime AI works.

## Bandit comparison

Add `--test-bandit-person ska_fricek` alongside `--test-person ska_matus` to
keep Matthew and add Fritz as a second comparison. `--bandit-template` selects
the native KCD2 soul; the default is `tpod_bandit_3` from Trosecko. Neither test
option is enabled by ordinary setup builds.

`character_bandit_clone` resolves the native soul and actor and translates its
exact-name Storm rules to the new identity. Native brain, combat level, social
class, faction and weapon/inventory rules are retained. KCD1 body/head/hair
and name replace the appearance; native bandit clothing and weapons remain.
The existing appearance builder also emits source outfit assets, but that
outfit preset is not selected for this test.

The actor uses a destination-local native `schedulerWait` and positioning
delegate, through `register_person_world`. This is an explicit isolated combat
test, not a complete transplantation of the source bandit's camp schedule.
Original camp links are recorded as replaced in `travel-build.json`; source
work/sleep/healing targets are not left pointing into Trosecko. There is no
forced hostility script or never-flee override. The native faction can make
this actor hostile; spawning 39 metres from the return coachman separates the
test from the travel interaction but does not guarantee combat isolation.

Fritz is placed at the terrain height at an offset of (-35, -18) metres from
the return coachman. Test targeted attacks, blocking, disengagement and any
flee/surrender transition against Matthew and the coachman. The existing
combat logging console files can capture the results. Do not claim that a
bandit is unable to flee based only on its name or faction.

## Retail observations, 2026-10-10

The user confirmed Matthew spawned and could hit the player, but both Matthew
and the return coachman exhibited an intermittent combat/target lock during
which the player could not attack. A captured Matthew combat event log showed
three successful targeted player hits, two hits from Matthew, then two player
hits marked `freeAttack=true`. It did not record rejected input or the exact
lock-on transition, so it did not establish the cause.

The user then tested Fritz without combat logging. More exchanges were
possible, but the lock still occurred during the NPC's apparent turn. Fritz
continued to attempt attacks or dodge, sometimes without completing an attack,
while the player could not attack back. He did not initiate combat until the
player attacked him. These are user observations, not captured engine-state
transitions; "turn" describes the apparent exchange rather than an identified
engine state.

The bandit comparison did not resolve the lock and weakens the surrender-only
hypothesis. Combat input, action/animation completion, targeting and shared
destination services remain candidates. Spawning and partial combat response
are confirmed; uninterrupted combat, autonomous aggression and full behavior
dependency conversion are not verified. Do not present this commit as a fix
for the combat lock.
