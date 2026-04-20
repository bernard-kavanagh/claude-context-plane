"""
claude-context-plane — pytidb table models + client factory.

Every script imports from this module so the schema stays defined in ONE
place. Changes here flow through load_context / write_outcome / write_memory /
recall / maintenance automatically.

Architecture:
    Three tiers of memory     — working (ephemeral), session, long-term
    Five custodial duties     — write control, dedup, reconcile, decay, compact

See:
    docs/architecture.md for the mental model
    sql/001_schema.sql   for reference DDL
    sql/002_maintenance.sql for duties 3-5
"""

from __future__ import annotations

import os
import warnings
from datetime import date, datetime
from typing import Optional

from dotenv import load_dotenv
from pytidb import TiDBClient
from pytidb.embeddings import EmbeddingFunction
from pytidb.schema import Field, TableModel
from sqlalchemy import JSON, TEXT

# ---------------------------------------------------------------------------
# Silence cosmetic warnings that don't affect correctness:
#   - pydantic serializer complaining about numpy float32 arrays in vector
#     fields (pytidb returns them; our TableModel declares list[float])
# These warnings are noise during demo output. Vectors still round-trip
# through TiDB's VECTOR columns cleanly — this is purely Python-side typing.
# ---------------------------------------------------------------------------
warnings.filterwarnings(
    "ignore",
    message=".*Pydantic serializer warnings.*",
)
warnings.filterwarnings(
    "ignore",
    message=".*PydanticSerializationUnexpectedValue.*",
)

load_dotenv()

# ---------------------------------------------------------------------------
# Embedding function — runs server-side via EMBED_TEXT()
# Default: tidbcloud_free (no API key required)
# To switch: set EMBEDDING_MODEL in .env (e.g. huggingface/... with BYOK)
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "tidbcloud_free/amazon/titan-embed-text-v2",
)

_embed = EmbeddingFunction(model_name=EMBEDDING_MODEL)


# ---------------------------------------------------------------------------
# Tier 2 + registry
# ---------------------------------------------------------------------------


class ProjectRegistry(TableModel):
    """Static / slowly-changing project metadata. Analogue of charger_registry."""

    __tablename__ = "project_registry"
    __table_args__ = {"extend_existing": True}

    project_id: str = Field(primary_key=True, max_length=64)
    name: str = Field(max_length=128)
    category: str = Field(default="other", max_length=16)
    status: str = Field(default="active", max_length=16)

    description: Optional[str] = Field(default=None, sa_type=TEXT)
    repo_url: Optional[str] = Field(default=None, max_length=256)
    stakeholders: Optional[list] = Field(default=None, sa_type=JSON)
    tags: Optional[list] = Field(default=None, sa_type=JSON)

    started_at: Optional[date] = Field(default=None)
    last_touched: Optional[datetime] = Field(default=None)
    created_at: Optional[datetime] = Field(default=None)


class AgentReasoning(TableModel):
    """
    Outcome records only. Tier 2 (session-scoped, becomes searchable history).

    WRITE CONTROL: `resolution` is the gatekeeper. Only
    'confirmed' | 'dismissed' | 'escalated' | 'promoted' are allowed.
    No row without a resolution — intermediate reasoning stays in the
    context window and in session_state.investigation_summary.
    """

    __tablename__ = "agent_reasoning"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: Optional[str] = Field(default=None, max_length=64)
    session_id: str = Field(max_length=64)
    created_at: Optional[datetime] = Field(default=None)

    observation: str = Field(sa_type=TEXT)
    hypothesis: Optional[str] = Field(default=None, sa_type=TEXT)
    evidence_refs: Optional[list] = Field(default=None, sa_type=JSON)

    confidence: float = Field(default=0.70)

    resolution: str = Field(max_length=16)  # ENUM enforced at DB level
    resolved_at: Optional[datetime] = Field(default=None)

    tags: Optional[list] = Field(default=None, sa_type=JSON)

    # Auto-embedded via pytidb. The source_field is `observation`, which is
    # the strongest retrieval signal. Hypothesis refinement stays in the row
    # but doesn't dominate the vector.
    reasoning_vec: Optional[list[float]] = _embed.VectorField(
        source_field="observation",
    )


class FleetMemory(TableModel):
    """
    Long-term cross-session knowledge. Tier 3.

    Durable facts the platform should remember across every session:
    preferences, working-style rules, project patterns, decisions.

    DEDUPLICATION (duty 2) is enforced at write time in write_memory.py.
    RECONCILIATION (duty 3), DECAY (duty 4), COMPACTION (duty 5) run in
    maintenance.py.
    """

    __tablename__ = "fleet_memory"
    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: Optional[datetime] = Field(default=None)
    updated_at: Optional[datetime] = Field(default=None)

    category: str = Field(max_length=32)
    scope: str = Field(default="global", max_length=128)

    content: str = Field(sa_type=TEXT)

    source_refs: Optional[list] = Field(default=None, sa_type=JSON)

    confidence: float = Field(default=0.70)
    supporting_evidence_count: int = Field(default=1)

    access_count: int = Field(default=0)
    last_accessed: Optional[datetime] = Field(default=None)

    status: str = Field(default="active", max_length=16)
    superseded_by: Optional[int] = Field(default=None)

    memory_vec: Optional[list[float]] = _embed.VectorField(
        source_field="content",
    )


