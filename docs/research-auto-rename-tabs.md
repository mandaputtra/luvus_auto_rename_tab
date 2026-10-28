# Research: auto-rename tabs port (herdr-auto-title → Luvus module)

Goal: a Luvus module that auto-names tabs (and panes), porting
[kryptamine/herdr-auto-title](https://github.com/kryptamine/herdr-auto-title).
Primary sources only. Luvus side verified against installed binary
(`luvus --version` → `0.14.3`); Herdr side from repo docs + source at `main`
(manifest `version = "0.16.0"`).

## (a) Luvus module/plugin contract

Module = plain directory with one manifest, `luvus-module.toml`. No SDK or
runtime; any executable. **"The luvus CLI is the module API"** — a command
reads injected `LUVUS_*` env and mutates state by shelling out to `luvus`.
Source: https://luvus.dev/docs/extend/writing-modules/ (top, "A module",
"The luvus CLI is the module API").

### Manifest (`luvus-module.toml`)

Top level, all required unless noted (`id`, `name`, `version`,
`min_luvus_version`; optional `description`, `platforms`):
`id` `[a-z0-9:._-]` ≤120 chars, dots allowed. Filename is `luvus-module.toml`.
Source: https://luvus.dev/docs/extend/writing-modules/ ("The manifest",
"Top level").

