"""Read actual native and installed item definitions for campaign tracking."""
from pathlib import Path

from campaign_quest_graph import identifier
from container_items import item_patches
from world_item_definitions import native_items, native_item_registry, usable_native_item


def from_definitions(definitions, player_asset):
    identifier(player_asset)
    return dict(item_map=native_item_registry(definitions),
                registered=sorted(key for key, row in definitions.items() if usable_native_item(row)),
                player_asset=player_asset)


def installed(target, mod, player_asset):
    target = Path(target)
    if not (target / 'Data/Tables.pak').is_file():
        raise ValueError('Target game is missing Data/Tables.pak')
    patches = []
    if mod is not None:
        mod = Path(mod)
        if not (mod / 'Data').is_dir():
            raise ValueError('Mod is missing its Data directory')
        patches = item_patches(mod)
    return from_definitions(native_items(target, patches), player_asset)
