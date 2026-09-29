import json
import subprocess

from release_collect.__main__ import main
from release_collect.collect import Collector

from .conftest import fix, generate, violations

ONLY_MAIN_AND_12 = """
[[series]]
branch = "main"
kind = "mainline"
tag_prefix = "fw/"

[[series]]
branch = "release/1.2"
kind = "release"
parent = "main"
tag_prefix = "fw/"
"""


def test_check_fails_on_sample(sample_repo, tmp_path, capsys):
    assert main([str(sample_repo), "-o", str(tmp_path / "data.json"), "--check"]) == 1
    err = capsys.readouterr().err
    assert "fix_missing" in err and "dependency_violation" in err
    assert (tmp_path / "data.js").read_text(encoding="utf-8").startswith("window.RELEASE_DATA = {")


def test_check_passes_when_clean(sample_repo, config_dir, tmp_path):
    (config_dir / "series.toml").write_text(ONLY_MAIN_AND_12, encoding="utf-8")
    (config_dir / "exclusions.toml").write_text("", encoding="utf-8")  # 1.1 系向けの宣言は不要
    args = [str(sample_repo), "--config-dir", str(config_dir), "-o", str(tmp_path / "d.json"), "--check"]
    assert main(args) == 0
    assert main(args + ["--strict"]) == 0


def test_strict_fails_on_warnings(sample_repo, config_dir, tmp_path):
    # release/1.1 だけを見る: error はなく patch_id_only の warning だけ
    (config_dir / "series.toml").write_text(ONLY_MAIN_AND_12.replace("1.2", "1.1"), encoding="utf-8")
    args = [str(sample_repo), "--config-dir", str(config_dir), "-o", str(tmp_path / "d.json"), "--check"]
    assert main(args) == 0
    assert main(args + ["--strict"]) == 1


def test_config_error_exit_2(sample_repo, config_dir, tmp_path):
    (config_dir / "series.toml").write_text('[[series]]\nbranch = "nope"\nkind = "mainline"\n',
                                            encoding="utf-8")
    assert main([str(sample_repo), "--config-dir", str(config_dir), "-o", str(tmp_path / "d.json")]) == 2
    (config_dir / "series.toml").write_text('[[series]]\nbranch = "main"\nkind = "trunk"\n',
                                            encoding="utf-8")
    assert main([str(sample_repo), "--config-dir", str(config_dir), "-o", str(tmp_path / "d.json")]) == 2


def test_exclusion_rules(sample_repo, config_dir):
    (config_dir / "exclusions.toml").write_text("""
[[exclude]]
fix = "FIX-102"
series = ["customer/gamma/1.2"]

[[exclude]]
fix = "FIX-101"
series = ["release/1.2"]
reason = "誤って宣言"

[[exclude]]
fix = "FIX-999"
series = ["release/9.9"]
reason = "存在しない"
""", encoding="utf-8")
    d = Collector(sample_repo, config_dir=config_dir).collect()
    # 理由のない除外は無効 → missing のまま + error
    assert fix(d, "FIX-102")["status"]["customer/gamma/1.2"]["state"] == "missing"
    assert [v["severity"] for v in violations(d, "exclusion_without_reason")] == ["error"]
    # 適用済みなのに除外宣言 → applied + warning
    assert fix(d, "FIX-101")["status"]["release/1.2"]["state"] == "applied"
    assert len(violations(d, "exclusion_but_applied")) == 1
    # 存在しない fix・系列
    assert len(violations(d, "stale_exclusion")) == 2


def test_remote_tracking_branches(sample_repo, tmp_path, data):
    """CI の clone のようにローカルブランチが main しかなくても、origin/* を追跡できる。"""
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(sample_repo), str(clone)], check=True)
    d = Collector(clone).collect()
    assert [s["ref"] for s in d["series"]][1] == "refs/remotes/origin/release/1.1"
    assert d["fixes"] == data["fixes"]
    assert d["violations"] == data["violations"]


def test_since_ref_limits_scan(sample_repo, data):
    d = Collector(sample_repo, since_ref="app/v1.2.0").collect()
    assert len(d["commits"]) < len(data["commits"])
    # 走査範囲内の判定は変わらない
    assert fix(d, "FIX-104")["status"] == fix(data, "FIX-104")["status"]


def test_generation_and_output_are_reproducible(sample_repo, tmp_path, monkeypatch):
    other = generate(tmp_path / "again")

    def refs(repo):
        return subprocess.run(["git", "-C", str(repo), "for-each-ref", "--format=%(objectname) %(refname)"],
                              check=True, capture_output=True, encoding="utf-8").stdout

    assert refs(other) == refs(sample_repo)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")
    a = Collector(sample_repo, name="sample-fw").collect()
    b = Collector(other, name="sample-fw").collect()
    assert json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False)
