# Authored ledge conversion review

Review date: 26 October 2026. Tracking: [PR #56](https://github.com/SamG-Coder/KingdomComeGlueMapper/pull/56)
and [issue #5](https://github.com/SamG-Coder/KingdomComeGlueMapper/issues/5).

This is structural conversion evidence. Runtime vaulting is not verified.
The contributor's recorded retail tests did not enable vaulting with either
world-bucket records, linked entities, or a profile with smart-object preparation.
Those results remain unresolved.

## Reproduced and repaired

Running the original PR's CLI against a synthetic level containing an existing
arrival profile and reserved entity ID reproduced three failures:

- `--replace` discarded the arrival profile when replacing `whdata_1`.
- The new reserved-ID file discarded the existing reservation.
- Running the conversion again raised `Ledge layer already registered`.

The reviewed converter replaces only `LedgeManager`, merges the owned reserved
IDs, preserves existing layer registration and stable generated entity IDs,
and allocates new IDs above mission, reserved and layer entities. It rejects
collisions and unowned content in its output layer. Archive rewriting streams
unchanged members, preserves their metadata, and uses an atomic temporary-file
replacement with cleanup on failure.

## Validation completed

- [x] Twelve synthetic ledge tests, including profile/reservation preservation,
  repeat conversion, collision rejection, duplicate archive members, streaming
  and interrupted-write behavior.
- [x] Full branch suite: 2,171 tests, 17 skips, no failures.
- [x] Python compilation and whitespace checks.
- [x] Real-data conversion on a temporary copy of the installed converted level:
  3,589 ledges, 30,140 markers and 228 generated linked entities.
- [x] Every marker and non-ID ledge field retained from the source, including flags.
- [x] Hashes of all 1,014 unrelated archive members unchanged.
- [x] Repeated preparation produces identical replacement members and entity IDs.
- [x] Installed level archive hash unchanged after testing.

The local detailed result is `outputs/pr-review-20261026/ledge-review.json`.
It is ignored output and contains no redistributed game assets.

## Remaining acceptance

- [ ] Establish and verify native loading/activation of the generated ledge entities.
- [ ] Observe fence/wall vaults and the relevant window/flip behavior in game.
- [ ] Verify collision and progression behavior on representative placements.
- [ ] Add and test saved-build finalization migration before claiming restore/upgrade support.

The PR currently adds a fresh-conversion stage only. Listing a layer in
`leveldata.xml` and reserving its IDs does not establish that a game profile
loads it. No runtime success, complete issue resolution or saved-build migration
is claimed by this review.

## Retail authored-object correction, 29 October 2026

The previous assumption that authored `LedgeObject` entities were available
only in the editor export was incorrect. The installed retail level contains
all 228 linked objects: 204 in `objects_mission0.xml` and 24 across state layers.
All 228 linked records have a first marker different from their authored entity
origin. Using that marker as `Pos` discards the original transform.

The converter now resolves the linked entities from retail mission/layer XML
and preserves `Pos`, `Rotate`, `Scale`, `CastShadowMinSpec` and the complete
nested `Properties` tree. Only generated identity and destination-layer
ownership change. Missing or duplicate source objects, invalid transforms and
unresolved parent transforms fail explicitly instead of generating substitutes.
The conversion report retains the source member for each linked object.

The updated synthetic tests exercise mission and state-layer discovery,
transform/property preservation, mutation isolation, missing/duplicate records,
and invalid/parented transforms. All 35 focused ledge/setup tests pass.
Read-only validation against installed retail assets verifies all 228 transforms
and property trees, 3,589 ledges and 30,140 markers. Repeated preparation yields
identical members; unrelated destination members remain unchanged. Evidence is
in the ignored `outputs/pr-review-20261029/pr56-authored-data.json`.

This correction does **not** resolve layer activation. The PR's latest runtime
review reported zero loaded `LedgeObject` entities despite 228 declared objects.
The output layer is absent from active profile `GameLayers`. Loading every
source-state object indiscriminately would also enable ledges belonging to
inactive quest states. Activation must preserve source-state ownership before
the remaining in-game and saved-build acceptance checks can be completed.
