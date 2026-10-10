"""Add a destination to the native Trosky driver's existing dialogue."""
import xml.etree.ElementTree as ET
from upgrade_map import xml

HOST = 'Quests/Final/Barbora/trosecko/trosecko_levelswitch/trosecko_levelswitch_inside.xml'
DIALOGUE = HOST[:-4] + '/prechod_z_trosecka_na_kutnohorsko.xml'
PORT = 'gluemapper_travel_kcd1'
STRING = 'ui_gluemapper_travel_kcd1'
LABEL = '(Travel to the Rattay region)'


def merge_localization(native, additions):
    """The engine replaces same-named localization files, not individual rows.

    Retain every original byte and insert only new rows before the closing tag.
    Refuse conflicting keys instead of overwriting another label.
    """
    original=ET.fromstring(native);extra=ET.fromstring(additions)
    if original.tag!='Table' or extra.tag!='Table':
        raise ValueError('Unsupported dialogue localization table')
    keys={r.findtext('Cell') for r in original.findall('Row')}
    rows=[]
    for row in extra:
        cells=row.findall('Cell')
        if row.tag!='Row' or len(cells)<3 or not cells[0].text:
            raise ValueError('Invalid localization row')
        key=cells[0].text
        if key in keys:raise ValueError('Localization key collision: '+key)
        keys.add(key);rows.append(ET.tostring(row,encoding='utf-8'))
    at=native.rfind(b'</Table>')
    if at<0:raise ValueError('Expected UTF-8 localization closing tag')
    return native[:at]+b'\n'.join(rows)+b'\n'+native[at:]


def patch(dialogue, host):
    d=ET.fromstring(dialogue);h=ET.fromstring(host)
    dialog=d.find('./Skald/FaderDialog');gameplay=h.find('./Skald/Gameplay')
    if dialog is None or gameplay is None:raise ValueError('Native travel dialogue format changed')
    choices=dialog.find('./Dialogue/Decision/Sequences')
    if choices is None or not any(s.find("UiPrompt[@StringName='ui_prechod_z_seq1_Gr38']") is not None for s in choices):
        raise ValueError('Expected native Kuttenberg travel choice')
    if dialog.find(f"Ports/Port[@Name='{PORT}']") is not None:raise ValueError('Dialogue already patched')
    ET.SubElement(dialog.find('Ports'),'Port',Name=PORT,Direction='Out',Type='trigger')
    choice=ET.Element('Sequence',Name=PORT,EndType='EndDialogue',GrayOutIfSequencesUsed='Never',
        EntryCondition="Port('kone_ziju') AND Port('kone_su_v_tabore') AND !Port('npc_videlo_crime') AND !Port('jindra_je_indisponovan')")
    ET.SubElement(choice,'UiPrompt',StringName=STRING,Text=LABEL)
    ET.SubElement(ET.SubElement(choice,'Triggers'),'Port',Name=PORT)
    # Native menu-only responses use an empty HENRY response. Do not reuse a
    # spoken line naming Kuttenberg for a different destination.
    ET.SubElement(ET.SubElement(choice,'Elements'),'Response',Role='HENRY')
    choices.insert(1,choice)
    nodes=gameplay.find('Nodes')
    waiter=ET.SubElement(nodes,'SceneFinishedWaiter',Name='gluemapper_wait_for_dialogue')
    ET.SubElement(waiter,'Edge',From='prechod_z_trosecka_na_kutnohorsko.'+PORT,To='Enqueue')
    prepare=ET.SubElement(nodes,'Function',Name='gluemapper_prepare_travel',MethodName='wh::conceptmodule::PassLongTime',DeclaringType='wh::conceptmodule')
    ET.SubElement(prepare,'Edge',From='gluemapper_wait_for_dialogue.OnFinished',To='Exec')
    switch=ET.SubElement(nodes,'Function',Name='gluemapper_switch_to_kcd1',MethodName='wh::game::SwitchLevel',DeclaringType='wh::game')
    ET.SubElement(switch,'Constant',Name='LevelSwitching',Value='gluemappertravel_to_kcd1')
    ET.SubElement(switch,'Edge',From='gluemapper_prepare_travel.OnExec',To='Exec')
    strings=ET.Element('Table');row=ET.SubElement(strings,'Row')
    for value in (STRING,LABEL,LABEL):ET.SubElement(row,'Cell').text=value
    return {DIALOGUE:xml(d),HOST:xml(h)},xml(strings)
