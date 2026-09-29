"""lint-pr: PR のコミット（系列のブランチ..HEAD、マージを除く）の規約チェック。"""

import pytest
from repobuilder import Repo

from release_viewer.__main__ import main

CONFIG = """
[[series]]
branch = "main"
kind = "mainline"

[[series]]
branch = "release/1.0"
kind = "release"
parent = "main"
"""


@pytest.fixture
def repo(tmp_path) -> Repo:
    """main と release/1.0 に加え、main 上に Fix-ID 付きの修正（FIX-1）を持つリポジトリ。"""
    r = Repo(tmp_path / "repo")
    r.init()
    r.write(".release/config.toml", CONFIG)
    r.write("components/hal/component.toml", '[component]\nname = "hal"\nversion = "1.0.0"\n')
    r.commit("chore: 初期インポート", "sato")
    r.checkout("release/1.0", new_from="main")
    r.checkout("main")
    r.write("components/hal/src/fix.c", "int fixed = 1;\n")
    r.fix = r.commit("hal: 修正\n\nFix-ID: FIX-1\n", "suzuki")
    return r


def pr(r: Repo, base: str, messages: list[str]) -> None:
    """base から作業ブランチ pr を切り、メッセージごとに1コミット積む。"""
    r.checkout("pr", new_from=base)
    for i, message in enumerate(messages):
        r.write(f"components/hal/src/pr{i}.c", f"int v{i};\n")
        r.commit(message, "tanaka")


def lint(r: Repo, capsys, *args: str) -> tuple[int, list[str]]:
    code = main(["lint-pr", str(r.path), "--head", "pr", *args])
    return code, capsys.readouterr().err.splitlines()


def kinds(lines: list[str]) -> list[str]:
    return [line.split("]")[0].split("[")[1] for line in lines if line.startswith(("ERROR", "WARNING"))]


def test_mainline_ok_and_misplaced(repo, capsys):
    pr(repo, "main", [
        "chore: バージョン更新\n",                                # Fix-ID なし: 対象外
        "hal: 修正\n\nFix-ID: FIX-2\n",                          # 正しいトレーラー（mainline 宛は -x 不要）
        "hal: 修正\n\nFix-ID: FIX-3\n\n- [x] テスト済み\n",       # トレーラーの位置にない
    ])
    code, lines = lint(repo, capsys, "--series", "main")
    assert code == 0
    assert kinds(lines) == ["fix_id_not_trailer"]
    assert lines[-1] == "errors=0 warnings=1 commits=3"
    assert lint(repo, capsys, "--series", "main", "--strict")[0] == 1


def test_mainline_invalid_fix_id(repo, capsys):
    pr(repo, "main", ["hal: 修正\n\nFix-ID: fix-2\n"])
    code, lines = lint(repo, capsys, "--series", "main")
    assert code == 1
    assert kinds(lines) == ["invalid_fix_id"]


def test_release_with_x(repo, capsys):
    repo.checkout("pr", new_from="release/1.0")
    repo.cherry_pick(repo.fix, "suzuki")
    code, lines = lint(repo, capsys, "--series", "release/1.0")
    assert (code, kinds(lines), lines[-1]) == (0, [], "errors=0 warnings=0 commits=1")


def test_release_without_x(repo, capsys):
    repo.checkout("pr", new_from="release/1.0")
    repo.git("cherry-pick", repo.fix, author="suzuki")  # -x なし（トレーラーはコピーされる）
    code, lines = lint(repo, capsys, "--series", "release/1.0")
    assert code == 1
    assert kinds(lines) == ["cherry_pick_without_x"]


def test_release_x_to_missing_commit(repo, capsys):
    pr(repo, "release/1.0", [f"hal: 修正\n\nFix-ID: FIX-1\n(cherry picked from commit {'0' * 40})\n"])
    code, lines = lint(repo, capsys, "--series", "release/1.0")
    assert code == 1
    assert kinds(lines) == ["cherry_pick_source_missing"]


@pytest.mark.parametrize("args", [["--series", "release/9.9"], ["--series", "main", "--head", "no-such-ref"]])
def test_errors_exit_2(repo, capsys, args):
    pr(repo, "main", ["chore: x\n"])
    assert main(["lint-pr", str(repo.path), *args]) == 2
