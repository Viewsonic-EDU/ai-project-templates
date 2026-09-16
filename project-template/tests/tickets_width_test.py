"""Ticket cell() width regression (ported from a mature downstream project).

Runs real ticket functions against an isolated, disposable git store."""

import argparse
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
import tempfile
import termios
import time
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZERO = [(0x0300, 0x036F), (0x200B, 0x200F), (0xFEFF, 0xFEFF)]
WIDE = [
    (0x1100, 0x115F),
    (0x2329, 0x232A),
    (0x2E80, 0x303E),
    (0x3041, 0x33FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xA000, 0xA4CF),
    (0xAC00, 0xD7A3),
    (0xF900, 0xFAFF),
    (0xFE10, 0xFE19),
    (0xFE30, 0xFE6F),
    (0xFF00, 0xFF60),
    (0xFFE0, 0xFFE6),
    (0x1F300, 0x1F64F),
    (0x1F900, 0x1F9FF),
    (0x20000, 0x3FFFD),
]
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# Frozen pre-wcwidth cell(), used only as an ASCII compatibility oracle.
LEGACY_CELL = r"""
cell() {
  CELL_TEXT=$1 LC_ALL=C awk -v w="$2" '
    BEGIN {
      for (b=1; b<256; b++) ord[sprintf("%c", b)]=b
      text=ENVIRON["CELL_TEXT"]
      for (i=1; (byte=substr(text, i, 1)) != ""; i++) {
        b=ord[byte]
        if (b<128 || b>=192) n++
        glyph[n]=glyph[n] byte
      }
      count=n
      if (n>w) count=w-1
      for (i=1; i<=count; i++) printf "%s", glyph[i]
      if (n>w) { printf "…"; count++ }
      for (i=count; i<w; i++) printf " "
    }
  '
}
"""


def width(text):
    return sum(
        0
        if any(a <= ord(c) <= b for a, b in ZERO)
        else 2
        if any(a <= ord(c) <= b for a, b in WIDE)
        else 1
        for c in text
    )


def expected_cell(text, target):
    if target == 0:
        return ""
    truncated = width(text) > target
    budget = target - int(truncated)
    result = ""
    for char in text:
        if width(result + char) > budget:
            break
        result += char
    if truncated:
        result += "…"
    return result + " " * (target - width(result))


def env_for(cols=120, color="never"):
    env = os.environ.copy()
    # Prevent inherited board/git knobs from redirecting or changing the fixture.
    for key in list(env):
        if key.startswith(("TICKETS_", "GIT_")) or key in ("BASH_ENV", "ENV"):
            env.pop(key)
    env.update(
        TICKET_PREFIX="TKT",
        TICKETS_COLOR=color,
        TICKETS_DONE_LIMIT="0",
        TICKETS_COLS=str(cols),
        TERM="xterm-256color",
    )
    return env


def shell(repo, code, data=None, cols=120, legacy=False, color="never"):
    source = "source ./tickets.sh\n" + (LEGACY_CELL if legacy else "")
    return subprocess.run(
        ["bash", "-c", source + code],
        cwd=repo,
        env=env_for(cols, color),
        input=data,
        capture_output=True,
        check=True,
    ).stdout


def cells(repo, cases, legacy=False):
    data = b"".join(text.encode() + b"\0" + str(w).encode() + b"\0" for text, w in cases)
    out = shell(
        repo,
        """while IFS= read -r -d '' text && IFS= read -r -d '' w; do
  cell "$text" "$w"
  printf '\\0'
done
""",
        data,
        legacy=legacy,
    )
    return out.decode().split("\0")[:-1]


def unit_tests(repo):
    texts = [
        "",
        "hello",
        "中文",
        "A中文B",
        "ASCII title " * 8,
        "中文標題" * 15,
        "A中B文 mixed " * 12,
        "e\u0301",
        "中\u0301文",
        "a\u200bb\u200fc\ufeff",
        "\u0301\u200b",
        "↳ TKT-4 中文",
        "😀🦊𠀀",
        "a\\b literal $value",
    ]
    cases = [(s, w) for s in texts for w in (0, 1, 2, 3, 4, 5, 8, 24, 120)]
    # Every specified range endpoint and its neighbors, including supplementary UTF-8.
    points = {p for a, b in ZERO + WIDE for p in (a - 1, a, b, b + 1)}
    cases += [("A" + chr(p) + "Z", w) for p in sorted(points) for w in (1, 3, 4, 5)]
    actual = cells(repo, cases)
    assert len(actual) == len(cases)
    for index, (text, w) in enumerate(cases):
        result = actual[index]
        assert width(result) == w, (text, w, result, width(result))
        assert result == expected_cell(text, w), (text, w, result)
        if width(text) > w and w > 0:
            assert result.rstrip(" ").endswith("…"), (text, w, result)
    ascii_cases = [("a" * n, w) for n in range(35) for w in (1, 2, 5, 24, 30)]
    assert cells(repo, ascii_cases) == cells(repo, ascii_cases, legacy=True)
    print(f"PASS unit: {len(cases)} width/content cases; {len(ascii_cases)} legacy ASCII cases")


def fixture(repo, ascii_only=False):
    tickets = repo / "tickets"
    tickets.mkdir(exist_ok=True)
    for i in range(9):
        status = "in-progress" if i == 0 else "done" if i == 1 else "wishlist" if i == 2 else "todo"
        title = ("Long ASCII title " if ascii_only else "中文標題 Mixed 😀 𠀀 e\u0301 ") * 18
        extra = "type: epic\n" if i == 3 else "epic: TKT-3\n" if i == 4 else ""
        (tickets / f"TKT-{i}.md").write_text(
            f"---\nkey: TKT-{i}\ntitle: {title}\nstatus: {status}\n"
            f"updated: 2026-09-15\n{extra}---\n",
            encoding="utf-8",
        )


