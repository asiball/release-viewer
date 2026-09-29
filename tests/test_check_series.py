"""check --series: 終了コードの判定を指定した系列の違反に絞る（系列に紐づかない違反は常に対象）。"""

import json

import pytest

from release_viewer.__main__ import main


@pytest.mark.parametrize("series,code", [
    (["release/1.2"], 0),
    (["customer/gamma/1.2"], 1),  # FIX-102 / FIX-105 が未適用
    (["main", "release/1.2"], 0),
])
def test_series_filter(sample_repo, series, code):
    args = ["check", str(sample_repo)]
    for s in series:
        args += ["--series", s]
    assert main(args) == code


def test_unknown_series_exit_2(sample_repo, capsys):
    assert main(["check", str(sample_repo), "--series", "release/9.9"]) == 2
    assert "release/9.9" in capsys.readouterr().err


def test_summary_line_and_json_not_filtered(sample_repo, tmp_path, capsys):
    out = tmp_path / "d.json"
    assert main(["check", str(sample_repo), "--series", "customer/gamma/1.2", "-o", str(out)]) == 1
    assert capsys.readouterr().err.splitlines()[-1] == "errors=2 warnings=0 (of 4/1 total)"
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["summary"] == {"errors": 4, "warnings": 1}


def test_violations_without_series_always_count(sample_repo, config_dir):
    # 存在しない fix の除外宣言 → refs.series を持たない stale_exclusion（warning）
    # 未定義の系列を指す宣言 → refs.series が定義済みの系列でない stale_exclusion（warning）
    for exclude in ('fix = "FIX-999"\nseries = ["release/1.2"]', 'fix = "FIX-101"\nseries = ["release/9.9"]'):
        (config_dir / "exclusions.toml").write_text(f'[[exclude]]\n{exclude}\nreason = "x"\n', encoding="utf-8")
        args = ["check", str(sample_repo), "--config-dir", str(config_dir), "--series", "release/1.2"]
        assert main(args) == 0
        assert main(args + ["--strict"]) == 1
