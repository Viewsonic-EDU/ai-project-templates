"""Context ledger: real CLI effects with isolated process/transcript fixtures (AC numbers
refer to the downstream spec this suite was ported from).

The deterministic ps shim models the process API, not the transcript parser. The
separate real-process tests retain the OS walk and may fail in a fenced worker.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests/fixtures/context_ledger"
KEY = "SAMPLE-900"
HEADER = (
    "| date | ticket | predicted | actual | ratio | compactions | turns |"
    " dispatches | window | notes |"
)


def assistant(usage: int, request: str, *tools: str) -> dict[str, Any]:
    """One provider content record with deliberately misleading nested usage."""
    return {
        "type": "assistant",
        "requestId": request,
        "message": {
            "model": "fixture-model",
            "usage": {
                "input_tokens": usage - 20,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 10,
                "output_tokens": 999,
                "iterations": [{"input_tokens": 999999}],
            },
            "content": [{"type": "tool_use", "name": tool} for tool in tools],
        },
    }


class LedgerSession:
    """Disposable repository with simulated/native surviving Claude ancestors (no exec)."""

    def __init__(self, root: Path, *, real_ps: bool = False, repo: Any = None) -> None:
        self.root = repo.root if repo else root / "repository"
        self.root.mkdir(parents=True, exist_ok=True)
        self.bin = repo.bin if repo else root / "bin"
        self.bin.mkdir(exist_ok=True)
        self.events = root / "events"
        self.events.mkdir()
        self.state = self.root / ".git/context-ledger"
        self.ledger = self.root / "docs/context-ledger.md"
        self.transcript = root / "own.jsonl"
        self.transcript.write_text("")
        self.env = {
            **(
                repo.env
                if repo
                else {
                    k: v
                    for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "CONTEXT_LEDGER_", "CLAUDE_CONTROL_"))
                }
            ),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "LC_ALL": "C",
            "TICKET_PREFIX": "SAMPLE",
            "CLAUDE_CONTROL_EVENTS_DIR": str(self.events),
            "CONTEXT_LEDGER_STATE_DIR": str(self.state),
            "CONTEXT_LEDGER_FILE": str(self.ledger),
            "CONTEXT_LEDGER_WINDOW": "1000000",
            "CONTEXT_LEDGER_DISPATCH_TOOLS": "Agent mcp__codex__codex",
            "FIXTURE_TRANSCRIPT": str(self.transcript),
            "FIXTURE_SESSION": "own-session",
            "FIXTURE_EVENT": "Stop",
            "FIXTURE_TS": str(int(time.time()) + 2),
            "FIXTURE_EVENT_MODE": "write",
            "FIXTURE_VERIFY_COMM": "0",
            "FIXTURE_TOUCH_TRANSCRIPT": "1" if real_ps else "0",
        }
        if not repo:
            subprocess.run(["git", "init", "-q", str(self.root)], check=True, env=self.env)
            scripts = self.root / "scripts"
            scripts.mkdir()
            shutil.copy(REPO / "scripts/context-ledger.sh", scripts)
        self.script = self.root / "scripts/context-ledger.sh"
        self.executable(
            "date",
            '#!/bin/bash\ncase "$1" in\n+%F) echo 2026-09-16 ;;\n'
            "+%s) /bin/date +%s ;;\n*) exit 99 ;;\nesac\n",
        )
        shell = shutil.which("bash", path="/usr/bin:/bin")
        assert shell is not None
        self.ancestor_shell = Path(shell)
        if real_ps:
            # Copied binaries are SIGKILLed by macOS signature enforcement here;
            # a symlink keeps the invoked basename claude without copying the binary.
            self.ancestor_shell = self.bin / "claude"
            os.symlink(shell, self.ancestor_shell)
        self.executable(
            "claude-wrapper.sh",
            """#!/bin/bash
export FIXTURE_CLAUDE_PID=$$ FIXTURE_CLAUDE_PPID=$PPID
python3 - <<'JSON'
import json, os, subprocess
from pathlib import Path
if os.environ['FIXTURE_EVENT_MODE'] == 'write':
    event = dict(event=os.environ['FIXTURE_EVENT'], session_id=os.environ['FIXTURE_SESSION'],
                 transcript_path=os.environ['FIXTURE_TRANSCRIPT'], ts=int(os.environ['FIXTURE_TS']))
    target = Path(os.environ['CLAUDE_CONTROL_EVENTS_DIR']) / (str(os.getppid()) + '.json')
    target.write_text(json.dumps(event))
    if os.environ['FIXTURE_VERIFY_COMM'] == '1':
        identity = dict(pid=os.getppid(), parent_pid=int(os.environ['FIXTURE_CLAUDE_PPID']))
        for name, pid in [('own', identity['pid']), ('outer', identity['parent_pid'])]:
            probe = subprocess.run(['ps', '-o', 'comm=', '-p', str(pid)],
                                   capture_output=True, text=True)
            identity[name] = dict(comm=probe.stdout.strip(), code=probe.returncode,
                                  error=probe.stderr.strip())
        (target.parent / 'ancestor-identity.json').write_text(json.dumps(identity))
        for name in ('own', 'outer'):
            assert identity[name]['code'] == 0, identity[name]['error']
            assert Path(identity[name]['comm']).name == 'claude', (
                f"{name} ancestor must have comm basename claude: {identity[name]['comm']!r}")
    if os.environ['FIXTURE_TOUCH_TRANSCRIPT'] == '1':
        # A live session writes after startup; avoid a one-second lstart boundary race.
        Path(os.environ['FIXTURE_TRANSCRIPT']).touch()
