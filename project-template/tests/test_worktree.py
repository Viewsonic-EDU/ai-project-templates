"""Real worktree/board CLI effects in disposable repositories with local bare origins."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TICKET = "SAMPLE-900"
TODAY = "2026-09-14"


class WorktreeRepo:
    """Isolate Git configuration, board storage, worktrees, remote, and date."""

    def __init__(self, root: Path) -> None:
        self.root = root / "main checkout"
        self.root.mkdir(parents=True)
        self.origin = root / "origin.git"
        self.wt_root = root / "worktrees"
        self.wt = self.wt_root / TICKET
        self.bin = root / "bin"
        self.bin.mkdir()
        date = self.bin / "date"
        date.write_text('#!/bin/bash\n[[ "$*" == +%F ]] || exit 99\necho "$TEST_DATE"\n')
        date.chmod(0o755)
        self.env = {
            **{
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("GIT_", "TICKETS_"))
                and k not in {"WT_ROOT", "MAIN_BRANCH", "NO_COLOR", "COLUMNS"}
            },
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "LC_ALL": "C",
            "TEST_DATE": TODAY,
            "TMPDIR": str(root),
            "WT_ROOT": str(self.wt_root),
            "MAIN_BRANCH": "main",
            "TICKET_PREFIX": "SAMPLE",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }
        self.git("init", "-q", "-b", "main")
        self.git("init", "-q", "--bare", "-b", "main", str(self.origin))
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in ("worktree.sh", "tickets.sh"):
            shutil.copy(SCRIPTS / name, scripts / name)
        (self.root / "notes.txt").write_text("original\n")
        # Seed valid tickets explicitly; worktree.sh new's best-effort seed may fail.
        for key, title in (
            (TICKET, "Land feature and synchronize the board"),
            ("SAMPLE-901", "Preserve unrelated board triage"),
            ("SAMPLE-902", "Keep the second ticket unchanged"),
        ):
            self.tickets("new", key, title)
        self.git("add", ".")
        self.git("commit", "-qm", "fixture baseline")
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-u", "origin", "main")

    def run(
        self, *args: str, cwd: Path | None = None, code: int = 0
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            args,
            cwd=cwd or self.root,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=20,
        )
        assert result.returncode == code, (args, result.returncode, result.stdout, result.stderr)
        return result

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return self.run("git", *args, cwd=cwd).stdout.strip()

    def worktree(
        self, *args: str, cwd: Path | None = None, code: int = 0
    ) -> subprocess.CompletedProcess[str]:
        return self.run(
            "bash", str((cwd or self.root) / "scripts/worktree.sh"), *args, cwd=cwd, code=code
        )

    def tickets(self, *args: str, cwd: Path | None = None) -> None:
        self.run("bash", str((cwd or self.root) / "scripts/tickets.sh"), *args, cwd=cwd)

    def feature(self) -> str:
        self.worktree("new", TICKET)
        (self.wt / "feature.txt").write_text("feature\n")
        self.git("add", "feature.txt", cwd=self.wt)
        self.git("commit", "-qm", "feature", cwd=self.wt)
        return self.git("rev-parse", "HEAD", cwd=self.wt)

    def remote(self, revision: str = "main") -> str:
        return self.git("rev-parse", revision, cwd=self.origin)

    def snapshot(self) -> tuple[str, str, str, dict[str, bytes]]:
        return (
            self.remote(),
            self.git("rev-parse", "HEAD"),
            self.git("status", "--porcelain"),
            {
                str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*")
                if p.is_file() and ".git" not in p.relative_to(self.root).parts
            },
        )

    def assert_board_commit(self, feature: str, paths: set[str]) -> None:
        assert self.remote("main^") == feature
        assert self.git("log", "-1", "--format=%s", cwd=self.origin) == (
            f"{TICKET}: board sync (land)"
        )
        assert (
            set(
                self.git(
                    "diff-tree", "--no-commit-id", "--name-only", "-r", "main", cwd=self.origin
                ).splitlines()
            )
            == paths
        )
        assert self.git("rev-parse", "main") == self.remote()
        assert self.git("status", "--porcelain", "--", "tickets/*.md") == ""
        assert self.git("diff", "--cached", "--name-only") == ""
        assert self.wt.is_dir()
        assert self.git("rev-parse", TICKET) == feature


@pytest.fixture
def repo(tmp_path: Path) -> WorktreeRepo:
    return WorktreeRepo(tmp_path)


@pytest.mark.parametrize("from_linked", [False, True])
def test_land_happy_path_shared_board(repo: WorktreeRepo, from_linked: bool) -> None:
    feature = repo.feature()
    linked_board = repo.wt / "tickets" / f"{TICKET}.md"
    before = linked_board.read_bytes()
    repo.tickets("close", TICKET, cwd=repo.wt)
    board = repo.root / "tickets" / f"{TICKET}.md"
    assert "status: done\n" in board.read_text()
    assert f"updated: {TODAY}\n" in board.read_text()
    assert linked_board.read_bytes() == before
    result = repo.worktree("land", TICKET, cwd=repo.wt if from_linked else repo.root)
    repo.assert_board_commit(feature, {f"tickets/{TICKET}.md"})
    assert (
        repo.git("show", f"main:tickets/{TICKET}.md", cwd=repo.origin) == board.read_text().strip()
    )
    assert repo.git("show", "main:feature.txt", cwd=repo.origin) == "feature"
    assert repo.git("status", "--porcelain") == ""
    assert result.stdout.strip() == str(repo.wt)
    assert f"worktree.sh rm {TICKET}" in result.stderr


def test_land_flushes_all_board_changes_in_one_commit(repo: WorktreeRepo) -> None:
    feature = repo.feature()
    repo.tickets("close", TICKET, cwd=repo.wt)
    repo.tickets("mv", "SAMPLE-901", "wishlist", cwd=repo.wt)
    repo.tickets("new", "SAMPLE-903", "new untracked ticket", cwd=repo.wt)
    repo.worktree("land", TICKET, cwd=repo.wt)
    repo.assert_board_commit(
        feature, {f"tickets/{key}.md" for key in (TICKET, "SAMPLE-901", "SAMPLE-903")}
    )
    for key, status in ((TICKET, "done"), ("SAMPLE-901", "wishlist"), ("SAMPLE-903", "todo")):
        assert f"status: {status}\n" in repo.git("show", f"main:tickets/{key}.md", cwd=repo.origin)


def test_land_flushes_derived_epic_with_child(repo: WorktreeRepo) -> None:
    repo.tickets("new-epic", "SAMPLE-904", "parent")
    repo.tickets("set-epic", TICKET, "SAMPLE-904")
    repo.git("add", "tickets/")
    repo.git("commit", "-qm", "epic baseline")
    repo.git("push", "origin", "main")
    feature = repo.feature()
    repo.tickets("close", TICKET, cwd=repo.wt)
    repo.worktree("land", TICKET, cwd=repo.wt)
    repo.assert_board_commit(feature, {f"tickets/{TICKET}.md", "tickets/SAMPLE-904.md"})
    for key in (TICKET, "SAMPLE-904"):
        assert "status: done\n" in repo.git("show", f"main:tickets/{key}.md", cwd=repo.origin)


def test_land_without_board_changes_has_no_empty_commit(repo: WorktreeRepo) -> None:
    feature = repo.feature()
    repo.git("restore", "tickets/")
    result = repo.worktree("land", TICKET, cwd=repo.wt)
    assert repo.remote() == feature == repo.git("rev-parse", "main")
    assert repo.git("status", "--porcelain") == ""
    assert "board already current" in result.stderr
    assert repo.wt.is_dir()


def test_land_refuses_branch_not_ahead(repo: WorktreeRepo) -> None:
    repo.worktree("new", TICKET)
    # A dirty board is now actionable even without feature commits; exercise a true no-op.
    repo.git("restore", "tickets/")
    before = repo.snapshot()
    result = repo.worktree("land", TICKET, cwd=repo.wt, code=1)
    assert repo.snapshot() == before
    assert "no commits ahead" in result.stderr


def test_land_recovers_stranded_board_commit(repo: WorktreeRepo) -> None:
    repo.worktree("new", TICKET)
    feature = repo.git("rev-parse", TICKET)
    repo.tickets("close", TICKET, cwd=repo.wt)
    repo.git("add", "--", "tickets/*.md")
    repo.git("commit", "-qm", f"{TICKET}: board sync (land)")
    pending = repo.git("rev-parse", "main")
    assert repo.remote() == feature != pending
    assert repo.git("rev-list", "--count", f"origin/main..{TICKET}") == "0"
    result = repo.worktree("land", TICKET, cwd=repo.wt)
    assert repo.remote() == pending
    repo.assert_board_commit(feature, {f"tickets/{TICKET}.md"})
    assert "status: done\n" in repo.git("show", f"main:tickets/{TICKET}.md", cwd=repo.origin)
    assert "recovered / pushed pending commit(s)" in result.stderr
    assert "nothing to land" not in result.stderr


def test_land_flushes_dirty_board_without_feature_commits(repo: WorktreeRepo) -> None:
    repo.worktree("new", TICKET)
    feature = repo.remote()
    repo.tickets("close", TICKET, cwd=repo.wt)
    repo.worktree("land", TICKET, cwd=repo.wt)
    repo.assert_board_commit(feature, {f"tickets/{TICKET}.md"})
    assert "status: done\n" in repo.git("show", f"main:tickets/{TICKET}.md", cwd=repo.origin)


def test_failed_atomic_push_retries_without_another_board_commit(repo: WorktreeRepo) -> None:
    feature = repo.feature()
    repo.tickets("close", TICKET, cwd=repo.wt)
    remote_before = repo.remote()
    reject = repo.origin / "hooks/pre-receive"
    reject.write_text("#!/bin/sh\nexit 1\n")
    reject.chmod(0o755)
    trace = repo.root.parent / "atomic-push.trace"
    repo.env["GIT_TRACE"] = str(trace)
    result = repo.worktree("land", TICKET, cwd=repo.wt, code=1)
    assert repo.remote() == remote_before
    pending = repo.git("rev-parse", "main")
    assert repo.git("rev-parse", "main^") == feature
    assert repo.git("status", "--porcelain") == ""
    assert f"worktree.sh land {TICKET}' to retry" in result.stderr
    commands = trace.read_text().splitlines()
    pushes = [line for line in commands if "git push " in line]
    assert len(pushes) == 1 and "git push origin main" in pushes[0]
    assert "--force" not in pushes[0] and "+" not in pushes[0]
    assert len([line for line in commands if "git fetch origin" in line]) == 1
    reject.unlink()
    result = repo.worktree("land", TICKET, cwd=repo.wt)
    assert repo.remote() == pending
    repo.assert_board_commit(feature, {f"tickets/{TICKET}.md"})
    assert "status: done\n" in repo.git("show", f"main:tickets/{TICKET}.md", cwd=repo.origin)
    assert "recovered / pushed pending commit(s)" in result.stderr


@pytest.mark.parametrize("missing", ["ticket", "worktree", "branch", "all"])
def test_land_refuses_unknown_ticket_or_worktree(repo: WorktreeRepo, missing: str) -> None:
    if missing != "all":
        repo.feature()
        if missing == "ticket":
            (repo.root / "tickets" / f"{TICKET}.md").unlink()
        elif missing == "worktree":
            repo.git("worktree", "remove", str(repo.wt))
        else:
            repo.git("checkout", "--detach", cwd=repo.wt)
            repo.git("branch", "-D", TICKET)
    before = repo.snapshot()
    result = repo.worktree("land", TICKET, code=1)
    assert repo.snapshot() == before
    assert "unknown ticket/worktree" in result.stderr


def test_non_fast_forward_rejects_without_force_or_worktree_changes(repo: WorktreeRepo) -> None:
    feature = repo.feature()
    repo.tickets("close", TICKET, cwd=repo.wt)
    other = repo.root.parent / "other clone"
    repo.git("clone", str(repo.origin), str(other))
    (other / "concurrent.txt").write_text("concurrent change\n")
    repo.git("add", ".", cwd=other)
    repo.git("commit", "-qm", "concurrent", cwd=other)
    repo.git("push", "origin", "main", cwd=other)
    before = repo.snapshot()
    linked_before = repo.git("status", "--porcelain", cwd=repo.wt)
    trace = repo.root.parent / "git.trace"
    repo.env["GIT_TRACE"] = str(trace)
    result = repo.worktree("land", TICKET, cwd=repo.wt, code=1)
    assert repo.snapshot() == before
    assert repo.git("rev-parse", TICKET) == feature
    assert repo.git("status", "--porcelain", cwd=repo.wt) == linked_before
    assert f"rebase {TICKET} onto origin/main first" in result.stderr
    pushes = [line for line in trace.read_text().splitlines() if "git push " in line]
    assert pushes == []  # Reject divergence before any local merge, board commit, or push.


def test_land_preserves_unrelated_dirty_files(repo: WorktreeRepo) -> None:
    feature = repo.feature()
    (repo.root / "notes.txt").write_text("unrelated edit\n")
    (repo.root / "untracked.txt").write_text("unrelated new file\n")
    (repo.root / "tickets/scratch.txt").write_text("not a board file\n")
    repo.worktree("land", TICKET, cwd=repo.wt)
    repo.assert_board_commit(feature, {f"tickets/{TICKET}.md"})
    assert repo.git("show", "main:notes.txt", cwd=repo.origin) == "original"
    assert (repo.root / "notes.txt").read_text() == "unrelated edit\n"
    assert (repo.root / "untracked.txt").read_text() == "unrelated new file\n"
    assert (repo.root / "tickets/scratch.txt").read_text() == "not a board file\n"
    assert set(repo.git("status", "--porcelain").splitlines()) == {
        "M notes.txt",
        "?? tickets/scratch.txt",
        "?? untracked.txt",
    }


@pytest.mark.parametrize("path", ["notes.txt", "tickets/scratch.txt"])
def test_land_refuses_unrelated_staged_files_without_pushing(repo: WorktreeRepo, path: str) -> None:
    repo.feature()
    (repo.root / path).write_text("staged edit\n")
    repo.git("add", path)
    before = repo.snapshot()
    staged = repo.git("diff", "--cached")
    result = repo.worktree("land", TICKET, cwd=repo.wt, code=1)
    assert "unstage non-tickets" in result.stderr
    assert repo.snapshot() == before
    assert repo.git("diff", "--cached") == staged


@pytest.mark.parametrize("detached", [False, True])
def test_land_requires_main_checkout_on_main(repo: WorktreeRepo, detached: bool) -> None:
    repo.feature()
    repo.git("checkout", "--detach") if detached else repo.git("checkout", "-b", "other")
    before = repo.snapshot()
    result = repo.worktree("land", TICKET, cwd=repo.wt, code=1)
    assert repo.snapshot() == before
    assert "main working tree must be on main" in result.stderr


def test_existing_new_list_rm_behavior(repo: WorktreeRepo) -> None:
    result = repo.worktree("new", TICKET)
    assert result.stdout.splitlines()[-1] == str(repo.wt)
    board = repo.root / "tickets" / f"{TICKET}.md"
    assert "status: in-progress\n" in board.read_text()
    assert f"worktree: {TICKET}\n" in board.read_text()
    assert str(repo.wt_root) not in board.read_text()
    before = repo.snapshot()
    assert str(repo.wt) in repo.worktree("list").stdout
    assert repo.snapshot() == before
    repo.worktree("rm", TICKET)
    assert not repo.wt.exists()
    assert TICKET not in repo.git("branch", "--format=%(refname:short)").splitlines()
    assert repo.snapshot() == before


@pytest.mark.parametrize("root_setting", ["default", "absolute", "relative"])
def test_portable_worktree_key_resolves_from_linked_checkout(
    repo: WorktreeRepo, root_setting: str
) -> None:
    if root_setting == "default":
        del repo.env["WT_ROOT"]
        repo.wt_root = repo.root.parent / f"{repo.root.name}-worktrees"
    elif root_setting == "relative":
        repo.env["WT_ROOT"] = "../custom worktrees"
        repo.wt_root = repo.root.parent / "custom worktrees"
    repo.wt = repo.wt_root / TICKET
    repo.worktree("new", TICKET)
    board = repo.root / "tickets" / f"{TICKET}.md"
    assert f"worktree: {TICKET}\n" in board.read_text()
    assert not any(line.startswith("worktree: /") for line in board.read_text().splitlines())
    for cwd in (repo.root, repo.wt):
        resolved = repo.run(
            "bash", str(cwd / "scripts/tickets.sh"), "get-worktree", TICKET, cwd=cwd
        ).stdout.strip()
        assert Path(resolved).resolve() == repo.wt.resolve()
        assert repo.git("rev-parse", "--show-toplevel", cwd=Path(resolved)) == str(
            repo.wt.resolve()
        )
    # Create a second worktree FROM the linked checkout: default root must not drift.
    result = repo.worktree("new", "SAMPLE-901", cwd=repo.wt)
    second = Path(result.stdout.splitlines()[-1])
    assert second.resolve() == (repo.wt_root / "SAMPLE-901").resolve()
    assert second.is_dir()
    # Remove via a linked checkout too; no primary/shared project board is accessed.
    repo.worktree("rm", "SAMPLE-901", cwd=repo.wt)
    assert not second.exists()


def test_set_worktree_normalizes_legacy_caller_and_rejects_unresolvable_paths(
    repo: WorktreeRepo,
) -> None:
    repo.worktree("new", TICKET)
    board = repo.root / "tickets" / f"{TICKET}.md"
    repo.tickets("set-worktree", TICKET, str(repo.wt), cwd=repo.wt)
    assert f"worktree: {TICKET}\n" in board.read_text()
    before = board.read_bytes()
    for value in ("/arbitrary/location", "../escape", "SAMPLE-901"):
        result = repo.run(
            "bash",
            str(repo.wt / "scripts/tickets.sh"),
            "set-worktree",
            TICKET,
            value,
            cwd=repo.wt,
            code=1,
        )
        assert "set WT_ROOT" in result.stderr
        assert board.read_bytes() == before
    repo.tickets("set-worktree", TICKET, cwd=repo.wt)
    assert "worktree: \n" in board.read_text()
    result = repo.run(
        "bash", str(repo.wt / "scripts/tickets.sh"), "get-worktree", TICKET, cwd=repo.wt
    )
    assert result.stdout == ""


def test_committed_ticket_worktrees_never_store_absolute_paths() -> None:
    for path in (SCRIPTS.parent / "tickets").glob("*.md"):
        header = path.read_text().split("---", 2)[1]
        for line in header.splitlines():
            if line.startswith("worktree:"):
                assert not Path(line.partition(":")[2].strip()).is_absolute(), path.name


def test_ac10_land_flushes_blocked_by_without_closing(repo: WorktreeRepo) -> None:
    feature = repo.feature()
    linked = repo.wt / "tickets" / f"{TICKET}.md"
    linked_before = linked.read_bytes()
    repo.tickets("block", TICKET, "SAMPLE-901", cwd=repo.wt)
    shared = repo.root / "tickets" / f"{TICKET}.md"
    expected = shared.read_text()
    assert "blocked_by: [SAMPLE-901]\n" in expected
    assert "status: in-progress\n" in expected
    assert linked.read_bytes() == linked_before
    repo.worktree("land", TICKET, cwd=repo.wt)
    repo.assert_board_commit(feature, {f"tickets/{TICKET}.md"})
    assert repo.git("show", f"main:tickets/{TICKET}.md", cwd=repo.origin) == expected.strip()
    assert shared.read_text() == expected
