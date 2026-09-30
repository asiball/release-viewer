"""コンポーネント間の依存制約チェック（docs/conventions.md §8）。"""

from __future__ import annotations

from .semver import Constraint, Version, VersionError


def check_dependencies(snapshot: dict[str, dict]) -> list[dict]:
    """ある時点の全コンポーネントのメタデータから、依存制約ごとの充足結果を返す。

    snapshot: {component: {"version": str|None, "dependencies": {dep: constraint}, "error": str|None}}
    """
    results = []
    for comp in sorted(snapshot):
        for dep, text in sorted(snapshot[comp]["dependencies"].items()):
            r = {"component": comp, "dependency": dep, "constraint": text, "actual": None, "ok": False}
            try:
                constraint = Constraint.parse(text)
            except VersionError as e:
                r["error"] = f"制約式が不正: {e}"
                results.append(r)
                continue
            target = snapshot.get(dep)
            if target is None:
                r["error"] = f"依存先 {dep} が存在しない"
            else:
                r["actual"] = target["version"]
                try:
                    r["ok"] = constraint.satisfied_by(Version.parse(target["version"] or ""))
                except VersionError as e:
                    r["error"] = f"依存先のバージョンが不正: {e}"
            results.append(r)
    return results