JSON
precondition=$?
[ "$precondition" -eq 0 ] || exit "$precondition"
"$@"
result=$?
# This must run AFTER the child, proving the fake ancestor survives.
printf '%s\\n' "$$" > "$CLAUDE_CONTROL_EVENTS_DIR/survived"
exit "$result"
""",
        )
        self.executable(
            "outer-claude-wrapper.sh",
            """#!/bin/bash
export FIXTURE_OUTER_CLAUDE_PID=$$
python3 - <<'JSON'
import json, os, time
from pathlib import Path
event = dict(event='Stop', session_id='outer-decoy',
             transcript_path=os.environ['FIXTURE_DECOY_TRANSCRIPT'], ts=int(time.time()))
(Path(os.environ['CLAUDE_CONTROL_EVENTS_DIR']) / (str(os.getppid()) + '.json')).write_text(
    json.dumps(event))
JSON
"$@"
result=$?
printf '%s\\n' "$$" > "$CLAUDE_CONTROL_EVENTS_DIR/outer-survived"
exit "$result"
""",
        )
        if not real_ps:
            self.executable(
                "ps",
                """#!/bin/bash
case "$2" in
comm=)
  if [ "$4" = "$FIXTURE_CLAUDE_PID" ] || [ "$4" = "${FIXTURE_OUTER_CLAUDE_PID:-}" ]; then
    echo claude
  else echo bash; fi ;;
ppid=) echo "$FIXTURE_CLAUDE_PID" ;;
lstart=) echo 'Tue Sep  1 00:00:00 2026' ;;
*) exit 1 ;;
esac
""",
            )

    def executable(self, name: str, content: str) -> None:
        """Install a test-owned command, never modify a machine command."""
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def append(self, *records: dict[str, Any]) -> None:
        """Append complete JSONL records to the own transcript."""
        with self.transcript.open("a") as stream:
            for record in records:
                stream.write(json.dumps(record) + "\n")

    def run(
        self, *args: str, code: int = 0, ancestor: bool = True, outer_ancestor: bool = False
    ) -> subprocess.CompletedProcess[str]:
        """Run the actual CLI beneath the fixture parent."""
        return self.command(
            ["bash", str(self.script), *args],
            code=code,
            ancestor=ancestor,
            outer_ancestor=outer_ancestor,
        )

    def command(
        self,
        args: list[str],
        *,
        code: int = 0,
        ancestor: bool = True,
        outer_ancestor: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run another command (e.g. the worktree hooks) under the same session."""
        if ancestor:
            args = [str(self.ancestor_shell), str(self.bin / "claude-wrapper.sh"), *args]
        if outer_ancestor:
            args = [str(self.ancestor_shell), str(self.bin / "outer-claude-wrapper.sh"), *args]
        result = subprocess.run(
            args, cwd=self.root, env=self.env, capture_output=True, text=True, timeout=20
        )
        assert result.returncode == code, (args, result.returncode, result.stdout, result.stderr)
        return result

    def marker(self, key: str = KEY) -> Path:
        """Return a ticket's start-state path."""
        return Path(self.env["CONTEXT_LEDGER_STATE_DIR"]) / f"{key}.json"

    def row(self) -> list[str]:
        """Read the last persisted row independently of the production parser."""
        return [
            cell.strip()
            for cell in Path(self.env["CONTEXT_LEDGER_FILE"])
            .read_text()
            .splitlines()[-1]
            .strip("|")
            .split("|")
        ]

    def ticket(self, size: str) -> None:
        """Seed only the fixture board."""
        directory = self.root / "tickets"
        directory.mkdir(exist_ok=True)
        (directory / f"{KEY}.md").write_text(f"---\nsize: {size}\n---\nsize: 99\n")


@pytest.fixture
def session(tmp_path: Path) -> LedgerSession:
    return LedgerSession(tmp_path)


def test_now(session: LedgerSession) -> None:
    """AC1: last valid input+cache only, ignoring users/tool results/iterations."""
    session.append(
        assistant(120000, "a"), assistant(180000, "b"), {"type": "user"}, {"type": "tool_result"}
    )
    assert (
        session.run("now").stdout
        == "usage=180000 window=1000000 used=18.00% remaining=820000 slices=0.30\n"
    )


def test_no_claude_ancestor(session: LedgerSession) -> None:
    session.executable("ps", "#!/bin/sh\nexit 1\n")
    assert session.run("now", ancestor=False).stdout == "unmeasured: no claude ancestor\n"


