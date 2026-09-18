"""log_interaction: appends an anonymized usage event to a local JSONL file.

No Lambda, no LocalStack -- for a single logging call that infra was setup
time that didn't buy the demo anything (see the project doc's revision
history). This is the whole "cloud-shaped backend" for the weekend.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from strands import tool

LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "data", "interactions.jsonl")
LOG_PATH = os.path.normpath(LOG_PATH)


@tool
def log_interaction(category_matched: str, num_results: int) -> str:
    """Append one anonymized usage event to data/interactions.jsonl.

    Args:
        category_matched: The scheme category that best matched this turn
            (e.g. "insurance", "gig_labour_welfare"). Never pass PII here.
        num_results: How many candidate schemes were returned.

    Returns:
        A short confirmation string.
    """
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "category_matched": category_matched,
        "num_results": num_results,
    }
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(event) + "\n")
    return "Logged."