class SessionState(TableModel):
    """Per-chat-session working metadata."""

    __tablename__ = "session_state"
    __table_args__ = {"extend_existing": True}

    session_id: str = Field(primary_key=True, max_length=64)
    started_at: Optional[datetime] = Field(default=None)
    last_active: Optional[datetime] = Field(default=None)

    focus_projects: Optional[list] = Field(default=None, sa_type=JSON)
    focus_summary: Optional[str] = Field(default=None, max_length=256)

    investigation_summary: Optional[str] = Field(default=None, sa_type=TEXT)

    token_budget: int = Field(default=4000)
    tokens_used: int = Field(default=0)

    last_context_hash: Optional[str] = Field(default=None, max_length=64)


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def get_client() -> TiDBClient:
    """
    Build a TiDBClient from .env. Required keys:
        TIDB_HOST, TIDB_PORT, TIDB_USER, TIDB_PASSWORD, TIDB_DATABASE
    Optional:
        TIDB_SSL_CA — path to the TiDB Cloud CA bundle (isrgrootx1.pem).
                      Required on TiDB Cloud Essentials/Starter/Dedicated
                      unless your system trust store already has ISRG Root X1.

    pytidb forwards **kwargs straight through to SQLAlchemy's create_engine(),
    which does NOT accept ssl_* as top-level args — they must be wrapped in
    `connect_args={...}` so SQLAlchemy hands them to pymysql.Connection().
    This is the canonical pattern from TiDB's official SQLAlchemy guide.
    """
    host = os.environ["TIDB_HOST"]
    port = int(os.environ.get("TIDB_PORT", "4000"))
    user = os.environ["TIDB_USER"]
    password = os.environ["TIDB_PASSWORD"]
    database = os.environ.get("TIDB_DATABASE", "claude_context")
    ssl_ca = os.environ.get("TIDB_SSL_CA")

    connect_kwargs = dict(
        host=host,
        port=port,
        username=user,
        password=password,
        database=database,
        ensure_db=True,
    )

    if ssl_ca:
        # Wrap ssl options in connect_args so SQLAlchemy passes them to
        # pymysql.Connection() instead of trying to consume them itself.
        connect_kwargs["connect_args"] = {
            "ssl_verify_cert": True,
            "ssl_verify_identity": True,
            "ssl_ca": ssl_ca,
        }

    return TiDBClient.connect(**connect_kwargs)


def get_tables(client: TiDBClient):
    """
    Create-or-open all four tables and return them as a namespace.
    if_exists="skip" means this is idempotent — safe to call at every
    script invocation.
    """

    class _T:
        projects = client.create_table(schema=ProjectRegistry, if_exists="skip")
        reasoning = client.create_table(schema=AgentReasoning, if_exists="skip")
        memory = client.create_table(schema=FleetMemory, if_exists="skip")
        sessions = client.create_table(schema=SessionState, if_exists="skip")

    return _T


# ---------------------------------------------------------------------------
# Compatibility helpers — pytidb's Table.update() takes (values_dict, filters).
# Our scripts use the intuitive "mutate a row, then save it" pattern. This
# adapter bridges the two so we don't have to restructure every caller.
# ---------------------------------------------------------------------------


def update_row(table, row, pk_field: str = "id") -> None:
    """
    Save `row` back to `table` by diffing it against what's in the database
    and issuing a targeted UPDATE on just the primary key.

    Uses the row's current field values as the new values, and filters by
    the row's primary key. This is the behaviour every caller assumed when
    they wrote `table.update(row)`.
    """
    from sqlmodel import SQLModel

    pk_value = getattr(row, pk_field)
    if pk_value is None:
        raise ValueError(f"cannot update row without {pk_field}")

    # Build values dict from the row's current state. Exclude the PK itself
    # (it's in the filter, not in the SET clause).
    if isinstance(row, SQLModel):
        values = row.model_dump(exclude={pk_field})
    else:
        # Fallback for plain objects
        values = {
            k: v for k, v in vars(row).items()
            if not k.startswith("_") and k != pk_field
        }

    # Strip vector columns from the update payload — they're auto-generated
    # by EMBED_TEXT() and are read-only after insert.
    for vec_col in ("reasoning_vec", "memory_vec"):
        values.pop(vec_col, None)

    table.update(values=values, filters={pk_field: pk_value})


# ---------------------------------------------------------------------------
# Constants (duty thresholds — tune here, they reference everywhere)
# ---------------------------------------------------------------------------

DEDUP_DISTANCE_THRESHOLD = 0.15   # duty 2: cosine < 0.15 => merge
DECAY_FLOOR = 0.30                # duty 4: below this => auto-deprecate
DECAY_RATE = 0.95                 # duty 4: 5% monthly reduction
DECAY_AGE_DAYS = 30               # duty 4: apply after this much inactivity
COMPACTION_DISTANCE = 0.15        # duty 5: merge within this cosine radius

DEFAULT_TOKEN_BUDGET = 4000       # duty 0: context assembly cap
