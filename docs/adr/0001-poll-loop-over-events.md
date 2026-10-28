# Poll the session instead of subscribing to events

Luvus `[[events]]` covers created/closed/status hooks but not title-relevant signals (`pane.renamed`, terminal output/metadata); UHP event subscribe replays backlog with no skip cursor. So the auto-rename module runs a 500ms `session.snapshot` poll loop that diffs and renames only on change, like herdr-auto-title.

Considered Options: event-driven hooks (misses title changes from terminal output, needs a poll fallback anyway); hybrid events + slow poll (most moving parts, no proven need).

Consequences: ~0.5ms/snapshot cost, rename rate capped at 1/tab/poll; revisit if a title-capable event or cursor appears.
