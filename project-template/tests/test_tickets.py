"""Ticket CLI effects in disposable Git repositories; never access the shared board."""

import errno
import fcntl
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import termios
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest

TODAY = "2026-09-13"
OLD = "2001-02-03"
PRESENTATION_ENV = {
    "TICKETS_COLS",
    "TICKETS_COLOR",
    "TICKETS_DONE_LIMIT",
    "NO_COLOR",
    "COLUMNS",
    "LINES",
    "TICKETS_INTERVAL",
    "PAGER",
    "LESS",
    "LESSKEY",
    "LESSKEYIN",
    "LESSKEY_SYSTEM",
    "LESSKEYIN_SYSTEM",
    "LESSOPEN",
    "LESSCLOSE",
    "LESSHISTFILE",
}
ANSI = re.compile(r"\x1b\[[0-9;]*m")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts/tickets.sh"
LEGACY_HELP = """
The store lives ONCE in the main checkout's tickets/ dir and is shared by every
worktree: tickets.sh always resolves it via `git rev-parse --git-common-dir`, so a
status change made from any worktree lands on one board. Per-ticket files keep
parallel edits conflict-free (different ticket = different file).

Usage:
  tickets.sh                             live TUI on a terminal; static board in pipes
  tickets.sh watch                       live read-only board (TICKETS_INTERVAL=2)
  tickets.sh board                       kanban view (todo | in-progress | done)
  tickets.sh list [status]               one line per ticket, optionally filtered
  tickets.sh show SAMPLE-N               print one ticket file
  tickets.sh new SAMPLE-N "title"        create a ticket (status=todo; title required)
  tickets.sh mv SAMPLE-N <status> [--force]  set status; force overrides blockers on done
  tickets.sh close SAMPLE-N [--force]    shorthand for: mv SAMPLE-N done [--force]
  tickets.sh set-worktree SAMPLE-N [KEY|PATH]  store a portable key; omit to clear
  tickets.sh block SAMPLE-N B1 [B2…]     add ticket dependencies
  tickets.sh unblock SAMPLE-N B1 [B2…]   remove ticket dependencies
  tickets.sh ready                       todo tasks partitioned by dependency readiness
"""
EPIC_HELP = """  tickets.sh new-epic SAMPLE-N "title"  create an epic (status derived from children)
  tickets.sh new SAMPLE-N "title" --epic PARENT  create a child of an epic
  tickets.sh set-epic CHILD PARENT       attach/re-parent a task to an epic
  tickets.sh epic SAMPLE-N               list children and done/total progress
  tickets.sh get-worktree SAMPLE-N       resolve the stored key using WT_ROOT
"""


class TicketRepo:
    """Run the real Bash entry point with an isolated store and a deterministic date."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.script = root / "scripts/tickets.sh"
        self.script.parent.mkdir(parents=True)
        shutil.copy(SCRIPT, self.script)
        self.bin = root / "bin"
        self.bin.mkdir()
        date = self.bin / "date"
        date.write_text('#!/bin/bash\n[[ "$*" == +%F ]] || exit 99\necho "$TEST_DATE"\n')
        date.chmod(0o755)
        self.env = {
            **{
                k: v
                for k, v in os.environ.items()
                if not k.startswith("GIT_") and k not in PRESENTATION_ENV
            },
            "TICKET_PREFIX": "SAMPLE",
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "LC_ALL": "C",
            "TEST_DATE": TODAY,
            "TMPDIR": str(root),
        }
        subprocess.run(
            ["git", "init", "-q", str(root)], env=self.env, check=True, capture_output=True
        )

    def run(self, *args: str, code: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["bash", str(self.script), *args],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=15,
        )
        assert result.returncode == code, (args, result.stdout, result.stderr)
        return result

    def path(self, key: str) -> Path:
        return self.root / "tickets" / f"{key}.md"

    def field(self, key: str, name: str) -> str:
        header = self.path(key).read_text().split("---", 2)[1]
        return next(
            (
                line.partition(":")[2].strip()
                for line in header.splitlines()
                if line.startswith(f"{name}:")
            ),
            "",
        )

    def snapshot(self) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in (self.root / "tickets").glob("*.md")}

    def age(self, key: str) -> None:
        p = self.path(key)
        p.write_bytes(
            p.read_bytes().replace(f"updated: {TODAY}".encode(), f"updated: {OLD}".encode())
        )


@pytest.fixture
def board(tmp_path: Path) -> TicketRepo:
    return TicketRepo(tmp_path / "repo")


def creation_bytes(key: str, title: str) -> bytes:
    return (
        f"---\nkey: {key}\ntitle: {title}\nstatus: todo\nspec:\nworktree:\n"
        f"blocked_by: []\nupdated: {TODAY}\n---\n"
    ).encode()


def numeric_key(row: str) -> tuple[int, str, str]:
    key = row.removeprefix("! ").removeprefix("↳ ").split()[0]
    match = re.fullmatch(r"SAMPLE-(\d+)([a-z]*)", key)
    assert match is not None
    return int(match[1]), match[2], key


def board_golden(
    wish: list[str],
    todo: list[str],
    prog: list[str],
    done: list[str],
    *,
    cols: int = 120,
    limit: int = 10,
) -> str:
    """Independent codepoint oracle; DONE inputs already follow updated/key DESC."""
    width = max(24, (cols - 9) // 4)
    counts = [len(wish), len(todo), len(prog), len(done)]
    columns = [
        sorted(wish, key=numeric_key),
        sorted(todo, key=numeric_key),
        sorted(prog, key=numeric_key),
        done[:],
    ]
    if limit and len(done) > limit:
        columns[3] = done[:limit] + [f"… +{len(done) - limit} more (--all)"]

    def row(cells: list[str]) -> str:
        return (
            " │ ".join(
                (text[: width - 1] + "…" if len(text) > width else text).ljust(width)
                for text in cells
            )
            + "\n"
        )

    headers = [
        f"{label} ({n})"
        for label, n in zip(("WISHLIST", "TODO", "IN-PROGRESS", "DONE"), counts, strict=True)
    ]
    rows = [row(headers), row(["-" * width] * 4)]
    for i in range(max(map(len, columns))):
        rows.append(row([column[i] if i < len(column) else "" for column in columns]))
    return "".join(rows)


def test_legacy_golden(board: TicketRepo) -> None:
    assert board.run().stdout == board_golden([], [], [], [])
    assert board.run("board").stdout == board_golden([], [], [], [])
    assert board.run("list").stdout == ""
    assert not (board.root / "tickets").exists()
    for command in ("help", "-h", "--help"):
        result = board.run(command)
        assert result.stdout == LEGACY_HELP + EPIC_HELP
        assert result.stderr == ""
    result = board.run("unknown", code=1)
    assert result.stdout == LEGACY_HELP + EPIC_HELP
    assert result.stderr == "tickets.sh: unknown command 'unknown'\n"
    assert board.run("new", "SAMPLE-2", "descriptive title").stdout == "created SAMPLE-2 (todo)\n"
    original = creation_bytes("SAMPLE-2", "descriptive title")
    assert board.path("SAMPLE-2").read_bytes() == original
    assert board.run("show", "SAMPLE-2").stdout.encode() == original
    board.age("SAMPLE-2")
    result = board.run("set-worktree", "SAMPLE-2", "SAMPLE-2")
    assert (result.stdout, result.stderr) == ("", "")
    expected = original.replace(b"worktree:", b"worktree: SAMPLE-2")
    assert board.path("SAMPLE-2").read_bytes() == expected
    for command, status in (("mv", "in-progress"), ("close", "done"), ("mv", "todo")):
        board.age("SAMPLE-2")
        args = (command, "SAMPLE-2", status) if command == "mv" else (command, "SAMPLE-2")
        result = board.run(*args)
        assert (result.stdout, result.stderr) == (f"SAMPLE-2 -> {status}\n", "")
        assert board.path("SAMPLE-2").read_bytes() == expected.replace(
            b"status: todo", f"status: {status}".encode()
        )
    # Explicit type: task must follow the same standalone path.
    board.path("SAMPLE-2").write_bytes(expected.replace(b"spec:", b"type: task\nspec:"))
    assert board.run("close", "SAMPLE-2").stdout == "SAMPLE-2 -> done\n"


@pytest.mark.parametrize("command", ["new", "new-epic"])
@pytest.mark.parametrize("case", ["missing-title", "equal-title", "duplicate", "bad-key"])
@pytest.mark.parametrize("wishlist", [False, True])
def test_creation_errors_no_write(
    board: TicketRepo, command: str, case: str, wishlist: bool
) -> None:
    board.run("new", "SAMPLE-1", "existing")
    args, message = {
        "missing-title": (
            ["SAMPLE-2"],
            f"title required: tickets.sh {command} SAMPLE-2 "
            '"a descriptive title" (do not repeat the key)',
        ),
        "equal-title": (
            ["SAMPLE-2", "SAMPLE-2"],
            "title must not equal the key 'SAMPLE-2' — give a descriptive title",
        ),
        "duplicate": (["SAMPLE-1", "duplicate"], "ticket already exists: SAMPLE-1"),
        "bad-key": (["../bad", "title"], f'usage: tickets.sh {command} SAMPLE-N "title"'),
    }[case]
    if wishlist:
        # An explicit empty title keeps the original missing-title guard reachable.
        if case == "missing-title":
            args.append("")
        args.append("--wishlist")
    before = board.snapshot()
    result = board.run(command, *args, code=1)
    assert (result.stdout, result.stderr) == ("", f"tickets.sh: {message}\n")
    assert board.snapshot() == before


@pytest.mark.parametrize(
    "args,message",
    [
        (("show", "../bad"), "bad key '../bad' (expected SAMPLE-N or SAMPLE-Nx)"),
        (("close", "SAMPLE-1A"), "bad key 'SAMPLE-1A' (expected SAMPLE-N or SAMPLE-Nx)"),
        (
            ("show", "SAMPLE-999"),
            "no such ticket: SAMPLE-999 (create it with: tickets.sh new SAMPLE-999)",
        ),
        (
            ("mv", "SAMPLE-1", "invalid"),
            "bad status 'invalid' (one of: wishlist todo in-progress done)",
        ),
        (("list", "invalid"), "bad status 'invalid' (one of: wishlist todo in-progress done)"),
    ],
)
def test_legacy_validation(board: TicketRepo, args: tuple[str, ...], message: str) -> None:
    board.run("new", "SAMPLE-1", "one")
    before = board.snapshot()
    result = board.run(*args, code=1)
    assert (result.stdout, result.stderr) == ("", f"tickets.sh: {message}\n")
    assert board.snapshot() == before


def test_status_derivation_and_epic_timestamp(board: TicketRepo) -> None:
    board.run("new-epic", "SAMPLE-30", "e")
    assert board.field("SAMPLE-30", "type") == "epic"
    for child in ("a", "b", "c"):
        board.run("new", f"SAMPLE-30{child}", child, "--epic", "SAMPLE-30")
    assert board.field("SAMPLE-30", "status") == "todo"
    board.age("SAMPLE-30")
    before = board.path("SAMPLE-30").read_bytes()
    assert board.run("mv", "SAMPLE-30a", "todo").stdout == "SAMPLE-30a -> todo\n"
    assert board.path("SAMPLE-30").read_bytes() == before
    assert (
        "SAMPLE-30 -> in-progress (0/3 children done)"
        in board.run("mv", "SAMPLE-30a", "in-progress").stdout
    )
    assert board.field("SAMPLE-30", "updated") == TODAY
    for child in ("a", "b"):
        board.run("close", f"SAMPLE-30{child}")
        assert board.field("SAMPLE-30", "status") == "in-progress"
    result = board.run("close", "SAMPLE-30c")
    assert result.stdout == "SAMPLE-30c -> done\nSAMPLE-30 -> done (3/3 children done)\n"
    board.run("mv", "SAMPLE-30a", "todo")
    assert board.field("SAMPLE-30", "status") == "in-progress"
    board.run("mv", "SAMPLE-30b", "todo")
    assert "SAMPLE-30 -> todo (0/3 children done)" in board.run("mv", "SAMPLE-30c", "todo").stdout
    assert board.field("SAMPLE-30", "status") == "todo"


def test_membership_recomputes_both_and_empty_epic(board: TicketRepo) -> None:
    for key in ("SAMPLE-1", "SAMPLE-2", "SAMPLE-3"):
        board.run("new-epic", key, "e")
    board.age("SAMPLE-3")
    empty = board.path("SAMPLE-3").read_bytes()
    board.run("new", "SAMPLE-1a", "a", "--epic", "SAMPLE-1")
    board.run("close", "SAMPLE-1a")
    board.run("new", "SAMPLE-1b", "b", "--epic", "SAMPLE-1")
    assert board.field("SAMPLE-1", "status") == "in-progress"
    result = board.run("set-epic", "SAMPLE-1a", "SAMPLE-2")
    assert result.stdout == (
        "SAMPLE-1a -> epic SAMPLE-2\n"
        "SAMPLE-1 -> todo (0/1 children done)\n"
        "SAMPLE-2 -> done (1/1 children done)\n"
    )
    assert board.field("SAMPLE-1a", "epic") == "SAMPLE-2"
    assert board.field("SAMPLE-1", "status") == "todo"
    assert board.field("SAMPLE-2", "status") == "done"
    board.age("SAMPLE-2")
    complete = board.path("SAMPLE-2").read_bytes()
    board.run("set-epic", "SAMPLE-1a", "SAMPLE-2")
    assert board.path("SAMPLE-2").read_bytes() == complete
    # Moving the last child away leaves the now-empty epic unchanged, even if done.
    board.run("set-epic", "SAMPLE-1a", "SAMPLE-1")
    assert board.path("SAMPLE-2").read_bytes() == complete
    assert board.path("SAMPLE-3").read_bytes() == empty
    assert board.run("epic", "SAMPLE-3").stdout == (
        "SAMPLE-3 [epic 0/0] (0/0 children done)\nNo children.\n"
    )


def test_attach_done_task_and_create_child_reopen_complete_epic(board: TicketRepo) -> None:
    board.run("new-epic", "SAMPLE-1", "e")
    board.run("new", "SAMPLE-1a", "a")
    board.run("close", "SAMPLE-1a")
    assert (
        "SAMPLE-1 -> done (1/1 children done)"
        in board.run("set-epic", "SAMPLE-1a", "SAMPLE-1").stdout
    )
    board.run("new", "SAMPLE-1b", "b")
    board.run("close", "SAMPLE-1b")
    board.age("SAMPLE-1")
    before = board.path("SAMPLE-1").read_bytes()
    assert board.run("set-epic", "SAMPLE-1b", "SAMPLE-1").stdout == "SAMPLE-1b -> epic SAMPLE-1\n"
    assert board.path("SAMPLE-1").read_bytes() == before
    assert (
        "SAMPLE-1 -> in-progress (2/3 children done)"
        in board.run("new", "SAMPLE-1c", "c", "--epic", "SAMPLE-1").stdout
    )
    assert board.field("SAMPLE-1", "updated") == TODAY


def test_new_child_requires_parent_argument(board: TicketRepo) -> None:
    result = board.run("new", "SAMPLE-1", "a", "--epic", code=1)
    assert result.stderr == 'tickets.sh: usage: tickets.sh new SAMPLE-1 "title" --epic PARENT\n'
    assert not (board.root / "tickets").exists()


@pytest.mark.parametrize(
    "child,parent,message",
    [
        ("SAMPLE-1a", "SAMPLE-999", "no such ticket"),
        ("SAMPLE-1a", "SAMPLE-2", "not an epic"),
        ("SAMPLE-1a", "SAMPLE-1a", "own epic"),
        ("SAMPLE-1", "SAMPLE-3", "epic cannot be a child"),
        ("SAMPLE-1a", "../bad", "bad key"),
    ],
)
def test_set_epic_rejects_without_writes(
    board: TicketRepo, child: str, parent: str, message: str
) -> None:
    board.run("new-epic", "SAMPLE-1", "e")
    board.run("new-epic", "SAMPLE-3", "e")
    board.run("new", "SAMPLE-2", "task")
    board.run("new", "SAMPLE-1a", "a", "--epic", "SAMPLE-1")
    before = board.snapshot()
    result = board.run("set-epic", child, parent, code=1)
    assert message in result.stderr
    assert result.stdout == ""
    assert board.snapshot() == before


@pytest.mark.parametrize("parent", ["SAMPLE-999", "SAMPLE-1", "SAMPLE-2", "../bad", ""])
def test_new_child_rejects_before_creation(board: TicketRepo, parent: str) -> None:
    board.run("new", "SAMPLE-1", "task")
    before = board.snapshot()
    result = board.run("new", "SAMPLE-2", "child", "--epic", parent, code=1)
    assert result.stderr and not result.stdout
    assert board.snapshot() == before


@pytest.mark.parametrize("ending", [b"\n", b""])
def test_legacy_insertion_preserves_fields_and_body(board: TicketRepo, ending: bytes) -> None:
    board.run("new-epic", "SAMPLE-1", "e")
    board.run("new-epic", "SAMPLE-2", "e")
    original = (
        b"--- \t\nkey: SAMPLE-1a\ntitle: legacy  \nstatus: done\n"
        b"spec: specs/a.md\nworktree: /a path\ncustom: keep  \n"
        + f"updated: {OLD}\n".encode()
        + b"---\n\nBody with \\backslash and trailing spaces  \n"
        b"---\nepic: body-only\nstatus: body-only\n---\nlast" + ending
    )
    board.path("SAMPLE-1a").write_bytes(original)
    board.run("set-epic", "SAMPLE-1a", "SAMPLE-1")
    expected = original.replace(
        f"updated: {OLD}\n".encode(), f"updated: {TODAY}\nepic: SAMPLE-1\n".encode(), 1
    )
    assert board.path("SAMPLE-1a").read_bytes() == expected
    assert board.field("SAMPLE-1", "status") == "done"
    board.run("set-epic", "SAMPLE-1a", "SAMPLE-2")
    assert board.path("SAMPLE-1a").read_bytes() == expected.replace(
        b"epic: SAMPLE-1\n", b"epic: SAMPLE-2\n", 1
    )


@pytest.mark.parametrize(
    "args",
    [("mv", "wishlist"), ("mv", "todo"), ("mv", "in-progress"), ("mv", "done"), ("close",)],
)
def test_manual_epic_move_refused(board: TicketRepo, args: tuple[str, ...]) -> None:
    board.run("new-epic", "SAMPLE-1", "e")
    board.age("SAMPLE-1")
    before = board.snapshot()
    result = board.run(args[0], "SAMPLE-1", *args[1:], code=1)
    assert "epic status is derived from its children" in result.stderr
    assert result.stdout == ""
    assert board.snapshot() == before


@pytest.mark.parametrize("parent", ["SAMPLE-999", "SAMPLE-2", "../bad"])
@pytest.mark.parametrize("status", ["done", "wishlist"])
def test_dangling_parent_warns_but_child_move_succeeds(
    board: TicketRepo, parent: str, status: str
) -> None:
    board.run("new", "SAMPLE-1", "child")
    board.run("new", "SAMPLE-2", "task")
    other = board.path("SAMPLE-2").read_bytes()
    p = board.path("SAMPLE-1")
    p.write_bytes(p.read_bytes().replace(b"spec:", f"epic: {parent}\nspec:".encode()))
    args = ("close", "SAMPLE-1") if status == "done" else ("mv", "SAMPLE-1", status)
    result = board.run(*args)
    assert result.stdout == f"SAMPLE-1 -> {status}\n"
    assert result.stderr == (
        f"tickets.sh: warn: epic '{parent}' is missing or not an epic; skipping recompute\n"
    )
    assert board.field("SAMPLE-1", "status") == status
    assert board.path("SAMPLE-2").read_bytes() == other
    board.run("new-epic", "SAMPLE-3", "e")
    result = board.run("set-epic", "SAMPLE-1", "SAMPLE-3")
    assert "skipping recompute" in result.stderr
    assert board.field("SAMPLE-3", "status") == status


def test_display_golden_and_read_only_view(board: TicketRepo) -> None:
    long_title = "a title longer than thirty characters stays full in list"
    for key, title in (("SAMPLE-2", "two"), ("SAMPLE-10", long_title), ("SAMPLE-1", "one")):
        board.run("new", key, title)
    board.run("mv", "SAMPLE-2", "in-progress")
    # Both display commands order by the frontmatter key, independent of filename.
    board.path("SAMPLE-10").rename(board.root / "tickets" / "a.md")
    legacy_rows = [
        f"{'SAMPLE-1':12} {'todo':13} one\n",
        f"{'SAMPLE-2':12} {'in-progress':13} two\n",
        f"{'SAMPLE-10':12} {'todo':13} {long_title}\n",
    ]
    assert board.run("list").stdout == "".join(legacy_rows)
    legacy_board = board_golden(
        [], ["SAMPLE-1  one", f"SAMPLE-10  {long_title}"], ["SAMPLE-2  two"], []
    )
    assert board.run("board").stdout == legacy_board
    board.run("new-epic", "SAMPLE-3", "e")
    board.run("new", "SAMPLE-3a", "a", "--epic", "SAMPLE-3")
    board.run("close", "SAMPLE-3a")
    before = board.snapshot()
    assert board.run("list").stdout == (
        "".join(legacy_rows[:2])
        + f"{'SAMPLE-3':12} {'done':13} e [epic 1/1]\n{'SAMPLE-3a':12} {'done':13} a\n"
        + legacy_rows[2]
    )
    assert board.run("list", "todo").stdout == legacy_rows[0] + legacy_rows[2]
    expected = board_golden(
        [],
        ["SAMPLE-1  one", f"SAMPLE-10  {long_title}"],
        ["SAMPLE-2  two"],
        ["SAMPLE-3a  a", "SAMPLE-3  e [epic 1/1]"],
    )
    assert board.run("board").stdout == expected
    assert board.run().stdout == expected
    assert board.run("epic", "SAMPLE-3").stdout == (
        f"SAMPLE-3 [epic 1/1] (1/1 children done)\n{'SAMPLE-3a':12} {'done':13} a\n"
    )
    assert "not an epic" in board.run("epic", "SAMPLE-1", code=1).stderr
    assert "no such ticket" in board.run("epic", "SAMPLE-999", code=1).stderr
    assert board.snapshot() == before
    board.run("new-epic", "SAMPLE-4", long_title)
    assert board.run("board").stdout == board_golden(
        [],
        ["SAMPLE-1  one", f"SAMPLE-4  {long_title} [epic 0/0]", f"SAMPLE-10  {long_title}"],
        ["SAMPLE-2  two"],
        ["SAMPLE-3a  a", "SAMPLE-3  e [epic 1/1]"],
    )


def test_e2e_happy_path(board: TicketRepo) -> None:
    board.run("new-epic", "SAMPLE-90", "e")
    board.run("new", "SAMPLE-90a", "a", "--epic", "SAMPLE-90")
    board.run("new", "SAMPLE-90b", "b", "--epic", "SAMPLE-90")
    board.run("close", "SAMPLE-90a")
    assert board.field("SAMPLE-90", "status") == "in-progress"
    assert "2/2 children done" in board.run("close", "SAMPLE-90b").stdout
    assert board.field("SAMPLE-90", "status") == "done"


@pytest.mark.parametrize("first,second", [("a", "b"), ("b", "a")])
def test_interleaved_closes_converge_idempotently(
    board: TicketRepo, first: str, second: str
) -> None:
    """Pause after one child's status write; another close recomputes before it resumes.

    This checks eventual convergence, not linearizability: there is deliberately no lock.
    """
    board.run("new-epic", "SAMPLE-90", "e")
    for child in ("a", "b"):
        board.run("new", f"SAMPLE-90{child}", child, "--epic", "SAMPLE-90")
    marker = board.root / "paused"
    release = board.root / "release"
    mv = board.bin / "mv"
    mv.write_text(
        """#!/bin/bash