@pytest.mark.parametrize("real_ps", [False, True], ids=["process-api-fixture", "real-process-walk"])
def test_own_session_vs_decoy_and_fixture_ancestor_survives(tmp_path: Path, real_ps: bool) -> None:
    """AC2/26: another live-looking event can never supply our usage."""
    session = LedgerSession(tmp_path, real_ps=real_ps)
    session.append(assistant(120000, "own"))
    decoy = tmp_path / "decoy.jsonl"
    decoy.write_text(json.dumps(assistant(990000, "decoy")) + "\n")
    (session.events / "999999.json").write_text(
        json.dumps(
            {
                "event": "Stop",
                "session_id": "decoy",
                "transcript_path": str(decoy),
                "ts": int(time.time()),
            }
        )
    )
    session.env["FIXTURE_DECOY_TRANSCRIPT"] = str(decoy)
    session.env["FIXTURE_VERIFY_COMM"] = "1"
    assert (
        session.run("now", outer_ancestor=True).stdout
        == "usage=120000 window=1000000 used=12.00% remaining=880000 slices=0.20\n"
    )
    own_pid = int((session.events / "survived").read_text())
    outer_pid = int((session.events / "outer-survived").read_text())
    assert own_pid != outer_pid
    identity = json.loads((session.events / "ancestor-identity.json").read_text())
    assert (identity["pid"], identity["parent_pid"]) == (own_pid, outer_pid)
    for name in ("own", "outer"):
        assert identity[name]["code"] == 0, identity[name]["error"]
        assert Path(identity[name]["comm"]).name == "claude"
    own_event = json.loads((session.events / f"{own_pid}.json").read_text())
    outer_event = json.loads((session.events / f"{outer_pid}.json").read_text())
    assert own_event["transcript_path"] == str(session.transcript)
    assert outer_event["transcript_path"] == str(decoy)


def test_ancestor_identity_precondition_prevents_child_launch(session: LedgerSession) -> None:
    """AC26: a misnamed fixture must fail before the ledger can walk past it."""
    session.env["FIXTURE_VERIFY_COMM"] = "1"
    session.executable("ps", "#!/bin/sh\necho /bin/bash\n")
    child_marker = session.root / "child-launched"
    result = session.command(["touch", str(child_marker)], code=1)
    assert "own ancestor must have comm basename claude: '/bin/bash'" in result.stderr
    assert not child_marker.exists()
    assert not (session.events / "survived").exists()


def test_valid_record_selection_real_golden(session: LedgerSession) -> None:
    """AC3/T4a: provider usage and real synthetic error retain their observed shape."""
    session.transcript.write_bytes((FIXTURES / "real-scrubbed.jsonl").read_bytes())
    with session.transcript.open("a") as stream:
        stream.write('{"partial":')
    assert (
        session.run("now").stdout
        == "usage=51244 window=1000000 used=5.12% remaining=948756 slices=0.09\n"
    )
    synthetic = json.loads((FIXTURES / "real-scrubbed.jsonl").read_text().splitlines()[-1])
    session.transcript.write_text(json.dumps(synthetic) + "\n")
    assert session.run("now").stdout == "unmeasured: no usage line\n"


@pytest.mark.parametrize(
    "bad", [None, {}, {"input_tokens": None}, {"input_tokens": "900000"}, {"input_tokens": True}]
)
def test_invalid_usage_skipped_for_all_counters(session: LedgerSession, bad: Any) -> None:
    """AC3: malformed fields cannot change usage, turns, dispatches or compactions."""
    session.append(assistant(100000, "a"))
    session.run("start", KEY)
    record = assistant(0, "bad", "Agent")
    record["message"]["usage"] = bad
    session.append(record, assistant(160000, "b"))
    session.run("record", KEY)
    assert session.row()[3:9] == ["0.10", "n/a", "0", "1", "0", "1000000"]


@pytest.mark.parametrize(
    "case,reason",
    [
        ("missing-event", "no events file for pid"),
        ("ended", "stale events file"),
        ("pid-reuse", "stale events file (pid reuse)"),
        ("missing-transcript", "transcript missing"),
        ("absent-path", "transcript missing"),
        ("unreadable-transcript", "transcript missing"),
        ("old-transcript", "stale events file"),
        ("no-usage", "no usage line"),
    ],
)
def test_unmeasured_reasons(session: LedgerSession, case: str, reason: str) -> None:
    """AC4: failures stay session-specific and never borrow the decoy."""
    session.append(assistant(100000, "a"))
    if case == "missing-event":
        session.env["FIXTURE_EVENT_MODE"] = "none"
    elif case == "ended":
        session.env["FIXTURE_EVENT"] = "SessionEnd"
    elif case == "pid-reuse":
        session.env["FIXTURE_TS"] = "1"
    elif case == "missing-transcript":
        session.transcript.unlink()
    elif case == "absent-path":
        session.env["FIXTURE_TRANSCRIPT"] = ""
    elif case == "unreadable-transcript":
        session.transcript.chmod(0)
    elif case == "old-transcript":
        os.utime(session.transcript, (1, 1))
    else:
        session.transcript.write_text('{"bad":\n')
    result = session.run("now")
    assert result.stdout.startswith(f"unmeasured: {reason}")
    assert len(result.stdout.splitlines()) == 1
    assert result.stderr == ""


def test_malformed_line_before_valid(session: LedgerSession) -> None:
    session.transcript.write_text("not JSON\n")
    session.append(assistant(60000, "a"))
    assert "usage=60000 " in session.run("now").stdout


def test_start_state_and_freeze(session: LedgerSession) -> None:
    """AC5/6/14: every baseline field persists and G0 prediction is immutable."""
    session.ticket("0.5")
    session.append(assistant(100000, "a"))
    assert session.run("start", KEY).stdout == f"ledger start {KEY} usage=100000 predicted=0.5\n"
    saved = json.loads(session.marker().read_text())
    assert saved == dict(
        session_id="own-session",
        transcript_path=str(session.transcript),
        usage=100000,
        turns=0,
        dispatches=0,
        lines=1,
        window=1000000,
        predicted="0.5",
        ts=saved["ts"],
        reason="",
    )
    session.ticket("2")
    session.env["CONTEXT_LEDGER_WINDOW"] = "2000000"
    session.append(assistant(400000, "b"))
    session.run("record", KEY)
    assert session.row()[2:9] == ["0.5", "0.50", "1.00", "0", "1", "0", "1000000"]
    assert not session.marker().exists()


