"""FastAPI bridge over the Adhikar Strands agent.

Streams a single chat turn to the browser as Server-Sent Events. Two
sources are merged into one ordered queue:

  * text tokens  -- from ``Agent.stream_async()``, which yields dicts with a
    ``data`` key for assistant text (``reasoningText`` carries chain-of-thought
    and is dropped here).
  * tool + Cedar events -- from a HookProvider on Before/AfterToolCallEvent.
    These do NOT reach stream_async: ``ToolResultEvent.is_callback_event`` is
    False and stream_async only yields callback events, so a Cedar denial is
    invisible from the token stream alone. The hook is the only place the
    tool's ToolResult (status + content) is observable.

Run:
    cd adhikar
    ADHIKAR_MODEL_PROVIDER=groq GROQ_API_KEY=gsk_... \
        python -m uvicorn web.server:app --port 8000
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ADHIKAR = os.path.dirname(_HERE)
for p in (_ADHIKAR, os.path.join(_ADHIKAR, "agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

import httpx  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent  # noqa: E402

from main import build_agent  # noqa: E402
from lang import unwrap_from_history, wrap_for_language_lock  # noqa: E402

SENSITIVE = {"income_band", "disability_status", "caste_category"}

# ---- Sarvam TTS / LID -----------------------------------------------------
# Endpoint URLs, request field names, response keys, character limits, and the
# 11-code language taxonomy all match the Sarvam docs read on 2026-09-20:
#   TTS: https://docs.sarvam.ai/api-reference-docs/text-to-speech/convert
#   LID: https://docs.sarvam.ai/api-reference-docs/text/identify-language
SARVAM_BASE = "https://api.sarvam.ai"
SARVAM_TTS_URL = f"{SARVAM_BASE}/text-to-speech"
SARVAM_LID_URL = f"{SARVAM_BASE}/text-lid"
SARVAM_TTS_MAX_CHARS = 2500   # bulbul:v3
SARVAM_LID_MAX_CHARS = 1000
SARVAM_LANG_CODES = {
    "en-IN", "hi-IN", "bn-IN", "gu-IN", "kn-IN", "ml-IN",
    "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN",
}
SARVAM_DEFAULT_MODEL = "bulbul:v3"
# NB: Sarvam's docs table lists 40+ speakers for TTS as a whole, but the v3
# model only accepts a subset -- the docs are misleading here. The API itself
# rejects (`anushka`, `manisha`, `vidya`, `arya`, `karun`, `hitesh`) for v3,
# so we pick from the v3-valid set. `priya` is a warm female Indic voice.
SARVAM_DEFAULT_SPEAKER = "priya"
SARVAM_DEFAULT_LANG = "en-IN"


def _sarvam_key() -> str:
    key = os.environ.get("SARVAM_API_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=503,
            detail="SARVAM_API_KEY is not set on the server.",
        )
    return key


# Sentence enders across English + every Indic script we support. Devanagari
# and related scripts use U+0964 (।) as a full stop.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?।])\s+")


def _chunk_for_tts(text: str, limit: int = SARVAM_TTS_MAX_CHARS) -> list[str]:
    """Pack sentences into <=limit-char chunks. A single sentence longer than
    the limit is hard-sliced -- rare, but we cannot silently drop text."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    buf = ""
    for sentence in _SENTENCE_SPLIT.split(text):
        s = sentence.strip()
        if not s:
            continue
        if len(s) > limit:
            if buf:
                chunks.append(buf); buf = ""
            for i in range(0, len(s), limit):
                chunks.append(s[i:i + limit])
            continue
        if len(buf) + 1 + len(s) <= limit:
            buf = f"{buf} {s}".strip()
        else:
            chunks.append(buf); buf = s
    if buf:
        chunks.append(buf)
    return chunks