/bin/mv "$@" || exit $?
if [[ "$2" == "$PAUSE_CHILD" && ! -f "$PAUSE_MARKER" ]]; then
  touch "$PAUSE_MARKER"
  for ((i=0; i<1000; i++)); do
    [[ ! -f "$PAUSE_RELEASE" ]] || exit 0
    /bin/sleep 0.01
  done
  exit 99
fi
"""
    )
    mv.chmod(0o755)
    board.env.update(
        PAUSE_CHILD=str(board.path(f"SAMPLE-90{first}")),
        PAUSE_MARKER=str(marker),
        PAUSE_RELEASE=str(release),
    )
    process = subprocess.Popen(
        ["bash", str(board.script), "close", f"SAMPLE-90{first}"],
        cwd=board.root,
        env=board.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert process.poll() is None
            assert time.monotonic() < deadline, "child never reached write barrier"
            time.sleep(0.01)
        board.run("close", f"SAMPLE-90{second}")
        assert board.field("SAMPLE-90", "status") == "done"
    finally:
        release.touch()
        stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, (stdout, stderr)
    assert stdout == f"SAMPLE-90{first} -> done\n"
    board.age("SAMPLE-90")
    before = board.path("SAMPLE-90").read_bytes()
    # Repeated close triggers a final full recompute without exposing an internal command.
    for child in (second, first, second):
        assert board.run("close", f"SAMPLE-90{child}").stdout == f"SAMPLE-90{child} -> done\n"
        assert board.path("SAMPLE-90").read_bytes() == before


def put_ticket(
    board: TicketRepo,
    key: str,
    *,
    title: str = "task",
    status: str = "todo",
    updated: str = TODAY,
    epic: str = "",
    kind: str = "task",
) -> str:
    """Seed display-only cases directly in the disposable store, including malformed data."""
    board.path(key).parent.mkdir(exist_ok=True)
    board.path(key).write_text(
        f"---\nkey: {key}\ntitle: {title}\nstatus: {status}\nupdated: {updated}\n"
        f"type: {kind}\nepic: {epic}\n---\n"
    )
    return f"{key}  {title}"


def visible_columns(output: str) -> list[list[str]]:
    rows = [line.split(" │ ") for line in ANSI.sub("", output).splitlines()[2:]]
    assert all(len(row) == 4 for row in rows)
    return [[row[i].rstrip() for row in rows if row[i].strip()] for i in range(4)]


def tty_board(board: TicketRepo) -> str:
    """A real stdout PTY with bounded draining; no terminal/device belonging to the user."""
    master, slave = pty.openpty()
    process = None
    try:
        attrs = termios.tcgetattr(slave)
        attrs[1] &= ~termios.OPOST  # Preserve LF bytes for the plain/ANSI oracle.
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        process = subprocess.Popen(
            ["bash", str(board.script), "board"],
            cwd=board.root,
            env=board.env,
            stdout=slave,
            stderr=subprocess.PIPE,
            start_new_session=True,  # No inherited controlling TTY: exercise fallbacks.
        )
        output = bytearray()
        deadline = time.monotonic() + 15
        while True:
            assert time.monotonic() < deadline, "PTY board timed out"
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                output.extend(os.read(master, 65536))
            elif process.poll() is not None:
                break
        _, stderr = process.communicate(timeout=1)
        assert (process.returncode, stderr) == (0, b"")
        return output.decode()
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        os.close(master)
        os.close(slave)


def stub_tput(board: TicketRepo, value: str, code: int = 0) -> None:
    script = board.bin / "tput"
    script.write_text(
        '#!/bin/bash\nprintf "%s" "$TPUT_VALUE"\n'
        'echo called >> "$TPUT_CALLS"\necho hidden-diagnostic >&2\n'
        'exit "$TPUT_CODE"\n'
    )
    script.chmod(0o755)
    board.env.update(TPUT_VALUE=value, TPUT_CODE=str(code), TPUT_CALLS=str(board.root / "calls"))


def test_board_uses_controlling_pty_width(board: TicketRepo) -> None:
    """A real 150-column controlling terminal must beat captured tput's default 80."""
    todo = [put_ticket(board, "SAMPLE-2", title="a title that fills the wider column")]
    prog = [put_ticket(board, "SAMPLE-8", title="in flight", status="in-progress")]
    before = board.snapshot()
    board.env.update(TERM="xterm-256color", TICKETS_COLOR="never")
    # Synchronize before exec so the script cannot query the initial zero-sized PTY.
    ready_read, ready_write = os.pipe()
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.close(ready_write)
            if os.read(ready_read, 1) != b"1":
                os._exit(99)
            os.close(ready_read)
            os.chdir(board.root)
            os.execve("/bin/bash", ["bash", "./scripts/tickets.sh", "board"], board.env)
        finally:
            os._exit(99)

    os.close(ready_read)
    status = None
    try:
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 150, 0, 0))
        attrs = termios.tcgetattr(master)
        attrs[1] &= ~termios.OPOST  # Keep LF bytes for the exact golden comparison.
        termios.tcsetattr(master, termios.TCSANOW, attrs)
        os.write(ready_write, b"1")
        output = bytearray()
        deadline = time.monotonic() + 15
        while True:
            assert time.monotonic() < deadline, "controlling-PTY board timed out"
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno != errno.EIO:  # Linux PTY EOF; macOS returns b"".
                        raise
                    chunk = b""
                if not chunk:
                    break
                output.extend(chunk)
        while status is None:
            waited, child_status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                status = child_status
            else:
                assert time.monotonic() < deadline, "PTY child did not exit"
                time.sleep(0.01)
        assert os.waitstatus_to_exitcode(status) == 0, output.decode()
        rendered = output.decode()
        assert "\x1b" not in rendered
        assert rendered == board_golden([], todo, prog, [], cols=150)
        assert all(len(line) == 149 for line in rendered.splitlines())
        assert rendered.splitlines()[1].split(" │ ") == ["-" * 35] * 4
        assert board.snapshot() == before
    finally:
        if status is None:
            with suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        os.close(ready_write)
        os.close(master)


