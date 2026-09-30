"""コミットメッセージから Fix-ID を取り出す（docs/conventions.md §5.1）。"""

from __future__ import annotations

import re


def trailer_values(raw: str) -> list[str]:
    """git の `%(trailers:key=<名前>,valueonly,separator=%x1d)` の出力を、値の並びにする。"""
    return [v for t in raw.split("\x1d") for v in split_values(t)]


def line_values(message: str, key: str) -> list[str]:
    """メッセージ全文を行ごとに見て、`<key>: <値>` の行（キーは大小無視）の値を返す。

    git のトレーラー規則（空行で区切った最終段落の全行がトレーラー）に合わない位置の行も拾う。
    """
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
    return [v for m in pattern.finditer(message) for v in split_values(m.group(1))]


def split_values(text: str) -> list[str]:
    """1行の値はカンマ・空白区切りで複数可。"""
    return [v for v in re.split(r"[,\s]+", text) if v]


def parse_fix_ids(trailers: str, message: str, key: str,
                  pattern: re.Pattern) -> tuple[list[str], list[str], list[str]]:
    """トレーラーの値と、メッセージの行走査で取れた値を合わせて Fix-ID を決める。

    trailers: git の %(trailers) の出力。message: メッセージ全文（%B）。
    戻り値: (Fix-ID, 書式に合わない値, トレーラーとしては認識されない位置にあった Fix-ID)
    """
    from_trailer = trailer_values(trailers)
    fix_ids, invalid = [], []
    for value in dict.fromkeys(from_trailer + line_values(message, key)):
        (fix_ids if pattern.match(value) else invalid).append(value)
    not_trailer = [v for v in fix_ids if v not in from_trailer]
    return fix_ids, invalid, not_trailer


def fix_sort_key(fid: str):
    prefix, _, num = fid.rpartition("-")
    return (prefix, int(num) if num.isdigit() else 0, fid)
