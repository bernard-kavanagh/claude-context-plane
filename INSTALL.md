# INSTALL.md — step-by-step walkthrough

From zero to a working personal context plane in about 10 minutes.

---

## Prerequisites

- **Python 3.10+** — pytidb requires it
- **TiDB Cloud Essentials cluster** — you already have one. If not,
  sign up free at [tidbcloud.com](https://tidbcloud.com) and provision
  an Essentials cluster.
- **Claude Code** — the CLI client, `claude` on your `$PATH`. Download
  from [claude.com/download](https://claude.com/download).
- **`git`** — to clone the repo.

Optional:
- **`tidb-mcp-server`** — the official TiDB MCP server, for ad-hoc SQL
  from Claude Code. See §"Hook up the TiDB MCP server" below.

---

## Step 1 — Clone the repo

```bash
git clone https://github.com/bernard-kavanagh/claude-context-plane.git
cd claude-context-plane
```

(Replace the URL with wherever you actually host it. The `install.sh`
script works from any checkout location.)

---

## Step 2 — Get TiDB connection parameters

1. Open the [TiDB Cloud console](https://tidbcloud.com/console/clusters).
2. Click your Essentials cluster → **Connect**.
3. Note down `HOST`, `PORT`, `USERNAME`, `PASSWORD`. The username will
   look like `<prefix>.root`.

---

## Step 3 — Configure `.env`

```bash
cp .env.example .env
$EDITOR .env
```

Fill in the connection params from step 2. Leave `EMBEDDING_MODEL` at
its default (`tidbcloud_free/amazon/titan-embed-text-v2`) unless you
have a specific reason to switch — it's free, no API key needed, and
works on Essentials.

---

## Step 4 — Install Python dependencies

Use a virtualenv if you like — not required, but tidier:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

This pulls in `pytidb` and `python-dotenv` (plus `sqlalchemy` as a
transitive dep).

---

## Step 5 — Install the skill

```bash
./install.sh
```

This copies `skill/` into `~/.claude/skills/context-plane/` so Claude
Code can find it as a user-global skill. Re-running `install.sh` is safe
— it replaces the installed copy cleanly.

Output ends with instructions for the next two steps.

---

## Step 6 — Let scripts find `.env`

The scripts load environment variables from a `.env` next to them or
via the standard `dotenv` search path. Simplest is a symlink:

```bash
ln -sf "$PWD/.env" "$HOME/.claude/skills/context-plane/.env"
```

Alternatively, copy the file (you'll need to remember to re-copy on
credential changes):

```bash
cp .env "$HOME/.claude/skills/context-plane/.env"
```

---

## Step 7 — Smoke test

```bash
python ~/.claude/skills/context-plane/scripts/load_context.py \
       --focus "install smoke test"
```

What should happen:

1. pytidb connects to your Essentials cluster.
2. The four tables get created on first run (via `create_table(..., if_exists="skip")`).
3. A fresh `session_state` row is inserted.
4. Context assembly runs and prints a markdown block — mostly empty on
   the first run because you have no memories yet. That's correct.
5. JSON trailer prints with the new `session_id`.

If you see a connection error, double-check `.env`. If you see a pytidb
schema error, make sure your Essentials cluster has vector search
enabled (it does by default on AWS).

---

## Step 8 — Write your first memory

```bash
# Grab the session_id from the smoke test output
SESS=<sess_xxxxxxxxxx>

# Write an outcome
python ~/.claude/skills/context-plane/scripts/write_outcome.py \
       --session-id "$SESS" \
       --resolution confirmed \
       --observation "First write to the context plane on $(date +%F)" \
       --confidence 1.0 \
       --tags meta,bootstrap

# Promote a durable preference
python ~/.claude/skills/context-plane/scripts/write_memory.py \
       --category preference \
       --scope global \
       --content "Bernard prefers architectures that eat own dog food over bespoke code"
```

Both commands print JSON. Verify:

```bash
python ~/.claude/skills/context-plane/scripts/recall.py \
       --query "bespoke code"
```

You should see your preference come back as a hit.

---

## Step 9 — Use it with Claude Code

Open Claude Code in any directory:

```bash
claude
```

In the chat, type something like:

> *Load my context — where were we?*

Claude picks up the `context-plane` skill (its description contains
trigger phrases like "load context", "where did we leave off", "catch me
up"), reads `SKILL.md`, and runs `load_context.py`. From there on it
operates with platform-maintained memory.

---

## Step 10 — Schedule maintenance

Duties 3, 4, 5 are scheduled, not per-turn.

**Option A — launchd (macOS) or cron (Linux), weekly:**

```bash
# Edit your crontab
crontab -e

# Add: run --all every Sunday at 02:00
0 2 * * 0  /usr/bin/env bash -lc 'cd ~/projects/claude-context-plane && .venv/bin/python ~/.claude/skills/context-plane/scripts/maintenance.py --all >> ~/.claude/skills/context-plane/maintenance.log 2>&1'
```

**Option B — run manually when you remember, e.g. at the start of a
Monday morning session:**

```bash
python ~/.claude/skills/context-plane/scripts/maintenance.py --all
```

The output is JSON — easy to pipe into a digest. Personal scale means
weekly is plenty. The EV platform runs this more aggressively because
it deals with 112M rows/day. You don't.

---

## Hook up the TiDB MCP server (optional, recommended)

With the official TiDB MCP server, Claude can also run ad-hoc SELECTs
against your context plane without going through the scripts. Useful
for inspection, debugging, and the "what do you know about X" flows.

Add this to your Claude Code MCP settings (`~/.config/claude/mcp.json`
or via `claude mcp add` — check the current Claude Code docs):

```json
{
  "mcpServers": {
    "tidb": {
      "command": "uvx",
      "args": ["--from", "pytidb[mcp]", "tidb-mcp-server"],
      "env": {
        "TIDB_HOST": "gateway01.<region>.prod.aws.tidbcloud.com",
        "TIDB_PORT": "4000",
        "TIDB_USERNAME": "<prefix>.root",
        "TIDB_PASSWORD": "<password>",
        "TIDB_DATABASE": "claude_context"
      }
    }
  }
}
```

Now Claude can use `db_query` for read-only inspection. **Writes still
go through the scripts** — this preserves duties 1 and 2. The skill
reminds Claude of that rule; don't bypass it.

---

## Troubleshooting

**`ImportError: No module named pytidb`**

Virtualenv not active, or installed to a different interpreter than the
one Claude Code invokes. Test with the exact Python binary Claude Code
uses — in install.sh, scripts run via the shebang (`#!/usr/bin/env
python3`). Either install pytidb system-wide, activate your venv before
starting Claude Code, or edit `install.sh` to rewrite shebangs to your
venv's Python.

**Auto-embedding errors**

The default `tidbcloud_free/amazon/titan-embed-text-v2` is hosted by
TiDB Cloud. If you see `EMBED_TEXT` errors, confirm your cluster region
supports the free embedding model. If not, swap to a BYOK model in
`.env` and drop/recreate the tables (vector dimension will change).

**Skill doesn't trigger**

Claude Code matches the skill's `description` against what you're
asking. If simple phrases like "load my context" aren't triggering,
try a more explicit prompt: *"Use the context-plane skill to load my
memory."* Then look for `~/.claude/skills/context-plane/SKILL.md` in
your Claude Code's `/doctor` or skill-list output.

**Tables not being created**

pytidb creates tables on first `create_table(..., if_exists="skip")`.
If that's failing, run `sql/001_schema.sql` manually via the `mysql`
CLI as a fallback. The DDL is in sync with the pytidb models.

---

## Where things live after install

```
~/projects/claude-context-plane/        ← the repo you cloned (source of truth)
├── skill/                               ← edit here, then re-run install.sh
└── .env                                 ← your credentials (never committed)

~/.claude/skills/context-plane/          ← the installed skill (copy of skill/)
├── SKILL.md
├── references/
├── scripts/
└── .env → ~/projects/claude-context-plane/.env   (symlink)
```

The repo is the source of truth. The installed skill is a deployment
artifact. When you iterate on `SKILL.md` or a script, edit in the repo,
then `./install.sh` again.
