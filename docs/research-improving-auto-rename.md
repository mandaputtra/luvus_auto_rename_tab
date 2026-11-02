# Research: improving `luvus_auto_rename_tab`

Goal: close or confirm the 8 gaps in `docs/research-auto-rename-tabs.md` §c,
diff what upstream `kryptamine/herdr-auto-title` (now v0.18.0) has that this
port trimmed, and rank follow-ups. Read-only research; no code changed.

Primary sources only. Luvus side verified against the installed binary
(`luvus --version` → `luvus 0.14.3`) via `luvus uhp capabilities`,
`luvus uhp schema`, `luvus uhp snapshot`, `luvus help <topic>`, live
`luvus uhp proxy` probes, and https://luvus.dev/docs. Upstream side from
`https://github.com/kryptamine/herdr-auto-title` at `main` (manifest
`version = "0.18.0"`, HEAD `0f1794f1`, 2026-10-30) and
`docs/architecture/*.md` at that ref. Repo state: `auto_rename.py` (577
lines), `luvus-module.toml` 0.3.0, ADRs 0001/0002.

## 1. Gap-by-gap verdicts

### Gap 1 — argv/cwd from `pane.processes`: STAYS CLOSED

`luvus uhp capabilities` (197 methods) lists both `pane.processes`
(scope `read`) and `terminal.backend.processes` (scope `terminal`).
Correct proxy envelope is `{"id","method","params"}` over
`luvus uhp proxy` (the `jsonrpc: "2.0"` form is rejected with
`invalid_request: invalid versioned API request envelope`).

- `pane.processes {}` (no params; `{"pane_id":…}` is rejected as
  `unknown parameter`) returns `{"executables":[…],"arguments_exposed":false,…}`
  — confirmed live: a zsh+omp pane returned
  `executables: ["zsh","omp"], arguments_exposed: false, scan: "observed"`.
- `terminal.backend.processes` with the locator from
  `terminal.backend.snapshot` (`server_generation`, `terminal_id`, `pane_id`)
  returns the same shape: `arguments_exposed: false` on every pane probed
  (e.g. pane 3 `["zsh","moon","air","api"]`; pane 4
  `["zsh","moon","node","pnpm.cjs","bin.js","esbuild"]`), `scan: "observed"`.
  Per-process `argv`/`cwd`/`pid` objects do not exist in this API.

So herdr's ssh-host parsing (`ssh -p 2222 prod-01` → `prod-01`),
editor-file extraction (`nvim auth.provider.ts`), and per-process `cwd`
reads have no data source. `pane.read` / `terminal.backend.capture` return
screen text, not argv — a fallback, not a replacement (see §3, could-do).
**Verdict: unclosable on the current protocol. Keep the leaf-executable
ladder; revisit only if a future `terminal.backend.processes` exposes argv.**

### Gap 2 — terminal title: CLOSED, big win available

`terminal.backend.snapshot {}` (one call, whole session) returns per-terminal
records with **`terminal_title`** plus `cwd`, `pane_id`, `content_revision`,
`root_process{pid,start_marker}`, `tab{index,name}`,
`workspace{index,name,root}`. Verified live on 14 terminals, e.g.:

- pane 3: `moon run api:dev`
- pane 4: `moon run web:dev`
- pane 2 (Pi agent): `π > 🔀 ACT: Map permit activity flows`
- pane 6 (plain shell): `mandaputra@Mandas-MacBook-Pro:~/Code/project_a`

`session.snapshot` still carries no OSC title (pane keys confirmed:
`agent, agent_authority, agent_name, agent_session, agent_session_title,
agent_status, content_revision, cwd, focused, is_agent, kind, pane_id,
root_process, terminal_id` — no title field), so the backend snapshot is
the only source. Herdr ranks terminal title at confidence 90 (agent
title) / 80 (terminal title), above transcript (75) — see upstream
`docs/architecture/title-resolution.md` ("The confidence ladder"; agent
title "outranks everything"; terminal title "the richest source in
practice"). Upstream's per-agent grammars live in the same doc: Codex
`<project>` vs `<name> | <project>`, `[ ! ] Action Required` blink and
braille spinner stripped, cut at 48 cols so the transcript wins when it has
the full name; Grok `<name> - grok` vs spinner-status frames; Pi
`π - <dir>` / `π - <name> - <dir>`.

Two corollaries from live data: (a) the backend title already contains the
agent's own topic (`π > …`), which duplicates what the transcript reader
produces — the merge needs the same "higher source wins, lower completes the
other half" rule and the `Meaningful` rejections (shell prompts like
`user@host:~/dir`, bare program names), or titles regress to prompt text on
plain shells; (b) shell-prompt-shaped titles are common
(`mandaputra@Mandas-MacBook-Pro:~/Code/project_a`), so porting upstream's
prompt/location rejection is a prerequisite, not optional polish.

