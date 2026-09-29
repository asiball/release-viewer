import subprocess
import sys
from pathlib import Path

import pytest

from release_viewer.collect import Collector

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "sample" / "generate_sample_repo.py"

# テストから sample/repobuilder.py（Repo）を import できるようにする
sys.path.insert(0, str(ROOT / "sample"))


def generate(out: Path) -> Path:
    subprocess.run([sys.executable, str(GENERATOR), str(out)], check=True, capture_output=True)
    return out


@pytest.fixture(scope="session")
def sample_repo(tmp_path_factory) -> Path:
    return generate(tmp_path_factory.mktemp("sample") / "sample-fw")


@pytest.fixture(scope="session")
def data(sample_repo) -> dict:
    return Collector(sample_repo).collect()


@pytest.fixture
def config_dir(sample_repo, tmp_path) -> Path:
    """サンプルの main にある .release/ をコピーした、書き換え可能な設定ディレクトリ。"""
    d = tmp_path / "release-config"
    d.mkdir()
    for name in ("config.toml", "exclusions.toml"):
        text = subprocess.run(["git", "-C", str(sample_repo), "show", f"main:.release/{name}"],
                              check=True, capture_output=True, encoding="utf-8").stdout
        (d / name).write_text(text, encoding="utf-8")
    return d


def fix(data: dict, fid: str) -> dict:
    return next(f for f in data["fixes"] if f["id"] == fid)


def tag(data: dict, name: str) -> dict:
    return next(t for t in data["tags"] if t["name"] == name)


def violations(data: dict, kind: str) -> list[dict]:
    return [v for v in data["violations"] if v["kind"] == kind]
