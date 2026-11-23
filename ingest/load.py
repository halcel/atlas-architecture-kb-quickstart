# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo>=4.10", "beautifulsoup4>=4.12", "httpx>=0.27", "python-dotenv>=1.0"]
# ///
"""Load Confluence pages into MongoDB Atlas as heading-based chunks; Atlas embeds them with Voyage AI.

Usage:
  uv run ingest/load.py sample/*.html                    # Confluence HTML exports (or the samples)
  uv run ingest/load.py --confluence-page 123456 789012  # straight from the Confluence REST API
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup, Tag
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "agent" / "agents" / "architecture-kb-agent" / ".env")

INDEX_NAME = "kb_chunks_vector"
EMBED_MODEL = os.environ.get("EMBED_MODEL", "voyage-4")
MAX_CHARS = 1500
HEADINGS = ("h1", "h2", "h3", "h4")
BLOCKS = ("p", "li", "pre", "tr", "blockquote")


def fetch_confluence(page_id: str) -> dict:
    """Confluence Cloud (email + API token) or Data Center (personal access token)."""
    base = os.environ["CONFLUENCE_BASE_URL"].rstrip("/")
    if pat := os.environ.get("CONFLUENCE_PAT"):
        r = httpx.get(f"{base}/rest/api/content/{page_id}", params={"expand": "body.storage,space"},
                      headers={"Authorization": f"Bearer {pat}"}, timeout=30)
        r.raise_for_status()
        p = r.json()
        return {"pageId": p["id"], "title": p["title"], "space": p["space"]["key"],
                "url": base + p["_links"]["webui"], "html": p["body"]["storage"]["value"]}
    token = base64.b64encode(f"{os.environ['CONFLUENCE_EMAIL']}:{os.environ['CONFLUENCE_API_TOKEN']}".encode()).decode()
    r = httpx.get(f"{base}/wiki/api/v2/pages/{page_id}", params={"body-format": "storage"},
                  headers={"Authorization": f"Basic {token}"}, timeout=30)
    r.raise_for_status()
    p = r.json()
    return {"pageId": p["id"], "title": p["title"], "space": str(p.get("spaceId", "")),
            "url": f"{base}/wiki{p['_links']['webui']}", "html": p["body"]["storage"]["value"]}


def read_export(path: Path) -> dict:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    meta = {m.get("name"): m.get("content") for m in soup.find_all("meta") if m.get("name")}
    title = soup.title.get_text(strip=True) if soup.title else path.stem
    if " : " in title:  # Confluence exports title pages as "<Space> : <Page>"
        title = title.split(" : ", 1)[1]
    return {"pageId": meta.get("confluence-page-id", path.stem), "title": title,
            "space": meta.get("confluence-space-key", "DOCS"), "url": path.as_posix(),
            "html": str(soup.find(id="main-content") or soup.body or soup)}


def block_text(el: Tag) -> str:
    if el.name == "tr":
        return " | ".join(c.get_text(" ", strip=True) for c in el.find_all(["th", "td"]))
    if el.name == "li":
        return "- " + el.get_text(" ", strip=True)
    return el.get_text(" ", strip=True)


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def chunk_page(page: dict) -> list[dict]:
    """One chunk per section (heading path); long sections split on block boundaries."""
    soup = BeautifulSoup(page["html"], "html.parser")
    sections: list[tuple[list[str], list[str]]] = []
    path: list[str] = []
    for el in soup.find_all([*HEADINGS, *BLOCKS]):
        if el.name in HEADINGS:
            level = int(el.name[1])
            path = path[: level - 1] + [el.get_text(" ", strip=True)]
            sections.append((list(path), []))
        elif not el.find_parent(list(BLOCKS)):  # skip <p> inside <li> etc.
            if text := block_text(el):
                if not sections:
                    sections.append(([page["title"]], []))
                sections[-1][1].append(text)

    chunks = []
    for heading_path, blocks in sections:
        if not blocks:
            continue
        parts, current = [], ""
        for b in blocks:
            if current and len(current) + len(b) > MAX_CHARS:
                parts.append(current)
                current = ""
            current = f"{current}\n{b}" if current else b
        parts.append(current)
        if heading_path and _norm(heading_path[0]) == _norm(page["title"]):
            heading_path = heading_path[1:]
        for part in parts:
            section = " > ".join(heading_path) or "Overview"
            chunks.append({
                "_id": f"{page['pageId']}:{len(chunks):03d}",
                "pageId": page["pageId"],
                "pageTitle": page["title"],
                "space": page["space"],
                "url": page["url"],
                "section": section,
                # Page and section in the embedded text so each chunk carries its context.
                "text": f"{page['title']} — {section}\n\n{part}",
                "loadedAt": datetime.now(timezone.utc),
            })
    return chunks


def ensure_index(coll) -> None:
    if any(i["name"] == INDEX_NAME for i in coll.list_search_indexes()):
        return
    coll.create_search_index(SearchIndexModel(
        name=INDEX_NAME,
        type="vectorSearch",
        definition={"fields": [
            {"type": "autoEmbed", "modality": "text", "path": "text", "model": EMBED_MODEL},
            {"type": "filter", "path": "space"},
            {"type": "filter", "path": "pageId"},
        ]},
    ))
    print(f"created vector index {INDEX_NAME} (autoEmbed, {EMBED_MODEL})")


def wait_until_queryable(coll, timeout_s: int = 600) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        idx = next((i for i in coll.list_search_indexes() if i["name"] == INDEX_NAME), {})
        if idx.get("queryable") and idx.get("status") == "READY":
            print("index READY: Atlas has embedded the chunks")
            return
        print(f"  index status: {idx.get('status', 'PENDING')} ...")
        time.sleep(10)
    print("index not ready yet; check Atlas > Search & Vector Search, then run ingest/search.py")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path, help="Confluence HTML exports")
    ap.add_argument("--confluence-page", nargs="+", default=[], metavar="ID", help="page ids to fetch via REST API")
    args = ap.parse_args()
    if not args.files and not args.confluence_page:
        ap.error("pass HTML files or --confluence-page IDs")

    pages = [read_export(f) for f in args.files] + [fetch_confluence(p) for p in args.confluence_page]
    coll = MongoClient(os.environ["MONGODB_URI"])[os.environ.get("MONGODB_DB", "knowledge_base")]["chunks"]

    for page in pages:
        chunks = chunk_page(page)
        coll.delete_many({"pageId": page["pageId"]})
        if chunks:
            coll.insert_many(chunks)
        print(f"{page['title']}: {len(chunks)} chunks")

    ensure_index(coll)
    wait_until_queryable(coll)
    return 0


if __name__ == "__main__":
    sys.exit(main())
