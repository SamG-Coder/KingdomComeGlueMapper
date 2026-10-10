"""Shared regional travel wording, elapsed time and native weather tables."""
import xml.etree.ElementTree as ET

from upgrade_map import xml

JOURNEY_HOURS = 72
LABELS = {
    'ui_gluemapper_travel_kcd1': '(Buy transport to the Rattay region)',
    'ui_gluemapper_travel_trosky': '(Buy transport to the Trosky region)',
}
WEATHER_TABLE = 'tables/weatherprofiles.xml'
WEATHER_ALIAS = 'Mods/gluemappertravel/Data/Levels/kcd1_travel/' + WEATHER_TABLE


def update_resource(name, payload, native_weather=None):
    lower = name.replace('\\', '/').lower()
    if lower == WEATHER_ALIAS.lower() and native_weather is not None:
        return native_weather
    # Touch only our own travel records; native destination text stays intact.
    if not lower.endswith('.xml') or not any(key.encode() in payload for key in (*LABELS, 'gluemappertravel_to_')):
        return payload
    root = ET.fromstring(payload)
    changed = False
    for row in root.iter('Row'):
        cells = row.findall('Cell')
        if len(cells) >= 3 and cells[0].text in LABELS:
            for cell in cells[1:3]: cell.text = LABELS[cells[0].text]
            changed = True
    for prompt in root.iter('UiPrompt'):
        if prompt.get('StringName') in LABELS:
            prompt.set('Text', LABELS[prompt.get('StringName')]); changed = True
    for route in root.iter('LevelSwitchData'):
        if route.get('Name') in ('gluemappertravel_to_kcd1', 'gluemappertravel_to_trosky'):
            route.set('WorldTimeDurationInHours', str(JOURNEY_HOURS)); changed = True
    return xml(root) if changed else payload
