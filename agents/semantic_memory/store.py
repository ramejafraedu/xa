"""Semantic / episodic memory for the video pipeline (Phase 1).

Persists script outcomes, hooks, execution context, and relations in SQLite.
Uses FTS5 for lexical recall and optional float32 embeddings for similarity.
Edges can be exported as a NetworkX DiGraph for graph-style reasoning.

This complements ``services.niche_memory`` (human-edited niche notes) with
machine-ingested runs and retrieval for agents and supervisors.
"""
from __future__ import annotations

import json
import sqlite3
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

from loguru import logger

from config import settings

# Optional: heavy stacks may already ship numpy (e.g. clip_embedder).
try:
    import numpy as np

    _HAS_NUMPY = True
except Exception:  # pragma: no cover
    np = None  # type: ignore
    _HAS_NUMPY = False

try:
    import networkx as nx

    _HAS_NETWORKX = True
except Exception:  # pragma: no cover
    nx = None  # type: ignore
    _HAS_NETWORKX = False


SCHEMA_VERSION = 1

MEMORY_KIND_SCRIPT_PERFORMANCE = "script_performance"
MEMORY_KIND_HOOK = "hook"
MEMORY_KIND_EXECUTION_CONTEXT = "execution_context"
MEMORY_KIND_RENDER_OUTCOME = "render_outcome"
MEMORY_KIND_QA_SIGNAL = "qa_signal"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _embedding_to_blob(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _blob_to_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine_sim(a: Sequence[float], b: Sequence[float]) -> float:
    if _HAS_NUMPY:
        va = np.asarray(a, dtype=np.float64)
        vb = np.asarray(b, dtype=np.float64)
        na = np.linalg.norm(va)
        nb = np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))
    dot = sum(x * y for x, y in zip(a, b))
    ma = sum(x * x for x in a) ** 0.5
    mb = sum(x * x for x in b) ** 0.5
    if ma == 0 or mb == 0:
        return 0.0
    return dot / (ma * mb)


def _fts_token_query(user_query: str) -> str:
    """Turn a free-text line into a conservative FTS5 prefix query."""
    parts = [p.strip() for p in user_query.replace('"', " ").split() if p.strip()]
    if not parts:
        return ""
    # Prefix match per token; escape double quotes inside terms.
    escaped = ['"{}"'.format(p.replace('"', "")) + "*" for p in parts[:32]]
    return " AND ".join(escaped)


@dataclass
class MemoryNode:
    id: str
    created_at: str
    kind: str
    nicho_slug: Optional[str]
    job_id: Optional[str]
    title: Optional[str]
    body: str
    metadata: dict[str, Any]
    has_embedding: bool


@dataclass
class MemoryEdge:
    src_id: str
    dst_id: str
    rel: str
    weight: float
    created_at: str


