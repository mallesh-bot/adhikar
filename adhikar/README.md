# Adhikar -- Scheme Eligibility Agent

Build It track, First Commit hackathon. Full design rationale lives in the
project doc; this README is just how to actually run it.

## What's real vs. what needs your machine

Verified end-to-end on this machine (Windows 11, Python 3.14, Docker
Desktop) as of 2026-09-20:

- **OpenSearch** runs single-node via `docker compose`, healthy on
  `localhost:9200`. Note: OpenSearch 2.12+ rejects `plugins.security.disabled=true`
  on its own -- the demo install script runs first and demands
  `OPENSEARCH_INITIAL_ADMIN_PASSWORD`. The compose file uses
  `DISABLE_SECURITY_PLUGIN=true` + `DISABLE_INSTALL_DEMO_CONFIG=true`
  instead, which actually works.
- **All 16 schemes indexed** by `data/load_opensearch.py` using
  `all-MiniLM-L6-v2` embeddings (downloads on first run, ~90 MB). k-NN
  search returns real, well-ranked hits (Karnataka Gig Workers Act top of
  list for "food delivery rider Bengaluru no insurance", etc.).
- **Cedar consent gate proven live.** Test 2 in `consent_flow_test.py`
  forces a direct tool call with a sensitive attribute and no consent;
  `CedarAuthorization` returns `{"status": "error", "content": [{"text":
  "DENIED: Access denied by Cedar policy: policy1"}]}` every time. The
  policy uses `.containsAny(...)` / `.containsAll(...)` for list
  membership (Cedar's `in` operator is for entity hierarchy, not lists,
  and silently denied everything in the first draft).
- **Full ask -> consent -> retry conversational flow verified 3/3
  characterization runs + one clean canonical run.** In Test 3a the model
  asks "May I use the fact that you have a physical disability to look
  for appropriate schemes?" with zero tool calls; user consents in Test
  3b; agent then calls `record_consent` -> `search_schemes` (Cedar
  allows) -> `log_interaction` -> markdown shortlist. Working model:
  Groq's `openai/gpt-oss-120b`, reached through `strands.models.openai.OpenAIModel`
  pointed at `https://api.groq.com/openai/v1` (Groq exposes an
  OpenAI-compatible endpoint). Wall clock per turn: 1-3 s for asks,
  15-25 s for the full 3-tool retry-and-shortlist turn.
- **System prompt rule 2 tightened** to close a real ambiguity: earlier
  wording let the model treat a spontaneous disclosure as implicit
  consent, so it would skip the "may I use this" ask and go straight to
  `record_consent`. Now: "Even if the user volunteers a sensitive
  attribute unprompted, you must still explicitly ask ... volunteering
  the information is not itself consent to use it."
- **Cedar policy also tested directly against `cedarpy` 4.8.7** with the
  exact context shape `CedarAuthorization` builds -- no sensitive attrs
  -> allow, sensitive without consent -> deny, sensitive with consent ->
  allow, multiple attrs with partial consent -> deny. All four cases
  behave as expected.
- **`data/interactions.jsonl`** confirmed written by the runtime with
  real category/count entries after each Test 3 run.
- **FastAPI web app.** `uvicorn web.server:app` (not `python agent/main.py`)
  is the real demo entry point now. Two routes:
  - `/`  serves `web/static/landing.html` -- Fraunces wordmark, a hero
    tagline that cycles through the same phrase in English, Hindi,
    Kannada, Tamil, Bengali, Telugu on a ~2.2 s crossfade, a "Start
    chatting" CTA that fades and navigates to `/chat`, a static-shot
    of the Cedar consent chip as a differentiator callout, and a
    "Built with" pill row (Strands, Cedar, OpenSearch, Groq, Sarvam).
    Verified live: 6 taglines cycle in order over ~13 s; CTA sets
    `body.leaving` then navigates to `/chat`; no console errors; no
    horizontal overflow at 375 px.
  - `/chat` serves `web/static/index.html` -- the animated chat UI
    from the earlier commit, unchanged except for Indic font
    fallbacks (Noto Sans Devanagari/Kannada/Tamil/Telugu/Bengali/
    Malayalam/Gujarati/Gurmukhi/Oriya) and a per-message Listen
    button. All chat fetches use `/api/*` paths, so nothing depended
    on the old root path when routing was split.
- **Multi-language reply support.** System-prompt rules 8+9 tell the
  model to reply in the user's own language while always sending an
  English `query` to `search_schemes`. Verified 5/5 in Hindi, Kannada,
  Tamil, Bengali, Telugu with fresh agents: replies are dominantly in
  the target script, every captured `search_schemes` query is pure
  ASCII English.
- **Script-based per-turn language lock** (`agent/lang.py`). Pure
  Unicode-codepoint-range detector, 11/11 unit tests. On `gpt-oss-120b`
  the tightened rule 8 alone was not enough for the first English turn
  about Indian schemes (topical Hindi prior overrode the prompt).
  `web/server.py` wraps the user's message with a short reply-language
  directive derived from the detected script before `stream_async`,
  then unwraps it from `agent.messages` so the directive does not
  persist across turns. Verified end-to-end in the browser: the exact
  disability/mobility-aid English scenario that previously produced a
  Hindi reply now produces the English *"May I use your physical-
  disability status to look up mobility-aid schemes for you?"* and
  stays in English across the full consent flow.
