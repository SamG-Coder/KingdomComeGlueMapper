"""Spawn one packaged character visual in the running development level."""
import argparse
import json
import math
from pathlib import Path
import re

from launch_probe import console


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, default=Path('D:/SteamLibrary/steamapps/common/KCD2Mod'))
    parser.add_argument('--namespace', default='npc_native_v1')
    parser.add_argument('--layered', action='store_true', help='Use the experimental native clothing preset')
    parser.add_argument('--person', action='store_true', help='Spawn the registered source NPC with its native soul and inventory')
    parser.add_argument('--x', type=float, default=789.5)
    parser.add_argument('--y', type=float, default=3492)
    parser.add_argument('--heading', type=float, default=math.pi, help='Radians')
    args = parser.parse_args()
    if args.person and not args.layered:
        parser.error('--person requires --layered')
    if not re.fullmatch(r'[A-Za-z0-9_]+', args.namespace):
        parser.error('Invalid namespace')
    if not all(math.isfinite(v) for v in (args.x, args.y, args.heading)) or not (0 < args.x < 4096 and 0 < args.y < 4096):
        parser.error('Invalid placement')
    prefix = ('objects/characters/' if args.layered else '')+'gluenpc/' + args.namespace + '/'
    report = json.loads((args.tools/'Data'/prefix/'npc-report.json').read_text(encoding='utf-8'))
    if report['model'] != prefix+'character.cdf':
        parser.error('Unexpected character model path')
    name = 'GlueNPC_' + args.namespace
    model = report['model']
    config = ''
    body = ''
    if args.layered:
        layers = json.loads((args.tools/'Data'/prefix/'clothing-report.json').read_text(encoding='utf-8'))
        if layers['config'] != 'Glue_'+args.namespace or layers['model'] != prefix+'layered.cdf':
            parser.error('Unexpected clothing configuration')
        if not re.fullmatch(r'[0-9a-f-]{36}',layers['preset_id']):
            parser.error('Invalid clothing preset')
        model=layers['model'];config=layers['config']
        if not re.fullmatch(r'[0-9a-f-]{36}',layers.get('soul_id','')):
            parser.error('Missing clothing body soul; rebuild the clothing registration')
        body=f'Body={{guidBodyPresetId="{layers["soul_id"]}",guidClothingPresetId="{layers["preset_id"]}"}},'
    if args.person:
        if not layers.get('source_person_registered'):
            parser.error('Build with --register-source-person first')
        name = layers['soul_name']
        if not re.fullmatch(r'[A-Za-z0-9_]+', name):
            parser.error('Unsafe source NPC name')
        console(f'#local e=System.GetEntityByName("{name}"); if not e then '
                f'local p={{x={args.x},y={args.y},z=0}}; p.z=System.GetTerrainElevation(p); '
                f'e=System.SpawnEntity({{class="NPC_NAI",name="{name}",position=p,'
                f'properties={{fileModel="{model}",esClothingConfig="{config}",'
                f'guidSharedSoulId="{layers["soul_id"]}",esFaction="Civilians"}}}}); end; '
                f'if e then e:SetAngles({{x=0,y=0,z={args.heading}}}); '
                'System.LogAlways("GLUE_REGISTERED_NPC_IDLE "..tostring('
                'e:StartAnimation(0,"relaxed_idle_both",0,0.2,1,true))); end')
        print(f'Requested registered {name}; verify its shared soul, appearance and equipment in the runtime API.')
        return
    # Reuse this probe on repeated calls; do not duplicate world inhabitants.
    console(f'#local e=System.GetEntityByName("{name}"); if not e then '
            f'local p={{x={args.x},y={args.y},z=0}}; p.z=System.GetTerrainElevation(p); '
            f'e=System.SpawnEntity({{class="AnimChar",name="{name}",position=p,'
            f'properties={{object_Model="{model}",esClothingConfig="{config}",{body}'
            f'Physics={{bPhysicalize=0}}}}}}); end; if e then '
            f'e:SetAngles({{x=0,y=0,z={args.heading}}}); System.LogAlways("GLUE_NPC_SPAWN {name}"); end')
    if args.layered:
        console(f'#local e=System.GetEntityByName("{name}"); if e then '
                'EntityModule.AnimCharCopyVisual(e.id); end')
    if report.get('rig') == 'native':
        console(f'#local e=System.GetEntityByName("{name}"); if e then '
                'System.LogAlways("GLUE_NPC_NATIVE_IDLE "..tostring('
                'e:StartAnimation(0,"relaxed_idle_both",0,0.2,1,true))); end')
    print(f'Requested {name}; inspect GLUE_NPC markers in kcd.log and verify appearance in game.')


if __name__ == '__main__':
    main()