| Table | Purpose | Rename-port relevance |
|---|---|---|
| `[[actions]]` | On-demand argv commands. Local `id` (no dots); invoked as `luvus module run <module-id> <action-id>` (qualified `{module}.{action}`). Fire-and-forget, returns `log_id`; output via `module log`. | Ship a `restart` action like herdr's (see below). Optional `commander` and `contexts` (right-click) not needed. |
| `[[startup]]` | One-shot, runs once session is restored and API socket listens; **also runs on link/re-enable**. Explicitly **not supervised daemons**; failure logged, server continues. | The poller launch point. Pattern: startup action spawns the long-lived loop in background (a background child keeps running after the action exits; redirect its stdio so the action slot frees — max 32 in flight, 200 log entries kept). |
| `[[events]]` | `on = "<event>"`, command runs with `LUVUS_MODULE_EVENT` + `LUVUS_MODULE_EVENT_JSON` payload. | Available `on` values per docs: `workspace.created/closed` (`node.*` legacy aliases), `tab.created`, `tab.closed`, `pane.created`, `pane.closed`, `pane.agent_status_changed`, `agent.hook`, `task.*`, `lease.*`. **No `tab.renamed` / `pane.renamed` in the module table** (see gaps). |
| `[[settings]]` | Typed `bool`/`string`(`secret`)/`number`(`min/max/step`)`/`enum`(`options`). Rendered in Settings → Modules; values via `LUVUS_SETTING_<KEY>` + `LUVUS_MODULE_SETTINGS_JSON`; persisted in `$LUVUS_MODULE_CONFIG_DIR/settings.json`; `luvus module settings <id> [key] [value]` CLI. | Replaces herdr's `config.env` file with native typed settings (poll ms, max length, toggles). |
| `[[panes]]` / `[[docks]]` / `[[bars]]` | Long-lived UI pane; sidebar panel (module pushes rows); chrome widget (`bar push`). | Not needed for v1. |
| `[[build]]` | Install-time setup, scrubbed env. | Only if the poller needs vendored deps (Go build analog). |
| `[worktree_provider]` | Synchronous worktree create/remove. | Irrelevant. |

Validation rejects: empty commands, `min_luvus_version` newer than host,
bad id chars / dots in local ids, duplicate ids, `platforms = []`, unknown
`contexts`. Source: same page ("Validation").
Module lifecycle CLI (`luvus help all`, `luvus help module`): `module link |
install <owner>/<repo>[/sub] [--ref] | unlink | uninstall | enable |
disable | list | info | actions | run | log | config-dir | settings`.

### Environment (source: same page, "Environment variables", "The context blob")

Identity: `LUVUS_MODULE_ID/VERSION/ROOT` (= cwd), `LUVUS_MODULE_CONFIG_DIR`
(durable config), `LUVUS_MODULE_STATE_DIR` (durable state — manual-name locks
belong here, **not** the module root, which is a managed checkout for
git-installed modules), `LUVUS_SOCKET_PATH`, `LUVUS_BIN_PATH` (call back via
this, never a PATH lookup), `LUVUS_MODULE_TOKEN` (auto-forwarded; do not
persist). Context: `LUVUS_WORKSPACE_ID/CWD`, `LUVUS_TAB_INDEX` (1-based),
`LUVUS_PANE_ID/CWD/AGENT/STATUS`, full blob `LUVUS_MODULE_CONTEXT_JSON`
(`{workspace, node(alias), tab{index,name}, pane{id,cwd,agent,status},
selection, invocation_source, correlation_id}`).
Per-invocation: `LUVUS_MODULE_ACTION_ID / ENTRYPOINT_ID / EVENT /
EVENT_JSON / ROW_*`, plus `LUVUS_SETTING_*`.

### Exact rename-relevant commands (all verified live, luvus 0.14.3)

- `luvus tab rename <name> [--tab N]` — names current or Nth tab (1-based
  positions); **empty clears it**. Verified: `luvus help tab`; UHP
  `tab.rename {"tab":1,"name":"probe-rename-test"}` → `{"type":"ok"}`,
  `tab list` showed `"name":"probe-rename-test"`; `tab.rename
  {"tab":1,"name":""}` → ok (cleared). UHP method `tab.rename` in
  capabilities method list (197 methods; `luvus uhp capabilities`).
- `luvus pane name <name> [--pane <id> | --clear]` (operator alias) and UHP
  `pane.rename`. **Verified semantics differ from Herdr:** `pane.rename
  {"pane":"4","name":"probe-pane-label"}` → `{"type":"pane_rename"}` and the
  subsequent `session.snapshot` showed it as `agent_name:
  "probe-pane-label"`; clearing (`"name":""`) → `"name":null`. Per the UHP
  schema `$comment`
  (`https://luvus.dev/protocol/uhp/v1/event-catalog.schema.json`, via `luvus
  uhp schema`): snapshot `agent_name` is a read projection of operator
  aliases; `pane.rename` updates that map, **emits `pane.renamed`, advances
  `event_sequence`** (even on clear); `agent.name` is silent (no event, no
  sequence advance).
- `luvus pane processes [<id>]` / UHP `pane.processes` — **cached executable
  identities only, no arguments**. Verified live: `{"executables":["zsh"],
  "arguments_exposed":false, ...}`. (Biggest functional gap vs Herdr —
  see §c.)
- `luvus uhp snapshot` / UHP `session.snapshot` — whole session; verified pane
  keys: `agent, agent_authority, agent_name, agent_session,
  agent_session_title, agent_status, content_revision, cwd, focused,
  is_agent, kind, pane_id, root_process{pid,start_marker}, terminal_id`.
  **No terminal/OSC title field, no `foreground_cwd`.** `agent_session_title`
  is live session title, ≤160 chars, display-only (`luvus uhp schema` →
  methods docs; `agent_session_title` documented as single line ≤160 chars,
  targeting by pane id/terminal id/alias still required).
  Workspace rows carry `branch` (observed `null` and `"bugfix/8718-…"`) and
  `cwd`; tab rows carry stable `tab_id` (`"identity":{"tab":"stable"}` in
  capabilities) and nullable `name` (null = default position label).
- `workspace.report_metadata {workspace_id, source, tokens, ttl_ms}` —
  Herdr-topic analog exists (in capabilities method list). Sidebar `$topic`
  rendering unverified (gap §c).
- `ui agent-title push --titles '[{"pane"|"agent"+"session_id","title"}]'` —
  publishes onto AGENTS sidebar rows; **OSC titles win**, then pane title,
  then module title; empty clears; module token auto-authenticates; repaint
  from `[[startup]]` + `pane.agent_status_changed`. Source: writing-modules
  "AGENTS sidebar titles".
- Events (63 names in schema catalog via `luvus uhp schema`): relevant ones
  include `tab.created/closed/moved`, `pane.created/closed/moved/renamed/
  focused/agent_status_changed`, `terminal.created/output_ready/
  metadata_changed`, `workspace.metadata_reported`, `agent.hook`,
  `agent.authority_reported`. Raw `events.subscribe` takes **only
  `after_sequence`** (verified: `{"events":[...]}` →
  `invalid_params: runtime event subscription accepts only after_sequence`;
  future sequence → `after_sequence is newer than the current event
  sequence`). Loss/resume: `{"loss":"resync_required","resume":
  "after_sequence"}` (capabilities).
- Discovery authorities: `luvus --version`, `luvus help all`,
  `luvus uhp capabilities`, `luvus uhp schema`, `luvus uhp snapshot`
  (per https://luvus.dev/agent-readme.md). `luvus skill show` prints the
  bundled delegation skill (route `=target`, session selection,
  read-before-mutate); module-authoring skill is separate at
  `https://github.com/RizRiyz/luvus/blob/main/skills/luvus-module/SKILL.md`
  (writing-modules page, "Author with your AI agent").

