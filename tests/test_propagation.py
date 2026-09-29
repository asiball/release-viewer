"""伝播判定（docs/conventions.md §6）をサンプルリポジトリで検証する。"""

import pytest

from .conftest import fix, violations

EXPECTED = {
    # fix: {series: (state, method)}
    "FIX-101": {
        "main": ("applied", "ancestry"),
        "release/1.1": ("applied", "trailer"),
        "customer/beta/1.1": ("applied", "trailer"),
        "release/1.2": ("applied", "trailer"),
        "customer/acme/1.2": ("applied", "trailer"),  # release/1.2 のマージで入った
        "customer/gamma/1.2": ("applied", "trailer"),
    },
    "FIX-102": {
        "main": ("applied", "trailer"),  # release/1.2 からの前方移植
        "release/1.1": ("excluded", None),
        "customer/beta/1.1": ("excluded", None),
        "release/1.2": ("applied", "ancestry"),
        "customer/acme/1.2": ("applied", "ancestry"),
        "customer/gamma/1.2": ("missing", None),
    },
    "FIX-103": {
        "main": ("applied", "ancestry"),
        "release/1.1": ("patch_id_only", "patch_id"),
        "customer/beta/1.1": ("applied", "trailer"),
        "release/1.2": ("applied", "trailer"),
        "customer/acme/1.2": ("applied", "trailer"),
        "customer/gamma/1.2": ("applied", "trailer"),
    },
    "FIX-104": {
        "main": ("applied", "ancestry"),
        "release/1.1": ("applied", "trailer"),
        "customer/beta/1.1": ("applied", "trailer"),
        "release/1.2": ("applied", "cherry_pick_x"),
        "customer/acme/1.2": ("applied", "cherry_pick_x"),  # -x の連鎖（acme → release/1.2 → main）
        "customer/gamma/1.2": ("applied", "trailer"),
    },
    "FIX-105": {
        "main": ("applied", "ancestry"),
        "release/1.1": ("applied", "trailer"),
        "customer/beta/1.1": ("applied", "trailer"),
        "release/1.2": ("applied", "trailer"),
        "customer/acme/1.2": ("applied", "trailer"),
        "customer/gamma/1.2": ("missing", None),  # 2コミット中1つだけ
    },
}


def test_all_fixes_found(data):
    assert [f["id"] for f in data["fixes"]] == list(EXPECTED)


@pytest.mark.parametrize("fid,series,state,method", [
    (fid, s, st, m) for fid, rows in EXPECTED.items() for s, (st, m) in rows.items()
])
def test_state(data, fid, series, state, method):
    st = fix(data, fid)["status"][series]
    assert st["state"] == state
    assert st.get("method") == method


def test_origin(data):
    assert fix(data, "FIX-101")["origin"]["series"] == "main"
    assert fix(data, "FIX-102")["origin"]["series"] == "release/1.2"
    assert fix(data, "FIX-101")["components"] == ["hal"]


def test_multi_commit_fix_requires_all_units(data):
    f = fix(data, "FIX-105")
    assert len(f["units"]) == 2
    gamma = f["status"]["customer/gamma/1.2"]
    assert (gamma["units_total"], gamma["units_matched"]) == (2, 1)
    assert len(gamma["commits"]) == 1
    assert f["status"]["release/1.2"]["units_matched"] == 2


def test_excluded_carries_reason(data):
    st = fix(data, "FIX-102")["status"]["release/1.1"]
    assert "retry.c" in st["reason"]
    assert st["by"] == "tanaka"


def test_patch_id_match_points_to_evidence_commit(data):
    st = fix(data, "FIX-103")["status"]["release/1.1"]
    commit = next(c for c in data["commits"] if c["sha"] == st["commits"][0])
    assert commit["subject"] == "driver: デバウンス時間の修正を 1.1 系へ移植"
    assert commit["fix_ids"] == ["FIX-103"]


def test_missing_severity(data):
    missing = violations(data, "fix_missing")
    assert {(v["refs"]["fix"], v["refs"]["series"], v["severity"]) for v in missing} == {
        ("FIX-102", "customer/gamma/1.2", "error"),
        ("FIX-105", "customer/gamma/1.2", "error"),
    }
    assert [v["refs"] for v in violations(data, "patch_id_only")] == [
        {"fix": "FIX-103", "series": "release/1.1"}]


def test_maintenance_series_missing_is_warning(sample_repo, config_dir):
    from release_collect.collect import Collector
    # FIX-102 の除外宣言を外すと、maintenance の release/1.1 は warning、active の beta は error
    (config_dir / "exclusions.toml").write_text("", encoding="utf-8")
    d = Collector(sample_repo, config_dir=config_dir).collect()
    sev = {v["refs"]["series"]: v["severity"] for v in violations(d, "fix_missing")
           if v["refs"]["fix"] == "FIX-102"}
    assert sev == {"release/1.1": "warning", "customer/beta/1.1": "error", "customer/gamma/1.2": "error"}
