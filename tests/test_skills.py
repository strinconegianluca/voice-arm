import asyncio
import math

from arm.ur3e import REACH_M, UR3e, forward, within_reach
from arm.urscript import (
    JOINT_VELOCITY,
    MAX_JOINT_SPEED,
    MAX_TCP_SPEED,
    SPEED_LIMIT,
    TCP_VELOCITY,
    movej_script,
    movel_script,
    stopj_script,
)
from voice.commands import apply_command, parse_command
from voice.skills import SkillGate, is_stop


def test_confirmation_required_before_move():
    arm = UR3e(move_seconds=0.1)
    gate = SkillGate(arm)

    pending = gate.prepare("lift_tray", None)
    assert pending["moved"] is False

    blocked = asyncio.run(gate.execute())
    assert blocked["moved"] is False
    assert arm.holding_tray is False

    assert gate.on_user_text("sì") == "confirm"
    done = asyncio.run(gate.execute())
    assert done["moved"] is True
    assert arm.holding_tray is True
    assert "sollevat" in done["report"].lower()


def test_ambiguous_target_does_not_arm_a_move():
    gate = SkillGate(UR3e(move_seconds=0.05))
    result = gate.prepare("go_to_pose", None)
    assert result["needs_clarification"] is True
    assert gate.pending is None


def test_conferma_is_not_a_stop_word():
    assert is_stop("conferma") is False
    assert is_stop("ferma") is True
    gate = SkillGate(UR3e(move_seconds=0.05))
    gate.prepare("push", "destra")
    assert gate.on_user_text("confermo") == "confirm"


def test_stop_interrupts_a_move():
    arm = UR3e(move_seconds=2.0)

    async def scenario():
        task = asyncio.create_task(arm.lift_tray())
        await asyncio.sleep(0.1)
        arm.request_stop()
        return await task

    result = asyncio.run(scenario())
    assert result["stopped"] is True
    assert arm.holding_tray is False


def test_named_poses_fit_the_ur3e_reach():
    arm = UR3e(move_seconds=0.05)
    rotation, home = forward(arm.q)
    assert within_reach(home)
    assert math.dist(home, (0.20, 0.0, 0.30)) < 0.01
    assert rotation[2][2] < -0.9

    async def go():
        return await arm.go_to_pose("destra")

    done = asyncio.run(go())
    assert done["ok"] is True
    assert within_reach(arm.tcp)
    assert math.dist(arm.tcp, (0.28, -0.15, 0.12)) < 0.01
    assert not within_reach((0.70, 0.0, 0.20))
    assert REACH_M == 0.5


def test_typed_italian_commands_confirm_before_moving():
    arm = UR3e(move_seconds=0.05)
    gate = SkillGate(arm)

    asked = asyncio.run(apply_command(gate, "Solleva il vassoio"))
    assert "Confermi" in asked
    assert arm.holding_tray is False

    ambiguous = asyncio.run(apply_command(gate, "Mettilo da qualche parte"))
    assert "Dove" in ambiguous
    assert gate.pending is None or gate.pending["skill"] == "lift_tray"

    done = asyncio.run(apply_command(gate, "sì"))
    assert arm.holding_tray is True
    assert "sollevat" in done.lower()

    assert parse_command("ferma")["kind"] == "stop"
    assert parse_command("vai a destra")["skill"] == "go_to_pose"
    assert parse_command("spingi")["kind"] == "clarify"
    assert parse_command("indica il centro")["skill"] == "indicate"
    assert parse_command("punta verso la bottiglia") == {"kind": "point", "object": "bottiglia"}
    assert parse_command("prendi la bottiglia") == {"kind": "point", "object": "bottiglia"}
    assert parse_command("solleva la tazza") == {"kind": "point", "object": "tazza"}
    assert parse_command("vai verso la bottiglia") == {"kind": "point", "object": "bottiglia"}
    assert parse_command("solleva il vassoio")["skill"] == "lift_tray"
    assert parse_command("va bene")["kind"] == "confirm"


def test_urscript_matches_the_controller_protocol():
    script = movej_script([0.1, -1.2, 1.2, -1.5, -1.57, 0.2])
    assert "movej([" in script
    assert script.endswith("\n")
    assert "stopj(2.0)" in stopj_script()
    assert JOINT_VELOCITY <= SPEED_LIMIT * MAX_JOINT_SPEED
    assert TCP_VELOCITY <= SPEED_LIMIT * MAX_TCP_SPEED
    assert f"v={JOINT_VELOCITY:.3f}" in movej_script([0.0] * 6)
    assert f"v={TCP_VELOCITY:.3f}" in movel_script((0.2, 0.0, 0.2, 0.0, 3.14, 0.0))
