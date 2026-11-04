"""UR3e model and skill motions.

Kinematics are the official e-Series DH parameters published by Universal Robots
for the UR3e. Joint limits and reach come from the UR3e user manual: six
revolute joints, elbow ±160°, every other joint ±360° except wrist 3 which
rotates freely, reach 500 mm, payload 3 kg.

Skills are not free trajectories. go_to_pose and indicate are movej to a
solved joint target. lift, put_down and push are one short movel.
"""

from __future__ import annotations

import asyncio
import math
import os
import threading
import time

from arm.urscript import (
    JOINT_VELOCITY,
    TCP_VELOCITY,
    movel_script,
    movej_script,
    send,
    stopj_script,
)

# Official UR3e DH parameters, metres and radians.
# https://www.universal-robots.com/articles/ur/application-installation/dh-parameters-for-calculations-of-kinematics-and-dynamics/
_A = (0.0, -0.24355, -0.2132, 0.0, 0.0, 0.0)
_D = (0.15185, 0.0, 0.0, 0.13105, 0.08535, 0.0921)
_ALPHA = (math.pi / 2, 0.0, 0.0, math.pi / 2, -math.pi / 2, 0.0)

REACH_M = 0.500
ELBOW_LIMIT = math.radians(160)
JOINT_LIMITS = (
    (-math.tau, math.tau),
    (-math.tau, math.tau),
    (-ELBOW_LIMIT, ELBOW_LIMIT),
    (-math.tau, math.tau),
    (-math.tau, math.tau),
    None,
)
# Base, shoulder and elbow 180 deg/s. The three wrist joints 360 deg/s.
MAX_JOINT_SPEED = (math.pi, math.pi, math.pi, math.tau, math.tau, math.tau)

LIFT_M = 0.08
PUSH_M = 0.05
HOVER_M = 0.08

POSES = ("home", "sinistra", "destra", "centro", "vassoio")

# TCP targets in the base frame, metres. Tool flange points down.
_TARGETS = {
    "home": (0.20, 0.00, 0.30),
    "centro": (0.30, 0.00, 0.12),
    "sinistra": (0.28, 0.15, 0.12),
    "destra": (0.28, -0.15, 0.12),
    "vassoio": (0.34, 0.00, 0.08),
}
_PUSH = {
    "sinistra": (0.0, PUSH_M, 0.0),
    "destra": (0.0, -PUSH_M, 0.0),
    "centro": (PUSH_M, 0.0, 0.0),
    "vassoio": (PUSH_M, 0.0, 0.0),
}


def _rot_x(angle: float) -> tuple[tuple[float, float, float], ...]:
    c, s = math.cos(angle), math.sin(angle)
    return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))


def _rot_y(angle: float) -> tuple[tuple[float, float, float], ...]:
    c, s = math.cos(angle), math.sin(angle)
    return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))


def _mul(a, b):
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4))
        for i in range(4)
    )


def _transpose(r):
    return tuple(tuple(r[j][i] for j in range(3)) for i in range(3))


def _mul33(a, b):
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )


def forward(q: list[float]) -> tuple[tuple[tuple[float, float, float], ...], tuple[float, float, float]]:
    """Tool-flange pose of a UR3e. Returns rotation and xyz in the base frame."""
    transform = (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    for i in range(6):
        ct, st = math.cos(q[i]), math.sin(q[i])
        ca, sa = math.cos(_ALPHA[i]), math.sin(_ALPHA[i])
        link = (
            (ct, -st * ca, st * sa, _A[i] * ct),
            (st, ct * ca, -ct * sa, _A[i] * st),
            (0.0, sa, ca, _D[i]),
            (0.0, 0.0, 0.0, 1.0),
        )
        transform = _mul(transform, link)
    rotation = tuple(tuple(transform[i][j] for j in range(3)) for i in range(3))
    position = (transform[0][3], transform[1][3], transform[2][3])
    return rotation, position


def rotation_vector(rotation) -> tuple[float, float, float]:
    trace = rotation[0][0] + rotation[1][1] + rotation[2][2]
    angle = math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0)))
    if angle < 1e-8:
        return (0.0, 0.0, 0.0)
    if math.pi - angle < 1e-4:
        return (math.pi, 0.0, 0.0)
    scale = angle / (2.0 * math.sin(angle))
    return (
        scale * (rotation[2][1] - rotation[1][2]),
        scale * (rotation[0][2] - rotation[2][0]),
        scale * (rotation[1][0] - rotation[0][1]),
    )