def test_start_overwrite_and_unmeasured(session: LedgerSession) -> None:
    session.run("start", KEY)
    assert json.loads(session.marker().read_text())["usage"] is None
    session.append(assistant(100000, "a"))
    session.run("start", KEY, "--predicted", "0.75")
    assert json.loads(session.marker().read_text())["predicted"] == "0.75"
    assert json.loads(session.marker().read_text())["usage"] == 100000


@pytest.mark.parametrize("prediction", ["n/a", "0.5"])
def test_record_row_golden(session: LedgerSession, prediction: str) -> None:
    """AC6: exact ten-cell delta row including unknown prediction."""
    session.append(assistant(100000, "a"))
    args = [] if prediction == "n/a" else ["--predicted", prediction]
    session.run("start", KEY, *args)
    session.append(assistant(400000, "b", "Agent"))
    session.run("record", KEY)
    ratio = "n/a" if prediction == "n/a" else "1.00"
    assert (
        session.ledger.read_text().splitlines()[-1]
        == f"| 2026-09-16 | {KEY} | {prediction} | 0.50 | {ratio} | 0 | 1 | 1 | 1000000 |  |"
    )


def test_turns_delta(session: LedgerSession) -> None:
    """AC7: 73 content-block lines / 34 requests, with a start inside a request."""
    for i in range(5):
        session.append(assistant(100000 + i, str(i)))
    session.run("start", KEY)
    for i in range(5, 34):
        session.append(assistant(100000 + i, str(i)), assistant(100000 + i, str(i)))
    for _ in range(10):
        session.append(assistant(100033, "33"))
    assert len(session.transcript.read_text().splitlines()) == 73
    session.run("record", KEY)
    assert session.row()[6] == "29"
    # The active final request is excluded at each cut, so two intervals never count it twice.
    session.run("start", KEY)
    session.append(assistant(100034, "34"))
    session.run("record", KEY)
    assert session.row()[6] == "1"


def test_dispatches_delta(session: LedgerSession) -> None:
    """AC8: starts only; pre-start attempts and Codex replies excluded."""
    session.append(assistant(100000, "a", "Agent"))
    session.run("start", KEY)
    session.append(
        assistant(
            160000, "b", "Agent", "Agent", "mcp__codex__codex", *(["mcp__codex__codex-reply"] * 3)
        )
    )
    session.run("record", KEY)
    assert session.row()[7] == "3"


@pytest.mark.parametrize(
    "case,actual,count",
    [
        ("drop", "> 1.0 (compacted?)", "1"),
        ("first-drop", "> 1.0 (compacted?)", "1"),
        ("marker", "> 1.0 (compacted?)", "1"),
        ("both", "> 1.0 (compacted?)", "1"),
        ("marker-then-drop", "> 1.0 (compacted?)", "1"),
        ("marker-duplicate-then-drop", "> 1.0 (compacted?)", "1"),
        ("marker-then-two-drops", "> 1.0 (compacted?)", "2"),
        ("marker-nondrop-then-drop", "> 1.0 (compacted?)", "2"),
        ("before", "0.03", "0"),
        ("thirty", "> 1.0 (compacted?)", "1"),
    ],
)
def test_compaction(session: LedgerSession, case: str, actual: str, count: str) -> None:
    """AC9: SYNTHETIC markers and observed-drop heuristic, bounded by start."""
    session.append(assistant(90000, "a"))
    if case == "before":
        session.append(assistant(40000, "before"))
    session.run("start", KEY)
    if case == "drop":
        session.append(assistant(95000, "b"), assistant(40000, "c"), assistant(60000, "d"))
    elif case in {"first-drop", "thirty"}:
        session.append(assistant(40000 if case == "first-drop" else 63000, "b"))
    elif case == "both":
        record = assistant(40000, "b")
        record["isCompactSummary"] = True  # SYNTHETIC; not an observed provider marker.
        session.append(record)
    elif case == "marker":
        session.append({"type": "summary"}, assistant(95000, "b"))
    elif case.startswith("marker-"):
        session.append({"type": "summary"})
        if case == "marker-duplicate-then-drop":
            session.append(assistant(90000, "a"), {"type": "user"})
        session.append(assistant(95000 if case == "marker-nondrop-then-drop" else 40000, "b"))
        if case in {"marker-then-two-drops", "marker-nondrop-then-drop"}:
            session.append(assistant(20000, "c"))
    else:
        session.append(assistant(60000, "b"))
    session.run("record", KEY)
    assert session.row()[3] == actual
    assert session.row()[5] == count
    assert session.row()[4] == "n/a"


