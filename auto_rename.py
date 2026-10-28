#!/usr/bin/env python3
"""Auto-rename Luvus tabs and panes from their current work.

Trimmed port of herdr-auto-title: 500ms session.snapshot poll -> diff ->
rename iff changed. Title ladder: agent transcript > agent_session_title >
branch > cwd > "Shell". Hand-set titles lock (Current/Desired/Seen triple);
clearing hands the tab/pane back.

Usage (run by luvus, not by hand):
    auto_rename.py poller    # long-lived loop; started by [[startup]]
    auto_rename.py restart   # relaunch the loop in place
    auto_rename.py once      # single pass (debugging)

Settings arrive as LUVUS_SETTING_* env; state lives in LUVUS_MODULE_STATE_DIR.
Calls back via LUVUS_BIN_PATH over LUVUS_SOCKET_PATH.
"""

import json
import os
import re
import subprocess
import sys
import time
import unicodedata

SEP = " \u203a "  # `›` throughout, like herdr
FALLBACK = "Shell"

# ponytail: single-file poller, no deps. Split out transcripts/resolver
# only if a second consumer appears.


def sh(*args):
    env = dict(os.environ)
    p = subprocess.run(
        [os.environ.get("LUVUS_BIN_PATH", "luvus"), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )
    return p.stdout


def snapshot():
    try:
        out = sh("uhp", "snapshot")
        d = json.loads(out)
        res = d.get("result", d)
        # fenced harness snapshot nests under session or snapshot
        if isinstance(res, dict):
            for key in ("session", "snapshot"):
                if isinstance(res.get(key), dict) and "workspaces" in res[key]:
                    return res[key]
        return res if isinstance(res, dict) else {}
    except (json.JSONDecodeError, AttributeError):
        return {}


def uhp(method, params=None):
    req = json.dumps({"jsonrpc": "2.0", "id": "1",
                      "method": method, "params": params or {}}) + "\n"
    p = subprocess.run(
        [os.environ.get("LUVUS_BIN_PATH", "luvus"), "uhp", "proxy"],
        input=req, capture_output=True, text=True, timeout=30,
        env=dict(os.environ),
    )
    try:
        return json.loads(p.stdout).get("result", {})
    except (json.JSONDecodeError, AttributeError):
        return {}

def num(key, default):
    try:
        return int(os.environ.get("LUVUS_SETTING_" + key.upper(), default))
    except ValueError:
        return default


def flag(key, default):
    v = os.environ.get("LUVUS_SETTING_" + key.upper())
    return default if v is None else v not in ("", "0", "false", "off")


# --- sanitize (herdr internal/resolver/sanitization, trimmed) ----------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b\([0-9A-Z]")
_WS = re.compile(r"\s+")


def sanitize(text):
    text = _ANSI.sub("", text or "")
    text = "".join(c for c in text
                   if c == "\u200d" or unicodedata.category(c)[0] not in ("C",))
    text = _WS.sub(" ", text).strip()
    text = re.sub(r"(›\s*){2,}", "› ", text).strip(" ›")
    return text


def truncate(text, width):
    if width <= 0 or len(text) <= width:
        return text
    out, cols = [], 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cols + w > width - 1:
            break
        out.append(ch)
        cols += w
    return "".join(out).rstrip() + "…"


_PANE_SLUG = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")


def pane_slug(label):
    """Luvus pane names are @mention slugs; return label iff already valid."""
    return label if _PANE_SLUG.match(label or "") else None


# --- agent transcript readers (host-independent file formats) ---------------

def _jsonl_tail(path, offset):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            if offset >= end:
                return [], end
            f.seek(offset)
            return f.read().decode("utf-8", "replace").splitlines(), end
    except OSError:
        return [], offset


class TranscriptStore:
    """Incremental readers for Claude/Codex/Grok/Pi session files."""

    def __init__(self, homes):
        self.homes = homes
        self.offsets = {}

    def _read_new(self, path):
        off = self.offsets.get(path, 0)
        lines, new_off = _jsonl_tail(path, off)
        self.offsets[path] = new_off
        return lines

    def claude_topic(self, session_id, dirs):
        """custom-title > ai-title > first human prompt (herdr claude pkg)."""
        name = ai_title = opening = None
        for d in dirs:
            slug = re.sub(r"[^A-Za-z0-9]", "-", d)
            for home in self.homes.get("claude", []):
                path = os.path.join(home, "projects", slug, session_id + ".jsonl")
                if not os.path.exists(path):
                    continue
                for line in self._read_new(path):
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    t = e.get("type", "")
                    if t == "custom-title":
                        name = e.get("customTitle") or e.get("title") or name
                    elif t == "ai-title" and not name:
                        ai_title = e.get("title", ai_title)
                    elif not opening and e.get("role") == "user" \
                            and (e.get("origin", {}).get("kind", "human")) == "human":
                        content = e.get("content", "")
                        if isinstance(content, list):
                            content = " ".join(
                                b.get("text", "") for b in content
                                if isinstance(b, dict))
                        opening = first_line(strip_markup(str(content)))
        return name or ai_title or opening

    def codex_topic(self, session_id):
        """session_index name, else first user message of newest rollout."""
        home = self.homes.get("codex", [None])[0]
        if not home:
            return None
        name = None
        try:
            with open(os.path.join(home, "sessions", "session_index.jsonl")) as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if e.get("session_id") == session_id or e.get("id") == session_id:
                        name = e.get("name") or name
        except OSError:
            pass
        if name:
            return name
        # newest rollout wins
        best, best_mtime = None, -1.0
        sess_dir = os.path.join(home, "sessions")
        try:
            days = os.listdir(sess_dir)
        except OSError:
            return None
        for day in days:
            ddir = os.path.join(sess_dir, day)
            if not os.path.isdir(ddir):
                continue
            try:
                files = os.listdir(ddir)
            except OSError:
                continue
            for fn in files:
                if session_id not in fn or not fn.endswith(".jsonl"):
                    continue
                p = os.path.join(ddir, fn)
                try:
                    mt = os.path.getmtime(p)
                except OSError:
                    continue
                if mt > best_mtime:
                    best, best_mtime = p, mt
        if best:
            for line in self._read_new(best):
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("role") == "user" and not name:
                    content = e.get("content", "")
                    if isinstance(content, list):
                        content = " ".join(
                            b.get("text", "") for b in content
                            if isinstance(b, dict))
                    name = first_line(str(content))
                    break
        return name

    def grok_title(self, session_id, dirs):
        for d in dirs:
            slug = d.replace(" ", "%20")
            for home in self.homes.get("grok", []):
                for cand in (os.path.join(home, slug, "summary.json"),
                             os.path.join(home, session_id, "summary.json")):
                    try:
                        with open(cand) as f:
                            return json.load(f).get("generated_title")
                    except (OSError, json.JSONDecodeError, AttributeError):
                        continue
        return None

    def pi_topic(self, path):
        if not path or not path.endswith(".jsonl"):
            return None
        name = opening = None
        for line in self._read_new(path):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") == "session_info":
                name = (e.get("name") or "").strip() or None
            elif not opening and e.get("type") == "message" \
                    and e.get("message", {}).get("role") == "user":
                opening = first_line(text_of(e["message"].get("content", "")))
        return name or opening

def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") != "tool_use")
    return ""


def first_line(text):
    return (text or "").strip().split("\n", 1)[0].strip()


# --- title resolution -------------------------------------------------------

def shorten_branch(branch):
    """bugfix-…-mc-13675 -> MC-13675 (herdr title-resolution)."""
    if not branch:
        return None
    m = re.search(r"([A-Za-z]+-\d+)\s*$", branch)
    return m.group(1).upper() if m else branch


def dir_name(cwd, home):
    if not cwd:
        return None
    if home and os.path.realpath(cwd) == os.path.realpath(home):
        return None
    base = os.path.basename(os.path.realpath(cwd))
    return base or None


def context_pane(panes, prefer_agent):
    """focused -> working/blocked agent -> most-recently-changed, ties by id."""
    if not panes:
        return None
    for p in panes:
        if p.get("focused"):
            return p
    pool = list(panes)
    if prefer_agent:
        pool = [p for p in panes if p.get("is_agent")] or pool
    agents = [p for p in pool
              if p.get("agent_status") in ("working", "blocked")]
    if agents:
        agents.sort(key=lambda p: str(p.get("pane_id")))
        return agents[0]
    panes = sorted(panes, key=lambda p: (-(p.get("rev") or 0), str(p.get("pane_id"))))
    return panes[0]


def resolve_tab(panes, prefer_agent, transcripts, dirs, home, branch):
    """Parts{Context, Branch, Agent, Activity} -> title or None."""
    ctx = context_pane(panes, prefer_agent)
    if ctx is None:
        return None, None
    parts = []
    # agent activity (transcript > live session title)
    activity = None
    sess = ctx.get("agent_session")
    agent = (ctx.get("agent") or "").lower()
    if sess and transcripts:
        if agent == "claude":
            activity = transcripts.claude_topic(sess, dirs)
        elif agent == "codex":
            activity = transcripts.codex_topic(sess)
        elif agent == "grok":
            activity = transcripts.grok_title(sess, dirs)
        elif agent == "pi":
            activity = transcripts.pi_topic(sess)
    if not activity:
        activity = ctx.get("agent_session_title")
    if activity and sanitize(activity):
        parts.append(sanitize(activity))
    # branch (default branch omitted by caller passing None)
    short = shorten_branch(branch)
    if short:
        parts.append(short)
    # context dir
    d = dir_name(ctx.get("cwd"), home)
    if d:
        parts.append(d)
    if not parts:
        parts.append(FALLBACK)
    return SEP.join(parts), ctx


def resolve_pane(pane, transcripts, home):
    sess = pane.get("agent_session")
    agent = (pane.get("agent") or "").lower()
    activity = None
    if sess and transcripts and agent in ("claude", "codex", "grok", "pi"):
        try:
            if agent == "claude":
                activity = transcripts.claude_topic(sess, [pane.get("cwd") or ""])
            elif agent == "codex":
                activity = transcripts.codex_topic(sess)
            elif agent == "grok":
                activity = transcripts.grok_title(sess, [pane.get("cwd") or ""])
            else:
                activity = transcripts.pi_topic(sess)
        except OSError:
            activity = None
    if not activity:
        activity = pane.get("agent_session_title")
    if activity and sanitize(activity):
        return sanitize(activity)
    d = dir_name(pane.get("cwd"), home)
    if d:
        return d
    return (pane.get("agent") or FALLBACK)


# --- manual-title triple (Current/Desired/Seen) ------------------------------

class Locks:
    """Persisted manual-title locks, label-guarded against id reuse."""

    def __init__(self, path):
        self.path = path
        try:
            with open(path) as f:
                self.data = json.load(f)
        except (OSError, json.JSONDecodeError):
            self.data = {"tabs": {}, "panes": {}}
        self.first_poll = True

    def save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def judge(self, kind, key, current, desired, seen):
        """-> 'ours' | 'manual' | 'clear'."""
        entry = self.data[kind].get(key)
        if entry and entry.get("label") != current:
            del self.data[kind][key]  # stale id reuse releases
            entry = None
        if entry:
            if current in ("", None):
                del self.data[kind][key]  # cleared: hand back
                return "ours"
            return "manual"
        if self.first_poll:
            return "ours"
        if not current:
            return "ours"  # empty label = unnamed, not the user's
        if current == desired or current == seen:
            return "ours"
        self.data[kind][key] = {"label": current}
        return "manual"
    def release(self, kind, key):
        self.data[kind].pop(key, None)


# --- poll loop ---------------------------------------------------------------

def rename_tab(pos, name):
    """tab.rename by position is active-workspace scoped; empty clears."""
    return uhp("tab.rename", {"tab": pos, "name": name})


def rename_pane(pane_id, name):
    """CLI pane naming works cross-workspace; empty clears via --clear."""
    if name:
        return sh("pane", "name", name, "--pane", str(pane_id))
    return sh("pane", "name", "--clear", "--pane", str(pane_id))


def poll_once(state, cfg, transcripts, home):
    snap = snapshot()
    workspaces = snap.get("workspaces", [])
    # tab.rename by position is ACTIVE-workspace scoped (cross-workspace ids
    # return not_found); panes rename globally via the CLI. NEVER
    # workspace.focus here: that would steal the user's focus every poll.
    # Background tabs get titled when visited.
    for ws in workspaces:
        branch = ws.get("branch")
        active = ws.get("active")
        for pos, tab in enumerate(ws.get("tabs", []), 1):
            panes = [{"pane_id": p.get("pane_id"),
                      "focused": p.get("focused"),
                      "agent": p.get("agent"),
                      "is_agent": p.get("is_agent"),
                      "agent_status": p.get("agent_status"),
                      "agent_session": p.get("agent_session"),
                      "agent_session_title": p.get("agent_session_title"),
                      "cwd": p.get("cwd"),
                      "rev": p.get("content_revision", 0)}
                     for p in tab.get("panes", [])]
            key = "%s/%s" % (ws.get("name"), pos)
            desired, ctx = resolve_tab(
                panes, cfg["prefer_agent"], transcripts,
                [p.get("cwd") or "" for p in panes], home, branch)
            # Luvus caps tab names at 40 characters; truncate the final
            # string (prefix included) so the rename is never rejected.
            desired = truncate(desired or FALLBACK, min(cfg["max_length"], 40))
            if cfg["position"]:
                desired = truncate("%d · %s" % (pos, desired), 40)
            current = tab.get("name")  # None = default position label
            seen = state["seen_tabs"].get(key)
            verdict = state["locks"].judge(
                "tabs", key, current or "", desired, seen or "")
            state["seen_tabs"][key] = desired if verdict == "ours" else current
            if active and verdict == "ours" and current != desired:
                rename_tab(pos, desired)
                state["seen_tabs"][key] = desired
            if cfg["panes"]:
                for p, full in zip(panes, tab.get("panes", [])):
                    label = pane_slug(resolve_pane(p, transcripts, home))
                    if label is None:
                        continue  # not slug-shaped; leave the pane alone
                    cur = full.get("agent_name")
                    skey = state["seen_panes"].get(p["pane_id"])
                    v = state["locks"].judge(
                        "panes", p["pane_id"], cur or "", label, skey or "")
                    state["seen_panes"][p["pane_id"]] = label if v == "ours" else cur
                    if v == "ours" and cur != label:
                        rename_pane(p["pane_id"], label)
                        state["seen_panes"][p["pane_id"]] = label
    state["locks"].first_poll = False
    state["locks"].save()


def default_homes():
    home = os.path.expanduser("~")
    return {"claude": [os.path.join(home, ".claude"),
                       os.environ.get("CLAUDE_CONFIG_DIR", "")],
            "codex": [os.environ.get("CODEX_HOME",
                                     os.path.join(home, ".codex"))],
            "grok": [os.environ.get("GROK_CONFIG_DIR",
                                    os.path.join(home, ".grok"))],
            "pi": []}


def run_poller():
    cfg = {"poll_ms": num("poll_ms", 500),
           "max_length": num("max_length", 50),
           "position": flag("position", True),
           "panes": flag("panes", True),
           "prefer_agent": flag("prefer_agent", False)}
    state_dir = os.environ.get("LUVUS_MODULE_STATE_DIR", "/tmp")
    os.makedirs(state_dir, exist_ok=True)
    state = {"locks": Locks(os.path.join(state_dir, "manual-names.json")),
             "seen_tabs": {}, "seen_panes": {}}
    transcripts = TranscriptStore(default_homes())
    home = os.path.expanduser("~")
    interval = max(0.2, cfg["poll_ms"] / 1000.0)
    pidfile = os.path.join(state_dir, "poller.pid")
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))
    while True:
        try:
            poll_once(state, cfg, transcripts, home)
        except Exception as e:  # outage: skip, back off, never die loud
            print("poll skipped: %s" % e, file=sys.stderr, flush=True)
            time.sleep(min(5.0, interval * 2))
            continue
        time.sleep(interval)


