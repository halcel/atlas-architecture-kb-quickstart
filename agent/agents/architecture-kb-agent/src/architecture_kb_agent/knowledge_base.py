"""MongoDB queries over the Confluence chunks loaded by ingest/load.py."""

from __future__ import annotations

import os
from functools import lru_cache

from pymongo import MongoClient
from pymongo.collection import Collection

INDEX_NAME = "kb_chunks_vector"


@lru_cache(maxsize=1)
def chunks() -> Collection:
    client = MongoClient(os.environ["MONGODB_URI"], appname="architecture-kb-agent", serverSelectionTimeoutMS=15000)
    return client[os.environ.get("MONGODB_DB", "knowledge_base")]["chunks"]


def search(query: str, limit: int = 5, space: str | None = None) -> list[dict]:
    """Semantic search; Atlas embeds `query` with the same Voyage AI model as the index."""
    stage: dict = {"index": INDEX_NAME, "path": "text", "query": query, "numCandidates": limit * 20, "limit": limit}
    if space:
        stage["filter"] = {"space": space}
    return list(chunks().aggregate([
        {"$vectorSearch": stage},
        {"$project": {"_id": 0, "pageTitle": 1, "section": 1, "url": 1, "text": 1,
                      "score": {"$meta": "vectorSearchScore"}}},
    ]))


def pages() -> list[dict]:
    return list(chunks().aggregate([
        {"$group": {"_id": "$pageId", "title": {"$first": "$pageTitle"}, "space": {"$first": "$space"},
                    "url": {"$first": "$url"}, "chunks": {"$sum": 1}}},
        {"$sort": {"title": 1}},
    ]))