@pytest.mark.parametrize(
    "case", ["transcript replaced", "transcript truncated", "negative delta", "session changed"]
)
def test_delta_domain(session: LedgerSession, case: str) -> None:
    """AC10/11: invalid delta domains never become numeric calibration rows."""
    session.append(assistant(90000, "a"), assistant(100000, "b"))
    session.run("start", KEY)
    if case == "transcript replaced":
        other = session.transcript.with_name("replaced.jsonl")
        shutil.copy(session.transcript, other)
        session.env["FIXTURE_TRANSCRIPT"] = str(other)
    elif case == "transcript truncated":
        session.transcript.write_text(json.dumps(assistant(120000, "c")) + "\n")
    elif case == "negative delta":
        session.append(assistant(80000, "c"))
    else:
        session.env["FIXTURE_SESSION"] = "changed"
    assert session.run("record", KEY).stdout == f"unmeasured: {case}\n"
    assert session.row()[3:5] == ["n/a", "n/a"]
    assert session.row()[9] == f"unmeasured: {case}"
    assert not session.marker().exists()


def test_baseline_missing_or_changed(session: LedgerSession) -> None:
    session.append(assistant(407821, "a"))
    assert session.run("record", KEY).stdout == "unmeasured: no start marker\n"
    before = session.ledger.read_bytes()
    assert session.run("record", KEY).stdout == f"already recorded {KEY}\n"
    assert session.ledger.read_bytes() == before


def test_manual_seed_golden_and_idempotence(session: LedgerSession) -> None:
    """AC11/21: owner's one-off seed with unknown counters, then rm's no-op."""
    session.append(assistant(407821, "a"))
    session.run(
        "record",
        "SAMPLE-56",
        "--start-tokens",
        "107821",
        "--predicted",
        "0.5",
        "--note",
        "session c06f92a7",
    )
    assert session.ledger.read_text().splitlines()[-1] == (
        "| 2026-09-16 | SAMPLE-56 | 0.5 | 0.50 | 1.00 | unknown | unknown | unknown |"
        " 1000000 | manual start 107821; session c06f92a7 |"
    )
    before = session.ledger.read_bytes()
    assert session.run("record", "SAMPLE-56").stdout == "already recorded SAMPLE-56\n"
    assert session.ledger.read_bytes() == before


def test_manual_prediction_from_ticket(session: LedgerSession) -> None:
    session.ticket("0.25")
    session.append(assistant(160000, "a"))
    session.run("record", KEY, "--start-tokens", "10000")
    assert session.row()[2:9] == [
        "0.25",
        "0.25",
        "1.00",
        "unknown",
        "unknown",
        "unknown",
        "1000000",
    ]


def test_manual_unmeasured_upper_bound_unknown(session: LedgerSession) -> None:
    """AC11: missing usage cannot validate a manual upper bound or invent a measurement."""
    session.run("record", KEY, "--start-tokens", "999999999")
    assert session.row()[3:8] == ["n/a", "n/a", "unknown", "unknown", "unknown"]
    assert session.row()[9] == "manual start 999999999; unmeasured: no usage line"


def test_overlapping_note(session: LedgerSession) -> None:
    """AC12: inclusive deltas belong to each ticket independently."""
    session.append(assistant(100000, "a"))
    session.run("start", KEY)
    session.append(assistant(160000, "b"))
    session.run("start", "SAMPLE-901")
    session.append(assistant(400000, "c"))
    session.run("record", KEY)
    assert session.row()[3] == "0.50"
    assert session.row()[9] == "overlapping SAMPLE-901"
    session.run("record", "SAMPLE-901")
    assert session.row()[3] == "0.40"
    assert session.row()[9] == ""


@pytest.mark.parametrize(
    "ratios,factor",
    [
        ([], "0.60"),
        ([2], "2.00"),
        ([1, 3, 5, 7], "4.00"),
        ([1, 3, 5, 7, 9], "5.00"),
        ([100, 200, 1, 2, 3, 4, 5], "3.00"),
    ],
)
@pytest.mark.parametrize("junk", [False, True], ids=["clean", "junk-warning"])
def test_calibrate(session: LedgerSession, ratios: list[int], factor: str, junk: bool) -> None:
    """AC13: last five numeric ratios, with even-median and skipped non-measurements."""
    session.ledger.parent.mkdir()
    session.ledger.write_text(
        HEADER
        + "\n|---|---|---|---|---|---|---|---|---|---|\n"
        + "".join(f"| 2026-09-16 | {KEY} | 1 | 1 | {n} | 0 | 0 | 0 | 1000000 | |\n" for n in ratios)
        + f"| 2026-09-16 | {KEY} | 1 | > 1.0 (compacted?) | n/a | 1 | 1 | 0 |"
        " 1000000 | |\n" + ("| junk |\n" if junk else "")
    )
    result = session.run("calibrate")
    assert result.stdout == f"factor={factor} (n={min(len(ratios), 5)})\n"
    assert result.stderr == (
        "context-ledger.sh: warning: skipped junk ledger rows\n" if junk else ""
    )


def test_overrides(session: LedgerSession) -> None:
    """AC14: all environment knobs isolated, CLI wins and state window freezes."""
    session.append(assistant(120000, "a"))
    del session.env["CONTEXT_LEDGER_WINDOW"]
    assert "window=1000000" in session.run("now").stdout
    session.env["CONTEXT_LEDGER_WINDOW"] = "200000"
    assert "window=200000 " in session.run("now").stdout
    assert "window=400000 " in session.run("now", "--window", "400000").stdout
    session.env["CONTEXT_LEDGER_STATE_DIR"] = str(session.root / ".git/other-state")
    session.env["CONTEXT_LEDGER_FILE"] = str(session.root / ".git/other-ledger.md")
    session.env["CONTEXT_LEDGER_DISPATCH_TOOLS"] = "Custom"
    session.run("start", KEY)
    session.append(assistant(180000, "b", "Custom", "Agent"))
    session.run("record", KEY)
    assert session.row()[7:9] == ["1", "200000"]
    assert not session.ledger.exists()


