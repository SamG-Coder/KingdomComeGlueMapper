"""Development-only horse spawn/ownership probe on an imported KDC1 level.

Never installs a replacement horse into the retail travel mod or edits a save.
"""
import argparse
from pathlib import Path
import subprocess
from launch_probe import console


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--own',action='store_true',help='Assign the spawned test horse in this development session')
    args=p.parse_args()
    processes=subprocess.check_output(['powershell','-NoProfile','-Command',
        'Get-Process KingdomCome -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path'],text=True).splitlines()
    expected=Path('D:/SteamLibrary/steamapps/common/KCD2Mod/Bin/Win64ReleaseSteamLTO_DLL/KingdomCome.exe')
    if len(processes)!=1 or Path(processes[0])!=expected:
        raise SystemExit('Requires exactly one KCD2 modding-tools game process; retail is refused')
    log=expected.parents[2]/'kcd.log'
    loaded=[s for s in log.read_text(errors='replace').splitlines() if '*LOADING: Level ' in s and ' loading time:' in s]
    if not loaded or 'Level kcd1_world_v43 loading time:' not in loaded[-1]:
        raise SystemExit('Expected the isolated kcd1_world_v43 development level')
    command='''#local e=System.GetEntityByName("GlueTravelHorseProbe"); if not e then local p=player:GetWorldPos(); p.x=p.x+3; p.z=System.GetTerrainElevation(p)+0.1; e=System.SpawnEntity({class="Horse",name="GlueTravelHorseProbe",position=p,properties={guidSharedSoulId="4e5abeff-f19e-0eab-0921-a24611c4ad8f",bWH_RequiresHome=false,bMountIsLegal=true}}); end; if e then local p=e:GetWorldPos(); System.LogAlways("GLUE_HORSE_PROBE entity="..tostring(e.id).." horse="..tostring(e.horse).." actor="..tostring(e.actor).." position="..p.x..","..p.y..","..p.z); else System.LogAlways("GLUE_HORSE_PROBE spawn_failed"); end'''
    print(console(command).decode(errors='replace'))
    if args.own:
        print(console('#local e=System.GetEntityByName("GlueTravelHorseProbe"); if e then player.player:SetPlayerHorse(e.id); System.LogAlways("GLUE_HORSE_PROBE owned="..tostring(player.player:GetHorseId()).." expected="..tostring(e.id)); end').decode(errors='replace'))


if __name__=='__main__':main()
