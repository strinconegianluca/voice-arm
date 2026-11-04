"""Italian Grok Voice session that drives the UR3e through confirmed skills."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import threading
from pathlib import Path

import websockets
from dotenv import load_dotenv

from arm.ur3e import make_arm
from voice.commands import apply_command
from voice.skills import TOOLS, SkillGate

ROOT = Path(__file__).resolve().parent.parent
RATE = 24000
CHUNK_MS = 100

INSTRUCTIONS = """
You are the voice operator of a Universal Robots UR3e in a lab. Speak only Italian. Keep every reply to one short sentence. Call the arm UR3e. Its reach is 500 mm. If a tool says a point is out of reach, say so and do not claim the arm moved.

# Skills
You can only use these skills, through the tools: go_to_pose, indicate, lift_tray, put_down, push.
Poses: home, sinistra, destra, centro, vassoio.
There are no free trajectories.

# Safety
Never move the arm on the first request.
1. If the request maps to exactly one skill, call prepare_skill and then ask "Confermi?".
2. Call execute_pending only after the user has confirmed out loud.
3. If the skill or the target is ambiguous, ask one clarifying question. Do not call prepare_skill.
4. If a tool returns an error or says the arm did not move, say that. Do not claim a movement happened.
5. The moment the user says stop, ferma, alt, or basta, call stop_arm. Do not ask for confirmation.
6. After a successful move, report what the arm did in one sentence.

# Examples
User: Solleva il vassoio. You: call prepare_skill lift_tray, then "Confermi di sollevare il vassoio?"
User: Sì. You: call execute_pending, then report the result.
User: Mettilo da qualche parte. You: ask where. Do not prepare a skill.
User: Stop. You: call stop_arm, then "Braccio fermo."
""".strip()


def session_update() -> dict:
    return {
        "type": "session.update",
        "session": {
            "voice": "eve",
            "instructions": INSTRUCTIONS,
            "reasoning": {"effort": "none"},
            "turn_detection": {"type": "server_vad"},
            "tools": TOOLS,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": RATE},
                    "transcription": {
                        "model": "grok-transcribe",
                        "language_hint": "it",
                        "keyterms": [
                            "vassoio",
                            "sinistra",
                            "destra",
                            "ferma",
                            "conferma",
                            "braccio",
                            "UR3e",
                        ],
                    },
                },
                "output": {"format": {"type": "audio/pcm", "rate": RATE}},
            },
        },
    }


class Speaker:
    """Plays PCM16 audio from Grok and can be cleared on barge-in or stop."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd

        def callback(outdata, frames, _time, status) -> None:
            if status:
                print(f"[audio] {status}", file=sys.stderr)
            needed = frames * 2
            with self._lock:
                got = bytes(self._buffer[:needed])
                del self._buffer[: len(got)]
            if len(got) < needed:
                got += b"\x00" * (needed - len(got))
            outdata[:] = got

        self._stream = sd.RawOutputStream(
            samplerate=RATE,
            channels=1,
            dtype="int16",
            blocksize=RATE // 10,
            callback=callback,
        )
        self._stream.start()

    def push(self, pcm: bytes) -> None:
        with self._lock:
            self._buffer.extend(pcm)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    async def drain(self) -> None:
        while True:
            with self._lock:
                if not self._buffer:
                    break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.1)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()


