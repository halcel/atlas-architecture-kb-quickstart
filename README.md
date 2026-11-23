# Architecture knowledge base on MongoDB Atlas + Atlas Agent Engine

Turn Confluence pages into a searchable knowledge base in MongoDB Atlas, then put an agent on top that answers
architecture questions with sources. About 30 minutes end to end.

```
Confluence page ──► ingest/load.py ──► Atlas collection `chunks` ──► Vector Search index (autoEmbed, Voyage AI)
                    (chunk by heading)                                         ▲
                                                 Atlas Agent Engine agent ─────┘  search_knowledge_base, list_pages
```

- **No embedding code.** Atlas [Automated Embedding](https://www.mongodb.com/docs/vector-search/crud-embeddings/automated-embedding/)
  generates Voyage AI embeddings for every chunk on insert/update, and for the query text at search time.
- **One store.** Chunks, metadata filters, embeddings and the agent's conversation state all live in your Atlas cluster.
- Two sample pages in `sample/` (a database decision record and a caching guideline) so you can start before
  connecting your own Confluence.

## What you need

| | |
|---|---|
| Atlas cluster, **Flex or M10+** | Agent Engine does not deploy against M0. On M10+, Automated Embedding needs storage auto-scaling enabled. |
| Python 3.11+ and [uv](https://docs.astral.sh/uv/) | Runs the ingest scripts and the agent. |
| Anthropic API key | Or swap the chat model in `main.py` for any LangChain model (e.g. Azure OpenAI) and adjust the egress host in `agent.yaml`. |
| `agentengine` CLI (step 3 only) | [Install and sign in](https://www.mongodb.com/docs/agentengine/get-started/). Agent Engine is in Public Preview. |

```bash
cp agent/agents/architecture-kb-agent/env.example agent/agents/architecture-kb-agent/.env
# fill in MONGODB_URI and LLM_API_KEY (this one file is used by the scripts and, as secrets, by the deployed agent)
```

## 1. Load Confluence pages into Atlas

```bash
uv run ingest/load.py sample/*.html
```

Each page is split into one chunk per heading section (long sections are split again), stored in
`knowledge_base.chunks` with `pageId`, `pageTitle`, `space`, `section` and `url`, and the script creates this index:

```json
{ "fields": [
  { "type": "autoEmbed", "modality": "text", "path": "text", "model": "voyage-4" },
  { "type": "filter", "path": "space" },
  { "type": "filter", "path": "pageId" }
] }
```

**Your own pages:** export a page as HTML (*••• → Export → HTML*) and pass the file, or load straight from the API:

```bash
uv run ingest/load.py --confluence-page 123456 789012   # set CONFLUENCE_* in .env (Cloud token or Data Center PAT)
```

Re-running replaces the chunks of that page; Atlas re-embeds them automatically.

## 2. Ask the knowledge base

```bash
uv run ingest/search.py "When should we choose MongoDB over Postgres?"
```

The query is plain text: `$vectorSearch` with `query` instead of a vector. Atlas embeds it with the index's model.

## 3. Deploy the agent to Atlas Agent Engine

```bash
cd agent/agents/architecture-kb-agent
agentengine auth login
agentengine agent validate
agentengine deploy --auto      # first run: links your Atlas project, uploads .env values as secrets, builds, deploys
agentengine invoke --stream --session s1 "Welche Kriterien sprechen für MongoDB statt Postgres?"
```

`deploy --auto` asks interactively for anything missing (workspace, secrets, Atlas link). The agent has two tools,
`search_knowledge_base` and `list_pages`, in `src/architecture_kb_agent/`.

### What is in this repo, and what Agent Engine provides

This repo contains the agent: code (`main.py`, tools in `knowledge_base.py`), config (`agent.yaml`: tools, sandboxes,
egress) and dependencies. The platform pieces come from Agent Engine and are not in the repo:

| Piece | What it does | Docs |
|---|---|---|
| **`agentengine` CLI** | Scaffolds, validates, runs and deploys agents | [Download](https://agentengine.mongodb.com/download-cli) · [Install and sign in](https://www.mongodb.com/docs/agentengine/build/install-authenticate/) · [CLI reference](https://www.mongodb.com/docs/agentengine/cli/agentengine/) |
| **Local Docker stack** | `agentengine dev up` generates and starts Orchestration Engine, agent and tool sandboxes, and a Playground at http://localhost:3000 (Docker Desktop required). Good for iterating before you deploy. | [`agentengine dev up`](https://www.mongodb.com/docs/agentengine/cli/agentengine_dev_up/) · [`agentengine dev`](https://www.mongodb.com/docs/agentengine/cli/agentengine_dev/) |
| **Cloud runtime in Atlas** | `agentengine deploy` builds the agent and runs it in your Atlas project; `atlas setup` links the cluster; `invoke` calls it | [`deploy`](https://www.mongodb.com/docs/agentengine/cli/agentengine_deploy/) · [`atlas setup`](https://www.mongodb.com/docs/agentengine/cli/agentengine_atlas_setup/) · [`invoke`](https://www.mongodb.com/docs/agentengine/cli/agentengine_invoke/) |

Start here: [Atlas Agent Engine docs](https://www.mongodb.com/docs/agentengine/) ·
[Get started tutorial](https://www.mongodb.com/docs/agentengine/get-started/) ·
[More example agents](https://github.com/mongodb/agent-engine-examples)

## Optional: the same knowledge base from your IDE (MongoDB MCP Server)

With the [MongoDB MCP Server](https://www.mongodb.com/docs/mcp-server/) in Cursor, VS Code or Claude, your
assistant can query `knowledge_base.chunks` directly, e.g. *"Run a vector search on knowledge_base.chunks for
caching anti-patterns"*. Use a read-only database user (or `--readOnly`).

## Make it yours

- **More sources:** anything with headings works the same way (Markdown ADRs, SharePoint exports); map it to the
  same chunk fields.
- **Filters:** add fields like `labels` or `owner` to the chunk and the index to scope searches per team.
- **Model:** `voyage-4` is the general default; `voyage-4-large` for harder semantics, `voyage-4-lite` for volume.
  Change it in `.env` (`EMBED_MODEL`) before the first load.
