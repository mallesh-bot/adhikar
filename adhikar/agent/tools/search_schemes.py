"""search_schemes: semantic search over the local scheme corpus.

Embeds the query with a small local sentence-transformers model and runs a
k-NN search against the OpenSearch index built by data/load_opensearch.py.

If this tool is called with sensitive_attributes naming income_band,
disability_status, or caste_category, Strands' CedarAuthorization
intervention (see agent/main.py) will deny the call unless record_consent
has already recorded consent for every one of those attributes this
session -- Cedar enforces this, not this function's own logic.
"""
from __future__ import annotations

from functools import lru_cache

from opensearchpy import OpenSearch
from sentence_transformers import SentenceTransformer
from strands import tool

OPENSEARCH_HOST = "localhost"
OPENSEARCH_PORT = 9200
INDEX_NAME = "adhikar-schemes"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def _embedder() -> SentenceTransformer:
    return SentenceTransformer(EMBEDDING_MODEL)


@lru_cache(maxsize=1)
def _client() -> OpenSearch:
    return OpenSearch(
        hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
        http_auth=None,
        use_ssl=False,
        verify_certs=False,
    )


@tool
def search_schemes(
    query: str,
    sensitive_attributes: list[str],
    top_k: int = 4,
) -> list[dict]:
    """Search the curated government-scheme corpus for likely matches.

    Args:
        query: A plain-language description of the person's situation
            (occupation, state, family situation, whatever is relevant).
        sensitive_attributes: REQUIRED. Names of any sensitive attributes
            this query relies on -- must be a subset of
            {"income_band", "disability_status", "caste_category"}. Always
            pass a list: [] when the query does not touch any of these,
            and the exact names when it does (even implicitly), so Cedar's
            consent gate can see them. There is no default -- the caller
            must decide and pass one every time.
        top_k: Maximum number of candidate schemes to return (default 4).

    Returns:
        A list of candidate schemes, each with scheme_name, category,
        eligibility_text, benefits, apply_url, and a similarity score.
    """
    vector = _embedder().encode(query).tolist()

    body = {
        "size": top_k,
        "query": {"knn": {"embedding": {"vector": vector, "k": top_k}}},
    }
    resp = _client().search(index=INDEX_NAME, body=body)

    results = []
    for hit in resp.get("hits", {}).get("hits", []):
        src = hit["_source"]
        results.append(
            {
                "scheme_name": src["scheme_name"],
                "category": src["category"],
                "eligibility_text": src["eligibility_text"],
                "benefits": src["benefits"],
                "apply_url": src["apply_url"],
                "score": hit["_score"],
            }
        )
    return results