def board_tests(repo):
    fixture(repo)
    for cols in (120, 160):
        w = (cols - 9) // 4
        for color in ("never", "always"):
            raw = shell(repo, "cmd_board", cols=cols, color=color).decode()
            lines = ANSI.sub("", raw).splitlines()
            for line in lines:
                parts = line.split(" │ ")
                assert len(parts) == 4, line
                assert all(width(part) == w for part in parts), line
                assert width(line) <= cols, line
            assert sum("↳ TKT-4" in line for line in lines) == 1
    fixture(repo, ascii_only=True)
    for cols in (120, 160):
        for color in ("never", "always"):
            for command in ("cmd_list", "cmd_board"):
                assert shell(repo, command, cols=cols, color=color) == shell(
                    repo, command, cols=cols, color=color, legacy=True
                ), (command, cols, color)
    fixture(repo)
    print(
        "PASS board: four exact-width CJK cells, child prefix, colors; "
        "list/board ASCII byte compatibility"
    )


def watch_capture(repo, rows, cols, artifact_dir=None):
    env = env_for(cols, "always")
    env.pop("TICKETS_COLS")
    # Match standard terminal hints to the real PTY size. Sandboxed macOS can deny
    # stty on /dev/tty; captured tput then uses these existing fallback inputs.
    env.update(COLUMNS=str(cols), LINES=str(rows), TMPDIR=str(repo))
    env["TICKETS_INTERVAL"] = "60"
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(repo)
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        assert struct.unpack("HHHH", fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8))[:2] == (
            rows,
            cols,
        )
        os.execvpe("bash", ["bash", "./tickets.sh", "watch"], env)
    captured = bytearray()
    pending = bytearray()

    def frame():
        deadline = time.monotonic() + 15
        while b"\x1b[J" not in pending:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"watch frame timeout: {bytes(captured)!r}"
            ready, _, _ = select.select([fd], [], [], remaining)
            assert ready, "watch frame timeout"
            try:
                chunk = os.read(fd, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    raise AssertionError(f"watch exited before frame: {bytes(captured)!r}") from exc
                raise
            assert chunk, "watch exited before frame"
            pending.extend(chunk)
            captured.extend(chunk)
        end = pending.index(b"\x1b[J") + len(b"\x1b[J")
        result = bytes(pending[:end]).decode()
        del pending[:end]
        assert result.count("\x1b[H") == 1
        content = result.split("\x1b[H", 1)[1]
        # PTY ONLCR may turn the renderer CRLF into CRCRLF; CR has no width.
        rendered = re.split(r"\r*\n", ANSI.sub("", content))
        assert len(rendered) == rows, (len(rendered), rows)
        # Nonblank model rows and the footer are exactly cols wide; blanks do not wrap.
        assert all(width(line) <= cols for line in rendered), [width(x) for x in rendered]
        assert all(width(line) == cols for line in rendered if line), [width(x) for x in rendered]
        model = "\n".join(rendered[:-1])
        for header in ("WISHLIST", "TODO", "IN-PROGRESS", "DONE"):
            assert len(re.findall(r"^" + header + r" \(", model, re.M)) == 1, model
        for i in range(9):
            assert len(re.findall(rf"(?:^|↳ )TKT-{i}  ", model, re.M)) == 1, model
        assert "↳ TKT-4" in model
        selected = re.findall(r"\x1b\[7m(.*?)\x1b\[0m", content)
        assert len(selected) == 1 and width(selected[0]) == cols, selected
        return rendered, selected[0]

    try:
        # Refresh the same model, then move selection onto the child row.
        rendered, selected = frame()
        assert "TKT-2  " in selected
        os.write(fd, b"r")
        frame()
        for key in (b"j", b"j"):
            os.write(fd, key)
            rendered, selected = frame()
        assert "↳ TKT-4  " in selected
        if artifact_dir:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / f"watch-{rows}x{cols}.ansi").write_bytes(captured)
            (artifact_dir / f"watch-{rows}x{cols}.txt").write_text("\n".join(rendered) + "\n")
        print(
            f"PASS PTY {rows}x{cols}: 4 frames, each model item once, "
            "no row overflow, selected child exact width"
        )
    finally:
        # Bound cleanup even when an assertion fails; no live watcher survives the test.
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        # Close the master before reaping: macOS can wait for terminal output to
        # drain during child exit, even after SIGKILL, if the master stays open.
        os.close(fd)
        deadline = time.monotonic() + 3
        while os.waitpid(pid, os.WNOHANG)[0] == 0:
            if time.monotonic() >= deadline:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                break
            time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pty-only", action="store_true")
    parser.add_argument("--capture-dir", type=Path)
    args = parser.parse_args()
    # Stay inside the dispatched worktree, including the independent Git common dir.
    with tempfile.TemporaryDirectory(prefix=".tickets-width-", dir=ROOT) as directory:
        repo = Path(directory)
        subprocess.run(["git", "init", "-q", str(repo)], env=env_for(), check=True)
        shutil.copyfile(ROOT / "scripts/tickets.sh", repo / "tickets.sh")
        fixture(repo)
        if not args.pty_only:
            unit_tests(repo)
            board_tests(repo)
        for rows, cols in ((24, 120), (50, 160)):
            watch_capture(repo, rows, cols, args.capture_dir)
    print("PASS tickets width regression")


if __name__ == "__main__":
    main()
