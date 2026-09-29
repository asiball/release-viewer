"""--override <系列>=<ref>: 系列の HEAD を任意の ref に差し替えて走査する（PR のマージ後の評価用）。"""

import json

import pytest
from repobuilder import Repo

from release_viewer.__main__ import main

from .conftest import violations

CONFIG = """
[[series]]
branch = "main"
kind = "mainline"

[[series]]
branch = "release/1.0"
kind = "release"
parent = "main"
"""


def component(name: str, deps: str = "") -> str:
    return f'[component]\nname = "{name}"\nversion = "1.0.0"\n\n[dependencies]\n{deps}'


@pytest.fixture
def repo(tmp_path) -> Repo:
    """release/1.0 から作業ブランチ pr を切り、app の依存制約を壊すコミットを積んだリポジトリ。"""
    r = Repo(tmp_path / "repo")
    r.init()
    r.write(".release/config.toml", CONFIG)
    r.write("components/hal/component.toml", component("hal"))
    r.write("components/app/component.toml", component("app", 'hal = ">=1.0.0"\n'))
    r.commit("chore: 初期インポート", "sato")
    r.checkout("release/1.0", new_from="main")
    r.checkout("pr", new_from="release/1.0")
    r.write("components/app/component.toml", component("app", 'hal = ">=2.0.0"\n'))
    r.commit("app: hal 2.0 を要求する", "tanaka")
    return r


def run(r: Repo, tmp_path, *args: str) -> tuple[int, dict | None]:
    out = tmp_path / "data.json"
    code = main(["collect", str(r.path), "-o", str(out), *args])
    return code, json.loads(out.read_text(encoding="utf-8")) if code == 0 else None


def test_without_override(repo, tmp_path):
    code, d = run(repo, tmp_path)
    assert code == 0
    assert not violations(d, "dependency_violation")
    assert d["repository"]["overrides"] == {}
    assert d["series"][1]["ref"] == "refs/heads/release/1.0"


def test_override_with_sha(repo, tmp_path):
    sha = repo.rev("pr")
    code, d = run(repo, tmp_path, "--override", f"release/1.0={sha}")
    assert code == 0
    assert [(v["refs"]["series"], v["refs"]["ref"]) for v in violations(d, "dependency_violation")] == [
        ("release/1.0", "release/1.0")]
    s = d["series"][1]
    assert (s["ref"], s["head"]) == (sha, sha)
    assert d["repository"]["overrides"] == {"release/1.0": sha}
    head_check = next(c for c in d["dependency_checks"] if c["series"] == "release/1.0" and c["at"]["kind"] == "head")
    assert head_check["at"] == {"kind": "head", "ref": "release/1.0", "commit": sha}


def test_override_with_head(repo, tmp_path):
    """CI の pull_request では refs/pull/N/merge が detached HEAD として checkout される。"""
    repo.git("checkout", "-q", "--detach", "pr")
    code, d = run(repo, tmp_path, "--override", "release/1.0=HEAD")
    assert code == 0
    assert (d["series"][1]["ref"], d["series"][1]["head"]) == ("HEAD", repo.rev("pr"))
    assert len(violations(d, "dependency_violation")) == 1


def test_override_missing_equals(repo, tmp_path):
    with pytest.raises(SystemExit) as e:
        run(repo, tmp_path, "--override", "release/1.0")
    assert e.value.code == 2


@pytest.mark.parametrize("spec", ["release/9.9=HEAD", "release/1.0=no-such-ref"])
def test_override_invalid(repo, tmp_path, spec, capsys):
    assert run(repo, tmp_path, "--override", spec)[0] == 2
    assert "--override" in capsys.readouterr().err
