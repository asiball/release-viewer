"""fix の単位の決定と、系列ごとの伝播判定（docs/conventions.md §5.3, §6, §7）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .config import Config
from .fixid import fix_sort_key
from .gitcmd import Git

if TYPE_CHECKING:
    from .collect import Commit, ResolvedSeries

# 伝播判定の方式。数字が大きいほど根拠が弱い（docs/conventions.md §6）
METHOD_RANK = {"ancestry": 0, "trailer": 1, "cherry_pick_x": 2, "patch_id": 3}


class FixEvaluator:
    """Collector の走査結果（コミット・系列・所属系列）から、fix ごとの伝播状態を判定する。

    判定後の fix_defs（fix の定義）と fix_member（コミット -> {fix: 対応する単位}）は、
    タグの fix 集計とグラフで使う。
    """

    def __init__(self, *, commits: dict[str, Commit], series: dict[str, ResolvedSeries], owner: dict[str, str],
                 git: Git, config: Config, snapshot: Callable[[str], dict[str, dict]],
                 violation: Callable[..., None]):
        self.commits = commits
        self.series = series
        self.owner = owner
        self.git = git
        self.config = config
        self.snapshot = snapshot
        self._violation = violation
        self._patch_ids: dict[str, str | None] = {}
        self._roots_memo: dict[str, frozenset[str]] = {}
        self.fix_defs: dict[str, dict] = {}
        self.fix_member: dict[str, dict[str, set[str]]] = {}

    def _roots(self, sha: str) -> frozenset[str]:
        """`-x` の記録を辿った起点コミット。辿れる記録がなければ自分自身。"""
        memo = self._roots_memo
        if sha in memo:
            return memo[sha]
        memo[sha] = frozenset({sha})  # 循環よけ
        known = [p for p in self.commits[sha].picked_from if p in self.commits and p != sha]
        result = frozenset().union(*(self._roots(p) for p in known)) if known else frozenset({sha})
        memo[sha] = result
        return result

    def evaluate(self) -> list[dict]:
        trailer_commits: dict[str, list[str]] = {}
        for c in self.commits.values():
            for fid in c.fix_ids:
                trailer_commits.setdefault(fid, []).append(c.sha)

        by_component: dict[str, list[str]] = {}
        for c in self.commits.values():
            if len(c.parents) <= 1:
                for comp in c.components:
                    by_component.setdefault(comp, []).append(c.sha)

        # 各 fix の単位（units）と、-x で単位に繋がるコミットを決める
        self.fix_defs: dict[str, dict] = {}
        for fid, shas in trailer_commits.items():
            shas.sort(key=lambda s: self.commits[s].seq)
            originals = [s for s in shas if self._roots(s) == frozenset({s})]
            origin = (originals or shas)[0]
            origin_series = self.owner.get(origin)
            units = [s for s in originals if self.owner.get(s) == origin_series] or [origin]
            unit_set = set(units)
            linked = {c.sha: self._roots(c.sha) & unit_set for c in self.commits.values()}
            linked = {s: u for s, u in linked.items() if u and s not in unit_set}
            comps = sorted({comp for u in units for comp in self.commits[u].components})
            self.fix_defs[fid] = {"units": units, "trailer": shas, "linked": linked,
                                  "components": comps, "origin": origin, "origin_series": origin_series}

        # -x で単位に繋がらないトレーラー付きコミット（loose）が、どの単位に当たるかを patch-id で決める。
        # links（コミット -> 対応する単位）を伝播判定（_match_unit）とタグの fix 集計（fix_member）の両方で使う
        self._compute_patch_ids({s for fd in self.fix_defs.values() if len(fd["units"]) > 1
                                 for s in fd["units"] + fd["trailer"]})
        for fid, fd in self.fix_defs.items():
            links = {u: {u} for u in fd["units"]} | {s: set(us) for s, us in fd["linked"].items()}
            for s in fd["trailer"]:
                if s in links:
                    continue
                us = self._trailer_units(fd, s)
                if us:
                    links[s] = set(us)
                else:
                    self._violation("trailer_unmatched", "warning",
                                    f"{fid} の Fix-ID 付きコミット {s[:10]} が fix の単位に対応付けられない"
                                    f"（差分が変わっている）", fix=fid, series=self.owner.get(s), commit=s)
            fd["links"] = links

        # 1st pass: patch-id 以外で判定。決まらなかった (系列, unit) の候補を集める
        pending: list[tuple[str, str, str, list[str]]] = []
        results: dict[tuple[str, str], dict[str, tuple[str, list[str]]]] = {}
        for fid, fd in self.fix_defs.items():
            for sid, rs in self.series.items():
                matched: dict[str, tuple[str, list[str]]] = {}
                for u in fd["units"]:
                    m = self._match_unit(fd, u, rs)
                    if m:
                        matched[u] = m
                    else:
                        known = set(fd["trailer"]) | set(fd["linked"]) | set(fd["units"])
                        cands = sorted({s for comp in self.commits[u].components for s in by_component.get(comp, [])
                                        if s in rs.reach and s not in known})
                        if not self.commits[u].components:
                            cands = []
                        pending.append((fid, sid, u, cands))
                results[(fid, sid)] = matched

        self._compute_patch_ids({u for _, _, u, c in pending if c} | {s for _, _, _, c in pending for s in c})

        for fid, sid, u, cands in pending:
            pid = self._patch_ids.get(u)
            hits = [s for s in cands if pid and self._patch_ids.get(s) == pid]
            if hits:
                results[(fid, sid)][u] = ("patch_id", hits)

        # 2nd pass: 状態を決める。fix_member は判定と同じ対応付け（links と patch-id の一致）から作る
        self.fix_member: dict[str, dict[str, set[str]]] = {}  # commit -> {fix: 対応する units}
        for fid, fd in self.fix_defs.items():
            for s, us in fd["links"].items():
                self._add_member(s, fid, us)
        for (fid, sid), matched in results.items():
            for u, (method, shas) in matched.items():
                for s in shas:
                    self._add_member(s, fid, {u})

        excl = self._exclusion_index()
        fixes = []
        for fid in sorted(self.fix_defs, key=fix_sort_key):
            fd = self.fix_defs[fid]
            status = {}
            for sid, rs in self.series.items():
                status[sid] = self._fix_state(fid, fd, rs, results[(fid, sid)], excl.get((fid, sid)))
            origin = self.commits[fd["origin"]]
            fixes.append({
                "id": fid,
                "title": origin.subject,
                "components": fd["components"],
                "origin": {"commit": fd["origin"], "series": fd["origin_series"]},
                "units": fd["units"],
                "status": status,
            })
        return fixes

    def _trailer_units(self, fd: dict, sha: str) -> list[str]:
        """トレーラーは持つが -x で単位に繋がらないコミットが、どの単位に当たるか（単位が複数なら patch-id で）。"""
        if len(fd["units"]) == 1:
            return list(fd["units"])
        pid = self._patch_ids.get(sha)
        return [u for u in fd["units"] if pid and self._patch_ids.get(u) == pid]

    def _match_unit(self, fd: dict, u: str, rs: ResolvedSeries) -> tuple[str, list[str]] | None:
        if u in rs.reach:
            return ("ancestry", [u])
        fid_commits = [s for s, us in fd["linked"].items() if u in us and s in rs.reach]
        with_trailer = [s for s in fid_commits if s in fd["trailer"]]
        if with_trailer:
            return ("trailer", sorted(with_trailer, key=lambda s: self.commits[s].seq))
        # -x で繋がらないトレーラー付きコミット（links で、単位が複数なら patch-id でこの単位に対応付いたもの）
        loose = [s for s, us in fd["links"].items()
                 if u in us and s in rs.reach and s not in fd["linked"] and s not in fd["units"]]
        if loose:
            return ("trailer", sorted(loose, key=lambda s: self.commits[s].seq))
        if fid_commits:
            return ("cherry_pick_x", sorted(fid_commits, key=lambda s: self.commits[s].seq))
        return None

    def _compute_patch_ids(self, shas: set[str]) -> None:
        todo = sorted(s for s in shas if s not in self._patch_ids)
        if not todo:
            return
        patch = self.git.run_bytes("log", "--no-walk=unsorted", "--stdin", "-p", "--no-renames",
                                   "--no-ext-diff", "--no-textconv", "--format=commit %H",
                                   input=("\n".join(todo) + "\n").encode())
        out = self.git.run_bytes("patch-id", "--stable", input=patch).decode()
        for s in todo:
            self._patch_ids[s] = None  # 空の差分は patch-id が出ない
        for line in out.splitlines():
            pid, sha = line.split()
            self._patch_ids[sha] = pid

    def _add_member(self, sha: str, fid: str, units: set[str]) -> None:
        if units:
            self.fix_member.setdefault(sha, {}).setdefault(fid, set()).update(units)

    def _exclusion_index(self) -> dict[tuple[str, str], dict]:
        idx = {}
        for e in self.config.exclusions:
            for sid in e.series:
                idx[(e.fix, sid)] = {"reason": e.reason, "by": e.by, "decided": e.decided}
        return idx

    def _fix_state(self, fid: str, fd: dict, rs: ResolvedSeries,
                   matched: dict[str, tuple[str, list[str]]], exclusion: dict | None) -> dict:
        sid = rs.cfg.id
        total = len(fd["units"])
        base = {"units_total": total, "units_matched": len(matched)} if total > 1 else {}
        if rs.cfg.status == "eol":
            return {"state": "not_applicable", "reason": "系列が eol", **base}
        head_components = set(self.snapshot(rs.head))
        if fd["components"] and not (set(fd["components"]) & head_components):
            return {"state": "not_applicable", "reason": "対象コンポーネントが系列に存在しない", **base}

        if len(matched) == total:
            method = max((m for m, _ in matched.values()), key=METHOD_RANK.__getitem__)
            commits = sorted({s for _, shas in matched.values() for s in shas}, key=lambda s: self.commits[s].seq)
            state = "patch_id_only" if method == "patch_id" else "applied"
            entry = {"state": state, "method": method, "commits": commits, **base}
            if exclusion:
                entry["exclusion"] = exclusion
                self._violation("exclusion_but_applied", "warning",
                                f"{fid} は {sid} で対象外と宣言されているが適用されている", fix=fid, series=sid)
            if state == "patch_id_only":
                self._violation("patch_id_only", "warning",
                                f"{fid} は {sid} に patch-id でのみ一致（トレーラー・-x の記録がない）",
                                fix=fid, series=sid)
            return entry

        if exclusion:
            return {"state": "excluded", "exclusion": exclusion, **base}
        severity = "error" if rs.cfg.status == "active" else "warning"
        partial = f"（{total} コミット中 {len(matched)} のみ適用）" if matched else ""
        self._violation("fix_missing", severity, f"{fid} が {sid} に未適用{partial}", fix=fid, series=sid)
        entry = {"state": "missing", **base}
        if matched:
            entry["commits"] = sorted({s for _, shas in matched.values() for s in shas},
                                      key=lambda s: self.commits[s].seq)
        return entry

    def check_exclusions(self, fixes: list[dict]) -> None:
        known = {f["id"] for f in fixes}
        for e in self.config.exclusions:
            if e.fix not in known:
                self._violation("stale_exclusion", "warning",
                                f"除外宣言の {e.fix} に該当する fix が見つからない", fix=e.fix)
            for sid in e.series:
                if sid not in self.series:
                    self._violation("stale_exclusion", "warning",
                                    f"{e.fix} の除外宣言にある系列 {sid} が定義されていない", fix=e.fix, series=sid)
