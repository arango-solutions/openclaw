"""OpenClaw tool shim — drop-in ``memory_*`` functions.

Each function takes a :class:`~openclaw_brain.brain.DigitalBrain` instance
as its first argument and returns a plain dict matching the response format
expected by OpenClaw's agent gateway.
"""


def memory_store(brain, content, memory_type="fact", tags=None, agent_id="default"):
    """Store a durable memory.

    Call when the user says *remember*, *note that*, or a key fact arises.
    """
    doc = brain.store(
        content, memory_type=memory_type, tags=tags or [], agent_id=agent_id
    )
    return {"status": "stored", "key": doc["_key"], "type": memory_type}


def memory_search(brain, query, top_k=6, memory_type=None, agent_id="default"):
    """Semantic search over stored memories.

    Returns a ranked list of dicts with content, score, type, source path,
    tags, and creation timestamp.
    """
    return [
        {
            "content": r["content"],
            "score": round(r.get("score") or 0, 4),
            "type": r["memory_type"],
            "source": f'memories/{r["_key"]}',
            "tags": r.get("tags", []),
            "created": r.get("created_at", ""),
        }
        for r in brain.search(
            query, top_k=top_k, memory_type=memory_type, agent_id=agent_id
        )
    ]


def memory_get(brain, key):
    """Retrieve a memory by key.  Returns ``{text, path}``; graceful on miss."""
    doc = brain.get(key)
    if not doc:
        return {"text": "", "path": key}
    return {
        "text": doc["content"],
        "path": f"memories/{key}",
        "type": doc["memory_type"],
        "tags": doc.get("tags", []),
    }


def memory_delete(brain, key):
    """Remove a memory by key."""
    ok = brain.delete(key)
    return {"status": "deleted" if ok else "not_found", "key": key}