- **Sarvam TTS.** Per-message Listen button on each agent bubble.
  Backend: `web/server.py` calls Sarvam `text-lid` on the reply,
  then `text-to-speech` (`bulbul:v3`, `speaker=priya`); returns a
  base64 WAV to the frontend, which plays it via `new Audio(...)`.
  Long replies are chunked on sentence boundaries under Sarvam's
  2500-char limit. Verified live: English and Hindi/Devanagari
  replies both produced valid RIFF WAV, played through the button
  cycling `Listen -> Loading -> Stop`. Bad-key path also verified:
  Sarvam 403 -> UI shows amber "Key rejected" pill.
- All 16 scheme JSON files were checked against a live web search in
  September 2026, not written from memory. Files with a `source_note`
  flagging a subsidy amount that changes often (PMAY, Mudra's loan
  ceiling) should be re-checked against the official portal right before
  your demo.

Wired but NOT fully verified end-to-end on this machine:

- **`ADHIKAR_MODEL_PROVIDER=ollama`** (the fallback local path). Two 7-8B
  local models tried: `llama3.1` string-quotes `sensitive_attributes`
  (fails the tool schema) and `qwen2.5:7b` skips the plain-language
  consent ask. Also, Ollama's CUDA backend crashed `llama-server` with a
  stack overrun on this machine's GPU, so `main.py` sets `num_gpu=0`
  (CPU) by default; override with `OLLAMA_NUM_GPU=<int>` on a machine
  whose GPU works. Kept as a "no API key at all" option for the Build It
  track, but the demo runs on Groq.
- **`ADHIKAR_MODEL_PROVIDER=anthropic`** (Claude Haiku 4.5). Provider
  branch is wired and imports resolve, but the key in the environment
  was invalid so the model was never actually round-tripped. Should work
  the moment a real `sk-ant-...` key is available.
- **`ADHIKAR_MODEL_PROVIDER=gemini`** (`gemini-3.6-flash` via
  `strands.models.gemini.GeminiModel`). Provider branch wired; Tests 1
  and 2 passed on the one turn that completed, but every run of Test 3
  hit `503 UNAVAILABLE` from Google ("This model is currently experiencing
  high demand") through two 10-minute waits and a 30-minute wait.
  Full consent flow is untested against Gemini.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

docker compose up -d              # OpenSearch, ~10 s to healthy on localhost:9200
python data/load_opensearch.py    # embeds + indexes the 16 schemes; first run
                                    # downloads all-MiniLM-L6-v2 (~90 MB from
                                    # huggingface.co)

# Verified path -- Groq (OpenAI-compatible, free tier, email signup at
# console.groq.com/keys):
export GROQ_API_KEY=gsk_...
export ADHIKAR_MODEL_PROVIDER=groq

# Optional -- Sarvam TTS powers the per-message Listen button. Without
# this key the app still runs; the button shows an amber "Not configured"
# state instead. Sign up at dashboard.sarvam.ai.
export SARVAM_API_KEY=sk_...

# Run the web app (this replaced `python agent/main.py` as the entry point):
python -m uvicorn web.server:app --port 8000
# Then open http://localhost:8000/ for the landing page, or straight to
# http://localhost:8000/chat for the chat UI.

# End-to-end regression (still valid; uses the same agent underneath):
python consent_flow_test.py       # runs the 3-case suite against whatever
                                    # provider is currently selected
```

For the other providers (`ollama`, `anthropic`, `gemini`), see
`.env.example` and the caveats above.

## Layout

```
adhikar/
├── agent/
│   ├── main.py                 # Agent + CedarAuthorization wiring; provider
│   │                           #  branches for groq/ollama/anthropic/gemini
│   ├── lang.py                 # pure Unicode-range script detector for the
│   │                           #  per-turn reply-language lock
│   └── tools/
│       ├── search_schemes.py   # k-NN search over OpenSearch; sensitive_attributes
│       │                       #  is REQUIRED (no default) so a model can't
│       │                       #  silently omit it and bypass the Cedar gate
│       ├── record_consent.py   # writes agent.state; Cedar reads it back
│       └── log_interaction.py  # appends to data/interactions.jsonl
├── web/
│   ├── server.py               # FastAPI SSE bridge + /api/tts (Sarvam LID+TTS);
│   │                           #  applies the language-lock wrap before
│   │                           #  stream_async and unwraps after
│   └── static/
│       ├── landing.html        # multilingual hero + CTA -> /chat
│       └── index.html          # animated chat UI: Cedar chip, scheme cards,
│                               #  per-message Listen button
├── data/
│   ├── schemes/*.json          # the 16 curated scheme docs
│   ├── load_opensearch.py      # embeds + indexes them
│   └── interactions.jsonl      # created at runtime, gitignored
├── policies/
│   └── consent.cedar           # tested against cedarpy -- see above
├── docker-compose.yml          # OpenSearch only, single node
├── consent_flow_test.py        # end-to-end regression (Tests 1-3)
├── conversation_design.md      # the 6-turn flow, drafted before any code
├── requirements.txt
├── .env.example
└── README.md
```

## Adding a scheme

Drop a new `data/schemes/<name>.json` following the shape of the existing
files (`scheme_name`, `category`, `eligibility_text`, `benefits`,
`apply_url`, optional `source_note`), then re-run `load_opensearch.py`.
