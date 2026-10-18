"""Reproduce reviewed native shader fixes and compile them in the job workspace.

KCD2 retail cannot compile loose shader sources. Follow Warhorse KM-A-82 using
the installed mod tools. Compiled archives are packaged under the mod's ShaderCache
folder, with a launcher that builds a linked private root inside Mods. Automatic
replacement of retail Engine files is forbidden.
The SDK and retail installations are read-only build inputs.
"""
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import time
import zipfile

from fix_illum_shader import REPAIRS as ILLUM_REPAIRS
from upgrade_map import read

ARCHIVES = ('Shaders.pak', 'ShadersBin.pak', 'ShaderCache.pak', 'ShaderCacheStartup.pak')
PREFIX = 'Shaders/HWScripts/CryFX/'
REPAIRS = {
    'CommonShadowGenPass.cfi': (
        b'return clamp(CD_CustomData2.w * motionBias, 0, 3);',
        b'#if %_RT_ENVIRONMENT_CUBEMAP\n\treturn clamp(motionBias, 0, 3);\n#else\n\treturn clamp(CD_CustomData2.w * motionBias, 0, 3);\n#endif'),
    'ModificatorVT.cfi': (
        b'return asuint(CD_CustomData.z);',
        b'#if %_RT_ENVIRONMENT_CUBEMAP\n\treturn asuint(SInstancingData[0].CI_CustomData.z);\n#else\n\treturn asuint(CD_CustomData.z);\n#endif'),
}
CONFIG = '''sys_no_crash_dialog=1
log_IncludeTime=3
log_Verbosity=3
log_FileVerbosity=3
r_driver=DX12
sys_game_folder=Data
r_ShadersAsyncCompiling=0
r_ShadersAsyncMaxThreads=16
r_multithreaded=0
r_ShadersSubmitRequestline=0
r_PipelineStatePrecacheMode=3
sys_intromoviesduringinit=0
sys_PakPriority=0
'''


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def patched(name, blob):
    repairs = ILLUM_REPAIRS if name == 'Illum.cfx' else (REPAIRS[name],)
    for old, new in repairs:
        if blob.count(new) == 1:
            continue  # Rebuilding against our previously installed shader archive.
        if blob.count(old) != 1:
            raise ValueError('Shader source differs from reviewed version: ' + name)
        blob = blob.replace(old, new)
    return blob


TOOL_FILES = ('Tools/ShaderCacheGen/ShaderCacheGen.exe', 'Tools/rc/rc.exe',
              'Tools/rc/RCJob_WH.xml', 'Tools/modding/USER_ShaderCacheGen/Shaders/ShaderList.txt',
              'Bin/Win64ReleaseSteamLTO_DLL/CrySystem.dll',
              'Bin/Win64ReleaseSteamLTO_DLL/CryRenderD3D12.dll')


def tool_root(target, selected=None):
    """Use the user's official Steam installation, including another library."""
    explicit = selected or os.environ.get('KCD2_MOD_TOOLS')
    if explicit:
        candidates = [Path(explicit)]
    else:
        from steam_installations import discover
        found, _ = discover()
        candidates = [Path(target).parent/'KCD2Mod', *(Path(p) for p in found.get('mod_tools', []))]
    for root in candidates:
        if all((root/n).is_file() for n in TOOL_FILES): return root.resolve()
    detail = ''
    if explicit:
        detail = ' Missing: ' + ', '.join(n for n in TOOL_FILES if not (Path(explicit)/n).is_file())
    raise ValueError('KCD1 Map requires the official KCD2 Modding Tools before building or installing. '
                     'Install them through Steam, then select their folder in setup.' + detail)


def link_tree(source, destination):
    """Share read-only tool/game inputs; never duplicate the game asset library."""
    for path in source.rglob('*'):
        if not path.is_file(): continue
        dest = destination/path.relative_to(source)
        if dest.exists(): continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try: os.link(path, dest)
        except OSError: shutil.copy2(path, dest)


