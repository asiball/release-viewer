"""サンプル・テスト用に、固定の作者・日時で git リポジトリを組み立てる。

作者・日時は固定で、ユーザーの git 設定も読まないため、同じ操作を再実行すると同じコミットSHAになる。
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_TIME = datetime(2026, 1, 5, 9, 0, tzinfo=timezone(timedelta(hours=9)))
STEP = timedelta(hours=3)

AUTHORS = {
    "sato": ("Sato Hanako", "sato@example.com"),
    "suzuki": ("Suzuki Ichiro", "suzuki@example.com"),
    "tanaka": ("Tanaka Jiro", "tanaka@example.com"),
    "release": ("Release Bot", "release-bot@example.com"),
}


class Repo:
    """固定の作者・日時で git を操作する。コミット・タグを作るたびに時刻を進める。"""

    def __init__(self, path: Path):
        self.path = path
        self.tick = 0
        self.base_env = {
            **os.environ,
            # ユーザー・システムの git 設定（autocrlf、署名、フック等）を読まない
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }

    def init(self) -> None:
        """空のリポジトリを main ブランチで作り、出力に影響するリポジトリ設定を固定する。"""
        self.path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        for key, value in [("core.autocrlf", "false"), ("commit.gpgsign", "false"),
                           ("tag.gpgsign", "false"), ("core.hooksPath", ".git/no-hooks")]:
            self.git("config", key, value)

    def _env(self, author: str) -> dict[str, str]:
        self.tick += 1
        when = (BASE_TIME + STEP * self.tick).isoformat()
        name, email = AUTHORS[author]
        return {
            **self.base_env,
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_COMMITTER_DATE": when,
        }

    def git(self, *args: str, author: str | None = None, input: str | None = None,
            check: bool = True) -> subprocess.CompletedProcess:
        env = self._env(author) if author else self.base_env
        proc = subprocess.run(
            ["git", *args], cwd=self.path, env=env, input=input,
            capture_output=True, encoding="utf-8", check=False,
        )
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed:\n{proc.stdout}{proc.stderr}")
        return proc

    # --- ファイル操作 ---
    def write(self, rel: str, content: str) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")

    def edit(self, rel: str, old: str, new: str) -> None:
        p = self.path / rel
        text = p.read_text(encoding="utf-8")
        if old not in text:
            raise RuntimeError(f"{rel}: 置換対象が見つからない: {old!r}")
        self.write(rel, text.replace(old, new, 1))

    def set_version(self, component: str, version: str) -> None:
        rel = f"components/{component}/component.toml"
        text = (self.path / rel).read_text(encoding="utf-8")
        lines = [f'version = "{version}"' if l.startswith("version = ") else l
                 for l in text.split("\n")]
        self.write(rel, "\n".join(lines))

    def set_dependency(self, component: str, dep: str, constraint: str) -> None:
        rel = f"components/{component}/component.toml"
        text = (self.path / rel).read_text(encoding="utf-8")
        lines = [f'{dep} = "{constraint}"' if l.startswith(f"{dep} = ") else l
                 for l in text.split("\n")]
        self.write(rel, "\n".join(lines))

    # --- git 操作 ---
    def commit(self, message: str, author: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-F", "-", author=author, input=message)
        return self.rev("HEAD")

    def rev(self, ref: str) -> str:
        return self.git("rev-parse", ref).stdout.strip()

    def checkout(self, branch: str, new_from: str | None = None) -> None:
        if new_from:
            self.git("checkout", "-q", "-b", branch, new_from)
        else:
            self.git("checkout", "-q", branch)

    def tag(self, name: str, author: str = "release") -> None:
        self.git("tag", "-a", name, "-m", name, author=author)

    def cherry_pick(self, sha: str, author: str) -> str:
        """規約どおりの伝播: cherry-pick -x（トレーラーもそのままコピーされる）"""
        self.git("cherry-pick", "-x", sha, author=author)
        return self.rev("HEAD")

    def cherry_pick_rewritten(self, sha: str, message: str, author: str) -> str:
        """変更だけを取り込み、メッセージは手で書き直す（規約違反の再現用）"""
        self.git("cherry-pick", "-n", sha)
        return self.commit(message, author)
