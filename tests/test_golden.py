"""サンプルリポジトリから生成した data.json が tests/golden/data.json と完全一致することを確認する。

出力を意図して変えたときは `UPDATE_GOLDEN=1 python -m pytest tests/test_golden.py` で更新し、差分をレビューする。
"""

import os
from pathlib import Path

from release_viewer.__main__ import main

GOLDEN = Path(__file__).resolve().parent / "golden" / "data.json"


def test_golden(sample_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1790000000")
    out = tmp_path / "data.json"
    assert main(["collect", str(sample_repo), "-o", str(out), "--name", "sample-fw"]) == 0
    actual = out.read_text(encoding="utf-8")
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(actual, encoding="utf-8", newline="\n")
    # read_text は改行を正規化するので、Windows の checkout（autocrlf）でも比較できる
    assert actual == GOLDEN.read_text(encoding="utf-8")