def test_fixture_isolates_presentation_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PRESENTATION_ENV:
        monkeypatch.setenv(name, "poison")
    repo = TicketRepo(tmp_path / "isolated")
    assert PRESENTATION_ENV.isdisjoint(repo.env)
    assert repo.env["LC_ALL"] == "C"
    assert repo.run().stdout == board_golden([], [], [], [])


@pytest.mark.parametrize("status,column", [("wishlist", 0), ("todo", 1), ("in-progress", 2)])
def test_numeric_decimal_order(board: TicketRepo, status: str, column: int) -> None:
    keys = ["30a", "22d", "8b", "10", "2", "30", "9", "22b", "8", "22", "08", "010"]
    for key in keys:
        put_ticket(board, f"SAMPLE-{key}", status=status)
    expected = [
        f"SAMPLE-{key}"
        for key in ("2", "08", "8", "8b", "9", "010", "10", "22", "22b", "22d", "30", "30a")
    ]
    rows = visible_columns(board.run("board").stdout)[column]
    assert [row.split()[0] for row in rows] == expected
    for args in (("list",), ("list", status)):
        result = board.run(*args)
        assert [row.split()[0] for row in result.stdout.splitlines()] == expected
        assert result.stderr == ""


@pytest.mark.parametrize("cols,width", [("1", 24), ("60", 24), ("150", 35), ("0150", 35)])
def test_width_and_ellipsis(board: TicketRepo, cols: str, width: int) -> None:
    long = put_ticket(board, "SAMPLE-1", title="x" * 100)
    short = put_ticket(board, "SAMPLE-2", title="short")
    exact = put_ticket(board, "SAMPLE-3", title="y" * (width - len("SAMPLE-3  ")))
    board.env["TICKETS_COLS"] = cols
    result = board.run("board")
    assert result.stderr == ""
    assert result.stdout == board_golden([], [long, short, exact], [], [], cols=int(cols))
    assert all(len(line) == width * 4 + 9 for line in result.stdout.splitlines())
    assert visible_columns(result.stdout)[1] == [long[: width - 1] + "…", short, exact]


def test_multibyte_alignment_under_c_locale(board: TicketRepo) -> None:
    rows = [
        put_ticket(board, "SAMPLE-1", title="dash — section §", kind="epic") + " [epic 0/1]",
        "↳ " + put_ticket(board, "SAMPLE-2", title="—§" * 30, epic="SAMPLE-1"),
        put_ticket(board, "SAMPLE-3", title=r"literal \n and \t"),
    ]
    assert board.env["LC_ALL"] == "C"
    plain = board.run("board").stdout
    assert plain == board_golden([], rows, [], [])
    assert all(len(line) == 117 for line in plain.splitlines())
    assert all(line.index(" │ ") == 27 for line in plain.splitlines())
    assert "↳ SAMPLE-2" in plain and "…" in plain
    assert "\x1b" not in plain
    board.env["TICKETS_COLOR"] = "always"
    assert ANSI.sub("", board.run("board").stdout) == plain


@pytest.mark.parametrize(
    "name,invalid",
    [("TICKETS_COLS", value) for value in ("", "abc", "0", "-5", "1.5", "9" * 40)]
    + [("TICKETS_DONE_LIMIT", value) for value in ("", "abc", "-1", "1.5", "9" * 40)]
    + [("TICKETS_COLOR", "purple"), ("TICKETS_COLOR", "")],
)
def test_invalid_inputs_silently_degrade(board: TicketRepo, name: str, invalid: str) -> None:
    for i in range(14):
        put_ticket(board, f"SAMPLE-{i}", status="done")
    baseline = board.run("board").stdout
    assert "… +4 more (--all)" in baseline
    board.env[name] = invalid
    result = board.run("board")
    assert (result.stdout, result.stderr) == (baseline, "")


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize("color", [None, "", "purple", "always", "never"])
@pytest.mark.parametrize("no_color", [None, "", "1"])
def test_color_precedence(
    board: TicketRepo, tty: bool, color: str | None, no_color: str | None
) -> None:
    put_ticket(board, "SAMPLE-1", title="— §")
    put_ticket(board, "SAMPLE-2", status="in-progress")
    put_ticket(board, "SAMPLE-3", status="done")
    put_ticket(board, "SAMPLE-4", status="wishlist")
    board.env["TICKETS_COLS"] = "120"
    baseline = board.run("board").stdout
    if color is not None:
        board.env["TICKETS_COLOR"] = color
    if no_color is not None:
        board.env["NO_COLOR"] = no_color
    output = tty_board(board) if tty else board.run("board").stdout
    expected = color == "always" or (color != "never" and no_color is None and tty)
    assert ("\x1b" in output) == expected
    assert ANSI.sub("", output) == baseline


def test_color_layers(board: TicketRepo) -> None:
    put_ticket(board, "SAMPLE-1", title="epic", kind="epic", status="in-progress")
    put_ticket(board, "SAMPLE-1a", epic="SAMPLE-1", status="done")
    put_ticket(board, "SAMPLE-1b", epic="SAMPLE-1", status="in-progress")
    put_ticket(board, "SAMPLE-2", status="in-progress")
    put_ticket(board, "SAMPLE-3", status="wishlist")
    put_ticket(board, "SAMPLE-4", title="epic", kind="epic", status="wishlist")
    put_ticket(board, "SAMPLE-4a", epic="SAMPLE-4", status="wishlist")
    plain = board.run("board").stdout
    board.env["TICKETS_COLOR"] = "always"
    output = board.run("board").stdout
    assert ANSI.sub("", output) == plain
    assert "\x1b[1mTODO (0)" in output
    assert "\x1b[1mSAMPLE-2" in output
    assert "\x1b[1m\x1b[36mSAMPLE-1  epic [epic 1/2]" in output
    assert "\x1b[2mSAMPLE-1a" in output
    assert "\x1b[2m↳ \x1b[0m\x1b[1mSAMPLE-1b" in output
    assert "\x1b[2m │ \x1b[0m" in output
    assert "\x1b[2mSAMPLE-3" in output
    assert "\x1b[2m\x1b[36mSAMPLE-4  epic [epic 0/1]" in output
    assert "\x1b[2m↳ \x1b[0m\x1b[2mSAMPLE-4a" in output


@pytest.mark.parametrize(
    "tput_value,code,columns,expected",
    [
        ("150", 0, "180", 150),
        ("", 0, "180", 180),
        ("abc", 0, "180", 180),
        ("0", 0, "180", 180),
        ("-5", 0, "180", 180),
        ("150", 1, "180", 180),
        ("", 127, None, 120),
        ("", 0, "bad", 120),
        ("", 0, "0", 120),
        ("", 0, "", 120),
    ],
)
def test_tty_width_fallback(
    board: TicketRepo, tput_value: str, code: int, columns: str | None, expected: int
) -> None:
    stub_tput(board, tput_value, code)
    board.env.update(TICKETS_COLOR="never", TICKETS_COLS="invalid")
    if columns is not None:
        board.env["COLUMNS"] = columns
    assert tty_board(board) == board_golden([], [], [], [], cols=expected)
    assert (board.root / "calls").read_text() == "called\n"


def test_width_source_precedence(board: TicketRepo) -> None:
    stub_tput(board, "150")
    board.env.update(TICKETS_COLOR="never", COLUMNS="180", TICKETS_COLS="200")
    assert tty_board(board) == board_golden([], [], [], [], cols=200)
    assert not (board.root / "calls").exists()
    del board.env["TICKETS_COLS"]
    assert board.run("board").stdout == board_golden([], [], [], [], cols=180)
    assert not (board.root / "calls").exists()  # tput is TTY-only.
    for invalid in ("", "abc", "0", "-5"):
        board.env["COLUMNS"] = invalid
        result = board.run("board")
        assert (result.stdout, result.stderr) == (board_golden([], [], [], []), "")


@pytest.mark.parametrize(
    "limit,all_rows,shown",
    [
        (None, False, 10),
        ("3", False, 3),
        ("03", False, 3),
        ("0", False, 14),
        ("14", False, 14),
        ("20", False, 14),
        (None, True, 14),
        ("3", True, 14),
        ("invalid", True, 14),
    ],
)
def test_done_cap_and_all_override(
    board: TicketRepo, limit: str | None, all_rows: bool, shown: int
) -> None:
    # Reverse dates relative to numeric keys: recency must beat numeric order.
    done = [
        put_ticket(board, f"SAMPLE-{i}", status="done", updated=f"2026-08-{15 - i:02}")
        for i in range(1, 15)
    ]
    todo = [put_ticket(board, f"SAMPLE-{i}") for i in range(20, 32)]
    prog = [put_ticket(board, f"SAMPLE-{i}", status="in-progress") for i in range(40, 52)]
    wish = [
        put_ticket(board, f"SAMPLE-{key}", status="wishlist")
        for key in ["060", "60b", "60", "090", "90", *map(str, range(85, 60, -1))]
    ]
    before = board.snapshot()
    if limit is not None:
        board.env["TICKETS_DONE_LIMIT"] = limit
    result = board.run("board", *(["--all"] if all_rows else []))
    assert result.stderr == ""
    assert result.stdout == board_golden(wish, todo, prog, done, limit=shown)
    columns = visible_columns(result.stdout)
    assert columns[0] == sorted(wish, key=numeric_key)
    assert len(result.stdout.splitlines()) == len(wish) + 2
    assert "WISHLIST (30)" in result.stdout
    assert all("more (--all)" not in row for row in columns[0])
    assert len(columns[1]) == len(columns[2]) == 12
    assert columns[3][:shown] == done[:shown]
    assert "DONE (14)" in result.stdout
    assert ("more (--all)" in result.stdout) == (shown < 14)
    assert board.snapshot() == before


def test_done_total_tiebreak(board: TicketRepo) -> None:
    for key in ("8", "08", "010", "10", "8b", "8aa", "8a", "2"):
        put_ticket(board, f"SAMPLE-{key}", status="done")
    expected = [f"SAMPLE-{key}  task" for key in ("10", "010", "8b", "8aa", "8a", "8", "08", "2")]
    assert visible_columns(board.run("board").stdout)[3] == expected


@pytest.mark.parametrize("total", [0, 3, 10])
def test_done_at_or_below_default_limit(board: TicketRepo, total: int) -> None:
    rows = [put_ticket(board, f"SAMPLE-{i}", status="done") for i in range(total)]
    result = board.run("board")
    assert result.stdout == board_golden([], [], [], list(reversed(rows)))
    assert "more (--all)" not in result.stdout
    assert result.stderr == ""


def test_invalid_status_warn_and_skip(board: TicketRepo) -> None:
    put_ticket(board, "SAMPLE-1", status="bogus")
    row = put_ticket(board, "SAMPLE-2")
    before = board.snapshot()
    result = board.run("board")
    assert result.stderr == "tickets.sh: warn: SAMPLE-1 has invalid status 'bogus'\n"
    assert result.stdout == board_golden([], [row], [], [])
    assert board.snapshot() == before


