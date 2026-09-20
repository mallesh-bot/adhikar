"""Adhikar agent entrypoint.

Run with a local Ollama model (default, no AWS account, no API key):
    ollama pull llama3.1
    python agent/main.py

Or with a personal Anthropic API key instead of Ollama:
    pip install "strands-agents[anthropic]"
    export ADHIKAR_MODEL_PROVIDER=anthropic
    export ANTHROPIC_API_KEY=sk-...
    python agent/main.py
"""
from __future__ import annotations

import os
import sys

# Windows consoles default to cp1252 and crash on ₹ (₹), which appears
# in almost every scheme's benefits text. Reconfigure early so the streaming
# callback handler doesn't die mid-response.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from strands import Agent
from strands.handlers.callback_handler import PrintingCallbackHandler
from strands.vended_interventions.cedar.cedar_authorization import CedarAuthorization

from tools.log_interaction import log_interaction
from tools.record_consent import get_consented_attributes, record_consent
from tools.search_schemes import search_schemes


class _NoReasoningCallbackHandler(PrintingCallbackHandler):
    """Same as the default streaming handler but drops reasoningText -- gpt-oss
    (via Groq) streams chain-of-thought there and we don't want it in the
    user-visible transcript. Regular data, tool markers, and completion
    behavior are untouched. See PrintingCallbackHandler for the fields
    consumed."""

    def __call__(self, **kwargs):
        kwargs.pop("reasoningText", None)
        super().__call__(**kwargs)

SYSTEM_PROMPT = """\
You are Adhikar, an assistant that helps informal and gig workers in India
find government welfare schemes they may be eligible for.

Rules:
1. Ask one clarifying question before attempting a match -- occupation and
   state usually narrow it enough. Ask a second only if the first answer
   still leaves more than one plausible category.
2. Never assume caste category, religion, disability status, or income band.
   If a scheme's eligibility depends on one of these, ask directly. Even if
   the user volunteers a sensitive attribute unprompted, you must still
   explicitly ask whether you may use it for matching -- volunteering the
   information is not itself consent to use it. Only call
   record_consent(attribute) AFTER the user answers yes to that ask.
   Include the attribute in a search_schemes call only after record_consent
   -- Cedar will deny the call otherwise.
3. Use the search_schemes tool to retrieve candidate schemes before
   answering. Never invent a scheme or its eligibility criteria from memory.
4. For each scheme you return, state: the name, one sentence on why it
   matches, and the official application link. Return at most 4 schemes.
5. If nothing matches confidently, say so plainly and suggest the nearest
   Common Service Centre rather than guessing.
6. Keep answers short -- this is a chat on someone's work break, not a report.
7. After answering, call log_interaction with the best-matched category and
   the number of results -- no personal details, just the category.
8. Always reply in the same language and script the user wrote to you in,
   whatever that language is. Do not switch to English because the scheme
   data is in English, and do not ask the user to switch languages. If the
   user changes language mid-conversation, follow them.
9. The scheme corpus is written in English, so the `query` you pass to
   search_schemes must ALWAYS be in English -- translate the user's
   situation into an English query first. This is true no matter what
   language you are replying in. Keep scheme names and the official
   apply links exactly as the tool returns them (do not translate a
   scheme's official name), but write your one-sentence explanation of
   why it matches in the user's language.
"""


def build_agent() -> Agent:
    provider = os.environ.get("ADHIKAR_MODEL_PROVIDER", "ollama")

    if provider == "ollama":
        from strands.models.ollama import OllamaModel

        # num_gpu defaults to 0 (CPU) because this machine's CUDA backend
        # crashes llama-server with a stack overrun. Set OLLAMA_NUM_GPU to a
        # positive int to re-enable GPU offload once the driver is fixed.
        num_gpu = int(os.environ.get("OLLAMA_NUM_GPU", "0"))
        model = OllamaModel(
            host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model_id=os.environ.get("OLLAMA_MODEL_ID", "llama3.1"),
            options={"num_gpu": num_gpu},
        )
    elif provider == "anthropic":
        from strands.models.anthropic import AnthropicModel

        model = AnthropicModel(
            client_args={"api_key": os.environ["ANTHROPIC_API_KEY"]},
            model_id=os.environ.get("ANTHROPIC_MODEL_ID", "claude-haiku-4-5-20251001"),
            max_tokens=1024,
        )
    elif provider == "gemini":
        from strands.models.gemini import GeminiModel

        model = GeminiModel(
            client_args={"api_key": os.environ["GEMINI_API_KEY"]},
            model_id=os.environ.get("GEMINI_MODEL_ID", "gemini-3.6-flash"),
        )
    elif provider == "groq":
        # Groq exposes an OpenAI-compatible endpoint, so we reuse OpenAIModel
        # with client_args pointed at Groq's base URL. llama-3.3-70b-versatile
        # (the original pick) was retired from Groq's lineup; gpt-oss-120b is
        # the current biggest general chat model there.
        from strands.models.openai import OpenAIModel

        model = OpenAIModel(
            client_args={
                "api_key": os.environ["GROQ_API_KEY"],
                "base_url": "https://api.groq.com/openai/v1",
            },
            model_id=os.environ.get("GROQ_MODEL_ID", "openai/gpt-oss-120b"),
        )
    else:
        raise ValueError(f"Unknown ADHIKAR_MODEL_PROVIDER: {provider!r}")

    policy_path = os.path.join(os.path.dirname(__file__), "..", "policies", "consent.cedar")

    cedar = CedarAuthorization(
        policies=policy_path,
        principal={"type": "User", "id": "session"},
        context_enricher=lambda ctx: {
            "consented": get_consented_attributes(_agent_ref["agent"])
        },
    )

    # context_enricher only receives {tool_name, tool_input, invocation_state},
    # not the agent itself -- this small mutable box lets the enricher reach
    # agent.state without restructuring CedarAuthorization's callback shape.
    _agent_ref: dict = {}

    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[search_schemes, record_consent, log_interaction],
        interventions=[cedar],
        callback_handler=_NoReasoningCallbackHandler(),
    )
    _agent_ref["agent"] = agent
    return agent


def main() -> None:
    agent = build_agent()
    print("Adhikar is ready. Describe your situation (Ctrl+C to quit).\n")
    while True:
        try:
            user_input = input("you> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input.strip():
            continue
        response = agent(user_input)
        print(f"\nadhikar> {response}\n")


if __name__ == "__main__":
    main()