def within_reach(xyz: tuple[float, float, float]) -> bool:
    """500 mm spherical reach measured from the shoulder, joint 1."""
    shoulder_z = _D[0]
    distance = math.dist(xyz, (0.0, 0.0, shoulder_z))
    return distance <= REACH_M


def _clip(q: list[float]) -> list[float]:
    clipped = []
    for value, limits in zip(q, JOINT_LIMITS):
        if limits is None:
            clipped.append((value + math.pi) % math.tau - math.pi)
        else:
            clipped.append(min(limits[1], max(limits[0], value)))
    return clipped


def _error(q: list[float], xyz: tuple[float, float, float], orientation) -> list[float]:
    rotation, position = forward(q)
    pos = [xyz[i] - position[i] for i in range(3)]
    relative = _mul33(orientation, _transpose(rotation))
    return pos + list(rotation_vector(relative))


def inverse(
    xyz: tuple[float, float, float],
    seeds: list[list[float]],
    orientations: tuple | None = None,
) -> list[float] | None:
    """Damped least squares IK. None if the UR3e cannot reach the pose.

    Named poses leave the tool flange pointing down. A movel keeps the
    current flange orientation, which is what the controller does.
    """
    if not within_reach(xyz):
        return None
    if orientations is None:
        orientations = (_rot_x(math.pi), _rot_y(math.pi))
    best: list[float] | None = None
    best_cost = 1e9
    closest = 1e9
    for orientation in orientations:
        for seed in seeds:
            q = _clip(seed[:])
            for _ in range(100):
                err = _error(q, xyz, orientation)
                closest = min(closest, math.dist(err, (0, 0, 0, 0, 0, 0)))
                if math.dist(err[:3], (0, 0, 0)) < 1e-4 and math.dist(err[3:], (0, 0, 0)) < 1e-3:
                    cost = sum((q[i] - seed[i]) ** 2 for i in range(6))
                    if cost < best_cost:
                        best, best_cost = q, cost
                    break
                jac = []
                for joint in range(6):
                    bumped = q[:]
                    bumped[joint] += 1e-6
                    bumped_err = _error(bumped, xyz, orientation)
                    jac.append([(bumped_err[row] - err[row]) / 1e-6 for row in range(6)])
                # dq = J^T (J J^T + lambda I)^-1 e, solved with Gauss-Jordan on 6x6.
                a = [[sum(jac[c][r] * jac[c][k] for c in range(6)) for k in range(6)] for r in range(6)]
                for i in range(6):
                    a[i][i] += 1e-3
                    a[i].append(-err[i])
                for col in range(6):
                    pivot = max(range(col, 6), key=lambda row: abs(a[row][col]))
                    a[col], a[pivot] = a[pivot], a[col]
                    if abs(a[col][col]) < 1e-12:
                        break
                    scale = a[col][col]
                    a[col] = [value / scale for value in a[col]]
                    for row in range(6):
                        if row != col:
                            factor = a[row][col]
                            a[row] = [a[row][k] - factor * a[col][k] for k in range(7)]
                else:
                    step = [sum(jac[joint][row] * a[row][6] for row in range(6)) for joint in range(6)]
                    norm = math.sqrt(sum(value * value for value in step)) or 1.0
                    gain = min(1.0, 0.4 / norm)
                    q = _clip([q[i] + gain * step[i] for i in range(6)])
                    continue
                break
    inverse.last_error = closest  # type: ignore[attr-defined]
    return best


