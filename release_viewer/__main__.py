"""release-viewer CLI。

サブコマンド:
    collect  走査して data.json / data.js（--site なら画面一式）を出力する
    check    collect と同じ走査をして、違反があれば終了コード 1（CIゲート用）

終了コード: 0 = 成功 / 1 = check で違反あり / 2 = 設定・引数・git のエラー、想定外の例外
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from importlib.resources import files
from pathlib import Path

from .collect import Collector
from .config import ConfigError
from .gitcmd import GitError

WEB_ASSETS = ("index.html", "app.js", "style.css")


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("repo", type=Path, help="走査するgitリポジトリ")
    common.add_argument("-o", "--output", type=Path,
                        help="出力先の data.json（同じ場所に data.js も書く）")
    common.add_argument("--site", type=Path, metavar="DIR",
                        help="DIR に data.json / data.js と画面（index.html, app.js, style.css）を揃えて出力する")
    common.add_argument("--config-ref", default="main", help="設定（.release/）を読むref（既定: main）")
    common.add_argument("--config-dir", type=Path,
                        help="設定（config.toml, exclusions.toml）をリポジトリではなくこのディレクトリから読む")
    common.add_argument("--remote", default="origin",
                        help="ローカルブランチがないとき参照するリモート名（既定: origin）")
    common.add_argument("--since-ref", help="このrefの祖先を走査しない（大規模リポジトリ向け）")
    common.add_argument("--name", help="表示用のリポジトリ名（既定: ディレクトリ名）")
    common.add_argument("--override", action="append", default=[], type=_override, metavar="SERIES=REF",
                        help="系列の HEAD をブランチではなく REF（HEAD や SHA も可）にして走査する。繰り返し可")

    ap = argparse.ArgumentParser(prog="release-viewer",
                                 description="リリース系列・hotfix伝播・依存制約を走査して可視化用JSONを出力する",
                                 epilog="終了コード: 0 = 成功 / 1 = check で違反あり / 2 = 設定・引数・git のエラー")
    sub = ap.add_subparsers(dest="command", required=True, metavar="{collect,check}")
    sub.add_parser("collect", parents=[common], help="走査して JSON を出力する（-o も --site もなければ標準出力）",
                   description="走査して data.json / data.js を出力する。-o も --site もなければ JSON を標準出力に書く")
    check = sub.add_parser("check", parents=[common], help="走査して、error 級の違反があれば終了コード 1",
                           description="collect と同じ走査をして、error 級の違反があれば終了コード 1。"
                                       "-o / --site を付ければ JSON も出力する")
    check.add_argument("--strict", action="store_true", help="warning も違反として扱う")
    return ap


def _override(text: str) -> tuple[str, str]:
    series, sep, ref = text.partition("=")
    if not (sep and series and ref):
        raise argparse.ArgumentTypeError(f"SERIES=REF の形で指定する: {text!r}")
    return series, ref


def main(argv: list[str] | None = None) -> int:
    # Windows ではコンソールの既定が cp932 になるため、日本語のメッセージを UTF-8 で書く
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = _build_parser()
    args = ap.parse_args(argv)
    if len({s for s, _ in args.override}) != len(args.override):
        ap.error("--override で同じ系列が複数回指定されている")
    try:
        return _run(args)
    except (ConfigError, GitError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 — 想定外の例外も終了コード 2 に揃える（traceback は出す）
        # 想定外の例外（git が見つからない等）も「違反あり」の 1 と区別できるよう 2 で終える
        traceback.print_exc()
        return 2


def _run(args: argparse.Namespace) -> int:
    data = Collector(args.repo, config_ref=args.config_ref, config_dir=args.config_dir,
                     remote=args.remote, since_ref=args.since_ref, name=args.name,
                     overrides=dict(args.override)).collect()

    text = json.dumps(data, ensure_ascii=False, indent=1)
    if args.output:
        _write_data(args.output, text)
    if args.site:
        _write_data(args.site / "data.json", text)
        web = files(__package__).joinpath("web")
        for name in WEB_ASSETS:
            (args.site / name).write_bytes(web.joinpath(name).read_bytes())
    if args.command == "collect" and not (args.output or args.site):
        print(text)

    if args.command == "check":
        failing = [v for v in data["violations"] if v["severity"] == "error" or args.strict]
        for v in failing:
            print(f"{v['severity'].upper()}: [{v['kind']}] {v['message']}", file=sys.stderr)
        s = data["summary"]
        print(f"errors={s['errors']} warnings={s['warnings']}", file=sys.stderr)
        return 1 if failing else 0
    return 0


def _write_data(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")
    # file:// で開いたページからも読めるよう、スクリプトとしても書き出す
    path.with_suffix(".js").write_text(f"window.RELEASE_DATA = {text};\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    sys.exit(main())