class SemanticMemoryStore:
    """SQLite-backed memory with FTS5 index and optional vector similarity."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        root = settings.workspace / "semantic_memory"
        root.mkdir(parents=True, exist_ok=True)
        self._path = db_path or (root / "memory.sqlite3")
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def db_path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self._path),
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def initialize(self) -> None:
        """Create tables and indexes if missing (idempotent)."""
        conn = self._connect()
        conn.executescript(
            """
            PRAGMA journal_mode = WAL;
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS schema_meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_nodes (
              id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              kind TEXT NOT NULL,
              nicho_slug TEXT,
              job_id TEXT,
              title TEXT,
              body TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              embedding BLOB,
              embedding_dim INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_memory_nodes_kind ON memory_nodes(kind);
            CREATE INDEX IF NOT EXISTS idx_memory_nodes_nicho ON memory_nodes(nicho_slug);
            CREATE INDEX IF NOT EXISTS idx_memory_nodes_job ON memory_nodes(job_id);

            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
              node_id UNINDEXED,
              title,
              body,
              tokenize = 'porter unicode61'
            );

            CREATE TABLE IF NOT EXISTS memory_edges (
              src_id TEXT NOT NULL,
              dst_id TEXT NOT NULL,
              rel TEXT NOT NULL,
              weight REAL NOT NULL DEFAULT 1.0,
              created_at TEXT NOT NULL,
              PRIMARY KEY (src_id, dst_id, rel),
              FOREIGN KEY (src_id) REFERENCES memory_nodes(id) ON DELETE CASCADE,
              FOREIGN KEY (dst_id) REFERENCES memory_nodes(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_edges_src ON memory_edges(src_id);
            CREATE INDEX IF NOT EXISTS idx_edges_dst ON memory_edges(dst_id);
            """
        )
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES ('version', ?)",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()
        conn.commit()
        logger.debug(f"SemanticMemoryStore initialized at {self._path}")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "SemanticMemoryStore":
        self.initialize()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def add_memory(
        self,
        kind: str,
        body: str,
        *,
        title: str = "",
        nicho_slug: Optional[str] = None,
        job_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        embedding: Optional[Sequence[float]] = None,
        node_id: Optional[str] = None,
    ) -> str:
        """Insert a memory node and FTS row. Returns the node id."""
        nid = node_id or uuid.uuid4().hex
        meta = json.dumps(metadata or {}, ensure_ascii=False)
        emb_blob: Optional[bytes] = None
        emb_dim: Optional[int] = None
        if embedding is not None:
            emb_dim = len(embedding)
            emb_blob = _embedding_to_blob(embedding)

        conn = self._connect()
        conn.execute(
            """
            INSERT INTO memory_nodes (
              id, created_at, kind, nicho_slug, job_id, title, body,
              metadata_json, embedding, embedding_dim
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nid,
                _utc_now_iso(),
                kind,
                nicho_slug,
                job_id,
                title or "",
                body,
                meta,
                emb_blob,
                emb_dim,
            ),
        )
        conn.execute(
            "INSERT INTO memory_fts(node_id, title, body) VALUES (?, ?, ?)",
            (nid, title or "", body),
        )
        conn.commit()
        return nid

    def link(
        self,
        src_id: str,
        dst_id: str,
        rel: str,
        weight: float = 1.0,
    ) -> None:
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO memory_edges(src_id, dst_id, rel, weight, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(src_id, dst_id, rel) DO UPDATE SET
              weight = excluded.weight
            """,
            (src_id, dst_id, rel, weight, _utc_now_iso()),
        )
        conn.commit()

    def delete_node(self, node_id: str) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM memory_fts WHERE node_id = ?", (node_id,))
        conn.execute("DELETE FROM memory_nodes WHERE id = ?", (node_id,))
        conn.commit()

    def search_text(
        self,
        query: str,
        *,
        nicho_slug: Optional[str] = None,
        kinds: Optional[Sequence[str]] = None,
        limit: int = 12,
    ) -> list[MemoryNode]:
        """Lexical search via FTS5."""
        fts_q = _fts_token_query(query)
        if not fts_q:
            return []
        conn = self._connect()
        # FTS first (no JOIN) — avoids alias/MATCH quirks across SQLite builds.
        overfetch = min(500, max(limit * 8, limit))
        fts_sql = (
            "SELECT node_id FROM memory_fts WHERE memory_fts MATCH ? "
            "ORDER BY bm25(memory_fts) LIMIT ?"
        )
        id_rows = conn.execute(fts_sql, (fts_q, overfetch)).fetchall()
        if not id_rows:
            return []
        ids = [str(r["node_id"]) for r in id_rows]
        placeholders = ",".join("?" * len(ids))
        sql = f"""
          SELECT id, created_at, kind, nicho_slug, job_id, title, body,
                 metadata_json, embedding
          FROM memory_nodes
          WHERE id IN ({placeholders})
        """
        params: list[Any] = list(ids)
        if nicho_slug is not None:
            sql += " AND nicho_slug = ?"
            params.append(nicho_slug)
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        rows = conn.execute(sql, params).fetchall()
        by_id = {str(r["id"]): r for r in rows}
        out: list[MemoryNode] = []
        for nid in ids:
            raw = by_id.get(nid)
            if raw is None:
                continue
            out.append(self._row_to_node(raw))
            if len(out) >= limit:
                break
        return out

    def search_similar(
        self,
        query_embedding: Sequence[float],
        *,
        nicho_slug: Optional[str] = None,
        kinds: Optional[Sequence[str]] = None,
        limit: int = 12,
        prefilter_limit: int = 400,
    ) -> list[tuple[MemoryNode, float]]:
        """Cosine similarity against stored embeddings (requires non-null embeddings)."""
        conn = self._connect()
        sql = """
          SELECT id, created_at, kind, nicho_slug, job_id, title, body,
                 metadata_json, embedding
          FROM memory_nodes
          WHERE embedding IS NOT NULL
        """
        params: list[Any] = []
        if nicho_slug is not None:
            sql += " AND nicho_slug IS NOT DISTINCT FROM ?"
            params.append(nicho_slug)
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        sql += f" LIMIT {int(max(1, prefilter_limit))}"
        rows = conn.execute(sql, params).fetchall()
        scored: list[tuple[MemoryNode, float]] = []
        qe = list(query_embedding)
        for r in rows:
            blob = r["embedding"]
            if not blob:
                continue
            vec = _blob_to_embedding(bytes(blob))
            if len(vec) != len(qe):
                logger.debug(
                    f"Skip node {r['id']}: embedding dim {len(vec)} != query {len(qe)}"
                )
                continue
            sim = _cosine_sim(qe, vec)
            node = self._row_to_node(r)
            scored.append((node, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def iter_edges(self) -> Iterator[MemoryEdge]:
        conn = self._connect()
        for r in conn.execute(
            "SELECT src_id, dst_id, rel, weight, created_at FROM memory_edges"
        ):
            yield MemoryEdge(
                src_id=r["src_id"],
                dst_id=r["dst_id"],
                rel=r["rel"],
                weight=float(r["weight"]),
                created_at=r["created_at"],
            )

    def to_networkx(self) -> Any:
        """Build a directed graph of memory nodes and edges (optional dependency)."""
        if not _HAS_NETWORKX:
            raise RuntimeError(
                "networkx is not installed; pip install networkx to use to_networkx()"
            )
        g = nx.DiGraph()
        conn = self._connect()
        for r in conn.execute(
            "SELECT id, kind, title FROM memory_nodes"
        ):
            g.add_node(
                r["id"],
                kind=r["kind"],
                title=r["title"] or "",
            )
        for e in self.iter_edges():
            g.add_edge(e.src_id, e.dst_id, rel=e.rel, weight=e.weight)
        return g

    def context_snippets_for_prompt(
        self,
        query: str,
        *,
        nicho_slug: Optional[str] = None,
        text_limit: int = 6,
        max_chars: int = 4000,
    ) -> str:
        """Format top text hits into a single block for LLM injection."""
        nodes = self.search_text(query, nicho_slug=nicho_slug, limit=text_limit)
        parts: list[str] = []
        total = 0
        for n in nodes:
            line = f"[{n.kind}] {n.title or '(untitled)'}\n{n.body.strip()}\n"
            if total + len(line) > max_chars:
                break
            parts.append(line)
            total += len(line)
        return "\n---\n".join(parts).strip()

    def _row_to_node(self, r: sqlite3.Row) -> MemoryNode:
        raw_meta = r["metadata_json"]
        try:
            meta = json.loads(raw_meta) if isinstance(raw_meta, str) else {}
        except Exception:
            meta = {}
        emb = r["embedding"] if "embedding" in r.keys() else None
        return MemoryNode(
            id=r["id"],
            created_at=r["created_at"],
            kind=r["kind"],
            nicho_slug=r["nicho_slug"],
            job_id=r["job_id"],
            title=r["title"],
            body=r["body"],
            metadata=meta,
            has_embedding=bool(emb),
        )


def get_semantic_memory_store(path: Optional[Path] = None) -> SemanticMemoryStore:
    """Factory for a shared store path under the workspace."""
    return SemanticMemoryStore(db_path=path)
