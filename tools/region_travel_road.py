"""Choose arrival positions inside the converted world's actual road meshes."""
import math
import struct
import numpy as np

from water_volumes import read_water


def inside_mesh(points, triangles):
    """Every footprint sample must be covered by a non-degenerate triangle."""
    a, b, c = (triangles[:, i, :2] for i in range(3))
    u, v = b-a, c-a
    denominator = u[:, 0]*v[:, 1]-u[:, 1]*v[:, 0]
    valid = abs(denominator) > 1e-8
    if not valid.any():
        return False
    a, u, v, denominator = a[valid], u[valid], v[valid], denominator[valid]
    for point in points:
        delta = np.asarray(point[:2])-a
        s = (delta[:, 0]*v[:, 1]-delta[:, 1]*v[:, 0])/denominator
        t = (u[:, 0]*delta[:, 1]-u[:, 1]*delta[:, 0])/denominator
        if not ((s >= -1e-5) & (t >= -1e-5) & (s+t <= 1+1e-5)).any():
            return False
    return True


def road_spawn_points(terrain, arrival, obstacles, height):
    """Sample road centrelines, checking a horse-sized footprint and clearance.

    KCD2 road records contain half-float vertices relative to their bounds centre,
    triangle indices and a final array of paired world-space road-edge points.
    Candidates come from those edge pairs, then are checked against the rendered
    triangles. Empty/off-road bounds cannot masquerade as a usable road.
    ``obstacles`` is a sequence of (world position, clearance in metres).
    """
    if terrain.version != 29:
        raise ValueError('Road arrival placement requires converted KCD2 terrain')
    origin = np.asarray(arrival[:2], dtype=float)
    candidates = []

    def visit(data, offset, size, kind):
        if kind != 11:
            return
        bounds = np.asarray(struct.unpack_from('<6f', data, offset+4))
        delta = np.maximum(np.maximum(bounds[:2]-origin, origin-bounds[3:5]), 0)
        if np.linalg.norm(delta) > 14:
            return
        v, i, t, p, s = struct.unpack_from('<5I', data, offset+84)
        if v < 3 or i < 3 or i % 3 or t != v or s < 4 or s % 2:
            raise ValueError('Unsupported nearby road geometry')
        indices_at = offset+112+v*12
        edges_at = ((indices_at+i*2+3) & ~3)+t*16+p*40
        if edges_at+s*12 > offset+size:
            raise ValueError('Road edge array exceeds its record')
        vertices = np.frombuffer(data, '<f2', count=v*6, offset=offset+112).reshape(v, 6)[:, :3].astype(float)
        vertices += (bounds[:3]+bounds[3:])/2
        indices = np.frombuffer(data, '<u2', count=i, offset=indices_at)
        if indices.max() >= v or not np.isfinite(vertices).all():
            raise ValueError('Invalid road triangle data')
        triangles = vertices[indices].reshape(-1, 3, 3)
        edges = np.frombuffer(data, '<f4', count=s*3, offset=edges_at).reshape(-1, 2, 3).astype(float)
        if not np.isfinite(edges).all():
            raise ValueError('Non-finite road edge data')
        centres = edges.mean(axis=1)
        widths = np.linalg.norm(edges[:, 0, :2]-edges[:, 1, :2], axis=1)
        material = struct.unpack_from('<i', data, offset+108)[0]
        paths = terrain.tables['materials']['paths']
        if not 0 <= material < len(paths):
            return
        for j, (a, b) in enumerate(zip(centres[:-1], centres[1:])):
            direction = b[:2]-a[:2]
            length = np.linalg.norm(direction)
            if length < .01 or min(widths[j:j+2]) < 2.4:
                continue
            direction /= length
            across = np.array([-direction[1], direction[0]])
            for fraction in np.linspace(0, 1, math.ceil(length/.5)+1):
                xy = a[:2]*(1-fraction)+b[:2]*fraction
                distance = np.linalg.norm(xy-origin)
                if not 5 <= distance <= 12:
                    continue
                if any(np.linalg.norm(xy-np.asarray(pt[:2])) < clearance for pt, clearance in obstacles):
                    continue
                footprint = [xy+direction*forward+across*side
                             for forward in (-1.5, 0, 1.5) for side in (-.7, 0, .7)]
                if not inside_mesh(footprint, triangles):
                    continue
                heights = [height(*point) for point in footprint]
                if max(heights)-min(heights) > .9:
                    continue
                position = [float(xy[0]), float(xy[1]), float(height(*xy)+.25)]
                candidates.append((abs(distance-6), {
                    'position': position, 'direction': direction.tolist(),
                    'road_record_offset': offset, 'material': paths[material],
                    'road_width': float(min(widths[j:j+2])),
                    'footprint_size': [3., 1.4], 'footprint_height_range': max(heights)-min(heights),
                }))

    read_water(terrain, visit)
    selected = []
    for _, candidate in sorted(candidates, key=lambda row: (row[0], row[1]['road_record_offset'])):
        if all(math.dist(candidate['position'][:2], old['position'][:2]) >= 2 for old in selected):
            selected.append(candidate)
        if len(selected) == 6:
            break
    if not selected:
        raise ValueError('No road footprint with horse/coach clearance near the arrival')
    return {'arrival': list(arrival), 'candidates': selected,
            'placement_source': 'converted road edge pairs and triangle coverage',
            'runtime_verified': False}