@pytest.mark.parametrize(
    "args,rule",
    [
        ([], "expected"),
        (["bad"], "expected"),
        (["start"], "KEY"),
        (["record", "SAMPLE-9a"], "KEY"),
        (["start", "../escape"], "KEY"),
        (["now", "extra"], "extra argument"),
        (["now", "extra\nargument"], "extra argument"),
        (["now", "extra", "arg"], "extra"),
        (["now", "--window", "abc"], "positive integer"),
        (["now", "--window", "0"], "positive integer"),
        (["start", KEY, "--predicted", "bad"], "numeric"),
        (["record", KEY, "--start-tokens", "-1"], "nonnegative integer"),
        (["record", KEY, "--note", "a|b"], "pipe"),
        (["record", KEY, "--note", "a\nb"], "newline"),
        (["record", KEY, "--note", "a\\b"], "backslash"),
        (["record", KEY, "--start-tokens", "100001"], "exceed"),
        (["record", KEY, "--start-tokens", "9" * 100], "exceed"),
        (["record", KEY, "--window"], "missing value"),
    ],
)
def test_cli_refusals(session: LedgerSession, args: list[str], rule: str) -> None:
    """AC15: invalid requests leave the ledger and state byte-identical."""
    session.append(assistant(100000, "a"))
    session.state.mkdir()
    session.ledger.parent.mkdir()
    session.ledger.write_text(HEADER + "\n")
    before = session.ledger.read_bytes()
    result = session.run(*args, code=1)
    assert rule in result.stderr
    assert len(result.stderr.splitlines()) == 1
    assert result.stdout == ""
    assert session.ledger.read_bytes() == before
    assert list(session.state.iterdir()) == []


@pytest.mark.parametrize("state", ["broken", "{}", "[]"])
@pytest.mark.parametrize("command", ["start", "record"])
def test_invalid_state_no_write(session: LedgerSession, state: str, command: str) -> None:
    session.state.mkdir()
    session.marker().write_text(state)
    session.append(assistant(100000, "a"))
    result = session.run(command, KEY, code=1)
    assert result.stderr == "context-ledger.sh: invalid state file\n"
    assert session.marker().read_text() == state
    assert not session.ledger.exists()


@pytest.mark.parametrize("option,value", [("--predicted", "9"), ("--window", "200000")])
def test_frozen_override_refused_without_write(
    session: LedgerSession, option: str, value: str
) -> None:
    """AC14/15: explicit overrides cannot silently discard a frozen G0 decision."""
    session.append(assistant(100000, "a"))
    session.run("start", KEY, "--predicted", "0.5")
    before = session.marker().read_bytes()
    session.ledger.parent.mkdir()
    session.ledger.write_text(HEADER + "\n")
    ledger_before = session.ledger.read_bytes()
    result = session.run("record", KEY, option, value, code=1)
    field = "prediction" if option == "--predicted" else "window"
    assert result.stderr == f"context-ledger.sh: {option} cannot override {field} frozen at start\n"
    assert result.stdout == ""
    assert session.marker().read_bytes() == before
    assert session.ledger.read_bytes() == ledger_before


def test_invalid_env_window_names_source(session: LedgerSession) -> None:
    """AC15: the diagnostic names the invalid environment variable, and CLI still wins."""
    session.env["CONTEXT_LEDGER_WINDOW"] = "abc"
    result = session.run("start", KEY, code=1)
    assert result.stderr == "context-ledger.sh: CONTEXT_LEDGER_WINDOW must be a positive integer\n"
    assert result.stdout == ""
    assert not session.marker().exists() and not session.ledger.exists()
    session.append(assistant(100000, "a"))
    assert session.run("now", "--window", "200000").stdout == (
        "usage=100000 window=200000 used=50.00% remaining=100000 slices=0.83\n"
    )


def test_manual_cannot_override_state(session: LedgerSession) -> None:
    session.append(assistant(100000, "a"))
    session.run("start", KEY)
    before = session.marker().read_bytes()
    result = session.run("record", KEY, "--start-tokens", "0", code=1)
    assert "existing state" in result.stderr
    assert session.marker().read_bytes() == before
    assert not session.ledger.exists()


