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
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ADHIKAR = os.path.dirname(_HERE)
for p in (_ADHIKAR, os.path.join(_ADHIKAR, "agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent  # noqa: E402

from main import build_agent  # noqa: E402

SENSITIVE = {"income_band", "disability_status", "caste_category"}


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

    async def produce():
        try:
            async for ev in agent.stream_async(message):
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


@app.post("/api/reset")
async def reset():
    """Start a fresh session -- clears history and recorded consent."""
    global _agent
    async with _agent_lock:
        _agent = None
    return {"ok": True}


@app.get("/")
async def index():
    # no-store so an edited page is never served stale -- this is a local dev
    # and demo server, and a cached index.html silently hides UI changes.
    return FileResponse(
        os.path.join(_HERE, "static", "index.html"),
        headers={"Cache-Control": "no-store, must-revalidate"},
    )
