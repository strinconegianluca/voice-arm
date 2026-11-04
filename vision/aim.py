"""Turn a phone picture into a UR3e target on the table.

The phone is held above the table. The top of the picture is the far side
of the table (away from the robot base, +X). The right of the picture is
the robot's left (+Y).

The arm's own tool position is known from the joints. The picture gives
the end of the arm and the object, so the move is the difference between
those two pixels, added to the real tool position. A shift of the whole
camera cancels out.
"""

from __future__ import annotations

from dataclasses import dataclass

# Table rectangle covered by the camera, metres, in the UR3e base frame.
X_FAR = 0.42
X_NEAR = 0.18
Y_LEFT = -0.22
Y_RIGHT = 0.22
POINT_Z = 0.20


@dataclass
class Sight:
    ok: bool
    report: str
    xyz: tuple[float, float, float] | None = None


def image_to_table(u: float, v: float) -> tuple[float, float]:
    """Normalized pixels, origin top-left, into the table plane."""
    x = X_FAR + v * (X_NEAR - X_FAR)
    y = Y_LEFT + u * (Y_RIGHT - Y_LEFT)
    return x, y


def aim_from_pixels(
    tcp_xy: tuple[float, float],
    tool_uv: tuple[float, float] | None,
    object_uv: tuple[float, float],
) -> tuple[float, float, float]:
    obj_x, obj_y = image_to_table(*object_uv)
    if tool_uv is None:
        return obj_x, obj_y, POINT_Z
    tool_x, tool_y = image_to_table(*tool_uv)
    return (
        tcp_xy[0] + (obj_x - tool_x),
        tcp_xy[1] + (obj_y - tool_y),
        POINT_Z,
    )


def describe_aim(label: str, xyz: tuple[float, float, float], *, saw_tool: bool) -> str:
    forward_mm = int(round(xyz[0] * 1000))
    side_mm = int(round(xyz[1] * 1000))
    side = "a sinistra" if side_mm > 15 else "a destra" if side_mm < -15 else "al centro"
    how = "rispetto al braccio" if saw_tool else "nel riquadro, senza vedere il braccio"
    return (
        f"Vedo {label}, {how}. "
        f"Mi sposto a {forward_mm} mm in avanti, {abs(side_mm)} mm {side}. Confermi?"
    )
