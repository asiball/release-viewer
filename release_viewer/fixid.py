"""コミットメッセージから Fix-ID を取り出す（docs/conventions.md §5.1）。"""

from __future__ import annotations


def trailer_values(raw: str) -> list[str]:
    """git の `%(trailers:key=<名前>,valueonly,separator=%x1d)` の出力を、値の並びにする。"""
    return [v for v in (v.strip() for v in raw.split("\x1d")) if v]


def fix_sort_key(fid: str):
    prefix, _, num = fid.rpartition("-")
    return (prefix, int(num) if num.isdigit() else 0, fid)