def main(argv):
    if len(argv) < 2:
        print("usage: auto_rename.py [poller|restart|once]", file=sys.stderr)
        return 2
    if argv[1] == "poller":
        run_poller()
    elif argv[1] == "restart":
        import signal
        state_dir = os.environ.get("LUVUS_MODULE_STATE_DIR", "/tmp")
        pidfile = os.path.join(state_dir, "poller.pid")
        try:
            with open(pidfile) as f:
                os.kill(int(f.read().strip()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        subprocess.Popen(
            [sys.executable, os.path.join(
                os.environ.get("LUVUS_MODULE_ROOT", "."), "auto_rename.py"),
             "poller"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
            env=dict(os.environ))
        print("restarted")
    elif argv[1] == "once":
        cfg = {"poll_ms": 500, "max_length": num("max_length", 50),
               "position": flag("position", True), "panes": flag("panes", True),
               "prefer_agent": flag("prefer_agent", False)}
        state = {"locks": Locks(os.path.join(
            os.environ.get("LUVUS_MODULE_STATE_DIR", "/tmp"),
            "manual-names.json")),
            "seen_tabs": {}, "seen_panes": {}}
        state["locks"].first_poll = False  # never claim hands in once mode
        poll_once(state, cfg, TranscriptStore(default_homes()),
                  os.path.expanduser("~"))
    else:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
