"""Embeds every scheme in data/schemes/*.json with a local sentence-transformers
model and indexes it into OpenSearch as a k-NN vector field.

Run this once after `docker compose up -d`:
    python data/load_opensearch.py
"""
from __future__ import annotations

import glob
import json
import os

from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk
from sentence_transformers import SentenceTransformer

OPENSEARCH_HOST = "localhost"
OPENSEARCH_PORT = 9200
INDEX_NAME = "adhikar-schemes"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384  # all-MiniLM-L6-v2's output size

SCHEMES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemes")

INDEX_BODY = {
    "settings": {"index": {"knn": True}},
    "mappings": {
        "properties": {
            "scheme_name": {"type": "text"},
            "category": {"type": "keyword"},
            "eligibility_text": {"type": "text"},
            "benefits": {"type": "text"},
            "apply_url": {"type": "keyword"},
            "embedding": {
                "type": "knn_vector",
                "dimension": EMBEDDING_DIM,
                "method": {"name": "hnsw", "engine": "lucene", "space_type": "cosinesimil"},
            },
        }
    },
}


def main() -> None:
    client = OpenSearch(
        hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
        use_ssl=False,
        verify_certs=False,
    )

    if client.indices.exists(index=INDEX_NAME):
        print(f"Deleting existing index {INDEX_NAME!r}")
        client.indices.delete(index=INDEX_NAME)
    client.indices.create(index=INDEX_NAME, body=INDEX_BODY)

    model = SentenceTransformer(EMBEDDING_MODEL)

    actions = []
    for path in sorted(glob.glob(os.path.join(SCHEMES_DIR, "*.json"))):
        with open(path) as f:
            scheme = json.load(f)
        text_to_embed = f"{scheme['scheme_name']}. {scheme['eligibility_text']} {scheme['benefits']}"
        embedding = model.encode(text_to_embed).tolist()
        actions.append(
            {
                "_index": INDEX_NAME,
                "_source": {**scheme, "embedding": embedding},
            }
        )

    success, errors = bulk(client, actions)
    print(f"Indexed {success} schemes into {INDEX_NAME!r}. Errors: {errors}")


if __name__ == "__main__":
    main()
