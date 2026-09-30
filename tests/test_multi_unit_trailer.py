"""複数コミットの fix を -x なし（トレーラーは残る）で移植した場合の判定（docs/conventions.md §6）。

伝播判定（status）とタグの fix 集計（fixes_included）が同じ対応付けを使い、矛盾しないことを確認する。
"""

from repobuilder import Repo

from release_viewer.collect import Collector

from .conftest import fix, tag, violations

CONFIG = """
[[series]]
branch = "main"
kind = "mainline"
tag_prefix = "fw/"

[[series]]
branch = "release/1.0"
kind = "release"
parent = "main"
tag_prefix = "fw/"
"""


def build(path, *, alter_second: bool) -> dict:
    """main で 2 コミットの FIX-1 を作り、release/1.0 へ -x なしで cherry-pick してタグを打つ。

    alter_second: 2 コミット目は cherry-pick 後に差分を変えてコミットする（衝突解消相当）。
    """
    r = Repo(path)
    r.init()
    r.write(".release/config.toml", CONFIG)
    r.write("components/hal/component.toml", '[component]\nname = "hal"\nversion = "1.0.0"\n')
    r.write("components/hal/src/a.c", "int a = 0;\n")
    r.write("components/hal/src/b.c", "int b = 0;\n")
    r.commit("chore: 初期インポート", "sato")
    r.tag("fw/v1.0.0")
    r.checkout("release/1.0", new_from="main")
    r.checkout("main")
    r.write("components/hal/src/a.c", "int a = 1;\n")
    first = r.commit("hal: 修正 1/2\n\nFix-ID: FIX-1\n", "suzuki")
    r.write("components/hal/src/b.c", "int b = 1;\n")
    second = r.commit("hal: 修正 2/2\n\nFix-ID: FIX-1\n", "suzuki")

    r.checkout("release/1.0")
    r.git("cherry-pick", first, author="suzuki")
    if alter_second:
        r.git("cherry-pick", "-n", second)
        r.write("components/hal/src/b.c", "int b = 2;\n")
        r.commit("hal: 修正 2/2\n\nFix-ID: FIX-1\n", "suzuki")
    else:
        r.git("cherry-pick", second, author="suzuki")
    r.tag("fw/v1.0.1")
    return Collector(r.path).collect()


def test_cherry_pick_without_x_is_applied_by_trailer(tmp_path):
    d = build(tmp_path / "repo", alter_second=False)
    st = fix(d, "FIX-1")["status"]["release/1.0"]
    assert (st["state"], st["method"], st["units_matched"], st["units_total"]) == ("applied", "trailer", 2, 2)
    assert tag(d, "fw/v1.0.1")["fixes_included"] == ["FIX-1"]
    assert not violations(d, "trailer_unmatched")


def test_changed_diff_is_missing_and_not_included(tmp_path):
    d = build(tmp_path / "repo", alter_second=True)
    st = fix(d, "FIX-1")["status"]["release/1.0"]
    assert (st["state"], st["units_matched"], st["units_total"]) == ("missing", 1, 2)
    t = tag(d, "fw/v1.0.1")
    assert "FIX-1" not in t["fixes_included"] and "FIX-1" not in t["fixes_added"]
    unmatched = violations(d, "trailer_unmatched")
    assert [(v["severity"], v["refs"]["fix"], v["refs"]["series"]) for v in unmatched] == [
        ("warning", "FIX-1", "release/1.0")]
    assert unmatched[0]["refs"]["commit"] == t["commit"]