def test_epic_indent_and_membership_does_not_regroup(board: TicketRepo) -> None:
    board.run("new-epic", "SAMPLE-2", "e")
    for key in ("SAMPLE-2b", "SAMPLE-1a", "SAMPLE-2a"):
        board.run("new", key, "child", "--epic", "SAMPLE-2")
    assert visible_columns(board.run("board").stdout)[1] == [
        "SAMPLE-1a  child",
        "SAMPLE-2  e [epic 0/3]",
        "↳ SAMPLE-2a  child",
        "↳ SAMPLE-2b  child",
    ]
    # Preserve the separate epic command's filename order exactly.
    board.path("SAMPLE-2b").rename(board.root / "tickets" / "a.md")
    assert board.run("epic", "SAMPLE-2").stdout == (
        "SAMPLE-2 [epic 0/3] (0/3 children done)\n"
        + "".join(
            f"{key:12} {'todo':13} child\n" for key in ("SAMPLE-1a", "SAMPLE-2a", "SAMPLE-2b")
        )
    )
    board.run("close", "SAMPLE-2a")
    assert visible_columns(board.run("board").stdout) == [
        [],
        ["SAMPLE-1a  child", "SAMPLE-2b  child"],
        ["SAMPLE-2  e [epic 1/3]"],
        ["SAMPLE-2a  child"],
    ]
    long_key = "SAMPLE-2" + "a" * 50
    row = put_ticket(board, long_key, epic="SAMPLE-2")
    board.env["TICKETS_COLS"] = "60"
    assert row[:23] + "…" in visible_columns(board.run("board").stdout)[1]
    board.run("mv", "SAMPLE-2a", "todo")
    assert ("↳ " + row)[:23] + "…" in visible_columns(board.run("board").stdout)[1]


def test_board_readability_e2e(board: TicketRepo) -> None:
    for key in ("SAMPLE-10", "SAMPLE-2", "SAMPLE-8"):
        board.run("new", key, "a long title " * 6)
    board.run("new-epic", "SAMPLE-30", "family")
    for suffix in ("b", "a"):
        board.run("new", f"SAMPLE-30{suffix}", "child", "--epic", "SAMPLE-30")
    for i in range(40, 51):
        board.run("new", f"SAMPLE-{i}", "finished")
        board.run("close", f"SAMPLE-{i}")
    before = board.snapshot()
    plain = board.run("board").stdout
    wish, todo, _, done = visible_columns(plain)
    assert wish == []
    assert [row.removeprefix("↳ ").split()[0] for row in todo] == [
        "SAMPLE-2",
        "SAMPLE-8",
        "SAMPLE-10",
        "SAMPLE-30",
        "SAMPLE-30a",
        "SAMPLE-30b",
    ]
    assert todo[0].endswith("…") and todo[-1].startswith("↳ ")
    assert done[-1] == "… +1 more (--all)" and done[0] == "SAMPLE-50  finished"
    board.env.update(TICKETS_COLS="200", TICKETS_COLOR="always")
    colored = board.run("board").stdout
    assert "\x1b" in colored
    assert all(len(line) == 197 for line in ANSI.sub("", colored).splitlines())
    board.env["TICKETS_COLOR"] = "never"
    assert ANSI.sub("", colored) == board.run("board").stdout
    assert board.snapshot() == before


def test_cross_column_children_render_plain(board: TicketRepo) -> None:
    board.run("new", "SAMPLE-29", "unrelated")
    board.run("new-epic", "SAMPLE-30", "family")
    for suffix in ("a", "b", "c", "d"):
        board.run("new", f"SAMPLE-30{suffix}", "child", "--epic", "SAMPLE-30")
    board.run("mv", "SAMPLE-30a", "in-progress")
    assert board.field("SAMPLE-30", "status") == "in-progress"
    expected_list = (
        f"{'SAMPLE-29':12} {'todo':13} unrelated\n"
        f"{'SAMPLE-30':12} {'in-progress':13} family [epic 0/4]\n"
        f"{'SAMPLE-30a':12} {'in-progress':13} child\n"
        + "".join(f"{'SAMPLE-30' + suffix:12} {'todo':13} child\n" for suffix in ("b", "c", "d"))
    )
    assert board.run("list").stdout == expected_list
    before = board.snapshot()
    assert visible_columns(board.run("board").stdout) == [
        [],
        ["SAMPLE-29  unrelated", "SAMPLE-30b  child", "SAMPLE-30c  child", "SAMPLE-30d  child"],
        ["SAMPLE-30  family [epic 0/…", "↳ SAMPLE-30a  child"],
        [],
    ]
    assert board.run("list").stdout == expected_list
    assert board.snapshot() == before


@pytest.mark.parametrize(
    "status,column", [("wishlist", 0), ("todo", 1), ("in-progress", 2), ("done", 3)]
)
def test_contiguous_children_keep_connector(board: TicketRepo, status: str, column: int) -> None:
    parent = put_ticket(board, "SAMPLE-30", title="family", kind="epic", status=status)
    children = [
        "↳ "
        + put_ticket(
            board,
            f"SAMPLE-30{suffix}",
            epic="SAMPLE-30",
            status=status,
            updated=f"2026-09-{12 - i:02}",
        )
        for i, suffix in enumerate(("a", "b", "c"))
    ]
    parent += f" [epic {3 if status == 'done' else 0}/3]"
    parent = parent[:26] + "…"
    expected = [[], [], [], []]
    expected[column] = [parent, *children]
    plain = board.run("board").stdout
    assert visible_columns(plain) == expected
    board.env["TICKETS_COLOR"] = "always"
    assert ANSI.sub("", board.run("board").stdout) == plain
    if status == "done":
        board.env["TICKETS_DONE_LIMIT"] = "3"
        capped = board.run("board").stdout
        assert visible_columns(capped)[3] == [parent, *children[:2], "… +1 more (--all)"]
        assert "DONE (4)" in capped
        assert ANSI.sub("", board.run("board", "--all").stdout) == plain


@pytest.mark.parametrize("separator", ["standalone", "other-child", "epic"])
@pytest.mark.parametrize("status,column", [("wishlist", 0), ("todo", 1)])
def test_intervening_row_clears_epic_group(
    board: TicketRepo, separator: str, status: str, column: int
) -> None:
    parent = put_ticket(board, "SAMPLE-30", kind="epic", status=status)
    first = put_ticket(board, "SAMPLE-30a", epic="SAMPLE-30", status=status)
    middle = put_ticket(
        board,
        "SAMPLE-30b",
        status=status,
        kind="epic" if separator == "epic" else "task",
        epic="SAMPLE-99" if separator == "other-child" else "",
    )
    if separator == "epic":
        middle += " [epic 0/0]"
    last = put_ticket(board, "SAMPLE-30c", epic="SAMPLE-30", status=status)
    later = put_ticket(board, "SAMPLE-30d", epic="SAMPLE-30", status=status)
    assert visible_columns(board.run("board").stdout)[column] == [
        parent + " [epic 0/3]",
        "↳ " + first,
        middle,
        last,
        later,
    ]


def test_done_child_before_or_without_visible_parent_is_plain(board: TicketRepo) -> None:
    parent = put_ticket(board, "SAMPLE-30", kind="epic", status="done", updated=OLD)
    child = put_ticket(board, "SAMPLE-30a", epic="SAMPLE-30", status="done")
    assert visible_columns(board.run("board").stdout)[3] == [child, parent + " [epic 1/1]"]
    board.env["TICKETS_DONE_LIMIT"] = "1"
    assert visible_columns(board.run("board").stdout)[3] == [child, "… +1 more (--all)"]


@pytest.mark.parametrize("destination", ["todo", "in-progress", "done"])
def test_wishlist_move_list_and_store_bytes(board: TicketRepo, destination: str) -> None:
    board.run("new", "SAMPLE-1", "parked")
    board.run("new", "SAMPLE-2", "queued")
    original = board.path("SAMPLE-1").read_bytes()
    board.age("SAMPLE-1")
    result = board.run("mv", "SAMPLE-1", "wishlist")
    assert (result.stdout, result.stderr) == ("SAMPLE-1 -> wishlist\n", "")
    parked = original.replace(b"status: todo", b"status: wishlist")
    assert board.path("SAMPLE-1").read_bytes() == parked
    assert board.run("show", "SAMPLE-1").stdout.encode() == parked
    row = f"{'SAMPLE-1':12} {'wishlist':13} parked\n"
    before = board.snapshot()
    assert board.run("list", "wishlist").stdout == row
    assert board.run("list").stdout == row + f"{'SAMPLE-2':12} {'todo':13} queued\n"
    assert board.run("board").stdout == board_golden(
        ["SAMPLE-1  parked"], ["SAMPLE-2  queued"], [], []
    )
    assert board.snapshot() == before
    board.age("SAMPLE-1")
    result = board.run("mv", "SAMPLE-1", destination)
    assert (result.stdout, result.stderr) == (f"SAMPLE-1 -> {destination}\n", "")
    assert board.path("SAMPLE-1").read_bytes() == original.replace(
        b"status: todo", f"status: {destination}".encode()
    )
    assert board.run("list", "wishlist").stdout == ""


@pytest.mark.parametrize(
    "flags",
    [(), ("--wishlist", "--epic", "SAMPLE-1"), ("--epic", "SAMPLE-1", "--wishlist")],
)
def test_create_wishlist_and_parent_recompute(board: TicketRepo, flags: tuple[str, ...]) -> None:
    if flags:
        board.run("new-epic", "SAMPLE-1", "e")
        board.age("SAMPLE-1")
    result = board.run("new", "SAMPLE-1a", "parked", *(flags or ("--wishlist",)))
    expected = creation_bytes("SAMPLE-1a", "parked").replace(b"status: todo", b"status: wishlist")
    output = "created SAMPLE-1a (wishlist)\n"
    if flags:
        expected = expected.replace(
            f"updated: {TODAY}\n".encode(), f"updated: {TODAY}\nepic: SAMPLE-1\n".encode()
        )
        output += "SAMPLE-1 -> wishlist (0/1 children done)\n"
        assert board.field("SAMPLE-1", "status") == "wishlist"
        assert board.field("SAMPLE-1", "updated") == TODAY
        assert visible_columns(board.run("board").stdout) == [
            ["SAMPLE-1  e [epic 0/1]", "↳ SAMPLE-1a  parked"],
            [],
            [],
            [],
        ]
    assert (result.stdout, result.stderr) == (output, "")
    assert board.path("SAMPLE-1a").read_bytes() == expected


@pytest.mark.parametrize(
    "command,flags,epic_usage",
    [
        ("new-epic", ("--wishlist",), False),
        ("new-epic", ("--wishlist", "--epic", "SAMPLE-1"), False),
        ("new-epic", ("--epic", "SAMPLE-1", "--wishlist"), True),
        ("new", ("--wishlist", "--wishlist"), False),
        ("new", ("--epic", "SAMPLE-1", "--epic", "SAMPLE-1"), True),
        ("new", ("--wishlist", "--epic", "SAMPLE-1", "--wishlist"), False),
        ("new", ("--wishlist", "--epic"), True),
        ("new", ("--epic", "--wishlist"), True),
        ("new", ("--wishlist", "stray"), False),
        ("new", ("--epic", "SAMPLE-1", "--stray"), False),
        ("new", ("--stray",), False),
        ("new-epic", ("--stray",), False),
    ],
)
def test_create_flags_reject_before_any_write(
    board: TicketRepo, command: str, flags: tuple[str, ...], epic_usage: bool
) -> None:
    board.run("new-epic", "SAMPLE-1", "e")
    board.age("SAMPLE-1")
    before = board.snapshot()
    result = board.run(command, "SAMPLE-2", "parked", *flags, code=1)
    suffix = " --epic PARENT" if epic_usage else ""
    assert (result.stdout, result.stderr) == (
        "",
        f'tickets.sh: usage: tickets.sh {command} SAMPLE-2 "title"{suffix}\n',
    )
    assert board.snapshot() == before
    assert not board.path("SAMPLE-2").exists()


@pytest.mark.parametrize("parent", ["SAMPLE-999", "SAMPLE-1", "SAMPLE-2", "../bad", ""])
@pytest.mark.parametrize("wishlist_first", [False, True])
def test_wishlist_child_validates_parent_before_write(
    board: TicketRepo, parent: str, wishlist_first: bool
) -> None:
    board.run("new", "SAMPLE-1", "task")
    before = board.snapshot()
    flags = ("--wishlist", "--epic", parent) if wishlist_first else ("--epic", parent, "--wishlist")
    result = board.run("new", "SAMPLE-2", "parked", *flags, code=1)
    assert result.stderr and not result.stdout
    assert board.snapshot() == before


@pytest.mark.parametrize(
    "statuses,expected",
    [
        (("wishlist", "wishlist"), "wishlist"),
        (("todo", "todo"), "todo"),
        (("done", "done"), "done"),
        (("in-progress", "in-progress"), "in-progress"),
        (("wishlist", "todo"), "todo"),
        (("wishlist", "done"), "in-progress"),
        (("wishlist", "in-progress"), "in-progress"),
        (("todo", "done"), "in-progress"),
        (("todo", "in-progress"), "in-progress"),
        (("done", "in-progress"), "in-progress"),
        (("wishlist", "todo", "done"), "in-progress"),
        (("wishlist", "todo", "in-progress"), "in-progress"),
        (("wishlist", "done", "in-progress"), "in-progress"),
        (("todo", "done", "in-progress"), "in-progress"),
        (("wishlist", "todo", "done", "in-progress"), "in-progress"),
    ],
)
def test_epic_full_four_state_derivation_and_count_tuple_regression(
    board: TicketRepo, statuses: tuple[str, ...], expected: str
) -> None:
    board.run("new-epic", "SAMPLE-1", "e")
    for i, status in enumerate(statuses):
        key = f"SAMPLE-1{chr(97 + i)}"
        board.run("new", key, "child", "--epic", "SAMPLE-1")
        board.run("mv", key, status)
    assert board.field("SAMPLE-1", "status") == expected
    done = statuses.count("done")
    suffix = f"[epic {done}/{len(statuses)}]"
    assert board.run("epic", "SAMPLE-1").stdout == (
        f"SAMPLE-1 {suffix} ({done}/{len(statuses)} children done)\n"
        + "".join(
            f"{'SAMPLE-1' + chr(97 + i):12} {status:13} child\n"
            for i, status in enumerate(statuses)
        )
    )
    assert board.run("list").stdout == (
        f"{'SAMPLE-1':12} {expected:13} e {suffix}\n"
        + "".join(
            f"{'SAMPLE-1' + chr(97 + i):12} {status:13} child\n"
            for i, status in enumerate(statuses)
        )
    )
    column = ("wishlist", "todo", "in-progress", "done").index(expected)
    assert f"SAMPLE-1  e {suffix}" in visible_columns(board.run("board").stdout)[column]
    board.age("SAMPLE-1")
    before = board.path("SAMPLE-1").read_bytes()
    result = board.run("mv", "SAMPLE-1a", statuses[0])
    assert (result.stdout, result.stderr) == (f"SAMPLE-1a -> {statuses[0]}\n", "")
    assert board.path("SAMPLE-1").read_bytes() == before