def _text_from_event(event: dict) -> str:
    for key in ("transcript", "delta", "text"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value
    item = event.get("item") or {}
    content = item.get("content")
    if isinstance(content, list):
        parts = [part.get("transcript") or part.get("text") or "" for part in content if isinstance(part, dict)]
        return " ".join(part for part in parts if part)
    return ""


class VoiceSession:
    def __init__(self, text_mode: bool) -> None:
        self.text_mode = text_mode
        load_dotenv(ROOT / ".env")
        self.arm = make_arm()
        self.gate = SkillGate(self.arm)
        self.speaker = Speaker()
        self._pending_calls: list[tuple[str, dict]] = []
        self._response_open = False
        self._seen_user_text = ""

    async def run(self) -> None:
        load_dotenv(ROOT / ".env")
        if self.text_mode:
            await self._text_commands()
            return

        api_key = os.environ.get("XAI_API_KEY", "").strip()
        if not api_key:
            print("Manca XAI_API_KEY. Copia .env.example in .env e incolla la chiave.")
            return

        self.speaker.start()

        url = "wss://api.x.ai/v1/realtime?model=grok-voice-latest"
        headers = {"Authorization": f"Bearer {api_key}"}
        async with websockets.connect(url, additional_headers=headers, max_size=None) as ws:
            await ws.send(json.dumps(session_update()))
            print("Sessione Grok Voice aperta. Parla in italiano. Spazio = stop immediato.")
            tasks = [
                asyncio.create_task(self._recv(ws)),
                asyncio.create_task(self._mic(ws)),
                asyncio.create_task(self._hotkey()),
            ]
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    task.cancel()
                self.speaker.close()

    async def _recv(self, ws) -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                self.speaker.push(raw)
                continue
            event = json.loads(raw)
            kind = event.get("type", "")
            if kind == "error":
                print(f"[grok] errore: {event.get('error') or event}")
            elif kind in {
                "conversation.item.input_audio_transcription.updated",
                "conversation.item.input_audio_transcription.completed",
            }:
                await self._on_user_text(ws, _text_from_event(event))
            elif kind == "response.output_audio_transcript.delta":
                print(event.get("delta", ""), end="", flush=True)
            elif kind == "response.output_audio_transcript.done":
                print()
            elif kind in {"response.output_audio.delta", "response.audio.delta"}:
                payload = event.get("delta") or ""
                if payload:
                    self.speaker.push(base64.b64decode(payload))
            elif kind == "response.function_call_arguments.done":
                arguments = json.loads(event.get("arguments") or "{}")
                self._pending_calls.append((event["call_id"], {"name": event["name"], "arguments": arguments}))
            elif kind == "response.created":
                self._response_open = True
            elif kind == "response.done":
                self._response_open = False
                await self._flush_tools(ws)
            elif kind == "input_audio_buffer.speech_started":
                self.speaker.clear()
            elif kind in {"session.created", "session.updated", "conversation.created"}:
                print(f"[grok] {kind}")

    async def _on_user_text(self, ws, text: str) -> None:
        if not text or text == self._seen_user_text:
            return
        self._seen_user_text = text
        print(f"\n[tu] {text}")
        action = self.gate.on_user_text(text)
        if action == "stop":
            self.speaker.clear()
            if self._response_open:
                await ws.send(json.dumps({"type": "response.cancel"}))

    async def _flush_tools(self, ws) -> None:
        if not self._pending_calls:
            return
        calls = self._pending_calls
        self._pending_calls = []
        for call_id, spec in calls:
            print(f"[tool] {spec['name']} {spec['arguments']}")
            result = await self.gate.call(spec["name"], spec["arguments"])
            print(f"[tool] {result.get('report')}")
            await ws.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(result, ensure_ascii=False),
                        },
                    }
                )
            )
        if not self.text_mode:
            await self.speaker.drain()
        await ws.send(json.dumps({"type": "response.create"}))

    async def _mic(self, ws) -> None:
        import sounddevice as sd

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        chunk = RATE * CHUNK_MS // 1000

        def callback(indata, _frames, _time, status) -> None:
            if status:
                print(f"[mic] {status}", file=sys.stderr)
            loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        stream = sd.RawInputStream(
            samplerate=RATE,
            channels=1,
            dtype="int16",
            blocksize=chunk,
            callback=callback,
        )
        with stream:
            print("[mic] in ascolto")
            while True:
                pcm = await queue.get()
                await ws.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(pcm).decode("ascii"),
                        }
                    )
                )

    async def _text_commands(self) -> None:
        print("Comandi in italiano, sul UR3e simulato. Riga vuota per uscire.")
        print("Prova: solleva il vassoio, sì, ferma, vai a destra, indica il centro, spingi verso sinistra, appoggia.")
        while True:
            line = (await asyncio.to_thread(input, "> ")).strip()
            if not line:
                return
            print(f"[tu] {line}")
            print(await apply_command(self.gate, line))

    async def _hotkey(self) -> None:
        if sys.platform != "win32":
            return
        import msvcrt

        while True:
            pressed = await asyncio.to_thread(msvcrt.kbhit)
            if pressed and msvcrt.getch() == b" ":
                print("\n[stop] tasto spazio")
                self.gate.stop()
                self.speaker.clear()
            await asyncio.sleep(0.05)


def main() -> None:
    parser = argparse.ArgumentParser(description="Controllo vocale dell'UR3e con Grok Voice")
    parser.add_argument("--text", action="store_true", help="Scrivi i comandi invece di usare il microfono")
    args = parser.parse_args()
    try:
        asyncio.run(VoiceSession(text_mode=args.text).run())
    except KeyboardInterrupt:
        print("\nChiuso.")


if __name__ == "__main__":
    main()
