"""Arm skills with a voice-confirmation gate.

The model may propose a skill, but the arm moves only after the user
has said a confirmation. Stop bypasses that gate.
"""

from __future__ import annotations

import re

from arm.ur3e import POSES, UR3e

SKILLS = ("go_to_pose", "indicate", "lift_tray", "put_down", "push")

_STOP = re.compile(
    r"(?:^|[^\w])(?:stop|ferma|fermati|fermo|alt|basta|emergenza)(?:$|[^\w])",
    re.IGNORECASE,
)
_CONFIRM = re.compile(
    r"(?:^|[^\w])(?:s[iì]|ok|okay|vai|conferma|confermo|d'accordo|fallo|procedi|va bene)(?:$|[^\w])",
    re.IGNORECASE,
)
_REJECT = re.compile(
    r"(?:^|[^\w])(?:no|annulla|aspetta|stop)(?:$|[^\w])",
    re.IGNORECASE,
)

TOOLS = [
    {
        "type": "function",
        "name": "prepare_skill",
        "description": (
            "Propose one arm skill and wait. Does not move the arm. "
            "Call this only when the request names exactly one skill. "
            "If the target or the skill is ambiguous, do not call this: ask a short question instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill": {"type": "string", "enum": list(SKILLS)},
                "target": {
                    "type": "string",
                    "enum": list(POSES),
                    "description": "Required for go_to_pose, indicate and push. Ignored for lift_tray and put_down.",
                },
            },
            "required": ["skill"],
        },
    },
    {
        "type": "function",
        "name": "execute_pending",
        "description": (
            "Move the arm using the skill prepared earlier. "
            "Call this only after the user has clearly confirmed out loud."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "cancel_pending",
        "description": "Drop the prepared skill. The arm does not move.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "stop_arm",
        "description": "Halt the arm immediately. Use this the moment the user says stop. No confirmation.",
        "parameters": {"type": "object", "properties": {}},
    },
]


def is_stop(text: str) -> bool:
    return _STOP.search(text) is not None


def is_confirm(text: str) -> bool:
    return _CONFIRM.search(text) is not None and not is_stop(text)


def is_reject(text: str) -> bool:
    return _REJECT.search(text) is not None


class SkillGate:
    def __init__(self, arm: UR3e) -> None:
        self.arm = arm
        self.pending: dict | None = None
        self.confirmed = False

    def on_user_text(self, text: str) -> str | None:
        if is_stop(text):
            self.pending = None
            self.confirmed = False
            self.arm.request_stop()
            return "stop"
        if self.pending and is_reject(text):
            self.pending = None
            self.confirmed = False
            return "cancel"
        if self.pending and is_confirm(text):
            self.confirmed = True
            return "confirm"
        return None

    async def call(self, name: str, arguments: dict) -> dict:
        if name == "prepare_skill":
            return self.prepare(arguments.get("skill", ""), arguments.get("target"))
        if name == "execute_pending":
            return await self.execute()
        if name == "cancel_pending":
            return self.cancel()
        if name == "stop_arm":
            return self.stop()
        return {"ok": False, "report": f"Skill sconosciuta: {name}"}

    def prepare(self, skill: str, target: str | None) -> dict:
        if skill not in SKILLS:
            return {"ok": False, "report": f"Skill '{skill}' non esiste. Skill: {', '.join(SKILLS)}."}
        if skill in {"go_to_pose", "indicate", "push"} and not target:
            return {
                "ok": False,
                "needs_clarification": True,
                "report": f"Manca il bersaglio per {skill}. Chiedi quale posa, senza muovere il braccio.",
            }
        if target and target not in POSES:
            return {"ok": False, "report": f"'{target}' non è una posa del UR3e. Poso: {', '.join(POSES)}."}
        self.pending = {"skill": skill, "target": target}
        self.confirmed = False
        where = f" verso {target}" if target else ""
        return {
            "ok": True,
            "pending": True,
            "moved": False,
            "report": f"In attesa di conferma vocale per {skill}{where}. Chiedi 'Confermi?' e non muovere.",
        }

    async def execute(self) -> dict:
        if not self.pending:
            return {"ok": False, "moved": False, "report": "Nessuna skill in attesa."}
        if not self.confirmed:
            return {
                "ok": False,
                "moved": False,
                "report": "L'utente non ha ancora confermato a voce. Chiedi conferma e aspetta.",
            }
        skill = self.pending["skill"]
        target = self.pending.get("target")
        self.pending = None
        self.confirmed = False
        if skill == "go_to_pose":
            result = await self.arm.go_to_pose(target)
        elif skill == "indicate":
            result = await self.arm.indicate(target)
        elif skill == "lift_tray":
            result = await self.arm.lift_tray()
        elif skill == "put_down":
            result = await self.arm.put_down()
        elif skill == "push":
            result = await self.arm.push(target or "centro")
        else:
            result = {"ok": False, "report": f"Skill '{skill}' non eseguibile."}
        result["moved"] = bool(result.get("ok"))
        return result

    def cancel(self) -> dict:
        self.pending = None
        self.confirmed = False
        return {"ok": True, "moved": False, "report": "Skill annullata. Il braccio non si è mosso."}

    def stop(self) -> dict:
        self.pending = None
        self.confirmed = False
        self.arm.request_stop()
        return {"ok": True, "moved": False, "stopped": True, "report": "Stop. Braccio fermo.", **self.arm.snapshot()}
