"""Read-only inspection of installed Kingdom Come map archives.

No extraction or game-folder changes. Header samples are not format decoders.
"""
import argparse
import collections
import copy
import json
from pathlib import Path
import struct
import zipfile


def open_entry(archive, entry):
    """Accept CryEngine's slash-only local/central ZIP name discrepancy."""
    with open(archive.filename, "rb") as source:
        source.seek(entry.header_offset)
        header = source.read(30)
        if len(header) != 30 or header[:4] != b"PK\x03\x04":
            raise ValueError("Invalid local ZIP header")
        name_length = struct.unpack_from("<H", header, 26)[0]
        flags = struct.unpack_from("<H", header, 6)[0]
        local_name = source.read(name_length).decode("utf-8" if flags & 0x800 else "cp437")
    if local_name.replace("\\", "/") != entry.orig_filename.replace("\\", "/"):
        raise ValueError("Local and central ZIP names disagree beyond separators")
    adjusted = copy.copy(entry)
    adjusted.orig_filename = local_name
    return archive.open(adjusted)


def inspect_archive(path):
    report = {"path": str(path), "bytes": path.stat().st_size}
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        report["entry_count"] = len(entries)
        report["uncompressed_bytes"] = sum(e.file_size for e in entries)
        report["directory_counts"] = dict(collections.Counter(
            str(Path(e.filename.replace("\\", "/")).parent).replace("\\", "/") for e in entries
        ).most_common())
        report["samples"] = []
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            if "/" in name and not (name.startswith("terrain/") and name.count("/") == 1):
                continue
            sample = {"name": name, "bytes": entry.file_size, "compression": entry.compress_type}
            try:
                with open_entry(archive, entry) as stream:
                    data = stream.read(min(entry.file_size, 4096))
                sample["header_hex"] = data[:64].hex(" ")
                if name.lower().endswith((".xml", ".cfg", ".txt", ".json")):
                    sample["text_prefix"] = data.decode("utf-8", errors="replace")
            except (ValueError, OSError, zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
                sample["read_error"] = str(error)
            report["samples"].append(sample)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path(r"D:\SteamLibrary\steamapps\common"))
    parser.add_argument("--output", type=Path, default=Path("reports/map-audit.json"))
    args = parser.parse_args()
    result = {"scope": "Installed retail map metadata and bounded header samples; runtime compatibility untested", "games": {}}
    for game in ("KingdomComeDeliverance", "KingdomComeDeliverance2"):
        root = args.library / game
        if not root.is_dir():
            parser.error(f"Missing game directory: {root}")
        game_report = {"root": str(root), "editor_paths": [str(p) for p in root.rglob("Editor.exe")], "maps": {}}
        for level in sorted((root / "Data" / "Levels").iterdir()):
            if not level.is_dir():
                continue
            game_report["maps"][level.name] = {
                "files": [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(level.iterdir()) if p.is_file()],
                "archives": [inspect_archive(level / name) for name in ("level.pak", "terrain.pak") if (level / name).is_file()],
            }
        result["games"][game] = game_report
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Saved {args.output.resolve()}")
    for name, game in result["games"].items():
        print(name, "maps:", ", ".join(game["maps"]), "editors:", len(game["editor_paths"]))


if __name__ == "__main__":
    main()
