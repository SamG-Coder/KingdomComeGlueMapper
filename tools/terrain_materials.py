"""Package original terrain materials and streamed textures under isolated paths."""
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
import hashlib
import re
import xml.etree.ElementTree as ET
import zipfile

from audit_maps import open_entry


def package_materials(library, surfaces, package, namespace, blend_factor=None):
    prefix = f"gluemapper/{namespace}/"
    if package.exists():
        raise FileExistsError(package)
    with ExitStack() as stack:
        index = {}
        root = library / "KingdomComeDeliverance/Data"
        # Base assets first, then installed HD overrides. Preserve the base
        # streamed mips that HD packages augment rather than replace.
        paths = [p for p in root.glob("*.pak") if p.name.startswith(("GameData", "Textures", "Objects", "Buildings"))]
        paths.sort(key=lambda p: ("_HD" in p.name, p.name.lower()))
        for path in paths:
            archive = stack.enter_context(zipfile.ZipFile(path))
            for entry in archive.infolist():
                name = entry.filename.replace("\\", "/").lower()
                if name.endswith(".mtl") or ".dds" in name:
                    index[name] = (archive, entry)

        def read(name):
            archive, entry = index[name]
            with open_entry(archive, entry) as stream:
                return stream.read()

        materials = {}
        textures = set()
        mapping = []
        blend_changes = []
        for surface in surfaces:
            original = surface.get("DetailMaterial").replace("\\", "/").lower()
            name = original + ".mtl"
            material = ET.fromstring(read(name))
            if material.get("Shader") != "Terrain.Layer":
                raise ValueError(f"Unsupported terrain shader: {name}")
            # Numeric shader feature bits changed between engine builds.
            # Let the target resolve the named features; retain original UVs
            # and material parameters and enable its terrain depth blending.
            material.attrib.pop("GenMask", None)
            flags = material.get("StringGenMask", "")
            if "%SOFT_DEPTH_TEST" not in flags:
                flags += "%SOFT_DEPTH_TEST"
            material.set("StringGenMask", flags)
            material.set("MtlFlags", "526336")
            params = material.find("PublicParams")
            if params is not None:
                if blend_factor is not None and "BlendFactor" in params.attrib:
                    blend_changes.append({"material": original, "original": params.get("BlendFactor"), "test": blend_factor})
                    params.set("BlendFactor", str(blend_factor))
                for key, value in {"DetailTextureStrengthFade": "1", "NormalMapBumpiness": "1", "SoftDepthTestDistRatio": "0.5", "SoftDepthTestRange": "0.02"}.items():
                    if key not in params.attrib:
                        params.set(key, value)
            for texture in material.iter("Texture"):
                source = texture.get("File").replace("\\", "/").lower()
                compiled = str(PurePosixPath(source).with_suffix(".dds"))
                if compiled not in index:
                    raise FileNotFoundError(compiled)
                textures.add(compiled)
                texture.set("File", prefix + compiled)
            materials[prefix + name] = ET.tostring(material, encoding="utf-8", xml_declaration=True)
            surface.set("Name", prefix + original)
            surface.set("DetailMaterial", prefix + original)
            mapping.append({"id": surface.get("SurfaceTypeID"), "source": original, "target": prefix + original})

        # Include complete DDS families: base, numbered streaming mips, and
        # split alpha/gloss mips (.a/.1a/etc.). Never copy base DDS alone.
        families = {}
        for texture in sorted(textures):
            members = [name for name in index if name == texture or
                       (name.startswith(texture + ".") and re.fullmatch(r"(?:a|[0-9]+a?)", name[len(texture)+1:]))]
            families[texture] = sorted(members)
        manifest = []
        with zipfile.ZipFile(package, "x", zipfile.ZIP_STORED) as output:
            for name, data in materials.items():
                output.writestr(name, data)
            for texture, members in families.items():
                for name in members:
                    data = read(name)
                    output.writestr(prefix + name, data)
                    archive, entry = index[name]
                    manifest.append({"path": prefix+name, "source_pak": Path(archive.filename).name,
                                     "source_entry": entry.filename, "bytes": len(data),
                                     "sha256": hashlib.sha256(data).hexdigest()})
        # Validate written bytes and every rewritten material reference.
        with zipfile.ZipFile(package) as check:
            for item in manifest:
                if hashlib.sha256(check.read(item["path"])).hexdigest() != item["sha256"]:
                    raise ValueError("Packaged texture checksum mismatch")
            for name in materials:
                for texture in ET.fromstring(check.read(name)).iter("Texture"):
                    check.getinfo(texture.get("File"))
            # The development runtime does not auto-mount arbitrary Data
            # archives. Install the isolated namespace as loose workspace
            # assets, which it resolves alongside the official packages.
            workspace_data = package.parent.resolve()
            destination = workspace_data / "gluemapper" / namespace
            if destination.exists():
                raise FileExistsError(destination)
            for name in check.namelist():
                target = (workspace_data / name).resolve()
                if not target.is_relative_to(destination):
                    raise ValueError("Asset path escapes its namespace")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(check.read(name))
        return {"package": str(package), "materials": mapping,
                "blend_factor_experiment": blend_changes,
                "loose_workspace_assets": str(destination),
                "texture_families": len(families), "texture_files": len(manifest),
                "hd_files": sum("_HD" in item["source_pak"] for item in manifest),
                "bytes": package.stat().st_size, "files": manifest,
                "validation": "All material texture references exist; every texture payload SHA256 verified"}
