"""Ask Grok where the tool and the named object are in the phone frame."""

from __future__ import annotations

import json
import os
import urllib.request

from arm.ur3e import within_reach
from vision.aim import Sight, aim_from_pixels, describe_aim
from vision.frames import camera_url, latest

_PROMPT = """You see one photo from a phone above a table. A robot arm may be in the picture.
Find the robot tool tip (the flange or gripper at the end of the arm) and the object named below.
Reply with JSON only, no markdown. Coordinates are fractions of the image, origin at the top-left, each axis from 0 to 1.
{"object_visible": true, "gripper": [0.40, 0.55], "object": [0.70, 0.48]}
Use null for gripper if the tool tip is not visible. Set object_visible to false if the named object is not visible.
Object name: {label}
"""


def ask_grok(jpeg: bytes, label: str) -> dict:
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Manca XAI_API_KEY nel file .env.")
    body = {
        "model": "grok-4.7",
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64," + _b64(jpeg),
                            "detail": "low",
                        },
                    },
                    {"type": "text", "text": _PROMPT.format(label=label)},
                ],
            }
        ],
    }
    request = urllib.request.Request(
        "https://api.x.ai/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return _parse_json(str(content))


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def _parse_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("La visione non ha restituito una posizione.")
    return json.loads(text[start : end + 1])


def _unit(value) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        u, v = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if abs(u) > 1.5 or abs(v) > 1.5:
        u, v = u / 100.0, v / 100.0
    if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
        return None
    return u, v


async def look(label: str, tcp: tuple[float, float, float]) -> Sight:
    import asyncio

    jpeg = latest()
    if jpeg is None:
        where = camera_url() or "l'indirizzo che il programma stampa all'avvio"
        return Sight(False, f"Non arriva video dal telefono. Aprilo su {where}.")
    try:
        found = await asyncio.to_thread(ask_grok, jpeg, label)
    except Exception as exc:
        return Sight(False, f"Non riesco a leggere il video: {exc}")
    if not found.get("object_visible"):
        return Sight(False, f"Nel video non vedo {label}.")
    object_uv = _unit(found.get("object"))
    if object_uv is None:
        return Sight(False, f"Ho visto il video ma non la posizione di {label}.")
    gripper_uv = _unit(found.get("gripper"))
    xyz = aim_from_pixels(tcp[:2], gripper_uv, object_uv)
    shift = abs(xyz[0] - tcp[0]) + abs(xyz[1] - tcp[1])
    if shift < 0.02:
        return Sight(False, f"La pinza è già su {label}.")
    if not within_reach(xyz):
        return Sight(False, "L'oggetto è fuori dallo sbraccio di 500 mm del UR3e.")
    return Sight(True, describe_aim(label, xyz, saw_gripper=gripper_uv is not None), xyz)
