from release_collect.deps import check_dependencies

from .conftest import tag, violations


def test_release_tag_snapshot_and_diff(data):
    t = tag(data, "fw-acme/v1.2.1")
    assert t["kind"] == "release"
    assert t["series"] == "customer/acme/1.2"
    assert t["snapshot"] == {"app": "1.2.0", "driver": "1.1.0", "hal": "1.2.1+acme.1", "lib-comm": "1.1.1"}
    assert t["previous"] == "fw-acme/v1.2.0"
    assert t["diff"] == [
        {"component": "hal", "from": "1.2.0+acme.1", "to": "1.2.1+acme.1"},
        {"component": "lib-comm", "from": "1.1.0", "to": "1.1.1"},
    ]
    assert t["fixes_added"] == ["FIX-101", "FIX-102", "FIX-105"]


def test_first_customer_release_falls_back_to_base_release(data):
    assert tag(data, "fw-acme/v1.2.0")["previous"] == "fw/v1.2.0"


def test_fixes_included_accumulate(data):
    t = tag(data, "fw/v1.2.2")
    assert t["fixes_added"] == ["FIX-103", "FIX-104"]
    assert t["fixes_included"] == ["FIX-101", "FIX-102", "FIX-103", "FIX-104", "FIX-105"]


def test_partial_multi_commit_fix_not_included(data):
    assert "FIX-105" not in tag(data, "fw-gamma/v1.2.1")["fixes_included"]


def test_patch_id_matched_fix_is_included(data):
    assert "FIX-103" in tag(data, "fw/v1.1.1")["fixes_added"]


def test_component_tag(data):
    t = tag(data, "hal/v1.2.1+acme.1")
    assert (t["kind"], t["component"], t["version"]) == ("component", "hal", "1.2.1+acme.1")
    assert t["previous"] == "hal/v1.2.1"
    assert t["fixes_included"] == ["FIX-101"]  # hal の fix だけ
    assert not violations(data, "tag_version_mismatch")


def test_series_head_snapshot(data):
    s = {x["id"]: x for x in data["series"]}
    assert s["customer/acme/1.2"]["head_snapshot"]["hal"] == "1.2.1+acme.1"
    assert s["release/1.2"]["fork_point"] == tag(data, "app/v1.2.0")["commit"]
    assert s["customer/acme/1.2"]["fork_point"] == tag(data, "fw/v1.2.0")["commit"]


def test_dependency_violation_on_beta(data):
    bad = [c for c in data["dependency_checks"] if not c["ok"]]
    assert {(c["series"], c["at"]["ref"]) for c in bad} == {
        ("customer/beta/1.1", "customer/beta/1.1"),
        ("customer/beta/1.1", "fw-beta/v1.1.1"),
    }
    r = next(r for r in bad[0]["results"] if not r["ok"])
    assert (r["component"], r["dependency"], r["actual"]) == ("app", "hal", "1.1.0")
    assert len(violations(data, "dependency_violation")) == 2


def test_dependency_checks_cover_heads_and_release_tags(data):
    refs = {(c["series"], c["at"]["kind"], c["at"]["ref"]) for c in data["dependency_checks"]}
    assert ("release/1.2", "head", "release/1.2") in refs
    assert ("release/1.2", "tag", "fw/v1.2.2") in refs
    assert ("customer/gamma/1.2", "tag", "fw-gamma/v1.2.0") in refs


def meta(version, **deps):
    return {"version": version, "dependencies": deps, "error": None}


def test_check_dependencies_unit():
    res = check_dependencies({
        "app": meta("1.0.0", hal=">=1.0.0", missing=">=1.0.0", odd="^1.0.0"),
        "hal": meta("1.0.0"),
    })
    by_dep = {r["dependency"]: r for r in res}
    assert by_dep["hal"]["ok"] is True
    assert by_dep["missing"]["ok"] is False and "存在しない" in by_dep["missing"]["error"]
    assert by_dep["odd"]["ok"] is False and "制約式" in by_dep["odd"]["error"]
