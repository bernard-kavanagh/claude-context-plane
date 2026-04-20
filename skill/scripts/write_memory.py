#!/usr/bin/env python3
"""
write_memory.py — write a row to fleet_memory with deduplication.

DUTY 2 — DEDUPLICATION.
Before inserting, vector-search existing active memories in the same scope.
If any match with cosine distance < 0.15, UPDATE the existing row
(increment supporting_evidence_count + access_count, refresh updated_at)
instead of inserting a duplicate.

One strong memory with high evidence_count beats ten weak duplicates.

Usage:
    python write_memory.py \\
        --category preference \\
        --scope global \\
        --content "Bernard prefers paths that eat own dog food over bespoke code" \\
        --confidence 0.90 \\
        --source-ids 12345,67890
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from _models import (
    DEDUP_DISTANCE_THRESHOLD,
    FleetMemory,
    get_client,
    get_tables,
)

VALID_CATEGORIES = {
    "pattern",
    "preference",
    "working_style",
    "project_note",
    "person_note",
    "tool_note",
    "operational_rule",
    "decision",
}


def main() -> int:
    p = argparse.ArgumentParser(
        description="Write a fleet_memory row with dedup (duty 2)."
    )
    p.add_argument("--category", required=True, choices=sorted(VALID_CATEGORIES))
    p.add_argument(
        "--scope",
        default="global",
        help="e.g. 'global', 'project:ev_charger', 'person:Stephen'",
    )
    p.add_argument("--content", required=True, help="The knowledge itself.")
    p.add_argument("--confidence", type=float, default=0.70)
    p.add_argument(
        "--source-ids",
        help=(
            "Comma-separated agent_reasoning.id values that produced this. "
            "Provenance chain."
        ),
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=DEDUP_DISTANCE_THRESHOLD,
        help=f"Cosine distance below which we merge (default {DEDUP_DISTANCE_THRESHOLD})",
    )

    args = p.parse_args()

    confidence = max(0.0, min(1.0, args.confidence))
    source_refs = (
        [int(x.strip()) for x in args.source_ids.split(",")]
        if args.source_ids
        else None
    )

    client = get_client()
    tables = get_tables(client)

    # ---------------------------------------------------------------------
    # DEDUP CHECK (duty 2)
    # ---------------------------------------------------------------------
    # Vector search against active memories in the same scope. Because
    # memory_vec is auto-embedded from `content`, searching by `args.content`
    # finds near-duplicates directly.
    hits = (
        tables.memory
        .search(args.content)
        .filter({"scope": args.scope, "status": "active"})
        .limit(1)
        .to_list()
    )

    if hits:
        top = hits[0]
        # pytidb returns distance in `_distance` key (lower = closer)
        distance = top.get("_distance", top.get("distance", 1.0))
        if distance < args.threshold:
            # Merge: update the winner row in place
            existing_id = top["id"]
            existing_row = tables.memory.get(existing_id)
            existing_row.supporting_evidence_count = (
                (existing_row.supporting_evidence_count or 1) + 1
            )
            existing_row.access_count = (existing_row.access_count or 0) + 1
            existing_row.last_accessed = datetime.utcnow()
            # Merge provenance
            merged_refs = list(existing_row.source_refs or [])
            for ref in source_refs or []:
                if ref not in merged_refs:
                    merged_refs.append(ref)
            existing_row.source_refs = merged_refs or None
            # Confidence cannot exceed 1.0; nudge slightly on reinforcement
            existing_row.confidence = min(
                1.0, (existing_row.confidence or 0.70) + 0.02
            )
            tables.memory.update(existing_row)

            print(
                json.dumps(
                    {
                        "ok": True,
                        "action": "merged",
                        "memory_id": existing_id,
                        "distance": round(float(distance), 4),
                        "evidence_count": existing_row.supporting_evidence_count,
                        "note": (
                            f"Existing memory within cosine {args.threshold}; "
                            "merged evidence rather than duplicating."
                        ),
                    },
                    indent=2,
                )
            )
            return 0

    # ---------------------------------------------------------------------
    # INSERT new memory
    # ---------------------------------------------------------------------
    row = FleetMemory(
        category=args.category,
        scope=args.scope,
        content=args.content,
        source_refs=source_refs,
        confidence=confidence,
        supporting_evidence_count=1,
        access_count=0,
        status="active",
    )
    tables.memory.insert(row)

    print(
        json.dumps(
            {
                "ok": True,
                "action": "inserted",
                "memory_id": row.id,
                "category": args.category,
                "scope": args.scope,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
