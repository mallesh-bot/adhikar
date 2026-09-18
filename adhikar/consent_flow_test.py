"""End-to-end consent-flow regression for Adhikar.

Runs three tests against the real agent + real OpenSearch + a real model:

  1) A benign query with no sensitive attributes -> Cedar allows.
  2) A DIRECT tool call to search_schemes with disability_status set and
     no consent recorded -> Cedar DENIES. The deny path is otherwise hard
     to observe when the model behaves correctly and records consent
     before searching, so we force it here.
  3) A conversational disability query. The agent should ask for consent
     in plain language; we say yes; it should call record_consent and
     re-issue search_schemes which Cedar now allows.

Between test cases the driver resets agent.state's consent list back to
empty and prints the transition, so consent from one case can't silently
carry into the next and make a later "no consent" test pass for the
wrong reason.

Run: `python consent_flow_test.py` from the adhikar/ folder.
"""
from __future__ import annotations

import os
import sys
import time

# The agent's default streaming callback prints tokens to stdout as they
# arrive; on Windows the console defaults to cp1252 which can't encode
# rupee/curly quotes and crashes mid-response.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "agent"))

# Provider chosen via ADHIKAR_MODEL_PROVIDER in the environment
# (ollama default; anthropic / gemini / groq for the API paths).
from main import build_agent  # noqa: E402
from tools.record_consent import CONSENT_STATE_KEY, get_consented_attributes  # noqa: E402


def hr(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def reset_consent(agent, label: str) -> None:
    before = get_consented_attributes(agent)
    agent.state.set(CONSENT_STATE_KEY, [])
    after = get_consented_attributes(agent)
    print(f"[state before {label}] consent={before} -> reset -> {after}")


def main() -> None:
    agent = build_agent()

    hr("Test 1: benign query, no sensitive attrs (Cedar should allow)")
    reset_consent(agent, "Test 1")
    t0 = time.monotonic()
    r1 = agent(
        "I deliver food on my bike in Bengaluru and have no insurance. "
        "What schemes might apply to me? Please keep it short."
    )
    print(f"\n[{time.monotonic()-t0:.1f}s] agent>", r1)
    print(f"[state after Test 1] consent={get_consented_attributes(agent)}")

    hr("Test 2: direct tool call with disability_status + NO consent (expect Cedar DENY)")
    reset_consent(agent, "Test 2")
    try:
        forced = agent.tool.search_schemes(
            query="disability aids delivery rider",
            sensitive_attributes=["disability_status"],
            top_k=2,
            record_direct_tool_call=False,
        )
        print("tool_result status:", forced.get("status"))
        print("tool_result content preview:", str(forced.get("content"))[:400])
    except Exception as e:
        print(f"direct call raised {type(e).__name__}: {e}")
    print(f"[state after Test 2] consent={get_consented_attributes(agent)}")

    hr("Test 3: conversational disability query, expect ask -> consent -> retry")
    reset_consent(agent, "Test 3")
    t0 = time.monotonic()
    r3a = agent(
        "I'm a delivery rider in Bengaluru. I have a physical disability that "
        "affects my leg, and I'd like to know about schemes that help with "
        "mobility aids or gig-worker welfare. Please keep it short."
    )
    print(f"\n[{time.monotonic()-t0:.1f}s] agent>", r3a)
    print(f"[state between Test 3a and 3b] consent={get_consented_attributes(agent)}")

    t0 = time.monotonic()
    r3b = agent("Yes, you can use my disability status to find matches. Please go ahead.")
    print(f"\n[{time.monotonic()-t0:.1f}s] agent>", r3b)
    print(f"[state after Test 3b] consent={get_consented_attributes(agent)}")

    hr("Cedar decision counters (agent.state)")
    print(agent.state.get("cedar-authorization"))


if __name__ == "__main__":
    main()