## (b) herdr-auto-title design, distilled

Repo: https://github.com/kryptamine/herdr-auto-title (`herdr-plugin.toml`:
`id="herdr.auto-title"`, `version="0.16.0"`, `platforms=[linux,macos,windows]`,
`[[build]] go build`, `[[startup]]` one-shot launch, `[[actions]] restart`
`contexts=["global"]`). Entrypoint
`cmd/herdr-auto-title/main.go`: `run()` loads config, claims the session
(`instance.Take`), starts `App.Run` poll loop; `restart` subcommand spawns a
successor + `notification.show`.

### Poll loop (docs/architecture/poll-loop.md)

One long-lived process, whole-session `session.snapshot` every
`HERDR_AUTO_TITLE_POLL_MS` (default 500 ms); no scrollback scanning, no LLM.
Per poll: snapshot → `Changes.Observe` (which panes' revisions advanced) →
`Labels.Pass` (drop dead tabs/panes) → assemble tabs → per tab pick context
pane, resolve title, `tab.rename` iff changed → per tab name panes (if
enabled) → report workspace topics (if enabled). Bounded by `PollTimeout`
5 s; `tab_not_found`/`pane_not_found` treated as benign races.
**Events deliberately unused**: subscribe replays ~10 s of backlog per pane
with no skip cursor. Cost measured ~0.47 ms + 6 KB per snapshot (6 panes);
per-pane `pane.process_info` ~0.11–0.17 ms each — hence reuse of process
answers until revision moves, bounded by `processRefresh` 2 s
(**revision ≠ process change**: measured 9 process changes vs 4 revision
moves; one pane went `env→node→esbuild→fish` with revision stuck at 10).
State carried between polls only: per-pane last-change time, last process
read, transcript offsets (+ per-session topic), last-set labels (manual-rename
book), workspace topic reports (TTL 60 s, refresh 20 s). Outage behavior:
one request per connection so nothing to reconnect; failures just skip; log
backoff; missing socket path is the only fatal. Successor detection: socket
file identity (dev+inode / Windows pid+start-time marker) + a claim file;
new server → old instance exits 0. Interval = rename rate (≤1 rename/tab/poll).

### Title resolution (docs/architecture/title-resolution.md)

Title = `N · part › part …`: leading 1-based position (not part of the
title), `›` (`Separator`) throughout. Structurally
`Parts{Context, Branch, Agent, Activity}` in that order; pane labels add
optional `Handle` (agent alias). **Context pane** (`TabFrom`): focused →
`working`/`blocked` agent pane → tokenless pane (plugin docks report
`pane.report_metadata` tokens and redraw constantly) → most-recently-changed;
ties by pane id. `PREFER_AGENT` puts any-state agent pane first.
**Confidence ladder** (higher never overridden; lower completes the other
half): agent title 90 → terminal title 80 → session transcript 75 →
foreground process 70 → remote session (ssh/mosh) 60 → git branch 40 →
working directory 30 → `Shell` fallback 10. Per-agent terminal-title grammars:
Codex `<project>` / `<name> | <project>`, `[ ! ] Action Required` blink and
braille spinner stripped, title cut at 48 chars so transcript wins when it
has the full name; Grok `<name> - grok` vs spinner-status frames; Pi
`π - <dir>` / `π - <name> - <dir>`. Transcript lines: Claude
`custom-title` > `ai-title` > first human-typed prompt (`origin.kind:
"human"` only; slash-command args kept); Codex `session_index.jsonl` then
first `user_message`, newest rollout wins; Grok `summary.json
generated_title`; Pi `session_info.name` then first prompt. Branch from the
agent's worktree dir when the agent works in one (transcript-gated);
**default branch omitted**, shortened (`bugfix-…-MC-13675` → `MC-13675`).
`$HOME` → `Shell`. Icons/status/agent-name/handle/pane-id are opt-in
prefixes.

### Manual-rename protection (docs/architecture/manual-rename-protection.md)

No events — a rename is a label that changed between polls. Compare
**Current** (snapshot) vs **Desired** (resolver) vs **Seen** (last observed):
label == Desired → not user's; == Seen → untouched; else user's → lock.
Details: **first poll never locks** (startup would claim everything); new
tab already carrying non-default label = named by creator (vs first-poll
exemption). **Default label = position**, not `TabInfo.number` (which counts
all-time and skips) — count tabs in display order; **empty label = unnamed
too** (clearing hands the tab back). Late-landing renames: requests sent but
unanswered (`ErrUnanswered`) are remembered (`claims.sent`) so a stale number
isn't misread as the user's. Labels left by label-owning programs
(herdr-mirror, `FOREIGN_PROCESSES`) are marked owned and the first post-exit
judgment is the plugin's. Locks persist (`manual-names.json`, atomic
write) **guarded by the label** (stale ids from a reused session release);
renames made while the plugin is down are deliberately forgotten. Tab and
pane locks are separate claims (naming a tab doesn't claim its panes); pane
unnamed spelling is single (`""`); moved panes (new id, old label) are
recognized via retained labels until the poll settles. Nothing expires.

### Configuration (docs/architecture/configuration.md, config.env.example)

17 `HERDR_AUTO_TITLE_*` vars (poll ms 500, max length 50 cols, branch max 12,
directory max unset, position on, manual file, transcript on, agent-name off,
panes on, prefer-agent off, pane-id off, agent-handle off, icons off,
agent-status off, workspaces on, workspace max unset, claude-dirs, foreign
processes) + passthrough `CLAUDE_CONFIG_DIR/CODEX_HOME/GROK_CONFIG_DIR`.
File `config.env` searched `$XDG_CONFIG_HOME` (absolute only) → `~/.config`
→ `os.UserConfigDir()`; **env wins**; read once at startup; `godotenv`,
one bad line sinks the file; Herdr's own plugin config dir deliberately
unused (invisible to hand-run debugging). Restart action applies changes.

### Sanitization (docs/architecture/sanitization.md)

Single gate `Sanitize` (`internal/resolver/sanitization`): strip ANSI,
normalize spaces, drop controls + invisible format chars (keep ZWJ), collapse
whitespace/separator runs, trim, truncate by **columns over grapheme
clusters** (`rivo/uniseg`), never leave dangling `›`. `Meaningful` rejects:
locations (absolute/home/URI paths — with 3 Windows-path continuation rules —
relative paths survive), bare program/shell names (agent echoing its own name
rejected by comparison with the recognized agent, not a list), shell prompts.
Nothing terminal-derived ever reaches a shell; renames go over the socket API.

### Socket API (docs/architecture/herdr-socket-api.md)

NDJSON, **one request per connection**, `params` required even if empty;
Herdr applies requests even if the caller gave up (hence `ErrUnanswered`).
Six methods used: `session.snapshot`, `pane.process_info` (name+argv+cwd per
process, fg group matched by `foreground_process_group_id`; Windows: shell or
recognized agent only), `tab.rename{tab_id,label}`,
`pane.rename{pane_id,label}` (goto-panel label; newline stored verbatim —
keep titles one line), `notification.show` (restart report; `shown` ≠
delivered), `workspace.report_metadata{workspace_id,source,tokens,ttl_ms}`
(`topic` token under source `herdr.auto-title`; sidebar renders where rows
ask `$topic`). Env inherited from server (not user shell) — the reason for
the config file. Tab ids belong to a session (hence label-guarded locks).

## (c) Port mapping: what transfers, what gaps remain

### Maps ~1:1

- **Manifest + lifecycle**: `luvus-module.toml` (+`[[startup]]` one-shot,
  +`restart`-style `[[action]]`) ≡ `herdr-plugin.toml`. Luvus `[[settings]]`
  is strictly nicer than `config.env` for the 17 knobs.
- **Poller shape**: startup action spawns the background loop; CLI-as-API
  (`tab rename`, `pane rename`, `uhp snapshot`, `workspace report-metadata`)
  covers all six Herdr methods except full `process_info` (below).
  Herdr's measured costs (≈0.5 ms snapshot, ≈0.15 ms/read) justify the same
  poll-then-diff design at 500 ms; `content_revision` gives the recency
  signal, `focused` + `agent_status` (idle/working/blocked/done) give the
  `TabFrom` rules 1–2 directly.
- **Manual-rename core**: Current/Desired/Seen triple, first-poll exemption,
  position-means-unnamed (verified `name:null` + 1-based `--tab N`), empty
  clears (verified for both tab and pane), label-guarded persisted locks in
  `LUVUS_MODULE_STATE_DIR`, separate tab/pane claims, late-landing
  (`ErrUnanswered`-equivalent: treat send-but-unconfirmed as ours) —
  all portable. Luvus stable `tab_id` removes Herdr's id-reuse hazard, but
  keep the label guard anyway.
- **Resolution + sanitization**: ladder, per-agent title grammars, transcript
  readers (agent file formats are host-independent), branch shortening,
  `›` format + position prefix, column-based truncation — pure logic, ports
  verbatim. Snapshot already gives `agent_session_title`, `branch`, `cwd`,
  `agent_authority` (integration_report ≈ "has transcript session" signal).
- **Topics**: `workspace.report_metadata` exists with `tokens` + `ttl_ms`.

### Gaps / open questions (verify before building)

1. **`pane.processes` hides argv and cwd** (`arguments_exposed:false`,
   verified live) — Herdr's process source needs argv (ssh host, `nvim
   file`, script names) and the fg process's cwd. **Open:** does
   `terminal.backend.processes` expose more? (`luvus uhp schema` lists the
   method; unprobed.) Fallbacks if not: `pane.read` scrollback parsing, or
   shell-integration OSC titles.
2. **No terminal/OSC title in `session.snapshot`** (verified pane keys).
   **Open:** does `terminal.backend.snapshot/capture` expose the OSC title?
   Note `agent_session_title` (≤160 chars, display-only) and
   `ui agent-title push` (module-published, loses to OSC) as partial native
   substitutes.
3. **Module `[[events]]` vocabulary**: docs list only created/closed/status
   hooks — `pane.renamed`, `terminal.output_ready`, `terminal.metadata_changed`
   exist in the UHP catalog (verified via `luvus uhp schema`) but are not in
   the module table. Polling (like Herdr) sidesteps this; confirm before
   relying on event-driven renames.
4. **Topic rendering**: `workspace.report_metadata` confirmed, but Herdr's
   `$topic` sidebar-row mechanism has no verified Luvus counterpart — check
   `docs/reference/configuration` / bar docs for token rendering.
5. **Pane identity/stability**: `pane_id` observed as small numeric strings;
   stability across moves and the `terminal_id` (pty-lifetime per
   capabilities `identity`) vs `pane_id` distinction need one probe each for
   the moved-pane logic.
6. **Per-session scoping**: startup hooks run per session restore; confirm
   one poller per session (key state by `LUVUS_SOCKET_PATH`) and that
   `LUVUS_MODULE_STATE_DIR` is per-session (Herdr's state dir was
   per-plugin, forcing socket-keyed claims).
7. **Windows**: Herdr notes apply unchanged (process info degrades to
   shell/agent); no extra work expected.
8. **`luvus` not installed** → point at
   https://luvus.dev/docs/getting-started/installation/ (per agent-readme);
   installed here, so all CLI claims above are primary observations, not docs
   hearsay.
