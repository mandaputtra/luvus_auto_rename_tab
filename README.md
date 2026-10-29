# Auto Rename Tabs

A [Luvus](https://luvus.dev) module that names your tabs from their
current work. Trimmed port of
[kryptamine/herdr-auto-title](https://github.com/kryptamine/herdr-auto-title).

## Install

```sh
luvus module install mandaputtra/luvus_auto_rename_tab
```

Or link a checkout while developing:

```sh
luvus module link /path/to/luvus_auto_rename_tab
```

No build step — the poller is dependency-free Python 3 stdlib.

> [!WARNING]
> The first start renames every tab, including tabs you had already named by
> hand. A tab you rename after that is left alone.

## What you get

```
~/work/dashboard                       →  1 · dashboard
~/work/dashboard on feature/MC-13200   →  2 · dashboard › MC-13200
an agent working on OAuth scopes       →  1 · Implement OAuth scopes › dashboard
$HOME                                  →  3 · Shell
```

- The number in front is the tab's position.
- Agent tabs are named after the agent's session (transcript files for
  Claude, Codex, Grok, Pi, else the live session title).
- The default branch has no special handling in v1: any branch shows,
  shortened (`bugfix-…-mc-13675` → `MC-13675`).
- Rename a tab yourself and the module stops touching it. Clear the title
  to hand it back.
- Panes are left alone (`p<number>`). Luvus pane names are `@mention`
  aliases (`[a-z][a-z0-9_-]{0,31}`), not display titles, so auto-naming
  them caused more blink than value.

## Limitations (v1)

- **Tabs rename in the active workspace only.** `tab.rename` by position is
  active-workspace scoped and cross-workspace ids return `not_found`; the
  poller never steals focus, so background tabs get titled when you visit
  them.
- **Tab names cap at 40 characters** (server limit); titles truncate to fit.
- **No ssh-host / editor-file / process sources.** `pane.processes` hides
  argv and cwd, and snapshots carry no terminal title. These return if
  `terminal.backend.*` probes find the data.
- **No workspace topics.** `workspace.report_metadata` exists but sidebar
  `$topic` rendering is unverified.

## Settings

Settings → Modules, or `luvus module settings luvus.auto-rename-tabs`:

| Key | Default | What it does |
| --- | ------- | ------------ |
| `poll_ms` | `500` | Snapshot poll interval |
| `max_length` | `40` | Longest title in columns (tabs cap at 40) |
| `position` | `true` | Prefix tab titles with their position |
| `prefer_agent` | `false` | Name tabs after the agent pane even when unfocused |

Restart after changing settings:

```sh
luvus module run luvus.auto-rename-tabs restart
```

## How it works

`[[startup]]` spawns `auto_rename.py poller`: every `poll_ms` it takes a
`session.snapshot`, picks each tab's context pane (focused → working/blocked
agent → most-recently-changed), resolves `activity › branch › directory`,
and renames iff changed. Manual titles are protected by a
Current/Desired/Seen triple with label-guarded locks in the module state
dir. See `docs/adr/0001-poll-loop-over-events.md` and
`docs/research-auto-rename-tabs.md`.
