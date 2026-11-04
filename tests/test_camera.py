import asyncio
import math

from arm.ur3e import UR3e, forward
from vision.aim import aim_from_pixels, image_to_table
from vision.frames import latest, update
from voice.commands import apply_command
from voice.skills import SkillGate


def test_relative_aim_uses_the_arm_and_the_object():
    center = image_to_table(0.5, 0.5)
    assert math.dist(center, (0.30, 0.0)) < 0.001

    # Object is to the right in the picture, gripper is in the middle.
    # Right of the picture is +Y, so the tool moves left of its real position.
    xyz = aim_from_pixels((0.25, -0.05), (0.5, 0.5), (0.75, 0.5))
    assert abs(xyz[0] - 0.25) < 0.001
    assert xyz[1] > -0.05
    assert xyz[2] == 0.20

    absolute = aim_from_pixels((0.25, -0.05), None, (0.5, 0.5))
    assert math.dist(absolute[:2], (0.30, 0.0)) < 0.001


def test_pointing_waits_for_confirmation():
    arm = UR3e(move_seconds=0.05)
    gate = SkillGate(arm)
    before = forward(arm.q)[1]

    async def locate(label, tcp):
        from vision.aim import Sight

        assert label == "bottiglia"
        return Sight(True, "Vedo la bottiglia. Confermi?", (0.30, 0.05, 0.20))

    asked = asyncio.run(apply_command(gate, "punta verso la bottiglia", locate=locate))
    assert "Confermi" in asked
    assert math.dist(forward(arm.q)[1], before) < 1e-9

    done = asyncio.run(apply_command(gate, "sì", locate=locate))
    assert "punto verso bottiglia" in done
    assert math.dist(arm.tcp, (0.30, 0.05, 0.20)) < 0.01


def test_a_frame_expires():
    update(b"jpeg")
    assert latest(max_age=10) == b"jpeg"
    assert latest(max_age=-1) is None
