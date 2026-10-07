"""Stage an UNCONVERTED map copy for target-loader diagnostics, not gameplay."""
import hashlib
import json
from pathlib import Path
import shutil

BASE = Path(r"D:\SteamLibrary\steamapps\common")
SOURCE = BASE / "KingdomComeDeliverance/Data/Levels/rataje"
DEST = BASE / "KCD2Mod/Data/Levels/rataje"
REPORT = Path(__file__).resolve().parents[1] / "reports/staged-map-probe.json"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    if DEST.exists():
        raise SystemExit(f"Refusing to overwrite existing destination: {DEST}")
    files = [SOURCE / name for name in ("level.pak", "recast.pak", "svo.pak")]
    for file in files:
        if not file.is_file():
            raise SystemExit(f"Missing source: {file}")
    if shutil.disk_usage(DEST.parent).free < sum(p.stat().st_size for p in files) + 1024**3:
        raise SystemExit("Insufficient disk space")
    DEST.mkdir()
    result = {"status": "unconverted diagnostic probe", "source": str(SOURCE), "destination": str(DEST), "files": []}
    for file in files:
        target = DEST / file.name
        shutil.copyfile(file, target)
        source_hash, target_hash = digest(file), digest(target)
        if source_hash != target_hash:
            raise RuntimeError(f"Copy hash mismatch: {file.name}")
        result["files"].append({"name": file.name, "bytes": file.stat().st_size, "sha256": target_hash})
        print(f"Copied and verified {file.name}", flush=True)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Diagnostic probe staged at {DEST}. Dependencies are not yet converted.")


if __name__ == "__main__":
    main()
