"""`.release/series.toml` と `.release/exclusions.toml` の読み込み（docs/conventions.md §4, §7）。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .gitcmd import Git

SERIES_FILE = ".release/series.toml"
EXCLUSIONS_FILE = ".release/exclusions.toml"

KINDS = ("mainline", "release", "customer")
STATUSES = ("active", "maintenance", "eol")


class ConfigError(ValueError):
    pass


@dataclass
class Series:
    branch: str
    kind: str
    status: str
    parent: str | None = None
    customer: str | None = None
    label: str | None = None
    tag_prefix: str | None = None

    @property
    def id(self) -> str:
        return self.branch


@dataclass
class Exclusion:
    fix: str
    series: list[str]
    reason: str
    decided: str | None = None
    by: str | None = None


@dataclass
class Config:
    series: list[Series]
    exclusions: list[Exclusion]
    # 読み込み時点で判明した問題（理由のない除外など）。violations に載せる
    problems: list[dict] = field(default_factory=list)

    def series_by_id(self) -> dict[str, Series]:
        return {s.id: s for s in self.series}


def load_config(git: Git, config_ref: str | None, config_dir: Path | None) -> Config:
    if config_dir is not None:
        series_text = _read_local(config_dir / "series.toml")
        excl_text = _read_local(config_dir / "exclusions.toml")
    else:
        files = git.cat_files([f"{config_ref}:{SERIES_FILE}", f"{config_ref}:{EXCLUSIONS_FILE}"])
        raw_series = files[f"{config_ref}:{SERIES_FILE}"]
        raw_excl = files[f"{config_ref}:{EXCLUSIONS_FILE}"]
        series_text = raw_series.decode("utf-8") if raw_series is not None else None
        excl_text = raw_excl.decode("utf-8") if raw_excl is not None else None
    if series_text is None:
        where = config_dir if config_dir is not None else f"{config_ref}:{SERIES_FILE}"
        raise ConfigError(f"系列定義が見つからない: {where}")
    return parse_config(series_text, excl_text or "")


def _read_local(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None


def parse_config(series_text: str, exclusions_text: str) -> Config:
    try:
        sdoc = tomllib.loads(series_text)
        edoc = tomllib.loads(exclusions_text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"TOML の構文エラー: {e}") from e

    series: list[Series] = []
    for i, raw in enumerate(sdoc.get("series", [])):
        where = f"series[{i}]"
        unknown = set(raw) - {"branch", "kind", "status", "parent", "customer", "label", "tag_prefix"}
        if unknown:
            raise ConfigError(f"{where}: 未知のキー {sorted(unknown)}")
        if not isinstance(raw.get("branch"), str) or not raw["branch"]:
            raise ConfigError(f"{where}: branch は必須")
        if raw.get("kind") not in KINDS:
            raise ConfigError(f"{where} ({raw['branch']}): kind は {KINDS} のいずれか")
        status = raw.get("status", "active")
        if status not in STATUSES:
            raise ConfigError(f"{where} ({raw['branch']}): status は {STATUSES} のいずれか")
        prefix = raw.get("tag_prefix")
        if prefix is not None and not (isinstance(prefix, str) and prefix.endswith("/") and len(prefix) > 1):
            raise ConfigError(f"{where} ({raw['branch']}): tag_prefix は '/' で終わる文字列")
        series.append(Series(branch=raw["branch"], kind=raw["kind"], status=status,
                             parent=raw.get("parent"), customer=raw.get("customer"),
                             label=raw.get("label"), tag_prefix=prefix))
    if not series:
        raise ConfigError("系列が1つも定義されていない")

    ids = [s.id for s in series]
    if len(set(ids)) != len(ids):
        raise ConfigError("同じブランチが複数回定義されている")
    by_id = {s.id: s for s in series}
    for s in series:
        if s.parent is not None and s.parent not in by_id:
            raise ConfigError(f"{s.id}: 親系列 {s.parent} が定義されていない")
        # 循環チェック
        seen, cur = set(), s
        while cur.parent is not None:
            if cur.id in seen:
                raise ConfigError(f"{s.id}: 親子関係が循環している")
            seen.add(cur.id)
            cur = by_id[cur.parent]

    exclusions: list[Exclusion] = []
    problems: list[dict] = []
    for i, raw in enumerate(edoc.get("exclude", [])):
        fix = raw.get("fix")
        targets = raw.get("series")
        if isinstance(targets, str):
            targets = [targets]
        if not isinstance(fix, str) or not isinstance(targets, list) or not targets:
            raise ConfigError(f"exclude[{i}]: fix と series は必須")
        reason = (raw.get("reason") or "").strip()
        if not reason:
            # 理由のない除外は認めない（除外として扱わず、エラーとして報告する）
            problems.append({
                "kind": "exclusion_without_reason", "severity": "error",
                "message": f"{fix} の除外宣言（{', '.join(targets)}）に理由がないため無効",
                "refs": {"fix": fix, "series": targets[0] if len(targets) == 1 else None},
            })
            continue
        exclusions.append(Exclusion(fix=fix, series=targets, reason=reason,
                                    decided=_str_or_none(raw.get("decided")),
                                    by=_str_or_none(raw.get("by"))))
    return Config(series=series, exclusions=exclusions, problems=problems)


def _str_or_none(v) -> str | None:
    return None if v is None else str(v)
