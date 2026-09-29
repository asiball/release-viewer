"""Fix-ID の取り出し（docs/conventions.md §5.1）。トレーラーの位置にない Fix-ID も拾い、警告を出す。"""

import re

import pytest
from repobuilder import Repo

from release_viewer.collect import Collector
from release_viewer.fixid import parse_fix_ids

from .conftest import violations

PATTERN = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")

CONFIG = """
[[series]]
branch = "main"
kind = "mainline"
"""

MESSAGES = {
    # 直前に空行がない（最終段落が全行トレーラーではない）
    "no_blank_line": ("hal: 修正\n\n本文。\nFix-ID: FIX-20\n", ["FIX-20"]),
    # 後ろに本文が続く（PR テンプレートのチェックリストが付いた squash merge 等）
    "body_follows": ("hal: 修正\n\nFix-ID: FIX-23\n\n補足。\n", ["FIX-23"]),
    # 小文字のキー（トレーラーの位置にない）
    "lowercase": ("hal: 修正\n\nfix-id: FIX-24\n\n- [x] テスト済み\n", ["FIX-24"]),
}


@pytest.mark.parametrize("message,expected", MESSAGES.values(), ids=MESSAGES.keys())
def test_line_scan_outside_trailer(message, expected):
    assert parse_fix_ids("", message, "Fix-ID", PATTERN) == (expected, [], expected)


def test_comma_separated_trailer():
    # トレーラーの位置にあるので警告なし。カンマ区切りの両方を採用する
    assert parse_fix_ids("FIX-21, FIX-22", "s\n\nFix-ID: FIX-21, FIX-22\n", "Fix-ID", PATTERN) == (
        ["FIX-21", "FIX-22"], [], [])


def test_invalid_value():
    assert parse_fix_ids("", "s\n\n本文。\nFix-ID: fix-1 FIX-2\n", "Fix-ID", PATTERN) == (["FIX-2"], ["fix-1"], ["FIX-2"])


def test_collector_detects_and_warns(tmp_path):
    r = Repo(tmp_path / "repo")
    r.init()
    r.write(".release/config.toml", CONFIG)
    r.write("components/hal/component.toml", '[component]\nname = "hal"\nversion = "1.0.0"\n')
    r.commit("chore: 初期インポート", "sato")
    shas = {}
    messages = {**{k: m for k, (m, _) in MESSAGES.items()},
                "comma": "hal: 修正\n\nFix-ID: FIX-21, FIX-22\n"}
    for i, (key, message) in enumerate(messages.items()):
        r.write("components/hal/src/hal.c", f"int v = {i};\n")
        shas[key] = r.commit(message, "tanaka")

    d = Collector(r.path).collect()
    assert [f["id"] for f in d["fixes"]] == ["FIX-20", "FIX-21", "FIX-22", "FIX-23", "FIX-24"]
    assert all(f["status"]["main"]["state"] == "applied" for f in d["fixes"])
    warned = {v["refs"]["commit"] for v in violations(d, "fix_id_not_trailer")}
    assert warned == {shas["no_blank_line"], shas["body_follows"], shas["lowercase"]}
    assert not violations(d, "invalid_fix_id")
