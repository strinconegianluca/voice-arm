"""URScript programs for a UR3e controller.

Primary interface, port 30001, as documented by Universal Robots.
A program must end with a newline. stopj is a software stop: the teach
pendant emergency stop remains the safety stop.
"""

from __future__ import annotations

import socket

PRIMARY_PORT = 30001
# Demo speeds, far below the UR3e maxima (joints 180 or 360 deg/s, TCP 3 m/s).
JOINT_ACCELERATION = 0.8
JOINT_VELOCITY = 0.4
TCP_ACCELERATION = 0.2
TCP_VELOCITY = 0.10
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
