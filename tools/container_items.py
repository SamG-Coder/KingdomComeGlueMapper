"""Import all container loot dependencies using the world-item converters."""
from contextlib import ExitStack
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from campaign_dependencies import DependencyImporter
from campaign_dependency_adapters import SourceTables, StaticAssetAdapter, ItemTextAdapter
from campaign_sources import RetailSourceReader
from character_person import native_xml
from world_item_definitions import native_items, native_item_registry, WorldItemAdapter
from world_item_clothing import ClothingAdapter
from world_item_documents import DocumentImageAdapter
from upgrade_map import read


def build(source, target, mod, output, identifiers, *, asset_namespace='containeritems', origin='container_loot', reuse_output=False):
    source, target, mod, output = map(Path, (source, target, mod, output))
    if output.exists() and not reuse_output: raise FileExistsError('Choose a fresh dependency output')
    output.mkdir(parents=True,exist_ok=reuse_output)
    patches = []
    for path in sorted((mod/'Data').glob('*.pak')):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if re.fullmatch(r'libs/tables/item/item(?:__[^/]+)?\.xml', name, re.I):
                    patches.append(read(archive, name))
    native = native_items(target, patches)
    with ExitStack() as stack:
        reader = stack.enter_context(RetailSourceReader(source))
        tables = SourceTables(source, reader)
        with zipfile.ZipFile(target/'Data/Tables.pak') as archive:
            classes = native_xml(read(archive, 'Libs/Tables/item/weapon_class.xml'))
            components = native_xml(read(archive, 'Libs/Tables/Character/CharacterComponent.xml'))
        weapons = {n.get('id'): n.get('name') for n in classes[0]}
        cache = output/'asset-cache'; cache.mkdir(exist_ok=reuse_output)
        assets = StaticAssetAdapter(stack, source.parent, asset_namespace, cache)
        from campaign_item_types import native_tool_types
        clothing = ClothingAdapter(tables, assets, target, stack, defer_bakes=True)
        importer = DependencyImporter({
            'items': WorldItemAdapter(tables, 'containeritems', native, weapons, components,
                                      native_tool_types=native_tool_types(target)),
            'clothing': clothing,
            'document_images': DocumentImageAdapter(source, reader, 'containeritems'),
            'assets': assets, 'strings': ItemTextAdapter(source, 'containeritems', 'English')},
            registered={'items': native_item_registry(native)})
        for index, identifier in enumerate(sorted(set(identifiers))):
            importer.ensure('items', identifier, {'origin':origin})
            if index % 50 == 0: print('Container item dependencies', index, '/', len(identifiers), flush=True)
        clothing.finish()
        report = importer.report()
        report['required_items'] = len(set(identifiers))
        report['unresolved_items'] = sorted(set(identifiers)-importer.registered['items'].keys())
        report['registered_items'] = {i:importer.registered['items'][i] for i in identifiers if i in importer.registered['items']}
        item_names = {i:node.get('Name') for i,node in native.items()}
        for name,payload in importer.files.items():
            if re.fullmatch(r'libs/tables/item/item(?:__[^/]+)?\.xml',name,re.I):
                root = ET.fromstring(payload.read_bytes() if isinstance(payload,Path) else payload)
                item_names.update({n.get('Id').lower():n.get('Name') for n in root.findall('ItemClasses/*')})
        report['item_names'] = {i:item_names[i] for i in report['registered_items'].values()}
        (output/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        for name, payload in importer.files.items():
            destination = output/'converted'/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload.read_bytes() if isinstance(payload, Path) else payload)
        if report['unresolved_items']:
            raise ValueError(str(len(report['unresolved_items']))+' loot item dependencies need conversion; see report.json')
        return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source','target','mod','output','audit'): parser.add_argument('--'+name,type=Path,required=True)
    args = vars(parser.parse_args()); audit = json.loads(args.pop('audit').read_text(encoding='utf-8'))
    ids = {i for inv in audit['inventories'].values() for i in inv.get('item_dependencies',[])}
    build(**args, identifiers=ids)
