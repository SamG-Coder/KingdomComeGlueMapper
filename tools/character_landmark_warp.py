"""Shared anatomical rest-pose conversion for a character and its wardrobe.

Bone axis conventions are not anatomical rotations (the female wrist axes
change by nearly a right angle between games). Fit joint *positions* instead.
One smooth field is shared by every part, independent of its skin weights.
"""
import re

import numpy as np

from audit_character_bodies import bone_table
from clothing_regions import read_chunks


class LandmarkWarp:
    def __init__(self, source, target):
        self.source = np.asarray(source, dtype=float)
        target = np.asarray(target, dtype=float)
        if self.source.shape != target.shape or self.source.ndim != 2 or self.source.shape[1] != 3:
            raise ValueError('Expected matching 3D landmarks')
        if not np.isfinite(self.source).all() or not np.isfinite(target).all():
            raise ValueError('Nonfinite landmarks')
        n = len(source)
        affine = np.column_stack((np.ones(n), self.source))
        distances = np.linalg.norm(self.source[:, None] - self.source[None, :], axis=2)
        if n < 4 or np.linalg.matrix_rank(affine) != 4:
            raise ValueError('Landmarks must span three dimensions')
        if np.any((distances + np.eye(n)) < 1e-6):
            raise ValueError('Duplicate landmarks')
        # The 3D biharmonic radial basis r plus an affine term reproduces rigid
        # motion and scales without interpreting the rig's local axis choices.
        system = np.block([[distances, affine], [affine.T, np.zeros((4, 4))]])
        self.coefficients = np.linalg.solve(system, np.vstack((target-self.source, np.zeros((4, 3)))))
        self.target = target
        moved, _ = self.transform(self.source)
        if not np.allclose(moved, target, atol=1e-7):
            raise ValueError('Landmark fit failed')

    def transform(self, positions):
        positions = np.asarray(positions, dtype=float)
        result = np.empty_like(positions)
        jacobian = np.empty((len(positions), 3, 3))
        radial, affine = self.coefficients[:-4], self.coefficients[-4:]
        for start in range(0, len(positions), 2048):
            p = positions[start:start+2048]
            delta = p[:, None] - self.source[None, :]
            distance = np.linalg.norm(delta, axis=2)
            result[start:start+len(p)] = p + distance @ radial + np.column_stack((np.ones(len(p)), p)) @ affine
            gradient = delta / np.maximum(distance[:, :, None], 1e-12)
            jacobian[start:start+len(p)] = np.eye(3) + affine[1:].T + np.einsum('nki,kj->nji', gradient, radial)
        if not np.isfinite(result).all() or np.any(np.linalg.det(jacobian) <= .05):
            raise ValueError('Anatomical conversion folds or collapses the surface')
        return result, jacobian


def anatomical_warp(source_skeleton, target_skeleton, mapping):
    """Use deforming anatomical joints, excluding IK targets and roll helpers."""
    source = bone_table(read_chunks(source_skeleton))
    target = {b['name'].casefold(): b for b in bone_table(read_chunks(target_skeleton))}
    pattern = re.compile(r'^(Hips|Spine\d*|Neck\d*|Head|(Left|Right)(Shoulder|Arm|ForeArm|Hand|'
                         r'InHand(Ring|Pinky)|Hand(Thumb|Index|Middle|Ring|Pinky)\d+|UpLeg|Leg|Foot|ToeBase))$')
    a, b, names = [], [], []
    for joint in source:
        name = joint['name']
        other = target.get(mapping.get(name, name).casefold())
        if not pattern.fullmatch(name) or other is None:
            continue
        point = np.asarray(joint['bind']).reshape(3, 4)[:, 3]
        destination = np.asarray(other['bind']).reshape(3, 4)[:, 3]
        if any(np.linalg.norm(point - previous) < 1e-6 for previous in a):
            continue
        a.append(point); b.append(destination); names.append(name)
    if 'Head' in names:
        # Keep facial proportions around the head anchor rather than allowing
        # hand/finger corrections to extrapolate into the face and hair.
        i = names.index('Head')
        for axis in np.eye(3):
            for sign in (-1, 1):
                a.append(a[i] + axis*.12*sign); b.append(b[i] + axis*.12*sign)
    warp = LandmarkWarp(a, b)
    warp.joint_names = names
    return warp
