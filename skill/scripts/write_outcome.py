#!/usr/bin/env python3
"""
write_outcome.py — write a row to agent_reasoning.

DUTY 1 — WRITE CONTROL.
This is the ONLY sanctioned way to write to agent_reasoning from the skill.
The resolution argument is REQUIRED and must be one of:
    confirmed | dismissed | escalated | promoted
No row exists without a resolution. Intermediate reasoning never writes here.

Usage:
    python write_outcome.py \\
        --session-id sess_abc123 \\
        --resolution confirmed \\
        --observation "Bernard confirmed Path A (official tidb-mcp-server)" \\
        --hypothesis "Skill composes SQL; no custom server needed" \\
        --project-id claude_context_plane \\
        --confidence 0.95 \\
        --tags architecture,tidb
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from _models import get_client, get_tables, AgentReasoning

VALID_RESOLUTIONS = {"confirmed", "dismissed", "escalated", "promoted"}


def main() -> int:
    p = argparse.ArgumentParser(
        description="Persist an outcome record to agent_reasoning."
    )
    p.add_argument("--session-id", required=True)
    p.add_argument(
        "--resolution",
        required=True,
        choices=sorted(VALID_RESOLUTIONS),
        help="The gatekeeper. No row without this.",
    )
    p.add_argument(
        "--observation",
        required=True,
        help="What was observed in the conversation. The retrieval signal.",
    )
    p.add_argument("--hypothesis", help="What was concluded.")
    p.add_argument("--project-id", help="Link to project_registry.project_id.")
    p.add_argument("--confidence", type=float, default=0.70)
    p.add_argument(
        "--tags",
        help="Comma-separated tags (e.g. architecture,tidb).",
    )
    p.add_argument(
        "--evidence",
        help="Comma-separated evidence refs (URLs, paths, reasoning ids).",
    )

    args = p.parse_args()

    if args.resolution not in VALID_RESOLUTIONS:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": (
                        "resolution must be one of: "
                        + ", ".join(sorted(VALID_RESOLUTIONS))
                    ),
                }
            )
        )
        return 1

    confidence = max(0.0, min(1.0, args.confidence))
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
    evidence = (
        [e.strip() for e in args.evidence.split(",")] if args.evidence else None
    )

    client = get_client()
    tables = get_tables(client)

    row = AgentReasoning(
        project_id=args.project_id,
        session_id=args.session_id,
        observation=args.observation,
        hypothesis=args.hypothesis,
        evidence_refs=evidence,
        confidence=confidence,
        resolution=args.resolution,
        resolved_at=datetime.now(timezone.utc).replace(tzinfo=None),
        tags=tags,
    )
    tables.reasoning.insert(row)

    print(
        json.dumps(
            {
                "ok": True,
                "reasoning_id": row.id,
                "session_id": args.session_id,
                "resolution": args.resolution,
                "note": (
                    "Vector embedding will populate asynchronously via "
                    "auto-embedding. Row is searchable within seconds."
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
