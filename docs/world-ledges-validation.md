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