**Verdict: closable now. One extra `terminal.backend.snapshot` per poll
(same ~8.5 ms cost class as `session.snapshot`, measured §4) restores the
two top ladder rungs.**

### Gap 3 — event-driven renames: CONFIRMED, keep polling

Module `[[events]]` vocabulary (https://luvus.dev/docs/extend/writing-modules/,
"[[events]]: react to lifecycle events") is unchanged: workspaces
created/closed, tabs created/closed, panes created/closed/
`agent_status_changed`, `agent.hook`, `task.*`, `lease.*`. No `pane.renamed`,
no `terminal.output_ready`, no `terminal.metadata_changed` in the module
table — while the UHP event catalog does carry `terminal.output_ready`
(observed streaming live: a `luvus uhp events` subscription delivered a
`terminal.output_ready` per pane redraw with `content_revision`,
`pane_id`, `terminal_id`, `workspace`, `tab`). ADR 0001's reasoning holds:
title-relevant signals are per-redraw firehoses with no skip cursor, so
polling stays correct. One refinement: `pane.agent_status_changed` (payload
`{pane, status, agent, cwd, project, branch}` per the writing-modules doc)
is the highest-value hook and could drive a fast-path poll or an AGENTS/title
repaint, but it cannot replace the poll loop.

**Verdict: confirmed unclosable as a replacement; keep the 500 ms poll.**

### Gap 4 — workspace topics: STAYS CLOSED (feature absent in 0.14.3)

`workspace.report_metadata` exists in capabilities (scope `workspace`) but
accepts **only** `{"workspace_id"}` in this build — every other key is
rejected live: `tokens` → `unknown parameter: tokens`,
`source` → `unknown parameter: source`, `ttl_ms` → `unknown parameter:
ttl_ms`, `metadata`/`topic`/`token`/`title` likewise. Called with the bare
workspace id it behaves as a **read** (returns the workspace row). No
`workspace report-metadata` CLI subcommand exists (`luvus workspace --help`
lists only list/new/open/focus/rename/pin/unpin/close). `session.snapshot`
workspace rows carry no `tokens`/`topic` key (verified: no matches for
`metadata|topic|tokens|ttl` anywhere in the snapshot). There is no `$topic`
sidebar-row mechanism in the docs: `docs/reference/configuration/` documents
`layout.workspace_paths` (cwd line under WORKSPACES rows) but no token
rendering; the bar guide (`docs/guides/bar/`) covers module widgets, not
workspace rows.

