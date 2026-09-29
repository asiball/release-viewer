"""release-collect CLI。

終了コード: 0 = 違反なし / 1 = --check で違反あり / 2 = 設定・引数・git のエラー
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .collect import Collector
from .config import ConfigError
from .gitcmd import GitError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="release-collect",
                                 description="リリース系列・hotfix伝播・依存制約を走査してJSONを出力する")
    ap.add_argument("repo", type=Path, help="走査するgitリポジトリ")
    ap.add_argument("-o", "--output", type=Path,
                    help="出力先の data.json（同じ場所に data.js も書く）。省略時は標準出力")
    ap.add_argument("--config-ref", default="main", help="設定（.release/）を読むref（既定: main）")
    ap.add_argument("--config-dir", type=Path, help="設定をリポジトリではなくこのディレクトリから読む")
    ap.add_argument("--remote", default="origin",
                    help="ローカルブランチがないとき参照するリモート名（既定: origin）")
    ap.add_argument("--since-ref", help="このrefの祖先を走査しない（大規模リポジトリ向け）")
    ap.add_argument("--name", help="表示用のリポジトリ名（既定: ディレクトリ名）")
    ap.add_argument("--check", action="store_true", help="error 級の違反があれば終了コード 1")
    ap.add_argument("--strict", action="store_true", help="--check で warning も違反として扱う")
    args = ap.parse_args(argv)

    try:
        data = Collector(args.repo, config_ref=args.config_ref, config_dir=args.config_dir,
                         remote=args.remote, since_ref=args.since_ref, name=args.name).collect()
    except (ConfigError, GitError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    text = json.dumps(data, ensure_ascii=False, indent=1)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8", newline="\n")
        # file:// で開いたページからも読めるよう、スクリプトとしても書き出す
        args.output.with_suffix(".js").write_text(
            f"window.RELEASE_DATA = {text};\n", encoding="utf-8", newline="\n")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)

    violations = data["violations"]
    if args.check:
        failing = [v for v in violations if v["severity"] == "error" or args.strict]
        for v in failing:
            print(f"{v['severity'].upper()}: [{v['kind']}] {v['message']}", file=sys.stderr)
        s = data["summary"]
        print(f"errors={s['errors']} warnings={s['warnings']}", file=sys.stderr)
        return 1 if failing else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
