# Session-pinned manual locks, single poller, success-gated renames

Manual tab locks froze a tab on our own stale auto-title after the agent
session moved on (lock poisoned during a two-poller fight). So: locks pin
the user title to the agent session id it was set under and release on
session turnover; old label-only locks are dropped and judged fresh;
startup claims a pidfile (TERM, then KILL, else exit) so only one poller
runs; seen-state advances only on confirmed renames, so a rejection is
retried, never mistaken for the user's hand.

Considered Options: lock expiry by timeout (stomps deliberate names
eventually); leaving multi-poller spawning alone (re-poisons locks).

Consequences: a hand rename lasts until the session turns over or the
title is cleared; a second poller start retires the first within ~3s.
