#!/usr/bin/env python3
"""
promote.py — promote an agent_reasoning outcome to fleet_memory.

This is how Tier 2 (session-scoped outcomes) becomes Tier 3 (durable
cross-session knowledge). Only rows with resolution IN ('confirmed','promoted')
should be promoted — dismissed and escalated are NOT memories we want to
keep forever.

Behaviour:
  1. Load the agent_reasoning row.
  2. Guard: refuse to promote dismissed / escalated rows.
  3. Mark the reasoning row as resolution='promoted' (audit trail).
  4. Call write_memory with dedup so we merge-on-match instead of duplicating.

Usage:
    python promote.py --reasoning-id 12345 \\
                      --category preference \\
                      --scope global \\
                      [--content "..." to override source text]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from _models import get_client, get_tables, update_row


SCRIPTS_DIR = Path(__file__).resolve().parent


def main() -> int:
    p = argparse.ArgumentParser(
        description="Promote agent_reasoning row to fleet_memory."
    )
    p.add_argument("--reasoning-id", type=int, required=True)
    p.add_argument("--category", required=True)
    p.add_argument("--scope", default="global")
    p.add_argument(
        "--content",
        help=(
            "Override the promoted text. Defaults to "
            "'observation | hypothesis' from the reasoning row."
        ),
    )
    p.add_argument("--confidence", type=float, default=None)

    args = p.parse_args()

    client = get_client()
    tables = get_tables(client)

    row = tables.reasoning.get(args.reasoning_id)
    if row is None:
        print(json.dumps({"ok": False, "error": "reasoning row not found"}))
        return 1

    if row.resolution in {"dismissed", "escalated"}:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"refusing to promote row with resolution="
                        f"{row.resolution!r}; only 'confirmed' or already-"
                        f"'promoted' rows may flow to fleet_memory."
                    ),
                }
            )
        )
        return 1

    content = args.content or " | ".join(
        part for part in [row.observation, row.hypothesis] if part
    )
    confidence = args.confidence if args.confidence is not None else float(row.confidence or 0.70)

    # Hand off to write_memory.py so dedup (duty 2) runs uniformly.
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "write_memory.py"),
        "--category", args.category,
        "--scope", args.scope,
        "--content", content,
        "--confidence", str(confidence),
        "--source-ids", str(args.reasoning_id),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "write_memory failed",
                    "stderr": result.stderr,
                }
            )
        )
        return 1

    # Audit: flip the reasoning row to 'promoted'
    row.resolution = "promoted"
    row.resolved_at = datetime.utcnow()
    update_row(tables.reasoning, row)

    # Pass through write_memory's output
    sys.stdout.write(result.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
