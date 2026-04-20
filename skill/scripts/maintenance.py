#!/usr/bin/env python3
"""
maintenance.py — scheduled custodial duties.

Duties 3, 4, 5 from the Cognitive Foundation pattern:

    --reconcile   Duty 3: mark superseded rows
    --decay       Duty 4: 5% monthly confidence decay + auto-deprecate < 0.30
    --compact     Duty 5: cosine-merge near-duplicate memories
    --all         run all three, in order

Recommended schedule:
    Daily:  python maintenance.py --reconcile
    Weekly: python maintenance.py --decay --compact
    OR:     python maintenance.py --all  (weekly is fine for personal scale)

Output is a JSON report so you can pipe it into logs or a digest.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta

from _models import (
    COMPACTION_DISTANCE,
    DECAY_AGE_DAYS,
    DECAY_FLOOR,
    DECAY_RATE,
    get_client,
    get_tables,
)


def duty_reconcile(client, tables) -> dict:
    """Duty 3 — mark rows with superseded_by IS NOT NULL but status='active'."""
    engine = client.db_engine
    with engine.begin() as conn:
        from sqlalchemy import text as sql_text
        result = conn.execute(
            sql_text(
                """
                UPDATE fleet_memory
                   SET status = 'superseded'
                 WHERE status = 'active'
                   AND superseded_by IS NOT NULL
                """
            )
        )
        rowcount = result.rowcount
    return {"duty": "reconcile", "rows_marked_superseded": rowcount}


def duty_decay(client, tables) -> dict:
    """
    Duty 4 — 5% monthly decay on memories older than DECAY_AGE_DAYS without
    reinforcement. Then auto-deprecate anything below DECAY_FLOOR.
    """
    engine = client.db_engine
    from sqlalchemy import text as sql_text

    with engine.begin() as conn:
        decay_res = conn.execute(
            sql_text(
                """
                UPDATE fleet_memory
                   SET confidence = ROUND(confidence * :rate, 2)
                 WHERE status      = 'active'
                   AND updated_at  < :cutoff
                   AND confidence  > :floor
                """
            ),
            {
                "rate": DECAY_RATE,
                "cutoff": datetime.utcnow() - timedelta(days=DECAY_AGE_DAYS),
                "floor": DECAY_FLOOR,
            },
        )
        decayed = decay_res.rowcount

        deprecate_res = conn.execute(
            sql_text(
                """
                UPDATE fleet_memory
                   SET status = 'deprecated'
                 WHERE status     = 'active'
                   AND confidence < :floor
                """
            ),
            {"floor": DECAY_FLOOR},
        )
        deprecated = deprecate_res.rowcount

    return {
        "duty": "decay",
        "rate": DECAY_RATE,
        "age_days": DECAY_AGE_DAYS,
        "floor": DECAY_FLOOR,
        "rows_decayed": decayed,
        "rows_auto_deprecated": deprecated,
    }


def duty_compact(client, tables) -> dict:
    """
    Duty 5 — cosine-merge near-duplicate active memories within the same scope.
    For each candidate pair, the row with higher supporting_evidence_count
    wins; the loser is marked superseded_by the winner, and the winner
    absorbs the loser's evidence count + provenance.
    """
    engine = client.db_engine
    from sqlalchemy import text as sql_text

    # Find candidate pairs first (read-only).
    with engine.connect() as conn:
        candidates = conn.execute(
            sql_text(
                """
                WITH a AS (
                    SELECT id, scope, memory_vec, supporting_evidence_count,
                           source_refs
                      FROM fleet_memory
                     WHERE status = 'active'
                )
                SELECT
                    a1.id                        AS id1,
                    a1.supporting_evidence_count AS ev1,
                    a2.id                        AS id2,
                    a2.supporting_evidence_count AS ev2,
                    VEC_COSINE_DISTANCE(a1.memory_vec, a2.memory_vec) AS dist
                  FROM a AS a1
                  JOIN a AS a2
                    ON a1.scope = a2.scope
                   AND a1.id < a2.id
                 WHERE VEC_COSINE_DISTANCE(a1.memory_vec, a2.memory_vec) < :threshold
                 ORDER BY dist ASC
                """
            ),
            {"threshold": COMPACTION_DISTANCE},
        ).fetchall()

    merges = 0
    seen_loser_ids: set[int] = set()

    for row in candidates:
        id1, ev1, id2, ev2, dist = row
        # If either side has already lost in this run, skip — we don't want
        # chained merges in a single pass; next run will catch them.
        if id1 in seen_loser_ids or id2 in seen_loser_ids:
            continue

        winner_id, loser_id = (id1, id2) if (ev1 or 0) >= (ev2 or 0) else (id2, id1)

        winner = tables.memory.get(winner_id)
        loser = tables.memory.get(loser_id)
        if winner is None or loser is None:
            continue

        # Merge
        winner.supporting_evidence_count = (
            (winner.supporting_evidence_count or 0)
            + (loser.supporting_evidence_count or 0)
        )
        # Confidence: max of the two (winner keeps at least its own)
        winner.confidence = max(
            float(winner.confidence or 0.0),
            float(loser.confidence or 0.0),
        )
        merged_refs = list(winner.source_refs or [])
        for ref in (loser.source_refs or []):
            if ref not in merged_refs:
                merged_refs.append(ref)
        winner.source_refs = merged_refs or None

        loser.status = "superseded"
        loser.superseded_by = winner_id

        tables.memory.update(winner)
        tables.memory.update(loser)
        seen_loser_ids.add(loser_id)
        merges += 1

    return {
        "duty": "compact",
        "cosine_threshold": COMPACTION_DISTANCE,
        "pairs_found": len(candidates),
        "merges_applied": merges,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Scheduled custodial duties.")
    p.add_argument("--reconcile", action="store_true")
    p.add_argument("--decay", action="store_true")
    p.add_argument("--compact", action="store_true")
    p.add_argument("--all", action="store_true")
    args = p.parse_args()

    if args.all:
        args.reconcile = args.decay = args.compact = True

    if not any([args.reconcile, args.decay, args.compact]):
        p.print_help()
        return 1

    client = get_client()
    tables = get_tables(client)

    report = {
        "started_at": datetime.utcnow().isoformat() + "Z",
        "results": [],
    }

    if args.reconcile:
        report["results"].append(duty_reconcile(client, tables))
    if args.decay:
        report["results"].append(duty_decay(client, tables))
    if args.compact:
        report["results"].append(duty_compact(client, tables))

    report["finished_at"] = datetime.utcnow().isoformat() + "Z"
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