class StreamHook:
    """Pushes tool-lifecycle events into whatever queue is currently bound.

    Strands hook callbacks are synchronous and fire on the event loop while
    stream_async is being consumed, so put_nowait onto an unbounded queue
    keeps tool events correctly interleaved with text tokens.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue | None = None

    def bind(self, queue: asyncio.Queue | None) -> None:
        self._queue = queue

    def _emit(self, payload: dict) -> None:
        if self._queue is not None:
            self._queue.put_nowait(payload)

    def register_hooks(self, registry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before)
        registry.add_callback(AfterToolCallEvent, self._after)

    def _before(self, event: BeforeToolCallEvent) -> None:
        tool_use = event.tool_use or {}
        name = tool_use.get("name", "unknown")
        raw = tool_use.get("input") or {}
        attrs = raw.get("sensitive_attributes") or [] if isinstance(raw, dict) else []
        self._emit({
            "type": "tool_call",
            "name": name,
            "sensitive_attributes": [a for a in attrs if a in SENSITIVE],
        })

    def _after(self, event: AfterToolCallEvent) -> None:
        tool_use = event.tool_use or {}
        name = tool_use.get("name", "unknown")
        raw = tool_use.get("input") or {}
        attrs = [a for a in (raw.get("sensitive_attributes") or []) if a in SENSITIVE] \
            if isinstance(raw, dict) else []

        result = event.result
        if isinstance(result, Exception):
            self._emit({"type": "tool_error", "name": name, "message": str(result)})
            return

        status = result.get("status") if isinstance(result, dict) else None
        text = _result_text(result)

        # A Cedar denial surfaces as an error-status result whose text the
        # CedarAuthorization intervention prefixes with "DENIED:". Any other
        # error status is the tool itself failing (e.g. OpenSearch down) and
        # must NOT be reported as an authorization outcome either way.
        denied = status == "error" and text.startswith("DENIED")
        allowed = status == "success"

        if name == "search_schemes":
            if denied:
                self._emit({
                    "type": "cedar_result", "tool": name, "allowed": False,
                    "attributes": attrs, "reason": text,
                })
            elif allowed and attrs:
                self._emit({
                    "type": "cedar_result", "tool": name, "allowed": True,
                    "attributes": attrs, "reason": "",
                })
            elif status == "error":
                self._emit({"type": "tool_error", "name": name, "message": text[:200]})

        if name == "record_consent" and status == "success":
            attribute = raw.get("attribute") if isinstance(raw, dict) else None
            if attribute in SENSITIVE:
                self._emit({"type": "consent_recorded", "attribute": attribute})

        if name == "search_schemes" and status == "success":
            schemes = _parse_schemes(text)
            if schemes:
                self._emit({"type": "schemes", "items": schemes})


def _result_text(result) -> str:
    if not isinstance(result, dict):
        return ""
    parts = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and "text" in block:
            parts.append(block["text"])
    return "\n".join(parts).strip()


def _parse_schemes(text: str) -> list[dict]:
    """search_schemes returns a JSON list serialized into the result text."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out.append({
            "scheme_name": item.get("scheme_name", ""),
            "category": item.get("category", ""),
            "benefits": item.get("benefits", ""),
            "apply_url": item.get("apply_url", ""),
            "score": item.get("score"),
        })
    return out


app = FastAPI(title="Adhikar")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # localhost demo only, never deployed
    allow_methods=["*"],
    allow_headers=["*"],
)

_hook = StreamHook()
_agent = None
_agent_lock = asyncio.Lock()


def _get_agent():
    """One agent for the process so conversation + consent state persist."""
    global _agent
    if _agent is None:
        _agent = build_agent()
        # Silence the terminal PrintingCallbackHandler; the browser is the UI now.
        _agent.callback_handler = lambda **kw: None
        _agent.hooks.add_hook(_hook)
    return _agent