def test_wishlist_membership_recomputes_both_and_preserves_empty_epic(board: TicketRepo) -> None:
    for key in ("SAMPLE-1", "SAMPLE-2"):
        board.run("new-epic", key, "e")
    board.run("new", "SAMPLE-1a", "parked", "--wishlist", "--epic", "SAMPLE-1")
    board.run("new", "SAMPLE-1b", "queued", "--epic", "SAMPLE-1")
    result = board.run("set-epic", "SAMPLE-1b", "SAMPLE-2")
    assert (result.stdout, result.stderr) == (
        "SAMPLE-1b -> epic SAMPLE-2\nSAMPLE-1 -> wishlist (0/1 children done)\n",
        "",
    )
    assert board.field("SAMPLE-1", "status") == "wishlist"
    assert board.field("SAMPLE-2", "status") == "todo"
    board.age("SAMPLE-1")
    parked_epic = board.path("SAMPLE-1").read_bytes()
    board.run("close", "SAMPLE-1b")
    assert board.field("SAMPLE-2", "status") == "done"
    assert board.run("set-epic", "SAMPLE-1a", "SAMPLE-2").stdout == (
        "SAMPLE-1a -> epic SAMPLE-2\nSAMPLE-2 -> in-progress (1/2 children done)\n"
    )
    assert board.field("SAMPLE-2", "status") == "in-progress"
    assert board.path("SAMPLE-1").read_bytes() == parked_epic
    assert board.run("epic", "SAMPLE-1").stdout == (
        "SAMPLE-1 [epic 0/0] (0/0 children done)\nNo children.\n"
    )
    board.run("mv", "SAMPLE-1b", "wishlist")
    assert board.field("SAMPLE-2", "status") == "wishlist"
    result = board.run("mv", "SAMPLE-1a", "todo")
    assert result.stdout == "SAMPLE-1a -> todo\nSAMPLE-2 -> todo (0/2 children done)\n"


def test_wishlist_orphans_and_contiguous_group(board: TicketRepo) -> None:
    board.run("new-epic", "SAMPLE-2", "e")
    for key in ("SAMPLE-1", "SAMPLE-2a", "SAMPLE-2b"):
        board.run("new", key, "child", "--wishlist", "--epic", "SAMPLE-2")
    assert visible_columns(board.run("board").stdout) == [
        ["SAMPLE-1  child", "SAMPLE-2  e [epic 0/3]", "↳ SAMPLE-2a  child", "↳ SAMPLE-2b  child"],
        [],
        [],
        [],
    ]
    board.run("mv", "SAMPLE-2a", "todo")
    assert visible_columns(board.run("board").stdout) == [
        ["SAMPLE-1  child", "SAMPLE-2b  child"],
        ["SAMPLE-2  e [epic 0/3]", "↳ SAMPLE-2a  child"],
        [],
        [],
    ]


def test_wishlist_four_column_e2e(board: TicketRepo) -> None:
    board.run("new-epic", "SAMPLE-1", "e")
    board.run("new", "SAMPLE-1a", "parked", "--wishlist", "--epic", "SAMPLE-1")
    board.run("new", "SAMPLE-2", "queued")
    board.run("new", "SAMPLE-3", "active")
    board.run("mv", "SAMPLE-3", "in-progress")
    for key in ("SAMPLE-4", "SAMPLE-5"):
        board.run("new", key, "finished")
        board.run("close", key)
    board.env["TICKETS_DONE_LIMIT"] = "1"
    before = board.snapshot()
    plain = board.run("board").stdout
    assert plain == board_golden(
        ["SAMPLE-1  e [epic 0/1]", "↳ SAMPLE-1a  parked"],
        ["SAMPLE-2  queued"],
        ["SAMPLE-3  active"],
        ["SAMPLE-5  finished", "SAMPLE-4  finished"],
        limit=1,
    )
    assert board.run("list", "wishlist").stdout == (
        f"{'SAMPLE-1':12} {'wishlist':13} e [epic 0/1]\n{'SAMPLE-1a':12} {'wishlist':13} parked\n"
    )
    board.env["TICKETS_COLOR"] = "always"
    colored = board.run("board").stdout
    assert ANSI.sub("", colored) == plain
    assert "\x1b[2m\x1b[36mSAMPLE-1  e [epic 0/1]" in colored
    assert "\x1b[2m↳ \x1b[0m\x1b[2mSAMPLE-1a" in colored
    assert board.snapshot() == before
    result = board.run("mv", "SAMPLE-1a", "todo")
    assert (result.stdout, result.stderr) == (
        "SAMPLE-1a -> todo\nSAMPLE-1 -> todo (0/1 children done)\n",
        "",
    )
    assert board.run("list", "wishlist").stdout == ""
    assert visible_columns(board.run("board").stdout)[0] == []
    assert board.field("SAMPLE-1", "status") == "todo"


class WatchTTY:
    """Controlling-PTY driver with exact frames and slave access while the child runs."""

    def __init__(self, pid: int, master: int, slave: int) -> None:
        self.pid = pid
        self.master = master
        self.slave = slave
        self.pending = bytearray()
        self.output = bytearray()
        self.status: int | None = None

    def send(self, keys: bytes) -> None:
        assert os.write(self.master, keys) == len(keys)

    def poll(self) -> None:
        if self.status is None:
            waited, status = os.waitpid(self.pid, os.WNOHANG)
            if waited == self.pid:
                self.status = status

    def drain(self, timeout: float) -> bool:
        ready, _, _ = select.select([self.master], [], [], timeout)
        if not ready:
            return False
        try:
            chunk = os.read(self.master, 65536)
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise
            chunk = b""
        self.pending.extend(chunk)
        self.output.extend(chunk)
        return bool(chunk)

    def through(self, marker: bytes, timeout: float = 10) -> bytes:
        deadline = time.monotonic() + timeout
        while marker not in self.pending:
            remaining = deadline - time.monotonic()
            assert remaining > 0, (marker, bytes(self.output))
            self.drain(min(0.05, remaining))
            self.poll()
            # Accept a marker delivered by this bounded read, even at the deadline.
            assert self.status is None or marker in self.pending, bytes(self.output)
        end = self.pending.index(marker) + len(marker)
        result = bytes(self.pending[:end])
        del self.pending[:end]
        return result

    def frame(self, timeout: float = 10) -> str:
        data = self.through(b"\x1b[J", timeout)
        frame = data[data.index(b"\x1b[H") :].decode()
        assert frame.count("\x1b[H") == 1
        assert "\x1b[2J" not in frame
        return frame

    def finish(self, timeout: float = 10) -> bytes:
        deadline = time.monotonic() + timeout
        while self.status is None:
            remaining = deadline - time.monotonic()
            assert remaining > 0, bytes(self.output)
            self.drain(min(0.05, remaining))
            self.poll()
        while self.drain(0):
            pass
        assert os.waitstatus_to_exitcode(self.status) == 0, bytes(self.output)
        # macOS revokes the controlling slave at child exit; inspect termios only
        # during the session. Cleanup output remains observable after the exit.
        tail = bytes(self.pending)
        assert tail.count(b"\x1b[?25h") == 1
        assert tail.count(b"\x1b[?1049l") == 1
        assert tail.endswith(b"\x1b[?25h\x1b[?1049l")
        return tail


@contextmanager
def watch_tty(board: TicketRepo, *args: str, rows: int = 24, cols: int = 120) -> Iterator[WatchTTY]:
    """Reuse the board PTY barrier, TIOCSWINSZ, deterministic tools and bounded draining."""
    date = board.bin / "date"
    date.write_text(
        '#!/bin/bash\ncase "$*" in\n'
        '  +%F) echo "$TEST_DATE" ;;\n'
        "  +%H:%M:%S) echo 12:34:56 ;;\n"
        "  *) exit 99 ;;\nesac\n"
    )
    stub_tput(board, "80")  # Controlling size must win over this captured fallback.
    env = {**board.env, "TERM": "xterm-256color", "LESSHISTFILE": "-"}
    env.setdefault("TICKETS_COLOR", "never")
    env.setdefault("TICKETS_INTERVAL", "30")
    ready_read, ready_write = os.pipe()
    name_read, name_write = os.pipe()
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.close(ready_write)
            os.close(name_read)
            os.write(name_write, os.ttyname(0).encode())
            os.close(name_write)
            if os.read(ready_read, 1) != b"1":
                os._exit(99)
            os.close(ready_read)
            os.chdir(board.root)
            os.execve("/bin/bash", ["bash", "./scripts/tickets.sh", *args], env)
        finally:
            os._exit(99)

    os.close(ready_read)
    os.close(name_write)
    slave = os.open(os.read(name_read, 4096).decode(), os.O_RDWR | os.O_NOCTTY)
    os.close(name_read)
    session = None
    try:
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        attrs = termios.tcgetattr(slave)
        attrs[1] &= ~termios.OPOST
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        session = WatchTTY(pid, master, slave)
        os.write(ready_write, b"1")
        yield session
    finally:
        if session is None or session.status is None:
            with suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        os.close(ready_write)
        os.close(master)
        os.close(slave)


def watch_lines(frame: str) -> list[str]:
    """Visible viewport including blank lines and the reserved bottom help line."""
    return [
        ANSI.sub("", line).removesuffix("\x1b[K").rstrip()
        for line in frame.removeprefix("\x1b[H").removesuffix("\x1b[J").split("\r\n")
    ]


def selected_ticket(frame: str) -> str:
    highlighted = re.findall(r"\x1b\[7m(.*?)\x1b\[0m", frame)
    assert len(highlighted) == 1, frame
    return highlighted[0].removeprefix("! ").removeprefix("↳ ").split()[0]


@pytest.mark.parametrize("limit,shown", [(None, 10), ("2", 2), ("0", 12), ("invalid", 10)])
def test_watch_model_order_headers_indent_done_cap(
    board: TicketRepo, limit: str | None, shown: int
) -> None:
    for key in ("10", "2", "08", "8", "8b"):
        put_ticket(board, f"SAMPLE-{key}", status="wishlist")
    put_ticket(board, "SAMPLE-30", title="family", kind="epic")
    put_ticket(board, "SAMPLE-30a", epic="SAMPLE-30")
    put_ticket(board, "SAMPLE-30b", epic="SAMPLE-30")
    put_ticket(board, "SAMPLE-40", status="in-progress")
    for i in range(50, 62):
        put_ticket(board, f"SAMPLE-{i}", status="done", updated=f"2026-08-{80 - i}")
    if limit is not None:
        board.env["TICKETS_DONE_LIMIT"] = limit
    before = board.snapshot()
    expected: list[str] = []
    for status in ("wishlist", "todo", "in-progress"):
        expected.extend(row.split()[0] for row in board.run("list", status).stdout.splitlines())
    expected.extend(f"SAMPLE-{i}" for i in range(50, 50 + shown))
    with watch_tty(board, "watch", rows=40, cols=150) as tty:
        frame = tty.frame()
        lines = watch_lines(frame)
        actual = [
            line.removeprefix("↳ ").split()[0]
            for line in lines
            if line.removeprefix("↳ ").startswith("SAMPLE-")
        ]
        assert actual == expected
        assert [line for line in lines if re.match(r"[A-Z-]+ \(\d+\)$", line)] == [
            "WISHLIST (5)",
            "TODO (3)",
            "IN-PROGRESS (1)",
            "DONE (12)",
        ]
        assert "SAMPLE-30  family [epic 0/2]" in lines
        assert "↳ SAMPLE-30a  task" in lines and "↳ SAMPLE-30b  task" in lines
        assert (f"… +{12 - shown} more" in lines) == (shown < 12)
        assert selected_ticket(frame) == "SAMPLE-2"
        assert len(lines) == 40
        assert lines[-1].startswith("30s | 12:34:56 |")
        for key in ("↑↓/jk move", "Enter/→/l open", "r refresh", "q/Esc/←/h quit"):
            assert key in lines[-1]
        padded = re.search(r"\x1b\[7m(.*?)\x1b\[0m", frame)
        assert padded is not None and len(padded[1]) == 150
        assert not (board.root / "calls").exists()
        tty.send(b"q")
        tty.finish()
    assert board.snapshot() == before


