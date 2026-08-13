# Bootstrap Knowledge Pack

Self-contained identity/instruction pack that turns a fresh Hermes profile into
a productive instance. It mirrors the repo's existing `docker/SOUL.md` seeding
convention: `SOUL.md` ships in the repo as a **seed source** and is copied into
`$HERMES_HOME` so the real loader picks it up.

## How SOUL.md is actually loaded (the real mechanism)

The loader is `load_soul_md()` in `agent/prompt_builder.py`:

```python
soul_path = get_hermes_home() / "SOUL.md"      # agent/prompt_builder.py:2142
if not soul_path.exists():
    return None
content = soul_path.read_text(encoding="utf-8").strip()
```

- **Location**: `<HERMES_HOME>/SOUL.md` — i.e. `~/.hermes/SOUL.md` for the
  default profile, or `~/.hermes/profiles/<name>/SOUL.md` for a named profile.
  It is **not** loaded from anywhere else; a SOUL.md that lives only in the
  repo tree is inert until it is copied into `$HERMES_HOME`.
- **Format**: plain Markdown. Optional YAML frontmatter is stripped before
  injection (`_strip_yaml_frontmatter`), so keep the actual instructions in the
  body. Content is run through a prompt-injection scanner and truncated to the
  `context_file_max_chars` budget (dynamic, floor 20K chars) — keep it lean,
  it is paid on every API call.
- **Identity slot**: `agent/system_prompt.py` builds the stable tier of the
  system prompt. When `load_soul_md()` returns content it **replaces** the
  hardcoded `DEFAULT_AGENT_IDENTITY` (`agent/prompt_builder.py:144`); when
  absent/empty, the built-in identity is used. SOUL.md is loaded fresh each
  message, so edits take effect immediately — no restart.
- **Seeding precedents** in this repo (the pack follows the same pattern):
  - `hermes profile create <name>` writes `DEFAULT_SOUL_MD` into the new
    profile (`hermes_cli/profiles.py:1169`).
  - First-run `_ensure_default_soul_md()` seeds `DEFAULT_SOUL_MD` into a fresh
    `$HERMES_HOME` (`hermes_cli/config.py:840`, template in
    `hermes_cli/default_soul.py`).
  - Docker containers seed `docker/SOUL.md` into the data volume on first boot
    (`docker/stage2-hook.sh`: `seed_one "SOUL.md" "docker/SOUL.md"`).
  - `install.sh` / `install.ps1` create `SOUL.md` if missing.

## Pack structure

```
docs/bootstrap/
├── SOUL.md          # identity + operating principles (the instruction file)
├── README.md        # this file — structure, loader, apply instructions
└── manifest.yaml    # machine-readable file inventory (files → roles)
```

`manifest.yaml` is the discovery entry point for tooling: it lists every file
in the pack, its role, and where it should land in a target profile.

## Applying the pack to a new profile

Pick the target, then copy `SOUL.md` into place. The loader reads only
`$HERMES_HOME/SOUL.md`, so the copy **is** the install.

**Default profile** (overwrites the seeded default — back it up first if
customized):

```bash
cp docs/bootstrap/SOUL.md ~/.hermes/SOUL.md
```

**Named profile** (create it, then seed it):

```bash
hermes profile create <name>
cp docs/bootstrap/SOUL.md ~/.hermes/profiles/<name>/SOUL.md
hermes -p <name>   # start the profile; SOUL.md is now live
```

**Fresh container or NixOS install**: drop the file at the same `$HERMES_HOME`
location, or wire it through the NixOS `documents."SOUL.md"` option which
installs it into the working directory.

**Note on `--clone`**: `hermes profile create --clone` copies the *active*
profile's `SOUL.md` — use a bare `hermes profile create` (or `--clone` from a
profile that already has this identity) to avoid inheriting another persona.

## Verifying

After copying, confirm the instance picked it up:

```bash
# shell: does the target file exist?
ls -l "$HERMES_HOME/SOUL.md"

# python: does the loader return it? (set HERMES_HOME for the target profile)
HERMES_HOME=~/.hermes python -c \
  "from agent.prompt_builder import load_soul_md; print(bool(load_soul_md()))"
```

A truthy output means the file is being injected as identity slot #1. Delete
`SOUL.md` to fall back to `DEFAULT_AGENT_IDENTITY`.

## Customization

`SOUL.md` sets **identity**, not project rules. It is loaded globally for the
profile regardless of working directory — keep project-specific instructions in
`AGENTS.md` / `CLAUDE.md` / `.hermes.md` at the project root, and keep the
profile's persona here. Edit freely; the file is re-read every message.
