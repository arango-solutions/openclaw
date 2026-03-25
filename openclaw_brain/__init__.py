"""openclaw_brain — ArangoDB-backed memory layer for OpenClaw agents."""

from openclaw_brain.brain import DigitalBrain
from openclaw_brain.db import get_db, ensure_schema, load_embedding_model, create_vector_index

__all__ = [
    "DigitalBrain",
    "connect",
    "get_db",
    "ensure_schema",
    "load_embedding_model",
    "create_vector_index",
]


def connect(model_name="all-MiniLM-L6-v2"):
    """One-liner setup: connect, ensure schema, load embeddings, return a ready brain.

    >>> from openclaw_brain import connect
    >>> brain = connect()
    """
    db = get_db()
    ensure_schema(db)
    embed_fn, dim = load_embedding_model(model_name)
    vector_native = create_vector_index(db, dim)
    return DigitalBrain(db, embed_fn, vector_native=vector_native)