# Base near pi points the arm forward, +X, which is the table side.
_SEEDS = [
    [math.pi, -1.2, 1.2, -1.57, -1.57, 0.0],
    [math.pi, -1.4, 1.5, -1.7, -1.57, 0.6],
    [math.pi, -1.0, 1.0, -1.5, -1.57, -0.6],
    [2.6, -1.3, 1.4, -1.6, -1.57, 0.8],
    [3.6, -1.5, 1.6, -1.6, -1.57, -0.8],
    [math.pi, -1.8, 2.0, -1.8, -1.57, 0.0],
]


def _solve_named(name: str, xyz: tuple[float, float, float]) -> list[float]:
    solution = inverse(xyz, _SEEDS)
    if solution is None:
        raise RuntimeError(
            f"UR3e non raggiunge la posa {name} {xyz} (errore minimo {inverse.last_error:.4f})"
        )
    return solution


JOINT_POSES = {name: _solve_named(name, xyz) for name, xyz in _TARGETS.items()}
HOVER_POSES = {
    name: _solve_named(name + " hover", (xyz[0], xyz[1], xyz[2] + HOVER_M))
    for name, xyz in _TARGETS.items()
    if name != "home"
}


class UR3e:
    def __init__(self, move_seconds: float | None = None, ip: str | None = None) -> None:
        self.q = JOINT_POSES["home"][:]
        self.pose = "home"
        self.holding_tray = False
        self.status = "idle"
        self.log: list[str] = []
        self._move_seconds = move_seconds
        self._ip = ip
        self._stop = threading.Event()
        self._lock = asyncio.Lock()

    @property
    def tcp(self) -> tuple[float, float, float]:
        return forward(self.q)[1]

    def request_stop(self) -> None:
        moving = self.status == "moving"
        self._stop.set()
        self.status = "stopping"
        self._note("STOP")
        if moving and self._ip:
            self._transmit(stopj_script(), "stopj")

    def snapshot(self) -> dict:
        joints = [round(math.degrees(value), 1) for value in self.q]
        tcp = [round(value * 1000, 1) for value in self.tcp]
        return {
            "model": "UR3e",
            "pose": self.pose,
            "holding_tray": self.holding_tray,
            "status": self.status,
            "joints_deg": joints,
            "tcp_mm": tcp,
        }

    async def go_to_pose(self, target: str) -> dict:
        if target not in JOINT_POSES:
            return self._reject(f"Posa '{target}' sconosciuta.")
        result = await self._movej(JOINT_POSES[target], f"movej verso {target}", target)
        return result

    async def indicate(self, target: str) -> dict:
        if target not in HOVER_POSES:
            return self._reject(f"Non posso indicare '{target}'.")
        return await self._movej(HOVER_POSES[target], f"movej indico {target}", target)

    async def lift_tray(self) -> dict:
        if self.holding_tray:
            return self._reject("Il vassoio è già in mano.")
        approach = await self._movej(JOINT_POSES["vassoio"], "movej sul vassoio", "vassoio")
        if not approach.get("ok"):
            return approach
        lifted = await self._movel((0.0, 0.0, LIFT_M), "movel sollevo il vassoio", reset_stop=False)
        if lifted.get("ok"):
            self.holding_tray = True
            lifted["holding_tray"] = True
            lifted["report"] = "Vassoio sollevato di 80 mm."
        return lifted

    async def put_down(self) -> dict:
        if not self.holding_tray:
            return self._reject("Non ho un vassoio in mano.")
        placed = await self._movel((0.0, 0.0, -LIFT_M), "movel appoggio il vassoio")
        if placed.get("ok"):
            self.holding_tray = False
            placed["holding_tray"] = False
            placed["pose"] = "vassoio"
            self.pose = "vassoio"
            placed["report"] = "Vassoio appoggiato."
        return placed

    async def push(self, target: str = "centro") -> dict:
        delta = _PUSH.get(target)
        if delta is None:
            return self._reject(f"Non posso spingere verso '{target}'.")
        return await self._movel(delta, f"movel spingo verso {target}")

    async def _movej(self, target: list[float], label: str, pose_name: str, reset_stop: bool = True) -> dict:
        script = movej_script(target)
        duration = self._duration(max(abs(target[i] - self.q[i]) for i in range(6)) / JOINT_VELOCITY)

        def step(fraction: float) -> None:
            self.q = [self.q_start[i] + (target[i] - self.q_start[i]) * fraction for i in range(6)]
            if fraction == 1.0:
                self.pose = pose_name

        result = await self._motion(label, script, duration, step, reset_stop)
        if result.get("ok"):
            result["pose"] = pose_name
            result["report"] = f"Fatto: {label}."
        return result

    async def _movel(self, delta: tuple[float, float, float], label: str, reset_stop: bool = True) -> dict:
        rotation, start = forward(self.q)
        goal = tuple(start[i] + delta[i] for i in range(3))
        if not within_reach(goal):
            return self._reject("Il punto è fuori dallo sbraccio di 500 mm del UR3e.")
        goal_q = inverse(goal, [self.q] + _SEEDS, orientations=(rotation,))
        if goal_q is None:
            return self._reject("Il UR3e non raggiunge quel punto con il polso verso il basso.")
        pose = goal + rotation_vector(rotation)
        script = movel_script(pose)
        distance = math.dist(start, goal)
        duration = self._duration(distance / TCP_VELOCITY)

        def step(fraction: float) -> None:
            point = tuple(start[i] + (goal[i] - start[i]) * fraction for i in range(3))
            solved = inverse(point, [self.q] + _SEEDS, orientations=(rotation,))
            if solved is not None:
                self.q = solved
            if fraction == 1.0:
                self.pose = "custom"

        result = await self._motion(label, script, duration, step, reset_stop)
        if result.get("ok"):
            result["report"] = f"Fatto: {label}."
        return result

    async def _motion(self, label: str, script: str, duration: float, step, reset_stop: bool) -> dict:
        async with self._lock:
            if reset_stop:
                self._stop.clear()
            if self._stop.is_set():
                self.status = "stopped"
                return {"ok": False, "stopped": True, "report": "Stop già richiesto.", **self.snapshot()}
            if self._ip:
                try:
                    self._transmit(script, label)
                except OSError as exc:
                    return self._reject(f"Controller UR3e non raggiungibile: {exc}")
            self.status = "moving"
            self.q_start = self.q[:]
            self._note(f"start {label}")
            steps = max(1, int(duration / 0.05))
            for index in range(steps):
                if self._stop.is_set():
                    self.status = "stopped"
                    self._note(f"halted during {label}")
                    return {
                        "ok": False,
                        "stopped": True,
                        "report": f"stopj: movimento interrotto durante '{label}'.",
                        **self.snapshot(),
                    }
                step((index + 1) / steps)
                await asyncio.sleep(duration / steps)
            self.status = "idle"
            self._note(f"done {label}")
            return {"ok": True, "stopped": False, **self.snapshot()}

    def _duration(self, seconds: float) -> float:
        if self._move_seconds is not None:
            return self._move_seconds
        return max(0.4, seconds)

    def _transmit(self, script: str, label: str) -> None:
        assert self._ip is not None
        send(self._ip, script)
        self._note(f"URScript {label} -> {self._ip}:{30001}")

    def _reject(self, reason: str) -> dict:
        self._note(f"rejected {reason}")
        return {"ok": False, "stopped": False, "report": reason, **self.snapshot()}

    def _note(self, message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {message} | pose={self.pose} tray={self.holding_tray}"
        self.log.append(line)
        print(f"[ur3e] {line}")


def make_arm() -> UR3e:
    ip = os.environ.get("UR3E_IP", "").strip() or None
    arm = UR3e(ip=ip)
    if ip:
        print(f"UR3e su {ip}:30001. Movimenti lenti. Lo stop vocale invia stopj; l'e-stop del pendant resta quello di sicurezza.")
    else:
        print("UR3e simulato, sbraccio 500 mm. Imposta UR3E_IP nel .env per il controller vero.")
    return arm
