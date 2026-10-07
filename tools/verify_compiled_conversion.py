"""Independent checks against installed input and generated output archives."""
from array import array
from pathlib import Path
import json
import argparse
import struct
import zipfile
import math

from audit_maps import open_entry
from compiled_terrain import parse


base = Path(r"D:\SteamLibrary\steamapps\common")
parser = argparse.ArgumentParser()
parser.add_argument("--level", default="kcd1_terrain_v6")
parser.add_argument("--library", type=Path, default=base)
args = parser.parse_args()
base = args.library
with zipfile.ZipFile(base / "KingdomComeDeliverance/Data/Levels/rataje/level.pak") as z:
    with open_entry(z, z.getinfo("terrain/terrain.dat")) as f:
        source = parse(f.read())
with zipfile.ZipFile(base / "KCD2Mod/Data/Levels" / args.level / "terrain.pak") as z:
    converted = parse(z.read("terrain/terrain.dat"))

checked = 0
max_error = 0
assert len(source.nodes) == len(converted.nodes) == 5461
for old, new in zip(source.nodes, converted.nodes):
    original_errors = struct.unpack_from("<6f", source.data, old["errors"])
    target_errors = struct.unpack_from("<6f", converted.data, new["errors"])
    expected_error = max(original_errors) if old["depth"] < 6 else 0.0
    assert target_errors == (expected_error, 0, 0, 0, 0, 0)
    n = old["size"]**2
    a, b = array("H"), array("I")
    a.frombytes(source.data[old["samples"]:old["samples"]+n*2])
    b.frombytes(converted.data[new["samples"]:new["samples"]+n*4])
    old_offset, old_scale = old["values"][8:10]
    new_offset, new_scale = new["values"][8:10]
    assert source.data[old["palette"]:old["palette"]+old["surface_count"]] == converted.data[new["palette"]:new["palette"]+new["surface_count"]]
    for x, y in zip(a, b):
        # Independent integer-grid calculation, rather than reusing converter.
        expected_height = (math.floor(old_offset*20) + (x >> 4))/20
        error = abs(expected_height - new_offset - (y >> 20)*new_scale)
        max_error = max(max_error, error)
        assert error <= 0.00003
        assert x & 15 == y & 0xfffff
    checked += n

# Independent spatial invariant: adjacent sectors must agree at shared
# samples, including sectors stored at different sampling resolutions.
# This catches a wrong decoder even if source-to-output comparisons pass.
leaves = {}
for node in converted.nodes:
    if node["depth"] == 6:
        values = array("I")
        values.frombytes(converted.data[node["samples"]:node["samples"]+node["size"]**2*4])
        leaves[tuple(node["values"][2:4])] = (node, values)
seam_count = 0
max_seam = 0
max_bounds_error = 0
max_full_resolution_bounds_error = 0
for (x, y), (node, values) in leaves.items():
    size = node["size"]
    def height(n, a, i):
        return n["values"][8] + (a[i] >> 20)*n["values"][9]
    bounds_error = abs(max(height(node, values, i) for i in range(len(values))) - node["values"][7])
    max_bounds_error = max(max_bounds_error, bounds_error)
    if size == 65:
        max_full_resolution_bounds_error = max(max_full_resolution_bounds_error, bounds_error)
    for dx, dy in ((64, 0), (0, 64)):
        other = leaves.get((x+dx, y+dy))
        if other is None:
            continue
        neighbor, samples = other
        other_size = neighbor["size"]
        step = math.lcm(64//(size-1), 64//(other_size-1))
        for distance in range(0, 65, step):
            a = distance*(size-1)//64
            b = distance*(other_size-1)//64
            i = (size-1)*size+a if dx else a*size+size-1
            j = b if dx else b*other_size
            max_seam = max(max_seam, abs(height(node, values, i)-height(neighbor, samples, j)))
            seam_count += 1
# Source sector edges can differ by one encoded height step. Quantizing their
# origins must not be mistaken for guaranteeing sub-millimetre edge agreement.
assert max_seam <= 0.051, max_seam
# Authored bounds retain the unquantized origin and maximum. Origin rounding
# and sample quantization can each contribute one 5 cm step. Reduced-resolution
# sectors may additionally omit the original maximum, so constrain full grids.
assert max_full_resolution_bounds_error <= 0.101, max_full_resolution_bounds_error

for payload in (source.data[:-1], bytes([99])+source.data[1:]):
    try:
        parse(payload)
    except ValueError:
        pass
    else:
        raise AssertionError("Unsupported/truncated input was accepted")
result = {"level": args.level, "nodes": len(source.nodes), "height_and_surface_samples_checked": checked,
          "geometry_error_mapping_checked": True,
          "max_height_difference_metres": max_error,
          "shared_boundary_samples_checked": seam_count,
          "max_sector_seam_metres": max_seam,
          "max_height_bound_difference_metres": max_bounds_error,
          "max_full_resolution_bound_difference_metres": max_full_resolution_bounds_error,
          "palette_bytes_preserved": True, "truncated_and_unknown_version_rejected": True,
          "runtime_compatibility": "separate test required"}
Path("reports").mkdir(exist_ok=True)
Path("reports/conversion-checks.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
