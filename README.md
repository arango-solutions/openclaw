# OpenClaw x ArangoDB — Digital Brain]

![OpenClaw and ArangoDB](assets/openclaw-arango.png)

A drop-in ArangoDB-backed memory layer for [OpenClaw](https://github.com/openclaw) agents, replacing the default SQLite + flat Markdown stack with a unified graph, vector, and document brain.

Provides `memory_store`, `memory_search`, `memory_get`, and `memory_delete` tool functions compatible with OpenClaw's agent interface, plus capabilities that don't exist out of the box: an entity knowledge graph with typed relationships, BFS graph traversal, session-linked conversation history, and daily heartbeat compaction.

---

## OpenClaw's Default Memorydoes

OpenClaw ships with a simple, file-oriented memory system:

- **`MEMORY.md` / `memory/YYYY-MM-DD.md`** — flat Markdown files for persistent context
- **SQLite** with FTS5 for keyword search and `sqlite-vec` for semantic/vector search
- **Optional QMD sidecar** for GGUF embeddings

This works for basic recall, but it's fundamentally a *flat* system.  Memories are rows in a table.  There's no native way to express "Kenneth Lay leads Enron," or "this memory was part of session X," or "these 50 memories were compacted into this daily summary."  Relationships have to live in the application layer — stitched together in Python, not in the data.

---

## Why ArangoDB — The Unified Multi-Model Intuition

### Agent memory is inherently multi-modal

An agent's memory isn't just text blobs.  It contains:

- **Documents** — facts, notes, decisions, preferences, each with metadata (type, tags, confidence, timestamps, TTL)
- **Vectors** — semantic embeddings for similarity search ("find memories about accounting fraud")
- **Graph relationships** — entities connected by typed edges (`person → works_at → company`), memories linked to sessions, temporal chains, compaction lineage

All three are first-class concerns, not afterthoughts.  Any memory system that only handles one or two forces the application to manually bridge the gap.

### The piecemeal approach creates integration tax

The alternative to a multi-model database would be bolting systems together: SQLite for documents, a Neo4j plugin for the graph, and Pinecone or a vector extension for embeddings.  This creates real costs:

**Three query languages.**  SQL for documents, Cypher for graphs, a vendor API for vectors.  Every query like "find similar memories and walk their entity graph" becomes three round-trips stitched together in Python.

**No transactional boundary.**  A `store()` call that writes to Pinecone, Neo4j, and Postgres has no atomicity guarantee.  If the vector insert succeeds but the graph insert fails, the memory is half-written.  You need retry logic, eventual consistency handling, or a saga pattern — all for what should be a single write.

**Application-level joins.**  "Find the 5 most similar memories, then for each one find all entities mentioned, then walk 2 hops in the entity graph" is trivial in AQL.  With separate systems, the application has to orchestrate multiple queries, deserialize results, join by key, and handle partial failures.

**Operational overhead.**  Three systems to back up, monitor, version, and scale.  Three connection strings.  Three failure modes.

**Schema drift.**  The same entity might be represented differently in the graph DB vs. the document store.  Keeping them in sync is the developer's burden.

### What the unified approach gives you

With ArangoDB:

- **One `store()` call** writes the document, its 384-dimensional embedding vector, and entity graph edges in a single operation against a single database.
- **A single AQL query** can combine vector cosine similarity with graph traversal — for example, "find the 5 most similar memories, then walk the entity graph 2 hops from each result's mentioned entities."
- **The memory document, its vector, and its graph edges are co-located** — no cross-system joins, no eventual consistency.
- **One backup target**, one connection string, one query language (AQL), one monitoring surface.
- **Schema changes** (adding a field, a new edge type, a new collection) happen in one place.

The analogy: it's the difference between a Swiss Army knife and carrying a separate knife, screwdriver, and corkscrew in three different pockets.  The individual tools might be marginally sharper, but the integration overhead dominates at the scale of an agent's memory system.

---

## Architecture

The brain uses six ArangoDB collections, wired together by the `brain_graph` named graph:

| Collection | Type | Role |
|---|---|---|
| `memories` | vertex | Facts, events, conversations, notes, decisions — each with an inline 384-dim embedding vector |
| `entities` | vertex | Named entities extracted from memories (people, companies, concepts) |
| `sessions` | vertex | Conversation session metadata |
| `daily_logs` | vertex | Compacted daily summaries |
| `memory_edges` | edge | Temporal, causal, session (`contains_message`), and compaction (`compacted_into`) links |
| `entity_edges` | edge | Typed relationships between entities (`works_at`, `manages`, `reported_fraud_to`) and `mentioned_in` links from entities to memories |

Indexes:

- **Vector** on `memories.embedding` (native ArangoDB 3.12+ cosine index; falls back to AQL dot-product on older versions)
- **Persistent** on `(memory_type, created_at)` for fast type + time filtering
- **TTL** on `expires_at` for auto-expiring ephemeral memories
- **Fulltext** on `memories.content` for keyword search
- **Unique** on `(entities.name, entities.entity_type)` for entity deduplication

---

## Key Capabilities

- **Semantic search** — cosine similarity over 384-dim `all-MiniLM-L6-v2` embeddings via AQL, with optional type filtering
- **Entity knowledge graph** — upsert entities, create typed directed edges, traverse neighborhoods via BFS
- **Session management** — link conversation turns to session nodes with `contains_message` edges, track message counts
- **Heartbeat compaction** — roll up a day's memories into a single searchable daily log, with `compacted_into` lineage edges back to the originals
- **Access tracking** — every search hit bumps `access_count` and `last_accessed`, giving a recency/importance signal
- **TTL expiration** — optional `expires_at` timestamp for ephemeral memories that auto-delete
- **Content-addressable storage** — `_key = SHA256(agent_id:content)[:16]`, so duplicate content upserts rather than duplicating

---

## Setup

### Prerequisites

- Python 3.9+
- An ArangoDB instance (cloud via [ArangoDB Oasis](https://cloud.arangodb.com/) or local; version 3.12+ recommended for the native vector index)

### Install

```bash
pip install -r requirements.txt
```

### Configure

Copy the example environment file and fill in your ArangoDB credentials:

```bash
cp .env.example .env
```

```
ARANGO_HOST=https://your-host.arangodb.cloud
ARANGO_USER=root
ARANGO_PASSWORD=your-password
ARANGO_DB_NAME=openclaw_brain
```

### Quick start

```python
from openclaw_brain import connect

brain = connect()  # reads .env, connects, ensures schema, loads embeddings

brain.store(
    "The project deadline is March 30th.",
    memory_type="fact",
    tags=["project"],
)

results = brain.search("when is the deadline?")
for r in results:
    print(f"[{r['score']:.3f}] {r['content']}")
```

### OpenClaw integration

Wire the tool shims into your OpenClaw gateway or SKILL.md:

```python
from openclaw_brain import connect
from openclaw_brain.tools import memory_store, memory_search, memory_get, memory_delete

brain = connect()

# These return dicts matching OpenClaw's expected tool response format
memory_store(brain, "User prefers dark mode.", memory_type="preference")
hits = memory_search(brain, "user preferences")
```

---

## Demo

The `demo/enron_demo.ipynb` notebook seeds the brain with 120 emails from the public Enron corpus and a hand-built executive knowledge graph (Kenneth Lay, Jeffrey Skilling, Andrew Fastow, Sherron Watkins, etc.), then walks through:

1. Semantic search ("accounting fraud", "bankruptcy 2001")
2. Session replay (multi-turn conversation stored with graph edges)
3. Entity graph traversal (BFS from "Enron" across 2 hops)
4. Heartbeat compaction (roll up a day's memories into a daily summary)
5. Tool shim verification
6. AQL brain inspector (type breakdown, entity graph table, most-accessed memories)
7. SKILL.md auto-generation
8. Interactive d3 dashboard (force-directed entity graph, search, stats)

---

## API Reference

### `DigitalBrain`

| Method | Description |
|---|---|
| `store(content, memory_type, tags, ...)` | Write a memory with inline embedding; optionally link mentioned entities |
| `search(query, top_k, memory_type, agent_id)` | Cosine similarity search; bumps access counts on hits |
| `get(key)` | Fetch a single memory by `_key` |
| `delete(key)` | Remove a memory by `_key` |
| `link_entities(a, b, relation, weight, bidirectional)` | Upsert two entities and create a typed edge between them |
| `open_session(session_id, agent_id, channel)` | Create or retrieve a conversation session |
| `store_message(session_id, role, content, agent_id)` | Store a chat turn and link it to its session |
| `compact_day(target_date, agent_id)` | Summarize a day's memories into a daily log with compaction edges |
| `entity_neighbourhood(entity_name, depth)` | BFS traversal from an entity across entity and memory edges |
| `stats()` | Document counts for every collection |
| `health_check()` | Boolean checks for connection, schema, data, and vector index status |

### Tool shims (`openclaw_brain.tools`)

| Function | Returns |
|---|---|
| `memory_store(brain, content, memory_type, tags)` | `{"status": "stored", "key": "...", "type": "..."}` |
| `memory_search(brain, query, top_k, memory_type)` | `[{"content", "score", "type", "source", "tags", "created"}, ...]` |
| `memory_get(brain, key)` | `{"text", "path", "type", "tags"}` |
| `memory_delete(brain, key)` | `{"status": "deleted"\|"not_found", "key": "..."}` |

### Memory types

| Type | Use for |
|---|---|
| `fact` | Timeless truths — user info, world knowledge, project details |
| `event` | Things that happened at a specific point in time |
| `decision` | Architectural, personal, or strategic choices |
| `preference` | User preferences and settings |
| `note` | General reminders and to-dos |
| `conversation` | Session message turns |
| `daily_summary` | Auto-generated daily compaction entries |

---

## Project Structure

```
openclaw_brain/
  __init__.py       # connect() factory + convenience imports
  db.py             # Connection, schema, embedding model, vector index
  brain.py          # DigitalBrain class — core runtime logic
  tools.py          # OpenClaw memory_* tool shim functions
demo/
  enron_demo.ipynb  # Full walkthrough with Enron dataset
.env.example        # Environment variable template
requirements.txt    # Python dependencies
```
