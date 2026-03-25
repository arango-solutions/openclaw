"""Database connection, schema setup, and embedding model loading.

Everything that happens before the DigitalBrain can run lives here:
connect to ArangoDB, ensure collections/indexes/graph exist, and load
the sentence-transformer model for embeddings.
"""

import os
from arango import ArangoClient
from sentence_transformers import SentenceTransformer

# ── Connection ────────────────────────────────────────────────────────────────

def get_db():
    """Connect to ArangoDB using environment variables.

    Reads ARANGO_HOST, ARANGO_USER, ARANGO_PASSWORD, ARANGO_DB_NAME
    from the environment (supports .env via python-dotenv).  Creates the
    target database if it doesn't already exist.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    host = os.environ["ARANGO_HOST"]
    user = os.environ["ARANGO_USER"]
    password = os.environ["ARANGO_PASSWORD"]
    db_name = os.environ.get("ARANGO_DB_NAME", "openclaw_brain")

    client = ArangoClient(hosts=host)
    sys_db = client.db("_system", username=user, password=password, verify=True)

    if not sys_db.has_database(db_name):
        sys_db.create_database(db_name)

    return client.db(db_name, username=user, password=password, verify=True)


# ── Schema ────────────────────────────────────────────────────────────────────

VERTEX_COLLECTIONS = ["memories", "entities", "sessions", "daily_logs"]
EDGE_COLLECTIONS = ["memory_edges", "entity_edges"]
GRAPH_NAME = "brain_graph"

GRAPH_EDGE_DEFINITIONS = [
    {
        "edge_collection": "memory_edges",
        "from_vertex_collections": ["memories", "daily_logs", "sessions"],
        "to_vertex_collections": ["memories", "daily_logs", "sessions"],
    },
    {
        "edge_collection": "entity_edges",
        "from_vertex_collections": ["entities"],
        "to_vertex_collections": ["entities", "memories"],
    },
]


def ensure_schema(db):
    """Create collections, the named graph, and secondary indexes.

    Safe to call repeatedly — skips anything that already exists.
    """
    for name in VERTEX_COLLECTIONS:
        if not db.has_collection(name):
            db.create_collection(name)

    for name in EDGE_COLLECTIONS:
        if not db.has_collection(name):
            db.create_collection(name, edge=True)

    if not db.has_graph(GRAPH_NAME):
        db.create_graph(GRAPH_NAME, edge_definitions=GRAPH_EDGE_DEFINITIONS)

    mem_col = db.collection("memories")

    try:
        mem_col.add_fulltext_index(fields=["content"])
    except Exception:
        pass

    try:
        mem_col.add_persistent_index(fields=["memory_type", "created_at"])
    except Exception:
        pass

    try:
        mem_col.add_ttl_index(fields=["expires_at"], expiry_time=0)
    except Exception:
        pass

    try:
        db.collection("entities").add_persistent_index(
            fields=["name", "entity_type"], unique=True, sparse=True
        )
    except Exception:
        pass


# ── Embeddings ────────────────────────────────────────────────────────────────

DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"


def load_embedding_model(model_name=DEFAULT_MODEL_NAME):
    """Load a sentence-transformer and return ``(embed_fn, dimension)``.

    ``embed_fn`` accepts a string and returns a list of floats (unit-normed).
    """
    model = SentenceTransformer(model_name)
    test_vec = model.encode("test", normalize_embeddings=True)
    dim = len(test_vec)

    def embed(text: str) -> list:
        return model.encode(text, normalize_embeddings=True).tolist()

    return embed, dim


def create_vector_index(db, dim):
    """Attempt to create a native ArangoDB vector index (3.12+).

    Returns True if the index was created, False if the server doesn't
    support it (falls back to AQL cosine similarity at query time).
    """
    try:
        db.collection("memories").add_index(
            {
                "type": "vector",
                "fields": ["embedding"],
                "params": {"metric": "cosine", "dimension": dim, "nLists": 2},
            }
        )
        return True
    except Exception:
        return False
