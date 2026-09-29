"""リポジトリを走査して可視化用JSON（docs/conventions.md §11）を組み立てる。

走査対象は config.toml の系列のブランチと、`[repository].tag_pattern`（既定 `<prefix>/v<version>`）に合うタグだけ。
"""

from __future__ import annotations

import heapq
import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .config import ConfigError, Series, load_config
from .deps import check_dependencies
from .gitcmd import Git
from .semver import Version, VersionError

SCHEMA_VERSION = "1.0"

CHERRY_RE = re.compile(r"^\(cherry picked from commit ([0-9a-f]{40})\)\s*$", re.MULTILINE)

# 伝播判定の方式。数字が大きいほど根拠が弱い（docs/conventions.md §6）
METHOD_RANK = {"ancestry": 0, "trailer": 1, "cherry_pick_x": 2, "patch_id": 3}

# {trailer} は [repository].fix_id_trailer
_LOG_FORMAT = "%x1e%H%x1f%P%x1f%an%x1f%cI%x1f%s%x1f%(trailers:key={trailer},valueonly,separator=%x1d)%x1f%B%x1f"


@dataclass
class Commit:
    sha: str
    parents: list[str]
    author: str
    date: str
    subject: str
    fix_ids: list[str]
    picked_from: list[str]
    components: list[str]
    seq: int = 0


@dataclass
class ResolvedSeries:
    cfg: Series
    ref: str
    head: str
    depth: int
    fork_point: str | None = None
    chain: list[str] = field(default_factory=list)  # 自系列の first-parent 上のコミット（新しい順）
    reach: set[str] = field(default_factory=set)


