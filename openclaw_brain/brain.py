"""DigitalBrain — ArangoDB-backed memory layer for OpenClaw agents.

Provides storage, semantic search, entity linking, session management,
graph traversal, and daily compaction over a unified ArangoDB backend.
"""

from datetime import datetime, timezone, date
import hashlib
import re


class DigitalBrain:
    """Unified document + vector + graph memory backed by ArangoDB."""

    MEMORY_TYPES = {
        "fact", "event", "note", "conversation",
        "decision", "preference", "daily_summary",
    }

    COLLECTION_NAMES = [
        "memories", "entities", "memory_edges",
        "entity_edges", "sessions", "daily_logs",
    ]

    def __init__(self, db, embed_fn, vector_native=False):
        self.db = db
        self.embed = embed_fn
        self.native_vec = vector_native
        self.memories = db.collection("memories")
        self.entities = db.collection("entities")
        self.mem_edges = db.collection("memory_edges")
        self.ent_edges = db.collection("entity_edges")
        self.sessions = db.collection("sessions")
        self.daily_logs = db.collection("daily_logs")

    # ── Store ─────────────────────────────────────────────────────────────

    def store(
        self,
        content,
        memory_type="fact",
        tags=None,
        source="agent",
        session_id=None,
        agent_id="default",
        expires_at=None,
        confidence=1.0,
        entities_mentioned=None,
    ):
        """Write a memory with an inline embedding and optional entity links."""
        if memory_type not in self.MEMORY_TYPES:
            memory_type = "note"

        now = datetime.now(timezone.utc).isoformat()
        key = hashlib.sha256(f"{agent_id}:{content}".encode()).hexdigest()[:16]

        doc = {
            "_key": key,
            "content": content,
            "memory_type": memory_type,
            "tags": tags or [],
            "source": source,
            "session_id": session_id,
            "agent_id": agent_id,
            "created_at": now,
            "confidence": confidence,
            "access_count": 0,
            "last_accessed": now,
            "embedding": self.embed(content),
            "expires_at": expires_at,
        }

        result = self.memories.insert(doc, overwrite=True, return_new=True)["new"]

        if entities_mentioned:
            for name in entities_mentioned:
                ent = self._upsert_entity(name)
                self._safe_edge(
                    {
                        "_from": ent["_id"],
                        "_to": result["_id"],
                        "relation": "mentioned_in",
                        "created_at": now,
                    },
                    self.ent_edges,
                )

        return result

    # ── Search ────────────────────────────────────────────────────────────

    def search(self, query, top_k=6, memory_type=None, agent_id="default"):
        """Cosine-similarity search over memory embeddings via AQL."""
        q_vec = self.embed(query)
        type_filter = "FILTER m.memory_type == @mtype" if memory_type else ""

        aql = f"""
        LET q = @q_vec
        FOR m IN memories
          FILTER m.agent_id == @agent_id
          FILTER m.embedding != null
          {type_filter}
          LET score = (
            LET pairs = (FOR i IN 0..LENGTH(m.embedding)-1 RETURN m.embedding[i] * q[i])
            RETURN SUM(pairs)
          )[0]
          SORT score DESC
          LIMIT @k
          RETURN MERGE(m, {{score: score}})
        """

        bv = {"q_vec": q_vec, "k": top_k, "agent_id": agent_id}
        if memory_type:
            bv["mtype"] = memory_type

        results = list(self.db.aql.execute(aql, bind_vars=bv))

        for r in results:
            try:
                self.memories.update(
                    {
                        "_key": r["_key"],
                        "access_count": r.get("access_count", 0) + 1,
                        "last_accessed": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception:
                pass

        return results

    # ── Direct access ─────────────────────────────────────────────────────

    def get(self, key):
        """Fetch a single memory by ``_key``."""
        try:
            return self.memories.get(key)
        except Exception:
            return None

    def delete(self, key):
        """Remove a memory by ``_key``.  Returns True on success."""
        try:
            self.memories.delete(key)
            return True
        except Exception:
            return False

    # ── Entities ──────────────────────────────────────────────────────────

    def _upsert_entity(self, name, entity_type="unknown"):
        key = re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower())[:64]
        doc = {
            "_key": key,
            "name": name,
            "entity_type": entity_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            return self.entities.insert(doc, return_new=True)["new"]
        except Exception:
            return self.entities.get(key)

    def _safe_edge(self, doc, col):
        try:
            col.insert(doc)
        except Exception:
            pass

    def link_entities(self, a, b, relation, weight=1.0, bidirectional=False):
        """Create a typed edge between two entities (upserting both)."""
        ea = self._upsert_entity(a)
        eb = self._upsert_entity(b)
        now = datetime.now(timezone.utc).isoformat()
        edge = {
            "_from": ea["_id"],
            "_to": eb["_id"],
            "relation": relation,
            "weight": weight,
            "created_at": now,
        }
        self._safe_edge(edge, self.ent_edges)
        if bidirectional:
            self._safe_edge(
                {**edge, "_from": eb["_id"], "_to": ea["_id"]}, self.ent_edges
            )

    # ── Sessions ──────────────────────────────────────────────────────────

    def open_session(self, session_id, agent_id="default", channel=None):
        """Create or retrieve a conversation session."""
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "_key": session_id,
            "agent_id": agent_id,
            "channel": channel,
            "started_at": now,
            "ended_at": None,
            "message_count": 0,
        }
        try:
            return self.sessions.insert(doc, return_new=True)["new"]
        except Exception:
            return self.sessions.get(session_id)

    def store_message(self, session_id, role, content, agent_id="default"):
        """Store a chat message and link it to its session."""
        mem = self.store(
            content,
            memory_type="conversation",
            source=role,
            session_id=session_id,
            agent_id=agent_id,
        )
        try:
            sess = self.sessions.get(session_id)
            if sess:
                self._safe_edge(
                    {
                        "_from": f"sessions/{session_id}",
                        "_to": mem["_id"],
                        "relation": "contains_message",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    self.mem_edges,
                )
                self.sessions.update(
                    {
                        "_key": session_id,
                        "message_count": sess.get("message_count", 0) + 1,
                    }
                )
        except Exception:
            pass
        return mem

    # ── Compaction ────────────────────────────────────────────────────────

    def compact_day(self, target_date=None, agent_id="default"):
        """Roll up a day's memories into a single searchable daily log."""
        target_date = target_date or date.today()
        day_str = target_date.isoformat()

        entries = list(
            self.db.aql.execute(
                """
                FOR m IN memories
                  FILTER m.agent_id == @agent_id
                  FILTER m.created_at >= @start AND m.created_at <= @end
                  SORT m.created_at ASC
                  RETURN {key: m._key, id: m._id, type: m.memory_type, content: m.content}
                """,
                bind_vars={
                    "agent_id": agent_id,
                    "start": f"{day_str}T00:00:00+00:00",
                    "end": f"{day_str}T23:59:59+00:00",
                },
            )
        )

        if not entries:
            return {}

        by_type = {}
        for e in entries:
            by_type.setdefault(e["type"], []).append(e["content"])

        summary = f"# Daily Summary — {day_str}\n\n"
        for t, cs in by_type.items():
            summary += f"## {t.title()}\n"
            for c in cs:
                summary += f"- {c}\n"
            summary += "\n"

        log_key = f"{agent_id}_{day_str}"
        log = {
            "_key": log_key,
            "agent_id": agent_id,
            "date": day_str,
            "entry_count": len(entries),
            "summary": summary,
            "embedding": self.embed(summary[:512]),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.daily_logs.insert(log, overwrite=True)

        now = datetime.now(timezone.utc).isoformat()
        for e in entries:
            self._safe_edge(
                {
                    "_from": f'memories/{e["key"]}',
                    "_to": f"daily_logs/{log_key}",
                    "relation": "compacted_into",
                    "created_at": now,
                },
                self.mem_edges,
            )

        return log

    # ── Graph traversal ───────────────────────────────────────────────────

    def entity_neighbourhood(self, entity_name, depth=2):
        """BFS traversal from an entity across entity and memory edges."""
        key = re.sub(r"[^a-zA-Z0-9_-]", "_", entity_name.lower())[:64]
        try:
            return list(
                self.db.aql.execute(
                    """
                    FOR v, e, p IN 1..@depth ANY @start
                      entity_edges, memory_edges
                      OPTIONS {bfs: true, uniqueVertices: "global"}
                      RETURN {vertex: v, edge_label: e.relation, depth: LENGTH(p.edges)}
                    """,
                    bind_vars={"start": f"entities/{key}", "depth": depth},
                )
            )
        except Exception:
            return []

    # ── Diagnostics ───────────────────────────────────────────────────────

    def stats(self):
        """Return document counts for every collection."""
        return {
            name: self.db.collection(name).count()
            for name in self.COLLECTION_NAMES
        }

    def health_check(self):
        """Run basic health checks.  Returns a dict of boolean results."""
        s = self.stats()
        return {
            "connection": True,
            "schema": all(self.db.has_collection(c) for c in self.COLLECTION_NAMES),
            "memories_stored": s["memories"] > 0,
            "entities_stored": s["entities"] > 0,
            "entity_edges": s["entity_edges"] > 0,
            "sessions_tracked": s["sessions"] > 0,
            "vector_index_native": self.native_vec,
            "counts": s,
        }