async def _event_stream(message: str, request: Request):
    queue: asyncio.Queue = asyncio.Queue()
    agent = _get_agent()
    _hook.bind(queue)

    # Per-turn language lock: the tightened system-prompt rule 8 is not enough
    # for gpt-oss-120b on the first turn of an English conversation about
    # Indian schemes -- topical priors override it. A local script-detection
    # wrap here is deterministic and beats that bias. The wrapped text goes
    # only to the model; the frontend showed the user's original text in the
    # user bubble before calling this endpoint, and `unwrap_from_history`
    # restores the stored user message so the directive does not persist
    # across turns.
    steered = wrap_for_language_lock(message)

    async def produce():
        try:
            async for ev in agent.stream_async(steered):
                # reasoningText is gpt-oss chain-of-thought; never show it.
                if ev.get("reasoning") or "reasoningText" in ev:
                    continue
                chunk = ev.get("data")
                if chunk:
                    queue.put_nowait({"type": "token", "text": chunk})
            queue.put_nowait({"type": "done"})
        except Exception as exc:  # surface model/API failures to the UI
            queue.put_nowait({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            unwrap_from_history(agent, message)
            queue.put_nowait(None)

    task = asyncio.create_task(produce())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            if await request.is_disconnected():
                break
    finally:
        task.cancel()
        _hook.bind(None)


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return StreamingResponse(
            iter([f"data: {json.dumps({'type': 'error', 'message': 'empty message'})}\n\n"]),
            media_type="text/event-stream",
        )
    # One turn at a time: the Strands Agent rejects concurrent invocations.
    await _agent_lock.acquire()

    async def guarded():
        try:
            async for chunk in _event_stream(message, request):
                yield chunk
        finally:
            _agent_lock.release()

    return StreamingResponse(
        guarded(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sarvam_detect_language(client: httpx.AsyncClient, text: str) -> str | None:
    """POST /text-lid.  Returns a language_code from SARVAM_LANG_CODES,
    or None if the response was unparseable (never raises for detect failures --
    the caller falls back to en-IN)."""
    probe = text[:SARVAM_LID_MAX_CHARS]
    try:
        r = await client.post(
            SARVAM_LID_URL,
            headers={"api-subscription-key": _sarvam_key(),
                     "Content-Type": "application/json"},
            json={"input": probe},
            timeout=15.0,
        )
        r.raise_for_status()
        code = (r.json() or {}).get("language_code")
        return code if code in SARVAM_LANG_CODES else None
    except (httpx.HTTPError, ValueError):
        return None


async def _sarvam_tts(client: httpx.AsyncClient, text: str, lang: str) -> list[str]:
    """POST /text-to-speech.  Returns a list of base64 WAV audio strings
    (one per chunk).  Raises HTTPException on Sarvam errors."""
    chunks = _chunk_for_tts(text)
    if not chunks:
        return []
    key = _sarvam_key()
    audios: list[str] = []
    for chunk in chunks:
        payload = {
            "text": chunk,
            "language_code": lang,
            "model": SARVAM_DEFAULT_MODEL,
            "speaker": SARVAM_DEFAULT_SPEAKER,
        }
        r = await client.post(
            SARVAM_TTS_URL,
            headers={"api-subscription-key": key,
                     "Content-Type": "application/json"},
            json=payload,
            timeout=45.0,
        )
        if r.status_code >= 400:
            # Bubble the upstream body up so the UI can show something real
            # (401/402/429 all look the same to the user otherwise).
            detail = r.text[:400]
            raise HTTPException(
                status_code=502 if r.status_code >= 500 else r.status_code,
                detail=f"Sarvam TTS {r.status_code}: {detail}",
            )
        body = r.json() or {}
        for a in body.get("audios") or []:
            if isinstance(a, str) and a:
                audios.append(a)
    return audios


@app.post("/api/tts")
async def tts(request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    # Client can pass a hint (e.g. from an earlier detection); otherwise
    # trust Sarvam's own LID over guessing.
    hint = body.get("language_code")
    if hint not in SARVAM_LANG_CODES:
        hint = None

    async with httpx.AsyncClient() as client:
        lang = hint or await _sarvam_detect_language(client, text) or SARVAM_DEFAULT_LANG
        audios = await _sarvam_tts(client, text, lang)

    if not audios:
        raise HTTPException(status_code=502, detail="Sarvam returned no audio")

    # Concatenating raw WAV bodies without stripping headers is wrong; return
    # the array so the frontend plays them back-to-back instead.
    return {
        "language_code": lang,
        "audios": audios,
        "audio_content_type": "audio/wav",
        "count": len(audios),
    }


@app.post("/api/reset")
async def reset():
    """Start a fresh session -- clears history and recorded consent."""
    global _agent
    async with _agent_lock:
        _agent = None
    return {"ok": True}


_NO_STORE = {"Cache-Control": "no-store, must-revalidate"}


@app.get("/")
async def landing():
    # Static marketing page; the chat app lives at /chat. Both are served
    # no-store because on a demo day an edited file must never be cached.
    return FileResponse(
        os.path.join(_HERE, "static", "landing.html"),
        headers=_NO_STORE,
    )


@app.get("/chat")
async def chat_page():
    return FileResponse(
        os.path.join(_HERE, "static", "index.html"),
        headers=_NO_STORE,
    )
