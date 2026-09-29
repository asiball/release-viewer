"""semver 2.0.0 の解析・比較と、依存制約式（docs/conventions.md §8）の評価。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_OPS = (">=", "<=", "==", "!=", ">", "<")


class VersionError(ValueError):
    pass


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...]
    build: str | None
    raw: str

    @classmethod
    def parse(cls, text: str) -> Version:
        m = _SEMVER.match(text)
        if not m:
            raise VersionError(f"semver ではない: {text!r}")
        pre = tuple(m.group(4).split(".")) if m.group(4) else ()
        return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)), pre, m.group(5), text)

    def key(self) -> tuple:
        """優先順位の比較キー。ビルドメタデータは無視する（semver §10）。"""
        if not self.prerelease:
            pre: tuple = (1,)
        else:
            pre = (0, tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in self.prerelease))
        return (self.major, self.minor, self.patch, pre)

    def __str__(self) -> str:
        return self.raw


def compare(a: Version, b: Version) -> int:
    ka, kb = a.key(), b.key()
    return (ka > kb) - (ka < kb)


@dataclass(frozen=True)
class Constraint:
    """`>=1.2.0, <2.0.0` のような比較のAND。"""
    terms: tuple[tuple[str, Version], ...]
    raw: str

    @classmethod
    def parse(cls, text: str) -> Constraint:
        terms = []
        for part in text.split(","):
            part = part.strip()
            op = next((o for o in _OPS if part.startswith(o)), None)
            if op is None:
                raise VersionError(f"制約式の演算子が不正: {part!r}（使えるのは {' '.join(_OPS)}）")
            terms.append((op, Version.parse(part[len(op):].strip())))
        if not terms:
            raise VersionError(f"制約式が空: {text!r}")
        return cls(tuple(terms), text)

    def satisfied_by(self, v: Version) -> bool:
        for op, bound in self.terms:
            c = compare(v, bound)
            ok = {">=": c >= 0, "<=": c <= 0, ">": c > 0, "<": c < 0, "==": c == 0, "!=": c != 0}[op]
            if not ok:
                return False
        return True
