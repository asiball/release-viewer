"""PR のコミットの規約チェック（lint-pr、docs/conventions.md §10）。

対象は `<系列のブランチ>..<head>` の、マージコミットを除くコミット。Collector は使わず、設定と git だけを読む。
"""

from __future__ import annotations

from pathlib import Path

from .collect import CHERRY_RE
from .config import ConfigError, load_config
from .fixid import parse_fix_ids
from .gitcmd import Git

# {trailer} は [repository].fix_id_trailer
_LOG_FORMAT = "%x1e%H%x1f%s%x1f%(trailers:key={trailer},valueonly,separator=%x1d)%x1f%B"


def lint_pr(repo: Path | str, *, series: str, head: str = "HEAD", config_ref: str = "main",
            config_dir: Path | None = None, remote: str = "origin") -> tuple[list[dict], int]:
    """PR のコミットを検査して (検出結果, 検査したコミット数) を返す。

    検出結果: {"kind", "severity", "commit", "subject", "message"}
    """
    git = Git(repo)
    if config_dir is None:
        ref = git.resolve_branch(config_ref, remote) or config_ref
        config_commit = git.resolve_commit(ref)
        if config_commit is None:
            raise ConfigError(f"設定参照ref {config_ref} が見つからない")
        config = load_config(git, config_commit, None)
    else:
        config = load_config(git, None, config_dir)

    target = config.series_by_id().get(series)
    if target is None:
        raise ConfigError(f"--series の系列が定義されていない: {series}")
    base = git.resolve_branch(target.branch, remote)
    if base is None:
        raise ConfigError(f"系列のブランチが見つからない: {target.branch}")
    if git.resolve_commit(head) is None:
        raise ConfigError(f"--head {head} が見つからない")

    repo_cfg = config.repository
    out = git.run("log", "--no-merges", f"--format={_LOG_FORMAT.format(trailer=repo_cfg.fix_id_trailer)}",
                  f"{base}..{head}")
    records = [r for r in out.split("\x1e") if r.strip()]
    findings: list[dict] = []
    for rec in records:
        sha, subject, trailers, body = rec.split("\x1f", 3)
        issues: list[tuple[str, str, str]] = []  # (kind, severity, message)
        fix_ids, invalid, not_trailer = parse_fix_ids(trailers, body, repo_cfg.fix_id_trailer, repo_cfg.fix_id_pattern)
        for value in invalid:
            issues.append(("invalid_fix_id", "error", f"{repo_cfg.fix_id_trailer} が書式に合わない: {value!r}"))
        if not_trailer:
            issues.append(("fix_id_not_trailer", "warning",
                           f"{repo_cfg.fix_id_trailer}（{', '.join(not_trailer)}）がトレーラーとして認識されない位置にある"))
        picked = CHERRY_RE.findall(body)
        # mainline 宛は squash merge されるので -x を求めない
        if target.kind != "mainline" and fix_ids and not picked:
            issues.append(("cherry_pick_without_x", "error",
                           f"{', '.join(fix_ids)} の移植に -x の記録がない（cherry-pick -x を使う）"))
        for src in picked:
            if git.resolve_commit(src) is None:
                issues.append(("cherry_pick_source_missing", "error",
                               f"-x の記録にある {src[:10]} がリポジトリに存在しない"))
        findings += [{"kind": k, "severity": sev, "commit": sha, "subject": subject, "message": m}
                     for k, sev, m in issues]
    return findings, len(records)