def prepare(target, output):
    target, output = Path(target).resolve(), Path(output).resolve()
    sdk = tool_root(target)
    for game in (target, sdk):
        if output.is_relative_to(game): raise ValueError('Shader build must be outside game/tool installations')
    output.mkdir(parents=True, exist_ok=True)
    root = output/'compiler'
    # The executable resolves its Bin/ and Engine/ relative to this private root.
    for relative in ('Bin', 'Tools/ShaderCacheGen', 'Tools/rc'):
        link_tree(sdk/relative, root/relative)
    for relative in ('Data', 'Localization'):
        link_tree(target/relative, root/relative)
    for name in ('whdlversions.json',):
        if (target/name).is_file(): shutil.copy2(target/name, root/name)
    engine = root/'Engine'; engine.mkdir(parents=True, exist_ok=True)
    for path in (target/'Engine').glob('*.pak'):
        if path.name.lower().startswith(('shader',)): continue
        dest = engine/path.name
        if dest.exists(): continue
        # Hard links cannot cross volumes (game on C:, build on D:).
        try: os.link(path, dest)
        except OSError: shutil.copy2(path, dest)
    seed = root/'USER_ShaderCacheGen'
    if not seed.exists(): shutil.copytree(sdk/'Tools/modding/USER_ShaderCacheGen', seed)
    (root/'system.cfg').write_text(CONFIG, encoding='utf-8')
    changes = {}
    with zipfile.ZipFile(target/'Engine/Shaders.pak') as archive:
        for member in archive.namelist():
            if member.endswith('/'): continue
            path = engine/member.replace('\\', '/')
            if not path.resolve().is_relative_to(engine): raise ValueError('Unsafe native shader path')
            blob = read(archive, member)
            name = member.replace('\\', '/')
            if name in {PREFIX+n for n in ('Illum.cfx', *REPAIRS)}:
                blob = patched(Path(name).name, blob)
                changes[name] = hashlib.sha256(blob).hexdigest()
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(blob)
    if len(changes) != 3: raise ValueError('Required native shader sources absent')
    signature = dict(source=digest(target/'Engine/Shaders.pak'),
        compiler=digest(sdk/'Tools/ShaderCacheGen/ShaderCacheGen.exe'), changes=changes)
    (output/'inputs.json').write_text(json.dumps(signature, indent=2))
    return root, signature


class _LiveLog:
    """Read only appended bytes; partial lines stay buffered until complete."""
    def __init__(self, path):
        self.path, self.offset, self.pending = path, 0, b''

    def drain(self, final=False):
        changed = False
        try:
            with self.path.open('rb') as source:
                if source.seek(0, 2) < self.offset:
                    self.offset, self.pending = 0, b''
                source.seek(self.offset)
                while True:
                    data = source.read(256 * 1024)
                    self.offset += len(data)
                    changed |= bool(data)
                    self.pending += data
                    lines = self.pending.split(b'\n')
                    self.pending = lines.pop()
                    for line in lines:
                        print(line.decode('utf-8', errors='replace').rstrip('\r'), flush=True)
                    if not final or not data:
                        break
        except FileNotFoundError:
            pass  # ShaderCacheGen creates its log after initialization.
        if final and self.pending:
            print(self.pending.decode('utf-8', errors='replace').rstrip('\r'), flush=True)
            self.pending = b''
        return changed


