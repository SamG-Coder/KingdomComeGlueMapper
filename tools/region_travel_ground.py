"""Ground a rigid station on the installed, converted heightfield."""
import math
import struct
import numpy as np
from compiled_terrain import parse
from clothing_regions import read_chunks


class Heightfield:
    def __init__(self, blob):
        self.terrain = parse(blob)
        if self.terrain.version != 29:
            raise ValueError('Station placement requires the converted target terrain')
        self.leaves = {tuple(n['values'][2:4]): n for n in self.terrain.nodes if n['depth'] == 6}

    def __call__(self, x, y):
        key = (math.floor(x / 64) * 64, math.floor(y / 64) * 64)
        n = self.leaves.get(key)
        if n is None or n['size'] < 2:
            raise ValueError(f'No terrain samples at {x}, {y}')
        size = n['size']; u = (x-key[0])*(size-1)/64; v = (y-key[1])*(size-1)/64
        i = min(int(u), size-2); j = min(int(v), size-2); u -= i; v -= j
        def h(a, b):
            sample = struct.unpack_from('<I', self.terrain.data, n['samples']+4*(a*size+b))[0]
            return n['values'][8] + (sample >> 20)*n['values'][9]
        return (1-u)*(1-v)*h(i,j)+u*(1-v)*h(i+1,j)+(1-u)*v*h(i,j+1)+u*v*h(i+1,j+1)


def wheel_bottom(blob):
    meshes = [c for c in read_chunks(blob) if c.kind == 0x1000]
    if not meshes or any(c.version not in (0x800,0x801) or len(c.data)!=264 for c in meshes):
        raise ValueError('Unsupported cart wheel bounds')
    return min(struct.unpack_from('<6f', c.data,108)[2] for c in meshes)


def fit_cart(origin, yaw, contacts, height, qmul, rotate):
    """Fit one rigid transform to all four wheels, never move individual parts."""
    if len(contacts)!=4:raise ValueError('Expected four wheel contact points')
    yawq=(math.cos(yaw/2),0,0,math.sin(yaw/2))
    def orientation(roll,pitch):
        return qmul(yawq,qmul((math.cos(roll/2),math.sin(roll/2),0,0),
                             (math.cos(pitch/2),0,math.sin(pitch/2),0)))
    def residual(p):
        q=orientation(*p[:2]); result=[]
        for c in contacts:
            v=rotate(q,c);x=origin[0]+v[0];y=origin[1]+v[1]
            result.append(origin[2]+p[2]+v[2]-height(x,y))
        return np.array(result)
    p=np.zeros(3)
    for _ in range(10):
        r=residual(p);eps=1e-5
        jac=np.column_stack([(residual(p+np.eye(3)[i]*eps)-r)/eps for i in range(3)])
        delta=np.linalg.lstsq(jac,-r,rcond=None)[0];p+=delta
        if np.linalg.norm(delta)<1e-6:break
    errors=residual(p)
    if max(abs(p[0]),abs(p[1]))>math.radians(25) or max(abs(errors))>.15:
        raise ValueError('Cart footprint is too steep or uneven; choose another position')
    return (origin[0],origin[1],origin[2]+float(p[2])),orientation(*p[:2]),list(map(float,errors))
