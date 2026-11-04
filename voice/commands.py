"""Italian phrases typed in place of speech. Same confirmation gate as the voice agent."""

from __future__ import annotations

import re

from voice.skills import SkillGate, is_confirm, is_reject, is_stop

_TARGET = r"(sinistra|destra|centro|vassoio|home|casa|riposo)"
_TARGET_RE = re.compile(_TARGET, re.IGNORECASE)
_PICK = re.compile(r"sollev|alz|prend|raccogli", re.IGNORECASE)
_PICK_LEAD = re.compile(
    r"^(?:sollev\w*|alz\w*|prend\w*|raccogli\w*|il|lo|la|i|gli|le|un|una|di|del|della|verso|a)\s+|^(?:l')",
    re.IGNORECASE,
)
_PUT = re.compile(r"appogg|posa\b|metti(?:lo)? (?:giù|giu)|abbass", re.IGNORECASE)
_BARE_CONFIRM = re.compile(
    r"^(?:s[iì]|ok|okay|conferma|confermo|d'accordo|fallo|procedi|va bene)[\s.!?]*$",
    re.IGNORECASE,
)
_INDICATE = re.compile(r"indic|mostra|punta", re.IGNORECASE)
_PUSH = re.compile(r"sping", re.IGNORECASE)
_GOTO = re.compile(r"\b(?:vai|va|porta|muovi|sposta)\b|in posa|posizione", re.IGNORECASE)
_SOMEWHERE = re.compile(r"da qualche parte|non so dove|dove vuoi", re.IGNORECASE)
_LEAD = re.compile(
    r"^(?:punta|indica\w*|mostra|vai|va|porta|muovi|sposta|verso|alla|allo|al|la|il|lo|un|una|di|del|della)\s+|^(?:all'|l')",
    re.IGNORECASE,
)
_NAMED = {"sinistra", "destra", "centro", "vassoio", "home", "casa", "riposo"}

_ASK = {
    "lift_tray": "Confermi di sollevare il vassoio?",
    "put_down": "Confermi di appoggiare il vassoio?",
    "go_to_pose": "Confermi di andare in posa {target}?",
    "indicate": "Confermi di indicare {target}?",
    "push": "Confermi di spingere verso {target}?",
}


def _target(text: str) -> str | None:
    match = _TARGET_RE.search(text)
    if not match:
        return None
    word = match.group(1).lower()
    if word in {"casa", "riposo"}:
        return "home"
    return word


def _payload(text: str) -> str:
    phrase = text.strip().lower()
    previous = None
    while phrase and phrase != previous:
        previous = phrase
        phrase = _PICK_LEAD.sub("", phrase).strip(" .!?")
    return phrase


def _object_phrase(text: str) -> str | None:
    phrase = text.strip().lower()
    previous = None
    while phrase and phrase != previous:
        previous = phrase
        phrase = _LEAD.sub("", phrase).strip()
    if not phrase or phrase in _NAMED or _TARGET_RE.search(phrase):
        return None
    return phrase


def parse_command(text: str) -> dict:
    """Map one Italian sentence to a skill, a confirmation, a stop, or a question."""
    if is_stop(text):
        return {"kind": "stop"}
    if _BARE_CONFIRM.match(text.strip()):
        return {"kind": "confirm"}
    if _SOMEWHERE.search(text):
        return {"kind": "clarify", "say": "Dove lo metto: sinistra, destra, centro o sul vassoio?"}
    if _PICK.search(text):
        payload = _payload(text)
        if not payload or payload == "vassoio":
            return {"kind": "prepare", "skill": "lift_tray", "target": None}
        if payload in _NAMED or _TARGET_RE.search(payload):
            return {"kind": "clarify", "say": "Verso quale oggetto mi sposto?"}
        return {"kind": "point", "object": payload}
    if _PUT.search(text):
        return {"kind": "prepare", "skill": "put_down", "target": None}
    if _PUSH.search(text):
        target = _target(text)
        if target in {None, "home"}:
            return {"kind": "clarify", "say": "Verso dove spingo: sinistra, destra o centro?"}
        return {"kind": "prepare", "skill": "push", "target": target}
    if _INDICATE.search(text):
        target = _target(text)
        if target not in {None, "home"}:
            return {"kind": "prepare", "skill": "indicate", "target": target}
        phrase = _object_phrase(text)
        if phrase:
            return {"kind": "point", "object": phrase}
        return {"kind": "clarify", "say": "Cosa indico: un oggetto nel video, oppure sinistra, destra, centro o vassoio?"}
    if _GOTO.search(text):
        target = _target(text)
        if target is not None:
            return {"kind": "prepare", "skill": "go_to_pose", "target": target}
        phrase = _object_phrase(text)
        if phrase:
            return {"kind": "point", "object": phrase}
        return {"kind": "clarify", "say": "In quale posa vado: home, sinistra, destra, centro o vassoio?"}
    if is_confirm(text):
        return {"kind": "confirm"}
    if is_reject(text):
        return {"kind": "cancel"}
    return {
        "kind": "clarify",
        "say": "Non ho capito. Puoi dire: vai verso la bottiglia, vai a destra, indica il centro, solleva il vassoio, spingi verso sinistra, appoggia, ferma.",
    }


async def apply_command(gate: SkillGate, text: str, locate=None) -> str:
    parsed = parse_command(text)
    kind = parsed["kind"]
    if kind == "stop":
        gate.on_user_text(text)
        return "Braccio fermo."
    if kind == "cancel":
        gate.cancel()
        return "Annullato. Il braccio non si muove."
    if kind == "clarify":
        return parsed["say"]
    if kind == "point":
        if locate is None:
            from vision.look import look

            locate = look
        sight = await locate(parsed["object"], gate.arm.tcp)
        if not sight.ok or sight.xyz is None:
            return sight.report
        gate.prepare_point_at(parsed["object"], sight.xyz)
        return sight.report
    if kind == "confirm":
        if gate.on_user_text(text) != "confirm":
            return "Non c'è un movimento da confermare."
        result = await gate.execute()
        return result.get("report", "Fatto.")
    prepared = gate.prepare(parsed["skill"], parsed.get("target"))
    if not prepared.get("ok"):
        return prepared.get("report", "Non posso farlo.")
    return _ASK[parsed["skill"]].format(target=parsed.get("target") or "")