Upstream semantics for reference (`herdr-socket-api.md`, "What the objects
carry"): `workspace.report_metadata{workspace_id, source, tokens, ttl_ms}`,
token keyed by name alone, `topic` token drawn via a `$topic` entry in
`ui.sidebar.spaces.rows`, TTL 60 s with 20 s refresh. None of that surface
exists here yet.

**Verdict: unclosable — the write path does not exist in 0.14.3. Do not
build topic code until the method grows params; then port upstream's
TTL/refresh discipline verbatim.**

### Gap 5 — pane identity/stability: PARTLY CLOSED

`luvus uhp capabilities` → `identity: {tab: "stable", terminal:
"pty_lifetime", workspace: "stable"}`. `pane_id` stability across moves was
not probed (moving panes around would disturb the live session), but two
facts reduce the need: `tab.rename` takes a **position** (`{"tab": N}`,
1-based; verified `tab.get`/`tab.list` show `tab: "1"` positions alongside
stable `tab_id: "tab_…"`), and `session.snapshot` tabs arrive in display
order — so the current position-keyed locks (`ws/name + pos`) already match
the addressing the rename uses. `terminal_id` (pty-lifetime) is available in
both snapshots as the durable pane handle if a future moved-pane rule needs
it. **Verdict: no action; position keys + pty-lifetime `terminal_id` cover
the moved-pane cases the port cares about.**

### Gap 6 — per-session scoping: CONFIRMED, needs one probe

`LUVUS_SOCKET_PATH` is per-session (`~/.luvus/luvus.sock` vs
`~/.luvus/sessions/<name>/luvus.sock`; method reference,
https://luvus.dev/docs/uhp/methods/, "Wire format"). The poller keys nothing
by socket today: `claim_pidfile` + `manual-names.json` both live in the
single `LUVUS_MODULE_STATE_DIR`. Whether that dir is per-session or shared
is still unverified (one `echo $LUVUS_MODULE_STATE_DIR` from two sessions
settles it). If shared, two sessions' pollers fight over one pidfile and one
lock file — the lock-poisoning shape ADR 0002 already fixed once for the
two-poller case. **Verdict: one-command probe open; key state dir by socket
hash if shared.**

### Gap 7 — Windows: CONFIRMED, no work

No Windows host here to probe, and upstream's note stands
(`herdr-socket-api.md`: Windows process lists degrade to shell/recognized
agent; the directory/title rungs still hold). The port reads no process
table beyond leaf names and leans on transcripts + titles, which is exactly
the subset that survives. **Verdict: correctly dropped; nothing to port.**

### Gap 8 — `luvus` missing: NOT APPLICABLE

Installed (`luvus 0.14.3`). The install pointer
(https://luvus.dev/docs/getting-started/installation/) stays as the fallback
note. **Verdict: closed by environment.**

## 2. Upstream delta: what the port trimmed, and what to (not) port back

Upstream is now at 0.18.0 (was 0.16.0 when the port was researched). Visible
delta since: release binaries attached to releases, `--version` reporting,
`stable` branch automation, grok percent-encoding + status-line fixes
(commit list, `f0779d9e`–`4fbe41d5`). Architecture docs unchanged in shape;
the ladder, poll loop, manual-rename, sanitization, socket, and configuration
docs read the same.

Trimmed features, assessed:

1. **Terminal-title rung + per-agent grammars (port back).** The largest
   trimmed capability and now unblocked (§1 gap 2). Upstream
   `title-resolution.md` §§ "Terminal title", "What each source knows":
   Codex/Grok/Pi grammars, blink/spinner stripping, prompt-shaped-title
   refusal. Worth porting with the `Meaningful` rejections, since raw titles
   include shell prompts.
2. **Context-pane rules (port back, cheap).** Upstream `TabFrom`
   (`title-resolution.md`, "One pane speaks for the tab"): focused →
   working/blocked agent → tokenless pane → most-recently-changed, ties by
   pane id; `PREFER_AGENT` opt-in. The port uses first-pane-always
   (`auto_rename.py:313-315`). The snapshot already carries `focused` and
   `agent_status` (idle/working/blocked/done), and `content_revision` gives
   recency — the "Maps ~1:1" row in the prior §c already noted this. A
   focused/tab-status rule is ~10 lines and fixes multi-pane tabs with no
   new I/O. Tokenless-pane detection needs `tokens`, which no Luvus snapshot
   surface exposes — skip that rule (no data), keep the other three.
3. **`Meaningful` rejections (port back with #1).** Upstream
   `sanitization.md`: locations (absolute/home/URI paths, Windows
   continuation rules), bare program/shell names (agent echo compared
   against the recognized agent, not a list), shell prompts. The port's
   `sanitize` (`auto_rename.py:88-94`) strips ANSI/controls/separators but
   accepts anything well-formed. Required before trusting backend titles.
4. **Process-answer caching with revision + 2 s bound (port back).**
   Upstream `poll-loop.md`: reuse per-pane answers until `content_revision`
   moves, bounded by `processRefresh` 2 s, because revision ≠ process change
   (measured 9 changes vs 4 revision moves). Directly applicable — see §4.
5. **Branch-from-agent-worktree + default-branch omission (correctly
   dropped for now).** Upstream `title-resolution.md` ("The git branch"):
   reads `.git/HEAD` directly (~0.019 ms vs 12.37 ms `git rev-parse`), agent
   worktree override with `SameRepository` guard, trunk silence via
   `refs/remotes/origin/HEAD`, worktree/branch dedup. The port takes
   `branch` from the workspace row and shortens issue keys
   (`auto_rename.py:273-278`). Luvus workspace rows expose `branch`
   (observed `null` and real values) but no per-pane worktree dir and no
   default-branch marker. A cheap local improvement exists (drop the branch
   when it equals the directory basename — the worktree-twice rule), but
   the full agent-worktree follow has no data source. Correctly dropped.
6. **Pane naming, icons/status/handles/pane-id decorators (correctly
   dropped).** Upstream `title-resolution.md` §§ "A pane is named…",
   "The icon…", "The agent status…", "The pane ID…": pane labels via
   `pane.rename`, Nerd-Font icons, `◐`-style status marks, `[w1:p2]`
   prefixes. Luvus `pane.rename` writes the `@mention` alias
   (`agent_name`), not a display title — the README already records that
   pane auto-naming "caused more blink than value". Status marks would
   rename on every status flip (upstream itself notes the cost and defaults
   them off). Correctly dropped; do not revisit without a display-title
   surface.
7. **Workspace topics (cannot port — see gap 4).** Upstream topic chain
   (`title-resolution.md`, "The workspace topic"): agent name + activity,
   no directory/branch, ssh host leads, empty clears. Blocked on the
   missing write path.
8. **Late-landing renames / `claims.sent` (probably unneeded).** Upstream
   `manual-rename-protection.md`: a rename applied after its call gave up
   (`ErrUnanswered`) locked tabs on stale numbers. The port's
   success-gated `seen` (ADR 0002; `auto_rename.py:463-470`) handles the
   inverse; `luvus` CLI calls over a local socket either answer or fail
   before reaching the server, and no `ErrUnanswered` analog was observed.
   Correctly dropped unless late-landings are ever seen live.
9. **Label-owning programs / `claims.owned` (correctly dropped).** No
   `herdr-mirror` analog writes Luvus tab labels today. Correctly dropped.
10. **Antgravity annotations reader (skip).** Upstream added
    `internal/antigravity` (`.pbtxt` title file). No evidence of agy panes
    in this environment; the transcript-reader pattern ports trivially if
    one appears. Skip.

## 3. Ranked recommendations

### Must-do (correctness)

1. **Scope poller state per session (gap 6).** Run one probe: compare
   `$LUVUS_MODULE_STATE_DIR` across two sessions (`luvus --session <name>`
   or two panes). If shared, key `poller.pid` and `manual-names.json` by a
   socket-path hash. Precedent: ADR 0002's two-poller lock poisoning; a
   second session would re-create it. Cost: ~5 lines. Source: method
   reference wire-format section (socket path is per-session);
   `auto_rename.py:522-525` (single shared pidfile/lock file).
2. **Fix `poll_once` iterating every workspace while renaming only the
   active one.** `auto_rename.py:431-470`: the loop reads all workspaces but
   `tab.rename` by position is active-workspace-scoped (`tab position is out
   of range` / cross-workspace ids → `not_found` per README). Background
   workspaces burn a `pane.processes` call per tab per poll for titles that
   cannot land. With per-tab ~7.6 ms process calls (measured §4), a
   10-tab background session costs ~76 ms/poll of pure waste. Gate the
   per-tab work on `ws.get("active")` (keep lock advancement cheap or skip
   it — either is defensible; say which). Source: `luvus tab rename --help`
   (positions are current-workspace); live `tab.rename {"tab":9999}` error
   above; `auto_rename.py:427-430` comment admits the waste.
3. **Restore the cleared-tab title promptly.** Live probe: `tab.rename
   {"tab":1,"name":""}` returned `ok` and the snapshot still showed the old
   auto-title — because the poller only renames `if current != desired`
   (`auto_rename.py:465`), and after a clear `current` is `None`/default
   label while `desired` still carries the position prefix the poller adds.
   Verify what `session.snapshot` reports for a cleared tab (`name: null`
   vs `""`) and make the compare treat both as unnamed, or the hand-back
   gesture (README: "clearing hands it back") stalls one poll behind user
   expectation. Source: live proxy probes above; `tab.rename` help text
   "(empty clears it)"; `auto_rename.py:458-465`.

### Should-do (verified signal gains)

4. **Add the `terminal.backend.snapshot` title rung.** One extra call per
   poll returning all terminals' `terminal_title` + `cwd` + `content_revision`
   (measured ~8.5 ms avg, same class as `session.snapshot`). Insert at
   confidence between agent title and transcript (upstream order: agent 90
   > terminal 80 > transcript 75), with Pi/Codex/Grok grammars and prompt
   refusal ported from upstream `title-resolution.md`. Prerequisite: #5.
   Source: live `terminal.backend.snapshot` output (§1 gap 2);
   `luvus uhp capabilities` (method in `terminal` scope).
5. **Port `Meaningful` rejections.** Shell-prompt titles
   (`user@host:~/dir`) and bare program names are already visible in live
   backend titles; trusting them raw regresses plain-shell tabs. Port the
   three upstream classes (locations, program names incl. agent-echo
   comparison, shell prompts) from `sanitization.md`; keep the port's
   column-width truncation (`auto_rename.py:97-107`, which already handles
   CJK/emoji widths that upstream's `splitAtWidth` covers). Source: upstream
   `sanitization.md` §§ "Rejecting values…", "The limit is columns";
   live pane-6 title.
6. **Cache process answers by `content_revision` + 2 s refresh.**
   `auto_rename.py:444-449` calls `pane.processes` once per tab per poll on
   every workspace (~25 ms per the code comment; ~7.6 ms measured here —
   either way the dominant poll cost, vs ~8.5 ms snapshots). Reuse the last
   answer until the pane's `content_revision` moves, re-read at most every
   2 s regardless (upstream measured revision missing real process changes:
   9 vs 4 over ten minutes). Key by `terminal_id` (pty-lifetime, stable)
   rather than `pane_id`. Source: upstream `poll-loop.md` ("Decide from
   freshly read state"); live `content_revision` + `terminal_id` in both
   snapshots; `auto_rename.py:444`.
7. **Pick the context pane, not the first pane.** Port upstream `TabFrom`
   minus the tokenless rule (no `tokens` surface in Luvus): focused →
   working/blocked agent → most-recently-changed by `content_revision`,
   ties by pane id. Snapshot already carries `focused`, `agent_status`,
   `content_revision`. Replaces `auto_rename.py:313-315`. Source: upstream
   `title-resolution.md` ("One pane speaks for the tab"); verified snapshot
   pane keys.

### Could-do (needs features that do not exist yet)

8. **Workspace topics** — blocked on `workspace.report_metadata` gaining
   write params (gap 4). When it lands, port upstream TTL 60 s / refresh
   20 s and the topic chain (agent + activity, ssh host leads, empty
   clears). Source: upstream `herdr-socket-api.md` + `poll-loop.md`
   ("reportTopics"); current `unknown parameter` rejections.
9. **argv/cwd-driven rungs (ssh host, editor file, fg cwd)** — blocked on
   `arguments_exposed: false` (gap 1). A `terminal.backend.capture` fallback
   (mode `recent_unwrapped`/`detection`, verified working live) could scrape
   `ssh host` / `nvim file` from screen text, but that is scrollback
   scanning upstream deliberately never does — noisy, fragile, and a privacy
   step up from reading argv. Do not build unless the protocol adds argv.
   Source: live capture probes; upstream `poll-loop.md` ("no scrollback
   scanning").
10. **Event fast-path on `pane.agent_status_changed`** — keep the poll, but
    an event hook could trigger an immediate single pass when an agent goes
    blocked/done (latency win, same renames). Needs measuring against the
    replay/cursor caveats in ADR 0001 first. Source: writing-modules
    `[[events]]` table + `pane.agent_status_changed` payload doc; live
    `terminal.output_ready` flood showing why the full stream is unusable.
11. **Grok `%20`-style slug lookup + Codex newest-rollout scan** — the port's
    transcript readers (`auto_rename.py:169-256`) already cover Claude
    custom/ai-title/human-prompt, Codex index + newest rollout, Grok
    `summary.json`, Pi `session_info`. Upstream fixes since the port
    (`4edf2133` grok percent-encoding, `cbe41d75` grok status-line name)
    suggest conformance tests against real session dirs rather than new
    features. Low priority; adopt on a bug report with a session file.

## 4. Other risks spotted in `auto_rename.py`

- **Subprocess-per-call overhead dominates the poll.** Every `snapshot()`,
  `pane.processes`, and `tab.rename` spawns `luvus` via `subprocess.run`
  with `timeout=30` (`auto_rename.py:33-39,57-68`). Measured: 8.5 ms/snapshot,
  7.6 ms/process call, 0.16 ms upstream rename — the fork+exec is the
  budget, not the server. Batching over one `uhp proxy` stdin stream or one
  persistent CLI call would cut poll cost several-fold; combined with #6
  the loop drops from ~N×8 ms to ~2 snapshots per poll. (No change made —
  research only.)
- **`truncate` counts ASCII as width 1 but never counts graphemes.**
  `auto_rename.py:97-107` walks chars with East-Asian width — fine for the
  40-column server cap (verified live: 100-char name → `tab name must be at
  most 40 characters`), but a ZWJ family emoji counts 4+ columns where the
  tab bar draws 2. Upstream `splitAtWidth` (`sanitization.md`) walks
  grapheme clusters via `rivo/uniseg`. Cosmetic only at 40 cols; note for
  the record.
- **`grok_title` ignores `session_id` for the second candidate and never
  strips the `- grok` frame** (`auto_rename.py:229-240`): any tab whose cwd
  matches decodes the same `summary.json`, and a raw `<name> - grok` string
  would reach the title. Upstream `withoutGrokFrame` + `slugOf` (with the
  `%20`/`%2B`/`%25` escapes and `.cwd` fallback) is the conformance target.
  Same class: `codex_topic` matches `session_id in filename` (substring —
  one session id can prefix another; upstream matches whole names, title
  doc § "Terminal title"). Both are latent wrong-title bugs, neither
  observed live.
- **`dir_name` resolves symlinks on every call** (`auto_rename.py:281-287`,
  two `os.path.realpath` per pane per poll). Upstream deliberately compares
  `CommonDir` spellings unresolved to avoid stat walks on the hot path
  (`title-resolution.md`, "The two common directories…"). Cache per-poll or
  drop the realpath; `$HOME` comparison does not need it.
- **`restart` orphans the pidfile race it claims to fix.**
  `auto_rename.py:545-561` SIGTERMs the old pid then spawns a successor with
  no exec fence: two rapid restarts can both pass `claim_pidfile` and run
  concurrently until the next TERM lands — the exact two-poller shape ADR
  0002 fixed for startup. Rare; noted, not ranked.
- **No `if_revision` / mutation guard on renames.** Capabilities advertise
  `concurrency: {mutation_guard: "if_revision"}`; the poller renames by
  position with no fence, so a tab closed/reordered mid-poll can mistarget
  the rename. Upstream treats `tab_not_found`/`pane_not_found` as benign
  races (`poll-loop.md`); the port's outage path (`auto_rename.py:532-536`,
  skip + back off) covers the failure but a revision check would narrow the
  window. Informational — positions slide only on tab close, which is rare
  mid-poll.

## 5. Gaps that stay unclosable (summary)

| Gap | Status | Why |
|---|---|---|
| argv/cwd process data (ssh host, editor file) | Unclosable | `arguments_exposed: false` on both `pane.processes` and `terminal.backend.processes`; no per-process argv/cwd anywhere in 0.14.3 |
| Workspace topics | Unclosable | `workspace.report_metadata` is read-only (`unknown parameter` for every write key); no `$topic` render surface in docs |
| Event-driven renames | Correctly not pursued | Module `[[events]]` has no title signals; UHP stream replays backlog with no skip cursor (ADR 0001 stands) |
| Pane naming / icons / status / handles | Correctly dropped | `pane.rename` writes the `@mention` alias, not a display title; status marks rename on every flip |
| Branch-from-worktree, late-landing claims, label-owning programs | Correctly dropped | No data source / no observed occurrence on Luvus |
| Windows process degradation | No work | Same note as upstream; title/transcript rungs survive it |
| Session scoping of state | One probe open | Key by socket hash if `LUVUS_MODULE_STATE_DIR` is shared |
| Terminal titles | **Closable now** | `terminal.backend.snapshot.terminal_title` verified live |
| Process caching, context-pane rules, `Meaningful` | **Portable now** | All inputs (`content_revision`, `focused`, `agent_status`, `terminal_id`) verified in snapshots |
