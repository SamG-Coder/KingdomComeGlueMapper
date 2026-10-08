"""Read Steam library/manifest files. No Steam credentials or network required."""
import os
from pathlib import Path
import re

APP_IDS = {'kcd1': '379430', 'kcd2': '1771300'}


def read_vdf(text):
    tokens = re.finditer(r'//[^\n]*|"((?:\\.|[^"\\])*)"|([{}])', text)
    values = []
    for token in tokens:
        if token.group(0).startswith('//'): continue
        string, brace = token.groups()
        values.append(brace or re.sub(r'\\(["\\])', r'\1', string))
    def parse(index, nested=False):
        result = {}
        while index < len(values):
            key = values[index]; index += 1
            if key == '}':
                if not nested: raise ValueError('Unexpected VDF brace')
                return result, index
            if index >= len(values): raise ValueError('Incomplete VDF pair')
            value = values[index]; index += 1
            if value == '{': value, index = parse(index, True)
            result[key] = value
        if nested: raise ValueError('Unclosed VDF object')
        return result, index
    return parse(0)[0]


def steam_roots():
    candidates = []
    if os.name == 'nt':
        import winreg
        for hive, key, value in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
                                 (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath')):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    candidates.append(Path(winreg.QueryValueEx(handle, value)[0]))
            except OSError:
                pass
    for variable in ('ProgramFiles(x86)', 'ProgramFiles'):
        if os.environ.get(variable): candidates.append(Path(os.environ[variable]) / 'Steam')
    return list(dict.fromkeys(p.resolve() for p in candidates if p.is_dir()))


def discover(roots=None):
    roots = steam_roots() if roots is None else [Path(p) for p in roots]
    libraries = set(roots)
    warnings = []
    for root in roots:
        vdf = root / 'steamapps/libraryfolders.vdf'
        if not vdf.is_file(): continue
        try:
            data = read_vdf(vdf.read_text(encoding='utf-8-sig')).get('libraryfolders', {})
            for key, value in data.items():
                if key.isdigit():
                    path = value.get('path') if isinstance(value, dict) else value
                    if path: libraries.add(Path(path))
        except (OSError, ValueError) as error:
            warnings.append(f'{vdf}: {error}')
    found = {key: [] for key in APP_IDS}
    for library in sorted(libraries):
        for key, appid in APP_IDS.items():
            manifest = library / 'steamapps' / f'appmanifest_{appid}.acf'
            if not manifest.is_file(): continue
            try:
                data = read_vdf(manifest.read_text(encoding='utf-8-sig'))['AppState']
                folder = data['installdir']
                if data.get('appid') != appid or not folder or '/' in folder or '\\' in folder or folder in ('.', '..'):
                    raise ValueError('Invalid Steam installation manifest')
                path = (library / 'steamapps/common' / folder).resolve()
                if path.is_dir() and str(path) not in found[key]: found[key].append(str(path))
            except (OSError, ValueError, KeyError) as error:
                warnings.append(f'{manifest}: {error}')
    return found, warnings
