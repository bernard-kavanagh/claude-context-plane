#!/usr/bin/env python3
"""
recall.py — semantic retrieval from fleet_memory.

Returns the top-K active memories most relevant to a query, filtered by
optional scope/category. Increments access_count + last_accessed on the hits
so confidence decay (duty 4) treats them as reinforced.

Usage:
    python recall.py --query "how does Bernard feel about bespoke code"
    python recall.py --query "..." --scope project:ev_charger --limit 5
    python recall.py --query "..." --category preference
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from _models import get_client, get_tables


def main() -> int:
    p = argparse.ArgumentParser(description="Vector search fleet_memory.")
    p.add_argument("--query", required=True)
    p.add_argument("--scope", help="Exact scope filter (e.g. 'global').")
    p.add_argument("--category", help="Exact category filter.")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument(
        "--distance-max",
        type=float,
        default=0.6,
        help="Drop hits with cosine distance above this (default 0.6).",
    )
    p.add_argument(
        "--no-touch",
        action="store_true",
        help="Don't update access_count / last_accessed on hits.",
    )

    args = p.parse_args()

    client = get_client()
    tables = get_tables(client)

    filt: dict = {"status": "active"}
    if args.scope:
        filt["scope"] = args.scope
    if args.category:
        filt["category"] = args.category

    hits = (
        tables.memory
        .search(args.query)
        .filter(filt)
        .distance_threshold(args.distance_max)
        .limit(args.limit)
        .to_list()
    )

    out = []
    for h in hits:
        distance = h.get("_distance", h.get("distance"))
        out.append(
            {
                "memory_id": h["id"],
                "category": h["category"],
                "scope": h["scope"],
                "content": h["content"],
                "confidence": float(h["confidence"]) if h.get("confidence") is not None else None,
                "evidence_count": h.get("supporting_evidence_count"),
                "distance": round(float(distance), 4) if distance is not None else None,
            }
        )

    # Touch access stats on the hits (duty 4 signal that these memories are
    # still useful — shields them from decay).
    if hits and not args.no_touch:
        for h in hits:
            row = tables.memory.get(h["id"])
            if row is None:
                continue
            row.access_count = (row.access_count or 0) + 1
            row.last_accessed = datetime.utcnow()
            tables.memory.update(row)

    print(json.dumps({"ok": True, "query": args.query, "hits": out}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
