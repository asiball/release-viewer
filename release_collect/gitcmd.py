"""git コマンドの薄いラッパー。出力に影響するユーザー設定は -c で打ち消す。"""

from __future__ import annotations

import subprocess
from pathlib import Path

_SAFE_CONFIG = [
    "-c", "color.ui=never",
    "-c", "log.showSignature=false",
    "-c", "core.quotepath=false",
    "-c", "i18n.logOutputEncoding=utf-8",
]


class GitError(RuntimeError):
    pass


class Git:
    def __init__(self, repo: Path | str):
        self.repo = str(repo)

    def run_bytes(self, *args: str, input: bytes | None = None, check: bool = True) -> bytes:
        proc = subprocess.run(
            ["git", "-C", self.repo, *_SAFE_CONFIG, *args],
            input=input, capture_output=True,
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {proc.stderr.decode('utf-8', 'replace').strip()}")
        return proc.stdout

    def run(self, *args: str, input: str | None = None, check: bool = True) -> str:
        out = self.run_bytes(*args, input=input.encode("utf-8") if input is not None else None,
                             check=check)
        return out.decode("utf-8", "replace")

    def rev_list(self, *args: str) -> list[str]:
        return self.run("rev-list", *args).split()

    def resolve_commit(self, ref: str) -> str | None:
        out = self.run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).strip()
        return out or None

    def resolve_branch(self, branch: str, remote: str) -> str | None:
        """ローカルブランチ → リモート追跡ブランチの順に探し、見つかった完全な参照名を返す。"""
        for ref in (f"refs/heads/{branch}", f"refs/remotes/{remote}/{branch}"):
            if self.resolve_commit(ref):
                return ref
        return None

    def cat_files(self, specs: list[str]) -> dict[str, bytes | None]:
        """`<rev>:<path>` の内容をまとめて読む。存在しなければ None。"""
        if not specs:
            return {}
        out = self.run_bytes("cat-file", "--batch", input=("\n".join(specs) + "\n").encode("utf-8"))
        result: dict[str, bytes | None] = {}
        pos = 0
        for spec in specs:
            nl = out.index(b"\n", pos)
            header = out[pos:nl].decode("utf-8", "replace").split()
            pos = nl + 1
            if len(header) == 3 and header[1] == "blob":
                size = int(header[2])
                result[spec] = out[pos:pos + size]
                pos += size + 1
            elif len(header) == 3:
                # blob 以外（tree 等）: 中身を読み飛ばす
                pos += int(header[2]) + 1
                result[spec] = None
            else:
                result[spec] = None  # "<spec> missing"
        return result