@pytest.mark.parametrize("open_key", [b"\r", b"\n", b"\x1b[C", b"l"])
def test_watch_navigation_detail_and_return(board: TicketRepo, open_key: bytes) -> None:
    for key, status in (("1", "wishlist"), ("2", "todo"), ("3", "in-progress")):
        put_ticket(board, f"SAMPLE-{key}", status=status)
    body = b"\nUnique third ticket body \\literal\nlast line without newline"
    ticket = board.path("SAMPLE-3")
    ticket.write_bytes(ticket.read_bytes() + body)
    expected = board.run("show", "SAMPLE-3").stdout.encode()
    # Capture exactly what the configured pager receives, then exercise real less.
    pager = board.bin / "capture-pager"
    pager.write_text('#!/bin/bash\ncat > "$PAGER_CAPTURE"\nexec less -R "$PAGER_CAPTURE"\n')
    pager.chmod(0o755)
    capture = board.root / "pager-input"
    board.env.update(PAGER="capture-pager", PAGER_CAPTURE=str(capture))
    before = board.snapshot()
    with watch_tty(board, "watch") as tty:
        assert selected_ticket(tty.frame()) == "SAMPLE-1"
        tty.send(b"\x1b[B\x1b[B" + open_key)
        assert selected_ticket(tty.frame()) == "SAMPLE-2"
        assert selected_ticket(tty.frame()) == "SAMPLE-3"
        detail = tty.through(b"(END)")
        assert b"Unique third ticket body \\literal" in detail
        assert b"last line without newline" in detail
        assert capture.read_bytes() == expected
        tty.send(b"q")
        assert selected_ticket(tty.frame()) == "SAMPLE-3"
        attrs = termios.tcgetattr(tty.slave)
        assert not attrs[3] & (termios.ECHO | termios.ICANON)
        # Pager return must restore the same CSI gap wait as initial entry.
        tty.send(b"\x1b[A")
        assert selected_ticket(tty.frame()) == "SAMPLE-2"
        tty.send(b"\x1b[B")
        assert selected_ticket(tty.frame()) == "SAMPLE-3"
        tty.send(b"q")
        tty.finish()
    assert board.snapshot() == before


# G3 R4: stock less cannot quit on lone Esc; detail returns via q/←/h.
@pytest.mark.parametrize("back_key", [b"q", b"h", b"\x1b[D"])
def test_watch_default_less_scroll_and_back(board: TicketRepo, back_key: bytes) -> None:
    put_ticket(board, "SAMPLE-1")
    ticket = board.path("SAMPLE-1")
    ticket.write_text(ticket.read_text() + "\n".join(f"body row {i:03}" for i in range(60)))
    with watch_tty(board, "watch", rows=12) as tty:
        tty.frame()
        tty.send(b"l")
        detail = tty.through(b"body row 000")
        assert b"body row 059" not in detail
        tty.send(b"G")
        assert b"body row 059" in tty.through(b"(END)")
        tty.send(back_key)
        assert selected_ticket(tty.frame()) == "SAMPLE-1"
        tty.send(b"q")
        tty.finish()


def test_watch_navigation_uses_cached_model_until_refresh(board: TicketRepo) -> None:
    put_ticket(board, "SAMPLE-1")
    put_ticket(board, "SAMPLE-2")
    board.env["TICKETS_INTERVAL"] = "30"
    with watch_tty(board, "watch") as tty:
        initial = tty.frame()
        assert selected_ticket(initial) == "SAMPLE-1"
        path = board.path("SAMPLE-2")
        path.write_bytes(path.read_bytes().replace(b"status: todo", b"status: done"))

        tty.send(b"j")
        cached = tty.frame()
        assert selected_ticket(cached) == "SAMPLE-2"
        assert watch_lines(cached) == watch_lines(initial)
        assert "TODO (2)" in watch_lines(cached)
        assert "DONE (0)" in watch_lines(cached)

        tty.send(b"r")
        refreshed = tty.frame(timeout=3)
        assert selected_ticket(refreshed) == "SAMPLE-2"
        lines = watch_lines(refreshed)
        assert "TODO (1)" in lines
        assert lines.index("SAMPLE-2  task") == lines.index("DONE (1)") + 1
        tty.send(b"q")
        tty.finish()


def test_watch_live_reorder_selection_and_immediate_refresh(board: TicketRepo) -> None:
    for key in ("2", "4", "6"):
        put_ticket(board, f"SAMPLE-{key}")
    board.env["TICKETS_INTERVAL"] = "1"
    with watch_tty(board, "watch") as tty:
        tty.frame()
        tty.send(b"j")
        assert selected_ticket(tty.frame()) == "SAMPLE-4"
        path = board.path("SAMPLE-4")
        path.write_bytes(path.read_bytes().replace(b"status: todo", b"status: done"))
        frame = tty.frame(timeout=2.5)
        assert selected_ticket(frame) == "SAMPLE-4"
        lines = watch_lines(frame)
        assert lines.index("SAMPLE-4  task") == lines.index("DONE (1)") + 1
        tty.send(b"q")
        tty.finish()

    # A long interval makes a repaint caused by r distinguishable from the timer.
    board.env["TICKETS_INTERVAL"] = "30"
    with watch_tty(board, "watch") as tty:
        tty.frame()
        tty.send(b"j")
        assert selected_ticket(tty.frame()) == "SAMPLE-6"
        put_ticket(board, "SAMPLE-1")
        tty.send(b"r")
        frame = tty.frame(timeout=3)
        assert selected_ticket(frame) == "SAMPLE-6"
        assert "SAMPLE-1  task" in watch_lines(frame)
        board.path("SAMPLE-6").unlink()
        tty.send(b"r")
        assert selected_ticket(tty.frame(timeout=3)) == "SAMPLE-4"
        board.path("SAMPLE-4").unlink()
        tty.send(b"r")
        assert selected_ticket(tty.frame(timeout=3)) == "SAMPLE-2"
        for path in board.path("SAMPLE-2").parent.glob("*.md"):
            path.unlink()
        tty.send(b"r")
        assert "\x1b[7m" not in tty.frame(timeout=3)
        tty.send(b"\r")  # Empty selection is a no-op, with the viewer still alive.
        assert "\x1b[7m" not in tty.frame()
        put_ticket(board, "SAMPLE-9")
        tty.send(b"r")
        assert selected_ticket(tty.frame(timeout=3)) == "SAMPLE-9"
        tty.send(b"q")
        tty.finish()


def test_watch_scroll_clamp_resize_and_read_only_keys(board: TicketRepo) -> None:
    for i in range(1, 9):
        put_ticket(board, f"SAMPLE-{i}", title="x" * 100)
    before = board.snapshot()
    with watch_tty(board, "watch", rows=6, cols=48) as tty:
        frame = tty.frame()
        assert selected_ticket(frame) == "SAMPLE-1"
        for key in (b"k", b"\x1b[A", b"m", b"c", b"x"):
            tty.send(key)
            assert selected_ticket(tty.frame()) == "SAMPLE-1"
        for i in range(2, 9):
            tty.send(b"j")
            frame = tty.frame()
            assert selected_ticket(frame) == f"SAMPLE-{i}"
            lines = watch_lines(frame)
            assert len(lines) == 6
            assert all(len(line) <= 48 for line in lines)
            assert any(line.startswith(f"SAMPLE-{i} ") and line.endswith("…") for line in lines)
        tty.send(b"\x1b[B")
        assert selected_ticket(tty.frame()) == "SAMPLE-8"
        fcntl.ioctl(tty.master, termios.TIOCSWINSZ, struct.pack("HHHH", 20, 150, 0, 0))
        tty.send(b"r")
        frame = tty.frame()
        assert len(watch_lines(frame)) == 20
        assert selected_ticket(frame) == "SAMPLE-8"
        assert "SAMPLE-8  " + "x" * 100 in watch_lines(frame)
        for i in range(7, 0, -1):
            tty.send(b"k")
            assert selected_ticket(tty.frame()) == f"SAMPLE-{i}"
        tty.send(b"q")
        tty.finish()
    assert board.snapshot() == before


@pytest.mark.parametrize("quit_key", [b"q", b"\x1b", b"h", b"\x1b[D", b"\x03", None])
@pytest.mark.parametrize("args", [("watch",), ()])
def test_watch_quit_restores_terminal(
    board: TicketRepo, quit_key: bytes | None, args: tuple[str, ...]
) -> None:
    put_ticket(board, "SAMPLE-1")
    with watch_tty(board, *args) as tty:
        tty.frame()
        assert tty.output.startswith(b"\x1b[?1049h\x1b[?25l")
        assert not termios.tcgetattr(tty.slave)[3] & (termios.ECHO | termios.ICANON)
        if quit_key is None:
            os.kill(tty.pid, signal.SIGTERM)
        else:
            tty.send(quit_key)
        tty.finish()


