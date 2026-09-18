# Adhikar -- Scheme Eligibility Agent

Build It track, First Commit hackathon. Full design rationale lives in the
project doc; this README is just how to actually run it.

## What's real vs. what needs your machine

Verified end-to-end on this machine (Windows 11, Python 3.14, Docker
Desktop) as of 2026-09-19:

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
python agent/main.py

# End-to-end regression:
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
│   └── tools/
│       ├── search_schemes.py   # k-NN search over OpenSearch; sensitive_attributes
│       │                       #  is REQUIRED (no default) so a model can't
│       │                       #  silently omit it and bypass the Cedar gate
│       ├── record_consent.py   # writes agent.state; Cedar reads it back
│       └── log_interaction.py  # appends to data/interactions.jsonl
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
