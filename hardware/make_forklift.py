"""Passive UR3e forklift for the ISO 9409-1-50-4-M6 flange.

No motor. Two forks slide under an object, then the arm lifts.
Print with the flat face on the bed, boss and stops pointing up.

Screws: four M6, length 12 mm. They pass through this 6 mm plate and
enter the flange about 6 mm. The UR manual forbids bolts that reach
more than 8 mm into the flange, so do not use anything longer than
M6x14, and do not drill the holes deeper.

Regenerate:
    .venv\\Scripts\\python.exe hardware\\make_forklift.py
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np
import mapbox_earcut as earcut

OUT = Path(__file__).with_name("ur3e-forklift.stl")

# Flange: EN ISO 9409-1-50-4-M6. Collar is 31.50 mm H7, about 3.5 mm deep.
PLATE_R = 36.0
FORK_T = 4.0
PLATE_T = 6.0
BOSS_R = 15.6
BOSS_H = 2.6
HOLE_R = 3.3
PCD = 50.0
HOLE_ANGLES = (45.0, 135.0, 225.0, 315.0)

FORK_INNER = 13.0
FORK_OUTER = 27.0
FORK_TIP = 120.0
STOP_H = 12.0

ARC_STEP = math.radians(4.0)


def circle(r: float, n: int, cw: bool, phase: float = 0.0) -> np.ndarray:
    angles = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False) + phase
    if cw:
        angles = -angles
    return np.stack([r * np.cos(angles), r * np.sin(angles)], axis=1)


def arc(r: float, a0: float, a1: float) -> np.ndarray:
    span = (a1 - a0) % (2.0 * math.pi)
    if span < 1e-9:
        span = 2.0 * math.pi
    n = max(2, int(math.ceil(span / ARC_STEP)))
    angles = a0 + np.linspace(0.0, span, n, endpoint=True)
    return np.stack([r * np.cos(angles), r * np.sin(angles)], axis=1)


def dedup(parts: list[np.ndarray]) -> np.ndarray:
    pts = np.concatenate(parts, axis=0)
    keep = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - keep[-1][0]) > 1e-6 or abs(p[1] - keep[-1][1]) > 1e-6:
            keep.append(p)
    if abs(keep[0][0] - keep[-1][0]) < 1e-6 and abs(keep[0][1] - keep[-1][1]) < 1e-6:
        keep.pop()
    return np.asarray(keep, dtype=np.float64)


def x_on_circle(y: float, r: float = PLATE_R) -> float:
    return math.sqrt(r * r - y * y)


def outline() -> np.ndarray:
    yo, yi, tip = FORK_OUTER, FORK_INNER, FORK_TIP
    a_top = math.atan2(yo, x_on_circle(yo))
    a_bot = math.atan2(-yo, x_on_circle(yo))
    a_in_bot = math.atan2(-yi, x_on_circle(yi))
    a_in_top = math.atan2(yi, x_on_circle(yi))
    return dedup(
        [
            arc(PLATE_R, a_top, a_bot + 2.0 * math.pi),
            np.array([[tip, -yo], [tip, -yi]], dtype=np.float64),
            arc(PLATE_R, a_in_bot, a_in_top),
            np.array([[tip, yi], [tip, yo]], dtype=np.float64),
        ]
    )


def rect(x0: float, y0: float, x1: float, y1: float, cw: bool) -> np.ndarray:
    pts = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64)
    return pts[::-1] if cw else pts


def bolt_holes(cw: bool) -> list[np.ndarray]:
    holes = []
    for deg in HOLE_ANGLES:
        a = math.radians(deg)
        c = circle(HOLE_R, 24, cw=cw)
        c[:, 0] += (PCD / 2.0) * math.cos(a)
        c[:, 1] += (PCD / 2.0) * math.sin(a)
        holes.append(c)
    return holes


def fork_stops() -> list[np.ndarray]:
    """Ribs on the fork roots, clear of the M6 holes."""
    return [
        rect(42.0, 15.0, 54.0, 25.0, cw=False),
        rect(42.0, -25.0, 54.0, -15.0, cw=False),
    ]


def center_stop() -> np.ndarray:
    return rect(18.0, -9.0, 33.0, 9.0, cw=False)


def triangulate(rings: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    verts = np.concatenate(rings, axis=0)
    ends = np.cumsum([len(r) for r in rings], dtype=np.uint32)
    idx = earcut.triangulate_float64(np.ascontiguousarray(verts), ends)
    if len(idx) < 3:
        raise RuntimeError("earcut returned no triangles")
    return verts, idx.reshape(-1, 3)


def cap(verts: np.ndarray, idx: np.ndarray, z: float, flip: bool) -> list[np.ndarray]:
    tris = []
    for i0, i1, i2 in idx:
        tri = [
            [verts[i0, 0], verts[i0, 1], z],
            [verts[i1, 0], verts[i1, 1], z],
            [verts[i2, 0], verts[i2, 1], z],
        ]
        if flip:
            tri[1], tri[2] = tri[2], tri[1]
        tris.append(np.asarray(tri, dtype=np.float64))
    return tris


def walls(rings: list[np.ndarray], z0: float, z1: float) -> list[np.ndarray]:
    tris = []
    for ring in rings:
        n = len(ring)
        for i in range(n):
            x0, y0 = ring[i]
            x1, y1 = ring[(i + 1) % n]
            a = [x0, y0, z0]
            b = [x1, y1, z0]
            c = [x1, y1, z1]
            d = [x0, y0, z1]
            tris.append(np.asarray([a, b, c], dtype=np.float64))
            tris.append(np.asarray([a, c, d], dtype=np.float64))
    return tris


def prism(outer: np.ndarray, z0: float, z1: float) -> list[np.ndarray]:
    """Open-bottom prism. outer is CCW; the caller cuts the same shape CW out of the face below."""
    verts, idx = triangulate([outer])
    return walls([outer], z0, z1) + cap(verts, idx, z1, flip=False)


def build() -> np.ndarray:
    body = outline()
    bolts_cw = bolt_holes(cw=True)
    seat = circle(PLATE_R - 1.0, 96, cw=False)
    seat_hole = circle(PLATE_R - 1.0, 96, cw=True)
    boss = circle(BOSS_R, 48, cw=False)
    boss_hole = circle(BOSS_R, 48, cw=True)
    stops = fork_stops()
    stops_cw = [s[::-1] for s in stops]
    lip = center_stop()
    lip_cw = lip[::-1]

    tris: list[np.ndarray] = []
    # Forks are FORK_T thick. The flange plate is thicker, so the disc
    # above the seat is a second layer. Bolt holes are one tube through
    # both layers; they are not cut into the fork top, which stops at the seat.
    bottom_v, bottom_i = triangulate([body, *bolts_cw])
    tris += cap(bottom_v, bottom_i, 0.0, flip=True)
    top_v, top_i = triangulate([body, seat_hole, *stops_cw])
    tris += cap(top_v, top_i, FORK_T, flip=False)
    tris += walls([body], 0.0, FORK_T)
    tris += walls(bolts_cw, 0.0, PLATE_T)

    disc_v, disc_i = triangulate([seat, *bolts_cw, boss_hole, lip_cw])
    tris += cap(disc_v, disc_i, PLATE_T, flip=False)
    tris += walls([seat], FORK_T, PLATE_T)

    tris += prism(boss, PLATE_T, PLATE_T + BOSS_H)
    for stop in stops:
        tris += prism(stop, FORK_T, FORK_T + STOP_H)
    tris += prism(lip, PLATE_T, PLATE_T + STOP_H)
    mesh = np.stack(tris, axis=0)
    keep = []
    for tri in mesh:
        area = np.linalg.norm(np.cross(tri[1] - tri[0], tri[2] - tri[0]))
        if area > 1e-8:
            keep.append(tri)
    return np.stack(keep, axis=0)


def signed_volume(tris: np.ndarray) -> float:
    return float(np.sum(np.einsum("ij,ij->i", tris[:, 0], np.cross(tris[:, 1], tris[:, 2]))) / 6.0)


def manifold_ok(tris: np.ndarray) -> tuple[bool, int]:
    edges: dict[tuple, int] = {}
    for tri in tris:
        keys = [tuple(np.round(p, 3)) for p in tri]
        for i in range(3):
            a, b = keys[i], keys[(i + 1) % 3]
            edges[tuple(sorted((a, b)))] = edges.get(tuple(sorted((a, b))), 0) + 1
    bad = sum(1 for n in edges.values() if n != 2)
    return bad == 0, bad


def write_stl(path: Path, tris: np.ndarray) -> None:
    header = b"UR3e ISO 9409-1-50-4-M6 passive forklift"
    with path.open("wb") as f:
        f.write(header.ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        for tri in tris:
            normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
            norm = np.linalg.norm(normal)
            if norm > 0:
                normal = normal / norm
            f.write(struct.pack("<3f", *normal))
            for p in tri:
                f.write(struct.pack("<3f", *p))
            f.write(struct.pack("<H", 0))


def main() -> None:
    tris = build()
    volume = signed_volume(tris)
    if volume < 0:
        tris = tris[:, ::-1, :]
        volume = -volume
    ok, bad = manifold_ok(tris)
    lo = tris.reshape(-1, 3).min(axis=0)
    hi = tris.reshape(-1, 3).max(axis=0)
    write_stl(OUT, tris)
    print(f"wrote {OUT}")
    print(f"triangles {len(tris)}")
    print(f"watertight {ok} bad_edges {bad}")
    print(f"volume_cm3 {volume / 1000.0:.2f}")
    print(f"bbox_min {lo}")
    print(f"bbox_max {hi}")
    if not ok or volume < 1000:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