@pytest.mark.parametrize("args", [("watch",), (), ("board",)])
@pytest.mark.parametrize("tty_input", [False, True])
def test_watch_headless_matches_static_board(
    board: TicketRepo, args: tuple[str, ...], tty_input: bool
) -> None:
    put_ticket(board, "SAMPLE-1")
    put_ticket(board, "SAMPLE-2", status="done")
    expected = board.run("board").stdout.encode()
    master, slave = pty.openpty()
    try:
        result = subprocess.run(
            ["bash", str(board.script), *args],
            cwd=board.root,
            env=board.env,
            stdin=slave if tty_input else subprocess.DEVNULL,
            capture_output=True,
            start_new_session=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout == expected
        assert b"\x1b[?1049h" not in result.stdout and b"\x1b[?25l" not in result.stdout
        assert result.stderr == (
            b"tickets.sh: watch needs a terminal on stdin and stdout; showing board once\n"
            if args == ("watch",)
            else b""
        )
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("interval,expected", [("0", "2"), ("abc", "2"), ("01", "1")])
def test_watch_interval_validation(board: TicketRepo, interval: str, expected: str) -> None:
    board.env["TICKETS_INTERVAL"] = interval
    with watch_tty(board, "watch") as tty:
        frame = tty.frame()
        assert watch_lines(frame)[-1].startswith(f"{expected}s | 12:34:56 |")
        tty.send(b"q")
        tty.finish()


@pytest.mark.parametrize("args", [("watch",), ()])
def test_watch_piped_stdin_with_tty_stdout_falls_back(
    board: TicketRepo, args: tuple[str, ...]
) -> None:
    put_ticket(board, "SAMPLE-1")
    board.env.update(TICKETS_COLS="120", TICKETS_COLOR="never")
    expected = board.run("board").stdout.encode()
    master, slave = pty.openpty()
    process = None
    try:
        attrs = termios.tcgetattr(slave)
        attrs[1] &= ~termios.OPOST
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        process = subprocess.Popen(
            ["bash", str(board.script), *args],
            cwd=board.root,
            env=board.env,
            stdin=subprocess.DEVNULL,
            stdout=slave,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        output = bytearray()
        deadline = time.monotonic() + 10
        while True:
            assert time.monotonic() < deadline, "headless watch timed out"
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                output.extend(os.read(master, 65536))
            elif process.poll() is not None:
                break
        _, stderr = process.communicate(timeout=1)
        assert process.returncode == 0
        assert output == expected
        assert stderr == (
            b"tickets.sh: watch needs a terminal on stdin and stdout; showing board once\n"
        )
        assert termios.tcgetattr(slave) == attrs
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize(
    "color,no_color,expected",
    [("always", "1", True), ("never", None, False), ("auto", "1", False), ("auto", None, True)],
)
def test_watch_color_and_highlight_padding(
    board: TicketRepo, color: str, no_color: str | None, expected: bool
) -> None:
    put_ticket(board, "SAMPLE-1", title="family", kind="epic", status="in-progress")
    put_ticket(board, "SAMPLE-1a", epic="SAMPLE-1", status="in-progress")
    board.env["TICKETS_COLOR"] = color
    if no_color is not None:
        board.env["NO_COLOR"] = no_color
    with watch_tty(board, "watch", cols=100) as tty:
        frame = tty.frame()
        assert selected_ticket(frame) == "SAMPLE-1"
        assert ("\x1b[1mIN-PROGRESS (2)" in frame) == expected
        tty.send(b"j")
        frame = tty.frame()
        assert selected_ticket(frame) == "SAMPLE-1a"
        assert "\x1b[7m" + "↳ SAMPLE-1a  task".ljust(100) + "\x1b[0m" in frame
        assert ("\x1b[36mSAMPLE-1" in frame) == expected
        tty.send(b"q")
        tty.finish()


def test_watch_failed_pager_returns_to_selection(board: TicketRepo) -> None:
    put_ticket(board, "SAMPLE-1")
    board.env["PAGER"] = "false"
    with watch_tty(board, "watch") as tty:
        tty.frame()
        tty.send(b"l")
        assert selected_ticket(tty.frame()) == "SAMPLE-1"
        assert not termios.tcgetattr(tty.slave)[3] & (termios.ECHO | termios.ICANON)
        tty.send(b"q")
        tty.finish()


def put_blockers(board: TicketRepo, key: str, value: str) -> None:
    """Hand-edit only fixture frontmatter, including invalid/stale dependency lists."""
    path = board.path(key)
    before = path.read_bytes()
    line = f"blocked_by: {value}\n".encode()
    if b"\nblocked_by:" in before:
        after = re.sub(rb"(?m)^blocked_by:.*\n", lambda _: line, before, count=1)
    else:
        head, body = before.split(b"\n---\n", 1)
        after = head + b"\n" + line + b"---\n" + body
    path.write_bytes(after)


def ready_line(key: str, title: str = "task") -> str:
    return f"{key:<12} {'todo':<13} {title}\n"


@pytest.mark.parametrize(
    "command,options,extra,status",
    [
        ("new", [], b"", "todo"),
        ("new-epic", [], b"type: epic\n", "todo"),
        ("new", ["--wishlist"], b"", "wishlist"),
        ("new", ["--epic", "SAMPLE-9"], b"epic: SAMPLE-9\n", "todo"),
    ],
)
def test_ac1_creation_bytes(
    board: TicketRepo, command: str, options: list[str], extra: bytes, status: str
) -> None:
    board.run("new-epic", "SAMPLE-9", "parent")
    board.run(command, "SAMPLE-1", "task", *options)
    expected = creation_bytes("SAMPLE-1", "task").replace(
        b"status: todo", f"status: {status}".encode()
    )
    expected = expected.removesuffix(b"---\n") + extra + b"---\n"
    assert board.path("SAMPLE-1").read_bytes() == expected


def test_ac1_ac10_field_absent_legacy_reads_and_setters(board: TicketRepo) -> None:
    board.run("new-epic", "SAMPLE-9", "parent")
    legacy = creation_bytes("SAMPLE-1", "legacy").replace(b"blocked_by: []\n", b"")
    legacy += b"\n## Body\ncustom: untouched\n"
    board.path("SAMPLE-1").write_bytes(legacy)
    before = board.snapshot()
    for args in (
        ("list",),
        ("board",),
        ("ready",),
        ("show", "SAMPLE-1"),
        ("epic", "SAMPLE-9"),
        ("get-worktree", "SAMPLE-1"),
    ):
        assert board.run(*args).stderr == ""
        assert board.snapshot() == before
    assert board.run("ready").stdout == (
        "READY (1)\n" + ready_line("SAMPLE-1", "legacy") + "BLOCKED (0)\n"
    )
    board.age("SAMPLE-1")
    board.run("set-worktree", "SAMPLE-1", "SAMPLE-1")
    expected = legacy.replace(b"worktree:", b"worktree: SAMPLE-1")
    assert board.path("SAMPLE-1").read_bytes() == expected
    board.age("SAMPLE-1")
    board.run("set-epic", "SAMPLE-1", "SAMPLE-9")
    expected = expected.replace(
        f"updated: {TODAY}\n".encode(), f"updated: {TODAY}\nepic: SAMPLE-9\n".encode()
    )
    assert board.path("SAMPLE-1").read_bytes() == expected
    board.age("SAMPLE-1")
    result = board.run("mv", "SAMPLE-1", "in-progress")
    assert result.stdout == "SAMPLE-1 -> in-progress\nSAMPLE-9 -> in-progress (0/1 children done)\n"
    assert board.path("SAMPLE-1").read_bytes() == expected.replace(
        b"status: todo", b"status: in-progress"
    )
    assert board.field("SAMPLE-9", "status") == "in-progress"


@pytest.mark.parametrize("final_newline", [False, True])
def test_ac1_ac2_legacy_block_preserves_body(board: TicketRepo, final_newline: bool) -> None:
    board.run("new", "SAMPLE-1", "task")
    legacy = creation_bytes("SAMPLE-2", "legacy").replace(b"blocked_by: []\n", b"")
    legacy = legacy.replace(TODAY.encode(), OLD.encode())
    body = b"\n## Depends on\nSAMPLE-1\n---\ncustom: \\n bytes" + (b"\n" if final_newline else b"")
    board.path("SAMPLE-2").write_bytes(legacy + body)
    result = board.run("block", "SAMPLE-2", "SAMPLE-1")
    assert (result.stdout, result.stderr) == ("SAMPLE-2 blocked_by [SAMPLE-1]\n", "")
    expected = legacy.replace(OLD.encode(), TODAY.encode()).removesuffix(b"---\n")
    assert board.path("SAMPLE-2").read_bytes() == expected + b"blocked_by: [SAMPLE-1]\n---\n" + body
    board.run("unblock", "SAMPLE-2", "SAMPLE-1")
    assert board.path("SAMPLE-2").read_bytes() == expected + b"blocked_by: []\n---\n" + body


@pytest.mark.parametrize(
    "value",
    ["[SAMPLE-30, SAMPLE-30a]", "[SAMPLE-30,SAMPLE-30a]", " [ SAMPLE-30 , SAMPLE-30a ] "],
)
def test_ac1_whitespace_and_whole_key_membership(board: TicketRepo, value: str) -> None:
    put_ticket(board, "SAMPLE-1")
    put_ticket(board, "SAMPLE-30", status="done")
    put_ticket(board, "SAMPLE-30a")
    put_blockers(board, "SAMPLE-1", value)
    assert board.run("ready").stdout == (
        "READY (1)\n" + ready_line("SAMPLE-30a") + "BLOCKED (1)\n"
        "SAMPLE-1  task  ← waiting on SAMPLE-30a (todo)\n"
    )
    board.run("unblock", "SAMPLE-1", "SAMPLE-30")
    assert board.field("SAMPLE-1", "blocked_by") == "[SAMPLE-30a]"
    board.run("close", "SAMPLE-1", code=1)


@pytest.mark.parametrize(
    "value", ["SAMPLE-1", "[foo]", "[]]", "[SAMPLE-1,]", "[SAMPLE-1, foo]", "", "[ , ]"]
)
def test_ac1a_malformed_fail_closed_all_readers_and_mutations(
    board: TicketRepo, value: str
) -> None:
    put_ticket(board, "SAMPLE-1", status="done")
    put_ticket(board, "SAMPLE-2")
    put_blockers(board, "SAMPLE-2", value)
    before = board.snapshot()
    warning = "tickets.sh: warn: SAMPLE-2 has malformed blocked_by\n"
    for command in ("list", "board", "ready"):
        result = board.run(command)
        assert result.stderr == warning
        if command == "list":
            assert ready_line("SAMPLE-2").rstrip() + " [blocked: malformed]\n" in result.stdout
        elif command == "board":
            assert visible_columns(result.stdout)[1] == ["! SAMPLE-2  task"]
        else:
            assert result.stdout == (
                "READY (0)\nBLOCKED (1)\nSAMPLE-2  task  ← waiting on (malformed blocked_by)\n"
            )
        assert board.snapshot() == before
    for args in (
        ("close", "SAMPLE-2"),
        ("mv", "SAMPLE-2", "done"),
        ("block", "SAMPLE-2", "SAMPLE-1"),
        ("unblock", "SAMPLE-2", "SAMPLE-1"),
    ):
        result = board.run(*args, code=1)
        assert result.stderr.count(warning) == 1
        if args[0] in ("close", "mv"):
            assert result.stderr == (
                warning + "tickets.sh: SAMPLE-2 has unresolved blocker(s): (malformed blocked_by)\n"
            )
        else:
            assert result.stderr == (
                warning + f"tickets.sh: SAMPLE-2 has malformed blocked_by; refusing {args[0]}\n"
            )
        assert board.snapshot() == before
    result = board.run("close", "SAMPLE-2", "--force")
    assert result.stderr == (
        warning
        + "tickets.sh: warn: SAMPLE-2 closed over unresolved blocker(s): (malformed blocked_by)\n"
    )
    assert board.field("SAMPLE-2", "status") == "done"
    assert board.field("SAMPLE-2", "blocked_by") == value.strip()


def test_ac2_append_order_idempotence_timestamp(board: TicketRepo) -> None:
    for key in ("SAMPLE-1", "SAMPLE-2", "SAMPLE-3"):
        board.run("new", key, "task")
    board.age("SAMPLE-1")
    result = board.run("block", "SAMPLE-1", "SAMPLE-3", "SAMPLE-2", "SAMPLE-3")
    assert result.stdout == "SAMPLE-1 blocked_by [SAMPLE-3, SAMPLE-2]\n"
    expected = creation_bytes("SAMPLE-1", "task").replace(b"[]", b"[SAMPLE-3, SAMPLE-2]")
    assert board.path("SAMPLE-1").read_bytes() == expected
    board.age("SAMPLE-1")
    assert board.run("block", "SAMPLE-1", "SAMPLE-2").stdout == result.stdout
    assert board.path("SAMPLE-1").read_bytes() == expected


@pytest.mark.parametrize(
    "case",
    [
        "bad",
        "missing",
        "self",
        "epic-blocker",
        "epic-target",
        "malformed-target",
        "2-cycle",
        "3-cycle",
        "malformed-intermediate",
    ],
)
def test_ac2_rejections_are_atomic(board: TicketRepo, case: str) -> None:
    for key in ("SAMPLE-1", "SAMPLE-2", "SAMPLE-3", "SAMPLE-4"):
        put_ticket(board, key)
    bad, reason = {
        "bad": ("foo", "bad key 'foo'"),
        "missing": ("SAMPLE-99", "no such ticket: SAMPLE-99"),
        "self": ("SAMPLE-1", "SAMPLE-1 cannot block itself"),
        "epic-blocker": ("SAMPLE-2", "epic cannot be a blocker: SAMPLE-2"),
        "epic-target": ("SAMPLE-2", "epic cannot have blockers: SAMPLE-1"),
        "malformed-target": ("SAMPLE-2", "SAMPLE-1 has malformed blocked_by"),
        "2-cycle": ("SAMPLE-2", "SAMPLE-2 creates a dependency cycle"),
        "3-cycle": ("SAMPLE-2", "SAMPLE-2 creates a dependency cycle"),
        "malformed-intermediate": ("SAMPLE-2", "SAMPLE-3 has malformed blocked_by"),
    }[case]
    if case == "epic-target":
        put_ticket(board, "SAMPLE-1", kind="epic")
    if case == "epic-blocker":
        put_ticket(board, "SAMPLE-2", kind="epic")
    if case == "malformed-target":
        put_blockers(board, "SAMPLE-1", "[foo]")
    if case == "2-cycle":
        put_blockers(board, "SAMPLE-2", "[SAMPLE-1]")
    if case in ("3-cycle", "malformed-intermediate"):
        put_blockers(board, "SAMPLE-2", "[SAMPLE-3]")
        put_blockers(board, "SAMPLE-3", "[SAMPLE-1]" if case == "3-cycle" else "[foo]")
    before = board.snapshot()
    # The first addition is valid: failure of the final argument must not persist it.
    result = board.run("block", "SAMPLE-1", "SAMPLE-4", bad, code=1)
    assert reason in result.stderr
    assert board.snapshot() == before


@pytest.mark.parametrize("ending", ["target", "empty", "old-cycle"])
def test_ac2_exhaustive_long_chain(board: TicketRepo, ending: str) -> None:
    put_ticket(board, "SAMPLE-1")
    for n in range(100, 230):
        put_ticket(board, f"SAMPLE-{n}")
        next_key = (
            f"SAMPLE-{n + 1}"
            if n < 229
            else {"target": "SAMPLE-1", "empty": "", "old-cycle": "SAMPLE-150"}[ending]
        )
        put_blockers(board, f"SAMPLE-{n}", f"[{next_key}]")
    before = board.snapshot()
    result = board.run("block", "SAMPLE-1", "SAMPLE-100", code=1 if ending == "target" else 0)
    if ending == "target":
        assert "SAMPLE-100 creates a dependency cycle reaching SAMPLE-1" in result.stderr
        assert board.snapshot() == before
    else:
        assert result.stdout == "SAMPLE-1 blocked_by [SAMPLE-100]\n"
        assert board.field("SAMPLE-1", "blocked_by") == "[SAMPLE-100]"


@pytest.mark.parametrize(
    "stored,args,remaining",
    [
        ("[SAMPLE-2, SAMPLE-3]", ["SAMPLE-2"], "[SAMPLE-3]"),
        ("[SAMPLE-99]", ["SAMPLE-99"], "[]"),
        ("[SAMPLE-2, SAMPLE-2]", ["SAMPLE-2"], "[]"),
        ("[SAMPLE-2]", ["SAMPLE-2", "SAMPLE-2"], "[]"),
        ("[SAMPLE-2]\nblocked_by: [SAMPLE-3]", ["SAMPLE-2"], "[]"),
    ],
)
def test_ac3_unblock_normalizes_and_removes_stale_entries(
    board: TicketRepo, stored: str, args: list[str], remaining: str
) -> None:
    for key in ("SAMPLE-1", "SAMPLE-2", "SAMPLE-3"):
        board.run("new", key, "task")
    put_blockers(board, "SAMPLE-1", stored)
    board.age("SAMPLE-1")
    result = board.run("unblock", "SAMPLE-1", *args)
    assert result.stdout == f"SAMPLE-1 blocked_by {remaining}\n"
    assert board.path("SAMPLE-1").read_bytes() == creation_bytes("SAMPLE-1", "task").replace(
        b"[]", remaining.encode()
    )
    assert ("SAMPLE-99" in result.stderr) == ("99" in stored)


@pytest.mark.parametrize(
    "command,args",
    [
        ("unblock", []),
        ("block", []),
        ("unblock", ["SAMPLE-3"]),
        ("unblock", ["SAMPLE-2", "SAMPLE-3"]),
        ("unblock", ["foo"]),
    ],
)
def test_ac2_ac3_invalid_requests_leave_bytes_unchanged(
    board: TicketRepo, command: str, args: list[str]
) -> None:
    put_ticket(board, "SAMPLE-1")
    put_ticket(board, "SAMPLE-2")
    put_blockers(board, "SAMPLE-1", "[SAMPLE-2]")
    before = board.snapshot()
    result = board.run(command, "SAMPLE-1", *args, code=1)
    expected = "usage:" if not args else args[-1]
    assert expected in result.stderr
    assert board.snapshot() == before


def test_ac4_ac5_ready_partitions_reasons_and_order(board: TicketRepo) -> None:
    assert board.run("ready").stdout == "READY (0)\nBLOCKED (0)\n"
    for key in ("SAMPLE-10", "SAMPLE-2a", "SAMPLE-2", "SAMPLE-08", "SAMPLE-8"):
        put_ticket(board, key)
    put_ticket(board, "SAMPLE-40", status="wishlist")
    put_ticket(board, "SAMPLE-41", status="in-progress")
    put_ticket(board, "SAMPLE-42", status="done")
    put_ticket(board, "SAMPLE-43", kind="epic", status="done")
    put_ticket(board, "SAMPLE-44", kind="epic")
    put_blockers(board, "SAMPLE-10", "[SAMPLE-42, SAMPLE-40, SAMPLE-99, SAMPLE-41]")
    put_blockers(board, "SAMPLE-2a", "[SAMPLE-2, SAMPLE-2]")
    put_blockers(board, "SAMPLE-8", "[SAMPLE-43]")  # Hand-edited epic resolves by status.
    before = board.snapshot()
    result = board.run("ready")
    assert result.stdout == (
        "READY (3)\n"
        + ready_line("SAMPLE-2")
        + ready_line("SAMPLE-08")
        + ready_line("SAMPLE-8")
        + "BLOCKED (2)\nSAMPLE-2a  task  ← waiting on SAMPLE-2 (todo)\n"
        "SAMPLE-10  task  ← waiting on SAMPLE-40 (wishlist), "
        "SAMPLE-99 (missing), SAMPLE-41 (in-progress)\n"
    )
    assert result.stderr == "tickets.sh: warn: SAMPLE-10 has missing blocker SAMPLE-99\n"
    assert board.snapshot() == before
    put_ticket(board, "SAMPLE-43", kind="epic", status="todo")
    assert "SAMPLE-8  task  ← waiting on SAMPLE-43 (todo)\n" in board.run("ready").stdout
    board.path("SAMPLE-43").write_text(
        board.path("SAMPLE-43").read_text().replace("status: todo\n", "")
    )
    assert "SAMPLE-8  task  ← waiting on SAMPLE-43 (unknown)\n" in board.run("ready").stdout


@pytest.mark.parametrize("value", ["[foo]", "[SAMPLE-99]"])
def test_ac4_non_dependency_commands_never_resolve_or_warn(board: TicketRepo, value: str) -> None:
    board.run("new", "SAMPLE-1", "task")
    board.run("new-epic", "SAMPLE-9", "parent")
    put_blockers(board, "SAMPLE-1", value)
    raw = board.path("SAMPLE-1").read_bytes()
    assert board.run("show", "SAMPLE-1").stdout.encode() == raw
    assert board.run("show", "SAMPLE-1").stderr == ""
    for args in (
        ("get-worktree", "SAMPLE-1"),
        ("set-worktree", "SAMPLE-1", "SAMPLE-1"),
        ("set-epic", "SAMPLE-1", "SAMPLE-9"),
        ("epic", "SAMPLE-9"),
        ("new", "SAMPLE-2", "task"),
        ("new-epic", "SAMPLE-3", "parent"),
        ("mv", "SAMPLE-1", "in-progress"),
    ):
        assert board.run(*args).stderr == ""
    assert board.field("SAMPLE-1", "blocked_by") == value
    assert board.field("SAMPLE-9", "status") == "in-progress"


@pytest.mark.parametrize("value", ["[SAMPLE-99]", "[foo]"])
def test_ac6_ac7_done_stale_blockers_render_silently_but_close_refuses(
    board: TicketRepo, value: str
) -> None:
    put_ticket(board, "SAMPLE-1", status="done")
    expected = {command: board.run(command).stdout for command in ("board", "list")}
    put_blockers(board, "SAMPLE-1", value)
    before = board.snapshot()
    for command in ("board", "list"):
        result = board.run(command)
        assert (result.stdout, result.stderr) == (expected[command], "")
    assert board.run("ready").stdout == "READY (0)\nBLOCKED (0)\n"
    warning, reason = (
        ("has missing blocker SAMPLE-99", "SAMPLE-99 (missing)")
        if value == "[SAMPLE-99]"
        else ("has malformed blocked_by", "(malformed blocked_by)")
    )
    for args in (("close", "SAMPLE-1"), ("mv", "SAMPLE-1", "done")):
        result = board.run(*args, code=1)
        assert result.stderr == (
            f"tickets.sh: warn: SAMPLE-1 {warning}\n"
            f"tickets.sh: SAMPLE-1 has unresolved blocker(s): {reason}\n"
        )
    assert board.snapshot() == before


def test_ac4_ac7_missing_and_duplicate_blockers_in_board_and_list(board: TicketRepo) -> None:
    put_ticket(board, "SAMPLE-1")
    put_ticket(board, "SAMPLE-2")
    put_blockers(board, "SAMPLE-1", "[SAMPLE-99, SAMPLE-2, SAMPLE-99]")
    before = board.snapshot()
    warning = "tickets.sh: warn: SAMPLE-1 has missing blocker SAMPLE-99\n"
    result = board.run("list")
    assert result.stderr == warning
    assert result.stdout == (
        ready_line("SAMPLE-1").rstrip()
        + " [blocked: SAMPLE-99, SAMPLE-2]\n"
        + ready_line("SAMPLE-2")
    )
    result = board.run("board")
    assert result.stderr == warning
    assert visible_columns(result.stdout)[1] == ["! SAMPLE-1  task", "SAMPLE-2  task"]
    assert board.snapshot() == before


@pytest.mark.parametrize("command", ["close", "mv"])
@pytest.mark.parametrize("status", ["todo", "done"])
def test_ac6_close_refused_then_forced_with_all_reasons(
    board: TicketRepo, command: str, status: str
) -> None:
    put_ticket(board, "SAMPLE-1", status=status)
    put_ticket(board, "SAMPLE-2", status="wishlist")
    put_blockers(board, "SAMPLE-1", "[SAMPLE-2, SAMPLE-99]")
    args = [command, "SAMPLE-1"] + (["done"] if command == "mv" else [])
    before = board.snapshot()
    reason = "SAMPLE-2 (wishlist), SAMPLE-99 (missing)"
    result = board.run(*args, code=1)
    assert f"SAMPLE-1 has unresolved blocker(s): {reason}\n" in result.stderr
    assert board.snapshot() == before
    result = board.run(*args, "--force")
    warning = f"tickets.sh: warn: SAMPLE-1 closed over unresolved blocker(s): {reason}\n"
    assert warning in result.stderr
    assert board.field("SAMPLE-1", "status") == "done"
    assert board.field("SAMPLE-1", "blocked_by") == "[SAMPLE-2, SAMPLE-99]"


@pytest.mark.parametrize(
    "args",
    [
        ("close", "--force", "SAMPLE-1"),
        ("mv", "--force", "SAMPLE-1", "done"),
        ("mv", "SAMPLE-1", "--force", "done"),
        ("mv", "SAMPLE-1", "done", "--force", "--force"),
        ("close", "SAMPLE-1", "--force", "--force"),
        ("close", "SAMPLE-1", "junk"),
        ("mv", "SAMPLE-1", "done", "junk"),
    ],
)
def test_ac6_misplaced_or_extra_flags_are_usage_errors(
    board: TicketRepo, args: tuple[str, ...]
) -> None:
    put_ticket(board, "SAMPLE-1")
    before = board.snapshot()
    assert "usage:" in board.run(*args, code=1).stderr
    assert board.snapshot() == before


def test_ac6_epic_refusal_precedes_gate_and_force_recomputes(board: TicketRepo) -> None:
    board.run("new-epic", "SAMPLE-1", "parent")
    board.run("new", "SAMPLE-1a", "child", "--epic", "SAMPLE-1")
    board.run("new", "SAMPLE-2", "blocker")
    board.run("block", "SAMPLE-1a", "SAMPLE-2")
    put_blockers(board, "SAMPLE-1", "[foo]")
    before = board.snapshot()
    for args in (("close", "SAMPLE-1"), ("mv", "SAMPLE-1", "done", "--force")):
        result = board.run(*args, code=1)
        assert result.stderr == (
            "tickets.sh: epic status is derived from its children; "
            "move/close the children of SAMPLE-1 instead\n"
        )
        assert board.snapshot() == before
    assert board.run("mv", "SAMPLE-1a", "in-progress").stderr == ""
    assert board.field("SAMPLE-1", "status") == "in-progress"
    result = board.run("close", "SAMPLE-1a", "--force")
    assert result.stdout == "SAMPLE-1a -> done\nSAMPLE-1 -> done (1/1 children done)\n"
    assert board.field("SAMPLE-1", "status") == "done"
    assert board.field("SAMPLE-1", "blocked_by") == "[foo]"


@pytest.mark.parametrize("color", ["never", "always"])
@pytest.mark.parametrize("status", ["wishlist", "todo", "in-progress"])
@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_ac7_board_marker_child_width_and_ansi_layers(
    board: TicketRepo, color: str, status: str, locale: str
) -> None:
    put_ticket(board, "SAMPLE-1", title="parent", kind="epic", status=status)
    put_ticket(board, "SAMPLE-1a", title="child", epic="SAMPLE-1", status=status)
    put_ticket(board, "SAMPLE-2", title="standalone", status=status)
    put_ticket(board, "SAMPLE-3", title="resolved row", status="done")
    put_ticket(board, "SAMPLE-9", title="blocker", status="wishlist")
    for key in ("SAMPLE-1", "SAMPLE-1a", "SAMPLE-2", "SAMPLE-3"):
        put_blockers(board, key, "[SAMPLE-9]")
    board.env.update(TICKETS_COLOR=color, TICKETS_COLS="120", LC_ALL=locale)
    before = board.snapshot()
    columns: list[list[str]] = [["SAMPLE-9  blocker"], [], [], ["SAMPLE-3  resolved row"]]
    columns[("wishlist", "todo", "in-progress").index(status)].extend(
        ["! SAMPLE-1  parent [epic 0/1]", "! ↳ SAMPLE-1a  child", "! SAMPLE-2  standalone"]
    )
    result = board.run("board")
    assert result.stderr == ""
    assert ANSI.sub("", result.stdout) == board_golden(*columns)
    assert all(len(line) == 117 for line in ANSI.sub("", result.stdout).splitlines())
    assert result.stdout.count("↳") == 1
    if color == "always":
        style = "\x1b[1m" if status == "in-progress" else "\x1b[2m" if status == "wishlist" else ""
        child = "! ↳ SAMPLE-1a  child".ljust(27)
        assert (
            style + "! \x1b[2m↳ \x1b[0m" + style + child.removeprefix("! ↳ ") + "\x1b[0m"
            in result.stdout
        )
        # The 29-column parent is truncated to W=27 before ANSI wraps the full cell.
        epic_cell = "! SAMPLE-1  parent [epic 0…"
        assert style + "\x1b[36m" + epic_cell + "\x1b[0m" in result.stdout
    else:
        assert "\x1b" not in result.stdout
    listing = board.run("list").stdout
    assert f"{'SAMPLE-1':<12} {status:<13} parent [epic 0/1] [blocked: SAMPLE-9]\n" in listing
    assert f"{'SAMPLE-3':<12} {'done':<13} resolved row\n" in listing
    assert board.snapshot() == before


def test_ac7_watch_refresh_resolves_marker_preserves_selection_and_width(
    board: TicketRepo,
) -> None:
    put_ticket(board, "SAMPLE-1", title="parent", kind="epic")
    put_ticket(board, "SAMPLE-1a", title="child", epic="SAMPLE-1")
    put_ticket(board, "SAMPLE-9", title="blocker")
    put_blockers(board, "SAMPLE-1a", "[SAMPLE-9]")
    board.env["TICKETS_INTERVAL"] = "1"
    with watch_tty(board, "watch", cols=120) as tty:
        tty.frame()
        tty.send(b"j")
        frame = tty.frame()
        assert selected_ticket(frame) == "SAMPLE-1a"
        assert "\x1b[7m" + "! ↳ SAMPLE-1a  child".ljust(120) + "\x1b[0m" in frame
        board.run("close", "SAMPLE-9")
        # A pre-close frame can already be queued; wait boundedly for the next model.
        deadline = time.monotonic() + 8
        while True:
            frame = tty.frame(timeout=3)
            if "! ↳ SAMPLE-1a  child" not in watch_lines(frame):
                break
            assert time.monotonic() < deadline, frame
        assert selected_ticket(frame) == "SAMPLE-1a"
        assert "\x1b[7m" + "↳ SAMPLE-1a  child".ljust(120) + "\x1b[0m" in frame
        assert "DONE (1)" in watch_lines(frame)
        tty.send(b"q")
        tty.finish()
    assert board.field("SAMPLE-1a", "blocked_by") == "[SAMPLE-9]"


def test_ac8_full_help_golden_and_unknown_exit(board: TicketRepo) -> None:
    for command in ("help", "-h", "--help", "unknown"):
        result = board.run(command, code=1 if command == "unknown" else 0)
        assert result.stdout == LEGACY_HELP + EPIC_HELP
        assert result.stderr == (
            "tickets.sh: unknown command 'unknown'\n" if command == "unknown" else ""
        )


def test_ac2_ac5_ac6_dependency_lifecycle_e2e(board: TicketRepo) -> None:
    board.run("new", "SAMPLE-1", "a")
    board.run("new", "SAMPLE-2", "b")
    board.run("block", "SAMPLE-2", "SAMPLE-1")
    assert board.run("ready").stdout == (
        "READY (1)\n" + ready_line("SAMPLE-1", "a") + "BLOCKED (1)\n"
        "SAMPLE-2  b  ← waiting on SAMPLE-1 (todo)\n"
    )
    before = board.snapshot()
    board.run("close", "SAMPLE-2", code=1)
    assert board.snapshot() == before
    board.run("close", "SAMPLE-1")
    assert board.run("ready").stdout == (
        "READY (1)\n" + ready_line("SAMPLE-2", "b") + "BLOCKED (0)\n"
    )
    board.run("close", "SAMPLE-2")
    assert board.run("ready").stdout == "READY (0)\nBLOCKED (0)\n"


def test_ac10_field_absent_board_list_goldens(board: TicketRepo) -> None:
    put_ticket(board, "SAMPLE-2", status="wishlist")
    put_ticket(board, "SAMPLE-3", kind="epic")
    put_ticket(board, "SAMPLE-3a", epic="SAMPLE-3")
    put_ticket(board, "SAMPLE-4", status="in-progress")
    put_ticket(board, "SAMPLE-5", status="done")
    before = board.snapshot()
    assert board.run("board").stdout == board_golden(
        ["SAMPLE-2  task"],
        ["SAMPLE-3  task [epic 0/1]", "↳ SAMPLE-3a  task"],
        ["SAMPLE-4  task"],
        ["SAMPLE-5  task"],
    )
    assert board.run("list").stdout == "".join(
        f"{key:<12} {status:<13} task{suffix}\n"
        for key, status, suffix in (
            ("SAMPLE-2", "wishlist", ""),
            ("SAMPLE-3", "todo", " [epic 0/1]"),
            ("SAMPLE-3a", "todo", ""),
            ("SAMPLE-4", "in-progress", ""),
            ("SAMPLE-5", "done", ""),
        )
    )
    assert board.snapshot() == before
