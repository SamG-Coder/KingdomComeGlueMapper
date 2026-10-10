"""Use converted scenery in the travel build without the old New Game probe."""
from pathlib import Path
import zipfile

MENU_FILES = {'libs/ui/menu.gfx', 'libs/ui/uielements/menu.xml'}
BOOTSTRAP = 'scripts/mods/kingdomcomegluemapper.lua'


def prepare_world_for_travel(world, render_diagnostics=False):
    path = Path(world) / 'Data/kingdomcomegluemapper.pak'
    temporary = path.with_suffix('.travel.tmp')
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(temporary, 'x', zipfile.ZIP_STORED, allowZip64=False) as dst:
        for name in src.namelist():
            if name.lower() in MENU_FILES: continue
            data = src.read(name)
            if name.lower() == BOOTSTRAP:
                data = (b'KingdomComeGlueMapper = KingdomComeGlueMapper or {}\n'
                        b'KingdomComeGlueMapper.campaignReady = false\n'
                        b'KingdomComeGlueMapper.startProbe = false\n'
                        b'System.LogAlways("[GlueMapper] Travel world assets loaded; campaign menu disabled")\n')
                if render_diagnostics:
                    data += (Path(__file__).resolve().parents[1] / 'runtime/render_diagnostics.lua').read_bytes()
            dst.writestr(name, data)
    temporary.replace(path)