def run_command(command, root, log, cancelled, notify, *, compiler_log=None):
    env = dict(os.environ, TEMP=str(root/'Temp'), TMP=str(root/'Temp'))
    (root/'Temp').mkdir(exist_ok=True)
    if compiler_log is not None:
        compiler_log.unlink(missing_ok=True)  # Never replay an earlier compilation.
    live = _LiveLog(compiler_log or log)
    label = 'Compile native shaders' if compiler_log else 'Package native shaders'
    started = last_activity = time.monotonic()
    print(f'{label}: starting; live output from {live.path}', flush=True)
    with log.open('wb') as stream:
        process = subprocess.Popen(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT,
            env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            while True:
                try:
                    code = process.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    if live.drain():
                        last_activity = time.monotonic()
                    if cancelled():
                        raise InterruptedError('Shader build cancelled')
                    now = time.monotonic()
                    if now - last_activity >= 15:
                        print(f'{label}: still running ({int(now - started)} s elapsed); waiting for compiler output.', flush=True)
                        last_activity = now
                    notify(label, 0, 0, f'{int(now - started)} s elapsed · {live.path.name}')
        finally:
            if process.poll() is None:
                process.terminate(); process.wait()
            live.drain(final=True)
    if code: raise ValueError(f'Native shader tool failed ({code}); see {log}')


def build(target, output, notify=lambda *a: None, cancelled=lambda: False):
    output = Path(output)
    root, signature = prepare(target, output)
    run_command([str(root/'Tools/ShaderCacheGen/ShaderCacheGen.exe'), '/PrecacheShaderList'],
                root, output/'compile.log', cancelled, notify, compiler_log=root/'shadercachegen.log')
    log = (root/'shadercachegen.log').read_text(errors='replace')
    compilation = compilation_result(log)
    run_command([str(root/'Tools/rc/rc.exe'), '/jobtarget=ShadersAll',
        '/OutputPath='+str(output/'package'), '/CryPakPlatform=D3D12', '/crashOnAssert',
        '/zip_encrypt=0', '/Streaming=1', '/IsShaderDebug=0', '/p=pc', '/job=Tools/rc/RCJob_WH.xml'],
        root, output/'pack.log', cancelled, notify)
    report = dict(signature, compiled=True, compilation=compilation, runtime_verified=False,
        installation='build_only', isolated_mount_verified=False, files={})
    for name in ARCHIVES:
        path = output/'package/Engine'/name
        with zipfile.ZipFile(path) as archive:
            if not archive.namelist(): raise ValueError('Empty native shader archive: '+name)
        report['files']['Engine/'+name] = digest(path)
    (output/'package/shader-build.json').write_text(json.dumps(report, indent=2))
    return validate(output/'package')


def compilation_result(log):
    counts = re.search(r'Combinations: \(Material: (\d+), Processed: (\d+); Compiled: (\d+); Removed: (\d+)\)', log)
    failures = [line for line in log.splitlines() if re.search(r'\berror\b|\bfailed\b', line, re.I)
                and "the file 'whdlversions.json' not found" not in line]
    if not counts or int(counts[3]) == 0 or failures:
        raise ValueError('Shader cache compilation incomplete: ' + '; '.join(failures[:5]))
    return dict(zip(('material', 'processed', 'compiled', 'removed'), map(int, counts.groups())))


def validate(package):
    package = Path(package)
    report = json.loads((package/'shader-build.json').read_text())
    expected = {'Engine/'+n for n in ARCHIVES}
    if (not report.get('compiled') or set(report['files']) != expected
            or set(report.get('changes', {})) != {PREFIX+n for n in ('Illum.cfx', *REPAIRS)}):
        raise ValueError('Incomplete compiled shader package')
    for name, value in report['files'].items():
        if digest(package/name) != value: raise ValueError('Shader archive changed: '+name)
    with zipfile.ZipFile(package/'Engine/Shaders.pak') as archive:
        for name, value in report['changes'].items():
            if hashlib.sha256(read(archive, name)).hexdigest() != value:
                raise ValueError('Shader repair missing from compiled source package: '+name)
    return dict(compiled=True, archives=len(expected), repairs=len(report['changes']),
                installation='build_only', isolated_mount_verified=False, runtime_verified=False)


def stage_for_mod(compiled, package, *, consume=False):
    """Package shaders for the setup's launch button; no retail files are written.

    Normal mod loading alone cannot override early Engine shaders. A separate
    launcher links the existing retail inputs and these archives inside Mods.
    The dynamically generated runtime is a sibling of the installed mod, so it
    cannot be recursively included in package inventories or installer rollback.
    """
    compiled, package = Path(compiled).resolve(), Path(package).resolve()
    validation = validate(compiled)
    files = {}
    for relative in ['shader-build.json', *('Engine/'+n for n in ARCHIVES)]:
        source, destination = compiled/relative, package/'ShaderCache'/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name(destination.name+'.pending')
        expected = digest(source)
        if consume:
            source.replace(pending)
        else:
            shutil.copy2(source, pending)
        if digest(pending) != expected: raise ValueError('Shader staging changed: '+relative)
        pending.replace(destination)
        files[destination.relative_to(package).as_posix()] = expected
    validation.update(installation='mod_private_root', launcher='setup',
                      native_files_replaced=0, requires_custom_launcher=True)
    return validation, files