class Collector:
    def __init__(self, repo: Path | str, *, config_ref: str = "main", config_dir: Path | None = None,
                 remote: str = "origin", since_ref: str | None = None, name: str | None = None):
        self.repo = Path(repo)
        self.git = Git(repo)
        self.config_ref = config_ref
        self.config_dir = config_dir
        self.remote = remote
        self.since_ref = since_ref
        self.name = name or self.repo.resolve().name
        self.violations: list[dict] = []
        self._snapshot_cache: dict[str, dict[str, dict]] = {}
        self._patch_ids: dict[str, str | None] = {}

    # ------------------------------------------------------------------ 入口
    def collect(self) -> dict:
        config_commit = None
        if self.config_dir is None:
            ref = self.git.resolve_branch(self.config_ref, self.remote) or self.config_ref
            config_commit = self.git.resolve_commit(ref)
            if config_commit is None:
                raise ConfigError(f"設定参照ref {self.config_ref} が見つからない")
            config = load_config(self.git, config_commit, None)
        else:
            config = load_config(self.git, None, self.config_dir)
        self.config = config
        self.violations.extend(config.problems)

        self.exclude: list[str] = []
        if self.since_ref:
            since = self.git.resolve_commit(self.since_ref)
            if since is None:
                raise ConfigError(f"--since-ref {self.since_ref} が見つからない")
            self.exclude = [f"^{since}"]

        self._resolve_series()
        self._scan_commits()
        self._assign_owners()
        self._read_tags()
        fixes = self._evaluate_fixes()
        tags = self._build_tags()
        dep_checks = self._check_dependencies(tags)
        self._check_exclusions(fixes)
        return self._assemble(config_commit, fixes, tags, dep_checks)

    # ------------------------------------------------------------------ 系列
    def _resolve_series(self) -> None:
        by_id = self.config.series_by_id()
        self.series: dict[str, ResolvedSeries] = {}
        for s in self.config.series:
            ref = self.git.resolve_branch(s.branch, self.remote)
            if ref is None:
                raise ConfigError(f"系列のブランチが見つからない: {s.branch}")
            depth, cur = 0, s
            while cur.parent is not None:
                depth += 1
                cur = by_id[cur.parent]
            self.series[s.id] = ResolvedSeries(cfg=s, ref=ref, head=self.git.resolve_commit(ref), depth=depth)

        for rs in self.series.values():
            rs.reach = set(self.git.rev_list(rs.ref, *self.exclude))
            if rs.cfg.parent is None:
                rs.chain = self.git.rev_list("--first-parent", rs.ref, *self.exclude)
            else:
                parent = self.series[rs.cfg.parent]
                rs.chain = self.git.rev_list("--first-parent", rs.ref, f"^{parent.ref}", *self.exclude)

    # ------------------------------------------------------------------ コミット
    def _scan_commits(self) -> None:
        repo = self.config.repository
        comp_prefix = f"{repo.component_dir}/"
        refs = [rs.ref for rs in self.series.values()]
        out = self.git.run("log", "--date-order", "--name-only", "--no-renames",
                           f"--format={_LOG_FORMAT.format(trailer=repo.fix_id_trailer)}", *refs, *self.exclude)
        records = [r for r in out.split("\x1e") if r.strip()]
        self.commits: dict[str, Commit] = {}
        for rec in records:
            f = rec.split("\x1f")
            sha, parents, author, date, subject, trailers, body, paths = f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7]
            fix_ids = []
            for value in (v.strip() for v in trailers.split("\x1d")):
                if not value:
                    continue
                if repo.fix_id_pattern.match(value):
                    fix_ids.append(value)
                else:
                    self._violation("invalid_fix_id", "warning",
                                    f"{sha[:10]} の {repo.fix_id_trailer} が書式に合わない: {value!r}", commit=sha)
            comps = sorted({p[len(comp_prefix):].split("/")[0] for p in paths.split("\n")
                            if p.startswith(comp_prefix) and "/" in p[len(comp_prefix):]})
            self.commits[sha] = Commit(
                sha=sha, parents=parents.split(), author=author, date=date, subject=subject,
                fix_ids=sorted(set(fix_ids)), picked_from=CHERRY_RE.findall(body), components=comps)
        n = len(records)
        for i, sha in enumerate(self.commits):
            self.commits[sha].seq = n - 1 - i  # 古いほど小さい。祖先は必ず子孫より小さい

        for rs in self.series.values():
            if rs.cfg.parent is None:
                rs.fork_point = None
            elif rs.chain:
                oldest = self.commits[rs.chain[-1]]
                rs.fork_point = oldest.parents[0] if oldest.parents else None
            else:
                rs.fork_point = rs.head  # 親から分岐後、固有のコミットがまだない

    def _assign_owners(self) -> None:
        """コミットの所属系列 = そのコミットを first-parent に持つ系列。なければ最も根に近い包含系列。"""
        self.owner: dict[str, str] = {}
        for rs in sorted(self.series.values(), key=lambda r: r.depth):
            for sha in rs.chain:
                self.owner.setdefault(sha, rs.cfg.id)
        ordered = sorted(self.series.values(), key=lambda r: r.depth)
        for sha in self.commits:
            if sha not in self.owner:
                for rs in ordered:
                    if sha in rs.reach:
                        self.owner[sha] = rs.cfg.id
                        break

    # ------------------------------------------------------------------ スナップショット
    def snapshot(self, commit: str) -> dict[str, dict]:
        """コミット時点の全コンポーネントのメタデータ {name: {version, dependencies, error}}"""
        if commit in self._snapshot_cache:
            return self._snapshot_cache[commit]
        repo = self.config.repository
        comp_prefix = f"{repo.component_dir}/"
        listing = self.git.run("ls-tree", "--name-only", commit, comp_prefix, check=False)
        names = sorted(line[len(comp_prefix):] for line in listing.splitlines() if line.startswith(comp_prefix))
        specs = [f"{commit}:{comp_prefix}{n}/{repo.meta_file}" for n in names]
        files = self.git.cat_files(specs)
        snap: dict[str, dict] = {}
        for name, spec in zip(names, specs):
            raw = files.get(spec)
            if raw is None:
                continue  # メタファイルがないディレクトリはコンポーネントとみなさない
            snap[name] = _parse_component_meta(name, raw)
        self._snapshot_cache[commit] = snap
        return snap

    def _report_meta_errors(self, snap: dict[str, dict], where: str, **refs) -> None:
        for name, meta in snap.items():
            if meta["error"]:
                self._violation("invalid_component_meta", "warning",
                                f"{where}: {name}/{self.config.repository.meta_file}: {meta['error']}",
                                component=name, **refs)

    # ------------------------------------------------------------------ タグ
    def _read_tags(self) -> None:
        out = self.git.run("for-each-ref", "refs/tags",
                           "--format=%(refname:strip=2)%1f%(objecttype)%1f%(objectname)%1f%(*objectname)%1f%(creatordate:iso-strict)")
        prefixes = {s.tag_prefix for s in self.config.series if s.tag_prefix}
        self.raw_tags: list[dict] = []
        component_names = set()
        for rs in self.series.values():
            component_names |= set(self.snapshot(rs.head))
        for line in out.splitlines():
            name, otype, oid, peeled, date = line.split("\x1f")
            m = self.config.repository.tag_pattern.match(name)
            if not m:
                continue
            prefix, ver = m.group("prefix"), m.group("version")
            if prefix + "/" in prefixes:
                kind, component = "release", None
            elif prefix in component_names:
                kind, component = "component", prefix
            else:
                continue
            commit = peeled or oid
            if otype == "tag" and not peeled:
                continue  # コミット以外を指す注釈付きタグ
            try:
                Version.parse(ver)
            except VersionError as e:
                self._violation("invalid_tag", "warning", f"タグ {name}: {e}", tag=name)
                continue
            if otype == "commit":
                self._violation("lightweight_tag", "warning", f"タグ {name} が軽量タグ", tag=name)
            self.raw_tags.append({"name": name, "kind": kind, "component": component, "prefix": prefix,
                                  "version": ver, "commit": commit, "annotated": otype == "tag",
                                  "date": date})

        # 走査範囲内のタグは commit の seq 順、範囲外は末尾
        self.raw_tags.sort(key=lambda t: (self.commits[t["commit"]].seq if t["commit"] in self.commits else -1,
                                          t["name"]))
        self.tags_at: dict[str, list[dict]] = {}
        for t in self.raw_tags:
            self.tags_at.setdefault(t["commit"], []).append(t)

    def _previous_tag(self, tag: dict) -> dict | None:
        """祖先方向で最も近い同種タグ。リリースタグは同じ prefix を優先し、なければ任意のリリースタグ。"""
        start = self.commits.get(tag["commit"])
        if start is None:
            return None

        def same(t):
            if tag["kind"] == "component":
                return t["kind"] == "component" and t["component"] == tag["component"]
            return t["kind"] == "release" and t["prefix"] == tag["prefix"]

        def any_release(t):
            return tag["kind"] == "release" and t["kind"] == "release"

        heap = [(-self.commits[p].seq, p) for p in start.parents if p in self.commits]
        heapq.heapify(heap)
        seen = {p for _, p in heap}
        fallback = None
        while heap:
            _, sha = heapq.heappop(heap)
            here = self.tags_at.get(sha, [])
            hit = [t for t in here if same(t)]
            if hit:
                return max(hit, key=lambda t: Version.parse(t["version"]).key())
            if fallback is None:
                alt = [t for t in here if any_release(t)]
                if alt:
                    fallback = max(alt, key=lambda t: Version.parse(t["version"]).key())
            for p in self.commits[sha].parents:
                if p in self.commits and p not in seen:
                    seen.add(p)
                    heapq.heappush(heap, (-self.commits[p].seq, p))
        return fallback

    # ------------------------------------------------------------------ fix
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

    def _evaluate_fixes(self) -> list[dict]:
        self._roots_memo: dict[str, frozenset[str]] = {}
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

        need = {u for _, _, u, c in pending if c} | {s for _, _, _, c in pending for s in c}
        # trailer 付きで対応が取れないコミット（複数単位の fix）の照合にも patch-id を使う
        for fid, fd in self.fix_defs.items():
            if len(fd["units"]) > 1:
                need |= set(fd["units"]) | set(fd["trailer"])
        self._compute_patch_ids(need)

        for fid, sid, u, cands in pending:
            pid = self._patch_ids.get(u)
            hits = [s for s in cands if pid and self._patch_ids.get(s) == pid]
            if hits:
                results[(fid, sid)][u] = ("patch_id", hits)

        # 2nd pass: 状態を決める
        self.fix_member: dict[str, dict[str, set[str]]] = {}  # commit -> {fix: 対応する units}
        for fid, fd in self.fix_defs.items():
            for u in fd["units"]:
                self._add_member(u, fid, {u})
            for s, us in fd["linked"].items():
                self._add_member(s, fid, set(us))
            for s in fd["trailer"]:
                if s not in fd["units"] and s not in fd["linked"]:
                    self._add_member(s, fid, set(self._trailer_units(fd, s)))
        for (fid, sid), matched in results.items():
            for u, (method, shas) in matched.items():
                for s in shas:
                    self._add_member(s, fid, {u})

        excl = self._exclusion_index()
        fixes = []
        for fid in sorted(self.fix_defs, key=_fix_sort_key):
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
        """トレーラーは持つが -x で単位に繋がらないコミットが、どの単位に当たるか。"""
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
        # -x で繋がらないトレーラー付きコミット（単位が1つならそれで足りる）
        loose = [s for s in fd["trailer"] if s in rs.reach and s not in fd["linked"] and s not in fd["units"]]
        if loose and len(fd["units"]) == 1:
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

    def _check_exclusions(self, fixes: list[dict]) -> None:
        known = {f["id"] for f in fixes}
        for e in self.config.exclusions:
            if e.fix not in known:
                self._violation("stale_exclusion", "warning",
                                f"除外宣言の {e.fix} に該当する fix が見つからない", fix=e.fix)
            for sid in e.series:
                if sid not in self.series:
                    self._violation("stale_exclusion", "warning",
                                    f"{e.fix} の除外宣言にある系列 {sid} が定義されていない", fix=e.fix, series=sid)

    # ------------------------------------------------------------------ タグ詳細
    def _build_tags(self) -> list[dict]:
        units_at: dict[str, dict[str, set[str]]] = {}
        out = []
        for t in self.raw_tags:
            prev = self._previous_tag(t)
            snap = self.snapshot(t["commit"])
            self._report_meta_errors(snap, f"タグ {t['name']}", tag=t["name"])
            versions = {n: m["version"] for n, m in snap.items()}

            if t["kind"] == "component":
                actual = versions.get(t["component"])
                if actual != t["version"]:
                    self._violation("tag_version_mismatch", "warning",
                                    f"タグ {t['name']} と {self.config.repository.meta_file} の version"
                                    f"（{actual}）が一致しない",
                                    tag=t["name"], component=t["component"])

            # 含まれる fix: 前版の集合 + 前版からの範囲に入ったコミット
            if t["commit"] in self.commits:
                if prev is not None and prev["commit"] in self.commits:
                    base = {f: set(u) for f, u in units_at.get(prev["name"], {}).items()}
                    rng = self.git.rev_list(t["commit"], f"^{prev['commit']}", *self.exclude)
                else:
                    base = {}
                    rng = self.git.rev_list(t["commit"], *self.exclude)
                cur = {f: set(u) for f, u in base.items()}
                for sha in rng:
                    for fid, us in self.fix_member.get(sha, {}).items():
                        cur.setdefault(fid, set()).update(us)
                units_at[t["name"]] = cur
                full = lambda d: {f for f, us in d.items() if us >= set(self.fix_defs[f]["units"])}
                included, before = full(cur), full(base)
            else:
                included, before = set(), set()
            if t["kind"] == "component":
                relevant = lambda f, comp=t["component"]: comp in self.fix_defs[f]["components"]
                included = {f for f in included if relevant(f)}
                before = {f for f in before if relevant(f)}

            prev_versions = ({n: m["version"] for n, m in self.snapshot(prev["commit"]).items()}
                             if prev else {})
            diff = []
            if prev is not None:
                for n in sorted(set(versions) | set(prev_versions)):
                    if versions.get(n) != prev_versions.get(n):
                        diff.append({"component": n, "from": prev_versions.get(n), "to": versions.get(n)})

            out.append({
                "name": t["name"], "kind": t["kind"], "component": t["component"],
                "version": t["version"], "commit": t["commit"],
                "series": self.owner.get(t["commit"]), "annotated": t["annotated"], "date": t["date"],
                "snapshot": versions,
                "previous": prev["name"] if prev else None,
                "diff": diff,
                "fixes_added": sorted(included - before, key=_fix_sort_key),
                "fixes_included": sorted(included, key=_fix_sort_key),
            })
        return out

    # ------------------------------------------------------------------ 依存
    def _check_dependencies(self, tags: list[dict]) -> list[dict]:
        checks = []
        for sid, rs in self.series.items():
            if rs.cfg.status == "eol":
                continue
            points = [{"kind": "head", "ref": rs.cfg.branch, "commit": rs.head}]
            points += [{"kind": "tag", "ref": t["name"], "commit": t["commit"]}
                       for t in tags if t["kind"] == "release" and t["series"] == sid]
            for at in points:
                snap = self.snapshot(at["commit"])
                if at["kind"] == "head":
                    self._report_meta_errors(snap, f"{sid} HEAD", series=sid)
                results = check_dependencies(snap)
                for r in results:
                    if not r["ok"]:
                        detail = r.get("error") or f"実際は {r['actual']}"
                        self._violation("dependency_violation", "error",
                                        f"{sid} @ {at['ref']}: {r['component']} は {r['dependency']} "
                                        f"{r['constraint']} を要求（{detail}）",
                                        series=sid, component=r["component"], ref=at["ref"])
                checks.append({"series": sid, "at": at, "results": results,
                               "ok": all(r["ok"] for r in results)})
        return checks

    # ------------------------------------------------------------------ グラフ
    def _graph(self, fixes: list[dict], tags: list[dict]) -> tuple[list[dict], list[dict]]:
        roles: dict[str, set[str]] = {}

        def mark(sha, role):
            if sha and sha in self.commits:
                roles.setdefault(sha, set()).add(role)

        for t in tags:
            mark(t["commit"], "tag")
        for sha in self.fix_member:
            mark(sha, "fix")
        for rs in self.series.values():
            mark(rs.head, "head")
            mark(rs.fork_point, "fork_point")
            for sha in rs.chain:
                c = self.commits[sha]
                if any(self.owner.get(p) != rs.cfg.id for p in c.parents[1:]):
                    mark(sha, "merge")

        def nearest_notable(sha):
            hidden = 0
            while sha is not None and sha in self.commits:
                if sha in roles:
                    return sha, hidden
                hidden += 1
                ps = self.commits[sha].parents
                sha = ps[0] if ps else None
            return None, hidden

        edges = []
        for rs in self.series.values():
            prev, hidden, first = rs.fork_point, 0, True
            for sha in reversed(rs.chain):
                if sha not in roles:
                    hidden += 1
                    continue
                if prev is not None and prev in roles:
                    edges.append({"from": prev, "to": sha, "hidden_commits": hidden,
                                  "kind": "fork" if first and rs.cfg.parent else "first_parent"})
                prev, hidden, first = sha, 0, False
            for sha in rs.chain:
                for p in self.commits[sha].parents[1:]:
                    src, h = nearest_notable(p)
                    if src and sha in roles:
                        edges.append({"from": src, "to": sha, "hidden_commits": h, "kind": "merge"})
        for sha in roles:
            for p in self.commits[sha].picked_from:
                if p in roles:
                    edges.append({"from": p, "to": sha, "hidden_commits": 0, "kind": "cherry_pick"})

        tags_by_commit: dict[str, list[str]] = {}
        for t in tags:
            tags_by_commit.setdefault(t["commit"], []).append(t["name"])
        nodes = []
        for sha in sorted(roles, key=lambda s: self.commits[s].seq):
            c = self.commits[sha]
            nodes.append({
                "sha": sha, "seq": c.seq, "series": self.owner.get(sha),
                "subject": c.subject, "author": c.author, "date": c.date,
                "components": c.components,
                "fix_ids": sorted(self.fix_member.get(sha, {}), key=_fix_sort_key),
                "cherry_picked_from": c.picked_from,
                "parents": c.parents,
                "tags": sorted(tags_by_commit.get(sha, [])),
                "roles": sorted(roles[sha]),
            })
        edges.sort(key=lambda e: (self.commits[e["to"]].seq, self.commits[e["from"]].seq, e["kind"]))
        return nodes, edges

    # ------------------------------------------------------------------ 出力
    def _assemble(self, config_commit, fixes, tags, dep_checks) -> dict:
        nodes, edges = self._graph(fixes, tags)
        components = set()
        for rs in self.series.values():
            components |= set(self.snapshot(rs.head))
        for t in tags:
            components |= set(t["snapshot"])
        series_out = []
        for s in self.config.series:
            rs = self.series[s.id]
            series_out.append({
                "id": s.id, "branch": s.branch, "ref": rs.ref, "kind": s.kind, "parent": s.parent,
                "customer": s.customer, "label": s.label, "status": s.status, "tag_prefix": s.tag_prefix,
                "head": rs.head, "fork_point": rs.fork_point,
                "head_snapshot": {n: m["version"] for n, m in self.snapshot(rs.head).items()},
            })
        sev_rank = {"error": 0, "warning": 1}
        violations = sorted(self.violations, key=lambda v: (sev_rank[v["severity"]], v["kind"], v["message"]))
        epoch = os.environ.get("SOURCE_DATE_EPOCH")
        now = datetime.fromtimestamp(int(epoch), UTC) if epoch else datetime.now(UTC)
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "generator": {"name": "release-viewer", "version": __version__},
            "repository": {"name": self.name, "config_ref": None if self.config_dir else self.config_ref,
                           "config_commit": config_commit, "since_ref": self.since_ref},
            "components": [{"name": n, "path": f"{self.config.repository.component_dir}/{n}"}
                           for n in sorted(components)],
            "series": series_out,
            "commits": nodes,
            "graph": {"edges": edges},
            "tags": tags,
            "fixes": fixes,
            "dependency_checks": dep_checks,
            "violations": violations,
            "summary": {
                "errors": sum(v["severity"] == "error" for v in violations),
                "warnings": sum(v["severity"] == "warning" for v in violations),
            },
            "extensions": {},
        }

    def _violation(self, kind: str, severity: str, message: str, **refs) -> None:
        v = {"kind": kind, "severity": severity, "message": message,
             "refs": {k: v for k, v in refs.items() if v is not None}}
        if v not in self.violations:
            self.violations.append(v)


def _parse_component_meta(name: str, raw: bytes) -> dict:
    meta = {"version": None, "dependencies": {}, "error": None}
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
        comp = doc.get("component", {})
        meta["version"] = comp.get("version")
        meta["dependencies"] = {str(k): str(v) for k, v in doc.get("dependencies", {}).items()}
        if comp.get("name") not in (None, name):
            meta["error"] = f"name（{comp.get('name')}）がディレクトリ名と一致しない"
        elif meta["version"] is None:
            meta["error"] = "version がない"
        else:
            Version.parse(meta["version"])
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, VersionError) as e:
        meta["error"] = str(e)
    return meta


def _fix_sort_key(fid: str):
    prefix, _, num = fid.rpartition("-")
    return (prefix, int(num) if num.isdigit() else 0, fid)
