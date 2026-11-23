# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo>=4.10", "python-dotenv>=1.0"]
# ///
"""Ask the knowledge base a question; Atlas embeds the query text with the index's Voyage model.

Usage:
  uv run ingest/search.py "When should we choose MongoDB over Postgres?"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "agent" / "agents" / "architecture-kb-agent" / ".env")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    coll = MongoClient(os.environ["MONGODB_URI"])[os.environ.get("MONGODB_DB", "knowledge_base")]["chunks"]
    results = coll.aggregate([
        {"$vectorSearch": {"index": "kb_chunks_vector", "path": "text", "query": " ".join(sys.argv[1:]),
                           "numCandidates": 100, "limit": 3}},
        {"$project": {"_id": 0, "pageTitle": 1, "section": 1, "text": 1, "score": {"$meta": "vectorSearchScore"}}},
    ])
    for r in results:
        body = r["text"].split("\n\n", 1)[-1]
        print(f"\n[{r['score']:.3f}] {r['pageTitle']} — {r['section']}\n{body[:400]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
