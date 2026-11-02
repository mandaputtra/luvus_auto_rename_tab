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

- Agent tabs are named from the terminal title (the agent's own topic, e.g.
  `π > 🔀 ACT: …`), else the transcript file (Claude, Codex, Grok, Pi),
  else the live session title — and follow it when the session changes.
- Plain shells fall back to the foreground app (`moon run api:dev` → `api`
  after agent frames and prompt-shaped titles are stripped); bare shells
  with only a prompt title fall back to branch and directory
  (`MC-13200 › dashboard`); `$HOME` alone is `Shell`.
- Tabs with several panes are named after the focused pane, else a
  working/blocked agent pane, else the most-recently-changed pane.

## Limitations (v1)

- **Tabs rename in the active workspace only.** `tab.rename` by position is
  active-workspace scoped; the poller skips background workspaces' per-tab
  work entirely and never steals focus, so their titles land when visited.
- **Tab names cap at 40 characters** (server limit); titles truncate to fit.
- **No ssh-host / editor-file / topic sources.** Both process APIs report
  `arguments_exposed: false` (no argv/cwd); `workspace.report_metadata`
  rejects every write param in this build. See
  `docs/research-improving-auto-rename.md` for the gap verdicts.

## Settings

Settings → Modules, or `luvus module settings luvus.auto-rename-tabs`:

| Key | Default | What it does |
| --- | ------- | ------------ |
| `poll_ms` | `500` | Snapshot poll interval |
| `max_length` | `40` | Longest title in columns (tabs cap at 40) |
| `position` | `true` | Prefix tab titles with their position |


Restart after changing settings:

```sh
luvus module run luvus.auto-rename-tabs restart
```

## How it works

`[[startup]]` spawns `auto_rename.py poller`: every `poll_ms` it takes a
`session.snapshot` plus one `terminal.backend.snapshot`, picks each tab's
context pane (focused → working/blocked agent → most-recently-changed),
resolves the title (terminal title → transcript → session title → app,
then `branch › directory`; process answers cache by `content_revision`),
and renames iff changed. Prompt-shaped titles and bare program names are
rejected. Manual titles are protected by a Current/Desired/Seen triple
with session-scoped, label-guarded locks. See `docs/adr/` and
`docs/research-*.md`.
