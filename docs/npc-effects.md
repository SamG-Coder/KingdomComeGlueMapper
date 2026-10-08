# Native NPC blood, grime and wear conversion

This development pass operates on an already packaged and registered NPC.
It reads the user's installed KCD2 material references and preserves the
imported KCD1 geometry, UVs, skin weights and selected outfit. No game assets
are distributed in this repository.

## Build and select the converted assets

```powershell
python -m pip install -r requirements-npc-effects.txt
python tools/build_npc_effects.py --namespace martin_v1 --revision effects_v1 --install
```

Choose an unused revision name. Omitting `--install` writes the generated
assets and proposed table overlays without selecting them. The current tool
requires the fixed assembled outfit from `build_npc_clothing_probe.py`.

The output lives inside the local NPC package's revision folder. Installation
backs up `CharacterComponent.xml` there as `CharacterComponent.before.xml`.
If a loose `ClothingMaterial.xml` already exists it is backed up too. Only those
two development table overlays are changed; native definitions are retained.
Restart the development game and spawn the registered person to load them.
Before rebuilding from another revision, restore the original component
overlay: the builder rejects converting already converted materials again.

## Material inputs

- **Skin:** the legacy dirt/blood texture's alpha channel becomes native blood
  red, and green becomes native grime green. The converted mask binds to
  `Emittance`. Skin remains opaque; the mask is not used as surface opacity.
  The head uses the native **head** blood/grime parameters, including its
  blood-zone falloff, rather than the body's softer falloff. Teeth are excluded
  from the generic exposed-skin fallback.
- **Clothing:** native Illum `CLOTHING_SYSTEM` uses a material feature plus the
  per-mesh macro-texture path. Vertex blue selects macro index 7 and feature 0;
  alpha leaves the fixed outfit outside hiding groups. Geometry and skinning
  streams are unchanged. Feature slots are declared once per multi-material.
- **Colour:** source tint zones are baked before disabling the legacy tint
  permutation. Converted diffuse/specular maps use half-float DDS with mips
  to retain dark colour values through the native macro conversion. The
  neutral installed atlas entry supplies the feature base.
- **Blood and dirt:** native actor blood zones and dirt drive the skin and
  clothing shaders. Materials without an authored BG mask use an explicit
  uniform fallback, recorded in `effects-report.json`.

## Current limits

Blood and grime have been exercised on registered Martin in the development
runtime. The face received an additional native head-parameter correction
after visual feedback. Automated tests cover mask channels, immutable material
conversion, vertex preservation, DDS handling and macro-colour compensation.

Wear is **provisional**, not a finished damage conversion. Native scratch
inputs are bound, but use a uniform coverage mask and a darker secondary
feature. Original garment-specific scratch maps, independent shoe/garment
condition values, visual repair validation and geometric tears remain
unfinished. The fixed outfit still occupies one native inventory item. Native
material-atlas detail also replaces the original tiled detail-map layer.
