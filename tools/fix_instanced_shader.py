"""Generate a local KCD2 shader include correction from the user's installation.

No shader source is distributed. The installed include incorrectly accesses a
non-instanced constant in Get_BindlessBoneOffset_Prev when hardware instancing
selects the alternative instance-data buffer.
"""
import argparse
import hashlib
from pathlib import Path
import re
import zipfile
from upgrade_map import read


def patch_include(source):
    pattern=r'(uint Get_BindlessBoneOffset_Prev\(\)\s*\{.*?)(\s*return asuint\(CD_CustomData\.z\);)(\s*\})'
    match=re.search(pattern,source,re.S)
    if not match or source.count('uint Get_BindlessBoneOffset_Prev()')!=1:
        raise ValueError('Installed shader does not match the inspected function')
    body=('\n#if %_RT_ENVIRONMENT_CUBEMAP\n'
          '\treturn asuint(SInstancingData[0].CI_CustomData.z);\n'
          '#else\n\treturn asuint(CD_CustomData.z);\n#endif')
    return source[:match.start(2)]+body+source[match.end(2):]


def patch_shadow_include(source):
    needle='return clamp(CD_CustomData2.w * motionBias, 0, 3);'
    if source.count(needle)!=1:
        raise ValueError('Installed shadow include does not match the inspected expression')
    # The instance buffer has no per-object shadow-motion multiplier. Use a
    # neutral multiplier of one, retaining the common pass bias calculation.
    return source.replace(needle,'#if %_RT_ENVIRONMENT_CUBEMAP\n'
                          '\treturn clamp(motionBias, 0, 3);\n'
                          '#else\n\t'+needle+'\n#endif')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tools',type=Path,default=Path(r'D:\SteamLibrary\steamapps\common\KCD2Mod'))
    args=p.parse_args()
    with zipfile.ZipFile(args.tools/'Engine/Shaders.pak') as archive:
        for filename, patch in [('modificatorvt.cfi',patch_include),('commonshadowgenpass.cfi',patch_shadow_include)]:
            name=next(n for n in archive.namelist() if n.lower().endswith('/'+filename))
            original=read(archive,name)
            output=patch(original.decode()).encode()
            target=args.tools/'Engine'/name
            if target.exists() and target.read_bytes()!=output:
                raise ValueError('A different loose override already exists; preserve it')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(output)
            Path('reports').mkdir(exist_ok=True)
            (Path('reports')/(filename+'-original.sha256')).write_text(hashlib.sha256(original).hexdigest())
            print(f'Generated {target}; archives unchanged. Runtime compilation still requires verification.')


if __name__=='__main__':main()
