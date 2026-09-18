# Adhikar -- Scheme Eligibility Agent

Build It track, First Commit hackathon. Full design rationale lives in the
project doc; this README is just how to actually run it.

## What's real vs. what needs your machine

Verified in the environment that built this scaffold (see the commit that
added this file for the exact commands):

- `pip install "strands-agents[ollama,cedar]"` installs cleanly and
  `strands-agents` version 1.56.0 exposes `Agent`, `@tool`, and
  `strands.vended_interventions.cedar.cedar_authorization.CedarAuthorization`
  exactly as used in `agent/main.py`.
- The Cedar policy in `policies/consent.cedar` was tested directly against
  `cedarpy` 4.8.7 with the exact context shape `CedarAuthorization` builds
  (`{input: <tool args>, session: {consented: [...], ...}}`) -- confirmed:
  no sensitive attributes -> allowed; a sensitive attribute with nothing in
  `session.consented` -> denied; the same attribute once consented ->
  allowed. The first draft of this policy used Cedar's `in` operator, which
  is for entity hierarchy, not list membership -- that version silently
  denied everything. Fixed to `.containsAny(...)` / `.containsAll(...)`.
- All 16 scheme JSON files were checked against a live web search in
  September 2026, not written from memory. Files with a `source_note`
  flagging a subsidy amount that changes often (PMAY, Mudra's loan ceiling)
  should be re-checked against the official portal right before your demo.

Not testable in the sandbox that built this (no Docker, no reachable
Ollama, no `huggingface.co` in the network allowlist there): actually
standing up OpenSearch, downloading the `all-MiniLM-L6-v2` model weights,
and running a live multi-turn conversation end to end. The code is correct
against the SDKs' real APIs, but run the steps below yourself before you
trust the demo.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

docker compose up -d          # starts OpenSearch on localhost:9200
python data/load_opensearch.py  # embeds + indexes the 16 schemes (downloads
                                  # all-MiniLM-L6-v2 the first time -- needs
                                  # internet access to huggingface.co)

ollama pull llama3.1           # or set ADHIKAR_MODEL_PROVIDER=anthropic, see .env.example
python agent/main.py
```

## Layout

```
adhikar/
├── agent/
│   ├── main.py                 # Agent + CedarAuthorization wiring
│   └── tools/
│       ├── search_schemes.py   # k-NN search over OpenSearch
│       ├── record_consent.py   # writes agent.state; Cedar reads it back
│       └── log_interaction.py  # appends to data/interactions.jsonl
├── data/
│   ├── schemes/*.json          # the 16 curated scheme docs
│   ├── load_opensearch.py      # embeds + indexes them
│   └── interactions.jsonl      # created at runtime, gitignored
├── policies/
│   └── consent.cedar           # tested against cedarpy -- see above
├── docker-compose.yml          # OpenSearch only, single node
├── conversation_design.md      # the 6-turn flow, drafted before any code
├── requirements.txt
├── .env.example
└── README.md
```

## Adding a scheme

Drop a new `data/schemes/<name>.json` following the shape of the existing
files (`scheme_name`, `category`, `eligibility_text`, `benefits`,
`apply_url`, optional `source_note`), then re-run `load_opensearch.py`.
