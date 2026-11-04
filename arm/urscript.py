"""URScript programs for a UR3e controller.

Primary interface, port 30001, as documented by Universal Robots.
A program must end with a newline. stopj is a software stop: the teach
pendant emergency stop remains the safety stop.

Motion speed is capped at 20% of the UR3e manual limits: 180 deg/s on
the base, shoulder and elbow, 360 deg/s on the wrists, about 3 m/s at
the tool. movej has one speed for every joint, so that cap is 20% of
the slowest joint.
"""

from __future__ import annotations

import math
import socket

PRIMARY_PORT = 30001
SPEED_LIMIT = 0.20
MAX_JOINT_SPEED = math.pi
MAX_TCP_SPEED = 3.0


def _limited(value: float, maximum: float) -> float:
    cap = SPEED_LIMIT * maximum
    if value > cap:
        raise ValueError(f"velocità {value} oltre il {SPEED_LIMIT:.0%} del limite UR3e ({maximum})")
    return value


# Kept under the 20% cap. Joints: 0.4 rad/s is about 13% of 180 deg/s.
# Tool: 0.10 m/s is about 3% of 3 m/s.
JOINT_ACCELERATION = 0.8
JOINT_VELOCITY = _limited(0.4, MAX_JOINT_SPEED)
TCP_ACCELERATION = 0.2
TCP_VELOCITY = _limited(0.10, MAX_TCP_SPEED)
STOP_ACCELERATION = 2.0


def _nums(values: tuple[float, ...] | list[float]) -> str:
    return ", ".join(f"{value:.6f}" for value in values)


def movej_script(joints: list[float]) -> str:
    return (
        "def voice_move():\n"
        f"  movej([{_nums(joints)}], a={JOINT_ACCELERATION:.3f}, v={JOINT_VELOCITY:.3f})\n"
        "end\n"
    )


def movel_script(pose: tuple[float, float, float, float, float, float]) -> str:
    return (
        "def voice_move():\n"
        f"  movel(p[{_nums(pose)}], a={TCP_ACCELERATION:.3f}, v={TCP_VELOCITY:.3f})\n"
        "end\n"
    )


def stopj_script() -> str:
    return f"def voice_stop():\n  stopj({STOP_ACCELERATION:.1f})\nend\n"


def send(ip: str, script: str, timeout: float = 2.0) -> None:
    with socket.create_connection((ip, PRIMARY_PORT), timeout=timeout) as sock:
        sock.sendall(script.encode("utf-8"))
