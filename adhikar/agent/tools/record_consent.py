"""record_consent: records that the user consented to a sensitive attribute
being used in this session. Strands' CedarAuthorization intervention reads
this (via a context_enricher) before it will allow search_schemes to use
that attribute -- see agent/main.py.
"""
from __future__ import annotations

from strands import ToolContext, tool

CONSENT_STATE_KEY = "consented_attributes"
VALID_ATTRIBUTES = {"income_band", "disability_status", "caste_category"}


@tool(context=True)
def record_consent(attribute: str, tool_context: ToolContext) -> str:
    """Record that the user has consented to a sensitive attribute being
    used to match schemes, for the rest of this session.

    Args:
        attribute: One of "income_band", "disability_status", or
            "caste_category". Call this only after the user has explicitly
            agreed to answer that question -- don't call it speculatively.
        tool_context: Injected by Strands; gives access to agent.state.

    Returns:
        A short confirmation string.
    """
    if attribute not in VALID_ATTRIBUTES:
        return f"Unknown attribute '{attribute}'; expected one of {sorted(VALID_ATTRIBUTES)}."

    agent = tool_context.agent
    consented = set(agent.state.get(CONSENT_STATE_KEY) or [])
    consented.add(attribute)
    agent.state.set(CONSENT_STATE_KEY, sorted(consented))
    return f"Recorded consent for '{attribute}' for this session."


def get_consented_attributes(agent) -> list[str]:
    """Helper for main.py's context_enricher -- reads what's been consented."""
    return list(agent.state.get(CONSENT_STATE_KEY) or [])