def assert_ledger_schema(text: str) -> None:
    """Check AC21's appendable schema and lower ticket bound on any ledger document."""
    table = [line for line in text.splitlines() if line.startswith("|")]
    assert table[0] == HEADER
    assert table[1] == "|---|---|---|---|---|---|---|---|---|---|"
    assert all(len(line.strip("|").split("|")) == 10 for line in table)
    for line in table[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        ticket = re.fullmatch(r"SAMPLE-(\d+)", cells[1])
        assert ticket is not None
        assert int(ticket[1]) >= 56


def test_ledger_doc_header_rows_and_no_backfill() -> None:
    """AC21: shipped table can be appended to without a schema change or backfill."""
    assert_ledger_schema((REPO / "docs/context-ledger.md").read_text())


def test_ledger_doc_guard_accepts_recorded_row(session: LedgerSession) -> None:
    """AC21: the real writer's header and one valid row pass the same documentation guard."""
    session.append(assistant(400000, "a"))
    session.run("record", "SAMPLE-56", "--start-tokens", "100000", "--predicted", "0.5")
    assert len(session.ledger.read_text().splitlines()) == 3
    assert_ledger_schema(session.ledger.read_text())


@pytest.mark.parametrize("bad", ["header", "separator", "nine-cells", "backfill", "ticket"])
def test_ledger_doc_guard_rejects_invalid_rows(bad: str) -> None:
    text = (
        HEADER + "\n|---|---|---|---|---|---|---|---|---|---|\n"
        "| 2026-09-17 | SAMPLE-56 | 0.5 | 0.50 | 1.00 | 0 | 1 | 1 | 1000000 | |\n"
    )
    if bad == "header":
        text = text.replace("| date |", "| day |")
    elif bad == "separator":
        text = text.replace("|---|", "|bad|", 1)
    elif bad == "nine-cells":
        text = text.replace("| 1000000 |", "|")
    else:
        text = text.replace("SAMPLE-56", "SAMPLE-55" if bad == "backfill" else "INVALID-56")
    with pytest.raises(AssertionError):
        assert_ledger_schema(text)


def test_slice_sizing_doc_sections() -> None:
    """AC22: report/denominators/rubric/vocabulary are recoverable by their owning headings."""
    doc = (REPO / "docs/slice-sizing.md").read_text()
    headings = re.findall(r"^## (.+)$", doc, re.M)
    assert headings == [
        "Unit",
        "G0 procedure and report",
        "Feature table and weights",
        "Worked examples",
        "Parallel safety",
        "Calibration and ledger operation",
        "Batch-split adversarial check",
        "Established gates & commands",
    ]
    gates = doc.split("## Established gates & commands")[1]
    assert re.findall(r"^\| (G[0-4]) \|", gates, re.M) == ["G0", "G1", "G2", "G3", "G4"]
    # Every shipped script's usage surface is in the closed vocabulary.
    for script in ("worktree.sh", "tickets.sh", "context-ledger.sh"):
        assert f"`scripts/{script} " in gates, script
    # A reader can extract the report into an actual valid `now` command.
    report = re.search(
        r"usage=(\d+) window=(\d+) used=([\d.]+)% remaining=(\d+) slices=([\d.]+)", doc
    )
    assert report
    usage, window, used, remaining, slices = map(float, report.groups())
    assert remaining == window - usage
    assert used == 100 * usage / window
    assert slices == usage / (0.6 * window)


def test_slice_split_template_contract() -> None:
    """AC23: the review packet exposes dependency edges, not just clauses."""
    template = (REPO / "scripts/batch-split-check-template.md").read_text()
    inputs = template.split("Review the proposed split from ONLY these inputs:")[1].split(
        "R1: effect"
    )[0]
    assert "## Acceptance criteria" in inputs and "blocked_by:" in inputs
    assert "Rubric R1–R4" in inputs and "Established gates & commands" in inputs
    assert "VETO circular blocked_by" in template and "VETO vacuously-true AC" in template
    assert "clause / rubric item / why" in template
    assert "human-adjudicated, never automatically blocking" in template


def test_slice_split_fixtures_carry_veto() -> None:
    """AC23: dependency lines alone decide the cycle, and clean clauses have concrete effects."""
    directory = REPO / "tests/fixtures/slice_split"

    def graph(name: str) -> dict[str, list[str]]:
        text = (directory / name).read_text()
        return {
            key: re.findall(r"SAMPLE-\d+", edges)
            for key, edges in re.findall(r"## (SAMPLE-\d+)\nblocked_by: \[([^]]*)\]", text)
        }

    def cyclic(edges: dict[str, list[str]]) -> bool:
        def visit(key: str, seen: set[str]) -> bool:
            return key in seen or any(visit(child, seen | {key}) for child in edges[key])

        return any(visit(key, set()) for key in edges)

    assert cyclic(graph("circular.md"))
    assert not cyclic(graph("clean.md"))
    assert not cyclic(graph("vacuous.md"))
    # A no-op implementation satisfies the bad fixture but fails every clean effect clause.
    vacuous = (directory / "vacuous.md").read_text()
    clean = (directory / "clean.md").read_text()
    assert "The command runs without error." in vacuous
    assert "runs without error" not in clean
    assert all(
        effect in clean
        for effect in ("writes KEY's ticket", "prints the stored title", "deletes KEY's ticket")
    )


def test_template_section_order() -> None:
    """AC24: insert sizing without claiming the future Decision flowchart anchor."""
    headings = re.findall(r"^## (.+)$", (REPO / "specs/_TEMPLATE.md").read_text(), re.M)
    ac = headings.index("Acceptance criteria")
    assert headings[ac + 1 : ac + 3] == ["Size estimate & slice split", "Design / architecture"]
    if "Decision flowchart" in headings:
        assert headings[ac + 3] == "Decision flowchart"


def test_slice_sizing_worked_examples() -> None:
    """AC24: independently recompute every example from its actual feature table."""
    from decimal import ROUND_HALF_UP, Decimal

    doc = (REPO / "docs/slice-sizing.md").read_text()
    table = doc.split("## Feature table and weights")[1].split("## Worked examples")[0]
    weights = [Decimal(value) for value in re.findall(r"^\| [^|]+ \| (0\.\d+) \|$", table, re.M)]
    assert weights == list(
        map(Decimal, ["0.03", "0.01", "0.10", "0.25", "0.25", "0.25", "0.25", "0.10"])
    )
    rows = re.findall(
        r"^\| (split|merge|sibling-conflict) \| ([\d,]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \|",
        doc,
        re.M,
    )
    assert len(rows) == 3
    for name, vector, factor, raw, displayed in rows:
        counts = list(map(int, vector.split(",")))
        computed = sum(c * w for c, w in zip(counts, weights, strict=True)) * Decimal(factor)
        assert computed == Decimal(raw)
        quarter = Decimal("0.25")
        rounded = max(
            quarter, (computed / quarter).quantize(Decimal(1), rounding=ROUND_HALF_UP) * quarter
        )
        assert rounded == Decimal(displayed)
        if name == "split":
            assert computed > 1 and rounded == 1
        elif name == "merge":
            assert computed < quarter and rounded == quarter


def test_calibration_doc_multiplicative_rule() -> None:
    """AC13/22/24: perfect actual/predicted preserves the factor and next displayed size."""
    from decimal import ROUND_HALF_UP, Decimal

    doc = (REPO / "docs/slice-sizing.md").read_text()
    rule = "`next factor = current factor × median(actual/predicted)`"
    for text in (
        doc.split("## Calibration and ledger operation")[1],
        (REPO / "specs/_TEMPLATE.md").read_text(),
    ):
        assert rule in text
    example = re.search(
        r"Calibration example: a feature sum of ([\d.]+) with current factor ([\d.]+) "
        r"gives raw ([\d.]+),\ndisplayed prediction ([\d.]+)\. If actual is ([\d.]+), "
        r"the ratio is ([\d.]+)\.",
        doc,
    )
    assert example is not None
    feature_sum, current, raw, predicted, actual, ratio = map(Decimal, example.groups())
    equation = re.search(r"`next factor = ([\d.]+) × ([\d.]+) = ([\d.]+)`", doc)
    assert equation is not None
    prior, median, next_factor = map(Decimal, equation.groups())
    assert prior == current
    assert median == ratio == actual / predicted == 1
    assert next_factor == current * median == current
    assert raw == feature_sum * next_factor
    quarter = Decimal("0.25")
    displayed = (raw / quarter).quantize(Decimal(1), rounding=ROUND_HALF_UP) * quarter
    assert displayed == predicted


def test_claude_md_g0_row_and_links() -> None:
    """AC25: G0's timing and decision fit the constitution budget and real paths."""
    path = REPO / "CLAUDE.md"
    content = path.read_text()
    assert len(path.read_bytes()) < 40000
    gates = re.findall(r"^\| \*\*(G\d)\*\* \| ([^|]+) \| (.+) \|$", content, re.M)
    assert [row[0] for row in gates] == ["G0", "G1", "G2", "G3", "G4"]
    assert "before `worktree.sh new`" in gates[0][1]
    assert "same session vs push & fresh session" in gates[0][2]
    s4 = content.split("- **S4.**")[1].split("- **S5.**")[0]
    assert "docs/slice-sizing.md" in s4
    for text in (s4, gates[0][2], (REPO / "docs/orchestration.md").read_text().split("## 2.")[0]):
        for link in re.findall(r"`((?:docs|scripts)/[^`]+\.md)`", text):
            assert (REPO / link).is_file(), link


@pytest.mark.parametrize("field", ["requestId", "synthetic-model", "missing-usage"])
def test_invalid_record_cannot_create_turn_or_dispatch(session: LedgerSession, field: str) -> None:
    """AC3/7/8: even a tool-looking synthetic or incomplete record adds no work."""
    session.append(assistant(100000, "a"))
    session.run("start", KEY)
    invalid = assistant(0, "bad", "Agent")
    if field == "requestId":
        del invalid["requestId"]
    elif field == "synthetic-model":
        invalid["message"]["model"] = "<synthetic>"
    else:
        del invalid["message"]["usage"]
    session.append(invalid, assistant(160000, "b"))
    session.run("record", KEY)
    assert session.row()[3:9] == ["0.10", "n/a", "0", "1", "0", "1000000"]


@pytest.mark.parametrize(
    "marker",
    [
        {"isCompactSummary": True},
        {"compact_boundary": True},
        {"type": "system", "subtype": "compact_boundary"},
    ],
)
def test_synthetic_marker_forms(session: LedgerSession, marker: dict[str, Any]) -> None:
    """AC9: explicitly SYNTHETIC known marker keys/subtype all suppress numeric ratios."""
    session.append(assistant(100000, "a"))
    session.run("start", KEY)
    session.append(marker, assistant(160000, "b"))
    session.run("record", KEY)
    assert session.row()[3:6] == ["> 1.0 (compacted?)", "n/a", "1"]


def test_unmeasured_start_preserves_reason(session: LedgerSession) -> None:
    session.run("start", KEY)
    session.append(assistant(100000, "a"))
    session.run("record", KEY)
    assert session.row()[3] == "n/a"
    assert session.row()[9] == "unmeasured: no usage line"


def test_empty_truncation_is_not_a_missing_baseline(session: LedgerSession) -> None:
    """AC10: truncating to zero lines retains the domain failure even without end usage."""
    session.append(assistant(100000, "a"))
    session.run("start", KEY)
    session.transcript.write_text("")
    assert session.run("record", KEY).stdout == "unmeasured: transcript truncated\n"
    assert session.row()[3] == "n/a"
