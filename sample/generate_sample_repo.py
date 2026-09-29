#!/usr/bin/env python3
"""架空の組み込みFWモノレポ（sample-fw）を生成する。

docs/conventions.md の規約に従ったリポジトリを作り、検証用に次のケースを含める。

- FIX-102: customer/gamma/1.2 にだけ伝播していない（部分未伝播）
- FIX-102: release/1.1 と customer/beta/1.1 では対象外として除外宣言
- FIX-103: release/1.1 へは -x なし・トレーラーなしで移植（patch-id でのみ一致）
- FIX-104: release/1.2 へはトレーラーを消して -x の記録だけ残して移植。
           customer/acme/1.2 はその release/1.2 のコミットから -x で移植（-x の連鎖）
- FIX-105: 2コミットからなる fix。customer/gamma/1.2 には1コミット目だけ伝播（部分未伝播）
- customer/beta/1.1: app の依存制約（hal >=1.2.0）を hal 1.1.0 のまま満たしていない
- customer/acme/1.2: 顧客固有ビルド hal/v1.2.1+acme.1、release/1.2 のマージ

作者・日時は固定で、ユーザーの git 設定も読まないため、再実行すると同じコミットSHAになる。

使い方:
    python sample/generate_sample_repo.py build/sample-fw [--force]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_TIME = datetime(2026, 1, 5, 9, 0, tzinfo=timezone(timedelta(hours=9)))
STEP = timedelta(hours=3)

AUTHORS = {
    "sato": ("Sato Hanako", "sato@example.com"),
    "suzuki": ("Suzuki Ichiro", "suzuki@example.com"),
    "tanaka": ("Tanaka Jiro", "tanaka@example.com"),
    "release": ("Release Bot", "release-bot@example.com"),
}

COMPONENTS = ["driver", "hal", "lib-comm", "app"]


class Repo:
    """固定の作者・日時で git を操作する。コミット・タグを作るたびに時刻を進める。"""

    def __init__(self, path: Path):
        self.path = path
        self.tick = 0
        self.base_env = {
            **os.environ,
            # ユーザー・システムの git 設定（autocrlf、署名、フック等）を読まない
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }

    def _env(self, author: str) -> dict[str, str]:
        self.tick += 1
        when = (BASE_TIME + STEP * self.tick).isoformat()
        name, email = AUTHORS[author]
        return {
            **self.base_env,
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_COMMITTER_DATE": when,
        }

    def git(self, *args: str, author: str | None = None, input: str | None = None,
            check: bool = True) -> subprocess.CompletedProcess:
        env = self._env(author) if author else self.base_env
        proc = subprocess.run(
            ["git", *args], cwd=self.path, env=env, input=input,
            capture_output=True, encoding="utf-8",
        )
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed:\n{proc.stdout}{proc.stderr}")
        return proc

    # --- ファイル操作 ---
    def write(self, rel: str, content: str) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")

    def edit(self, rel: str, old: str, new: str) -> None:
        p = self.path / rel
        text = p.read_text(encoding="utf-8")
        if old not in text:
            raise RuntimeError(f"{rel}: 置換対象が見つからない: {old!r}")
        self.write(rel, text.replace(old, new, 1))

    def set_version(self, component: str, version: str) -> None:
        rel = f"components/{component}/component.toml"
        text = (self.path / rel).read_text(encoding="utf-8")
        lines = [f'version = "{version}"' if l.startswith("version = ") else l
                 for l in text.split("\n")]
        self.write(rel, "\n".join(lines))

    def set_dependency(self, component: str, dep: str, constraint: str) -> None:
        rel = f"components/{component}/component.toml"
        text = (self.path / rel).read_text(encoding="utf-8")
        lines = [f'{dep} = "{constraint}"' if l.startswith(f"{dep} = ") else l
                 for l in text.split("\n")]
        self.write(rel, "\n".join(lines))

    # --- git 操作 ---
    def commit(self, message: str, author: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-F", "-", author=author, input=message)
        return self.rev("HEAD")

    def rev(self, ref: str) -> str:
        return self.git("rev-parse", ref).stdout.strip()

    def checkout(self, branch: str, new_from: str | None = None) -> None:
        if new_from:
            self.git("checkout", "-q", "-b", branch, new_from)
        else:
            self.git("checkout", "-q", branch)

    def tag(self, name: str, author: str = "release") -> None:
        self.git("tag", "-a", name, "-m", name, author=author)

    def cherry_pick(self, sha: str, author: str) -> str:
        """規約どおりの伝播: cherry-pick -x（トレーラーもそのままコピーされる）"""
        self.git("cherry-pick", "-x", sha, author=author)
        return self.rev("HEAD")

    def cherry_pick_rewritten(self, sha: str, message: str, author: str) -> str:
        """変更だけを取り込み、メッセージは手で書き直す（規約違反の再現用）"""
        self.git("cherry-pick", "-n", sha)
        return self.commit(message, author)


def component_toml(name: str, version: str, deps: dict[str, str]) -> str:
    lines = ["[component]", f'name = "{name}"', f'version = "{version}"', "", "[dependencies]"]
    lines += [f'{k} = "{v}"' for k, v in deps.items()]
    return "\n".join(lines) + "\n"


def series_toml(entries: list[dict[str, str]]) -> str:
    out = ["# 追跡する系列の定義（docs/conventions.md §4）", ""]
    for e in entries:
        out.append("[[series]]")
        for k in ("branch", "kind", "parent", "customer", "label", "status", "tag_prefix"):
            if k in e:
                out.append(f'{k} = "{e[k]}"')
        out.append("")
    return "\n".join(out)


SERIES = {
    "main": {"branch": "main", "kind": "mainline", "status": "active", "tag_prefix": "fw/"},
    "release/1.1": {"branch": "release/1.1", "kind": "release", "parent": "main",
                    "status": "maintenance", "tag_prefix": "fw/"},
    "customer/beta/1.1": {"branch": "customer/beta/1.1", "kind": "customer",
                          "parent": "release/1.1", "customer": "beta",
                          "label": "Beta 社向け 1.1", "status": "active",
                          "tag_prefix": "fw-beta/"},
    "release/1.2": {"branch": "release/1.2", "kind": "release", "parent": "main",
                    "status": "active", "tag_prefix": "fw/"},
    "customer/acme/1.2": {"branch": "customer/acme/1.2", "kind": "customer",
                          "parent": "release/1.2", "customer": "acme",
                          "label": "ACME 社向け 1.2", "status": "active",
                          "tag_prefix": "fw-acme/"},
    "customer/gamma/1.2": {"branch": "customer/gamma/1.2", "kind": "customer",
                           "parent": "release/1.2", "customer": "gamma",
                           "label": "Gamma 社向け 1.2", "status": "active",
                           "tag_prefix": "fw-gamma/"},
}

EXCLUSIONS_HEADER = "# 「この系列には適用しない」宣言（docs/conventions.md §7）\n"

EXCLUSION_FIX_102 = """
[[exclude]]
fix     = "FIX-102"
series  = ["release/1.1", "customer/beta/1.1"]
reason  = "lib-comm 1.1.0 で追加した再送処理の不具合。1.1 系には該当コード（retry.c）が存在しない"
decided = "2026-02-02"
by      = "tanaka"
"""

SOURCES = {
    "components/driver/src/driver.c": "/* driver: 周辺デバイスの初期化 */\n#include \"driver.h\"\n\nvoid driver_init(void)\n{\n    clock_enable();\n}\n",
    "components/driver/src/gpio.c": "/* driver: GPIO */\n#include \"driver.h\"\n\n#define DEBOUNCE_MS 5\n\nint gpio_read(int pin)\n{\n    return debounce(pin, DEBOUNCE_MS);\n}\n",
    "components/hal/src/hal.c": "/* hal: ハードウェア抽象化 */\n#include \"hal.h\"\n\nvoid hal_init(void)\n{\n    driver_init();\n}\n",
    "components/hal/src/i2c.c": "/* hal: I2C */\n#include \"hal.h\"\n\nint i2c_transfer(const uint8_t *buf, size_t len)\n{\n    if (wait_ack() == TIMEOUT) {\n        return -1;\n    }\n    return send(buf, len);\n}\n",
    "components/lib-comm/src/comm.c": "/* lib-comm: 通信スタック */\n#include \"comm.h\"\n\nvoid comm_init(void)\n{\n    hal_init();\n}\n",
    "components/lib-comm/src/frame.c": "/* lib-comm: フレーム組み立て */\n#include \"comm.h\"\n\nsize_t frame_build(uint8_t *out, const uint8_t *payload, size_t len)\n{\n    out[0] = SOF;\n    memcpy(&out[1], payload, len);\n    return len + 1;\n}\n",
    "components/lib-comm/src/crc.c": "/* lib-comm: CRC */\n#include \"comm.h\"\n\nuint16_t crc16(const uint8_t *p, size_t len)\n{\n    uint16_t crc = 0x0000;\n    while (len--) crc = step(crc, *p++);\n    return crc;\n}\n",
    "components/app/src/main.c": "/* app: エントリポイント */\n#include \"app.h\"\n\nint main(void)\n{\n    comm_init();\n    for (;;) app_loop();\n}\n",
    "components/app/src/watchdog.c": "/* app: ウォッチドッグ */\n#include \"app.h\"\n\nvoid wdt_on_reset(void)\n{\n    system_reset();\n}\n",
}


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    r = Repo(out)
    r.git("init", "-q", "-b", "main")
    for key, value in [("core.autocrlf", "false"), ("commit.gpgsign", "false"),
                       ("tag.gpgsign", "false"), ("core.hooksPath", ".git/no-hooks")]:
        r.git("config", key, value)

    # ---- main: 初期インポート -------------------------------------------------
    r.write("README.md", "# sample-fw\n\nrelease-viewer 検証用の架空FWモノレポ。\n")
    r.write("RELEASE", "main (development)\n")
    r.write(".release/series.toml", series_toml([SERIES["main"]]))
    r.write(".release/exclusions.toml", EXCLUSIONS_HEADER)
    r.write("components/driver/component.toml", component_toml("driver", "1.0.0", {}))
    r.write("components/hal/component.toml",
            component_toml("hal", "1.0.0", {"driver": ">=1.0.0, <2.0.0"}))
    r.write("components/lib-comm/component.toml",
            component_toml("lib-comm", "1.0.0", {"hal": ">=1.0.0, <2.0.0"}))
    r.write("components/app/component.toml",
            component_toml("app", "1.0.0", {"hal": ">=1.0.0, <2.0.0",
                                            "lib-comm": ">=1.0.0, <2.0.0"}))
    for rel, content in SOURCES.items():
        r.write(rel, content)
    r.commit("chore: 初期インポート", "sato")
    for c in COMPONENTS:
        r.tag(f"{c}/v1.0.0")

    r.edit("components/hal/src/hal.c", "    driver_init();\n", "    driver_init();\n    spi_dma_init();\n")
    r.set_version("hal", "1.1.0")
    r.commit("hal: SPI の DMA 転送に対応", "tanaka")
    r.tag("hal/v1.1.0")

    r.edit("components/app/src/main.c", "    comm_init();\n", "    board_init();\n    comm_init();\n")
    r.set_version("app", "1.1.0")
    r.commit("app: 起動シーケンスを整理", "sato")
    r.tag("app/v1.1.0")

    # ---- release/1.1 と customer/beta/1.1 -----------------------------------
    r.checkout("release/1.1", new_from="main")
    r.write("RELEASE", "fw 1.1.0\n")
    r.commit("release: fw 1.1.0", "release")
    r.tag("fw/v1.1.0")

    r.checkout("customer/beta/1.1", new_from="release/1.1")
    r.write("components/app/config/beta.cfg", "customer = beta\nbaudrate = 115200\n")
    r.write("RELEASE", "fw-beta 1.1.0\n")
    r.commit("beta: 顧客向け設定を追加", "tanaka")
    r.tag("fw-beta/v1.1.0")

    r.checkout("main")
    r.write(".release/series.toml", series_toml(
        [SERIES[s] for s in ("main", "release/1.1", "customer/beta/1.1")]))
    r.commit("chore: release/1.1 と customer/beta/1.1 を追跡対象に追加", "sato")

    # ---- main: 1.2 に向けた開発 ---------------------------------------------
    r.write("components/lib-comm/src/retry.c", "/* lib-comm: 再送処理 */\n#include \"comm.h\"\n\nstatic uint8_t retry_count;\n\nint comm_retry(void)\n{\n    retry_count++;\n    return resend();\n}\n")
    r.set_version("lib-comm", "1.1.0")
    r.commit("lib-comm: 再送処理を追加", "suzuki")
    r.tag("lib-comm/v1.1.0")

    r.edit("components/driver/src/driver.c", "    clock_enable();\n", "    clock_enable();\n    gpio_irq_enable();\n")
    r.set_version("driver", "1.1.0")
    r.commit("driver: GPIO 割り込みに対応", "tanaka")
    r.tag("driver/v1.1.0")

    r.edit("components/hal/src/hal.c", "    spi_dma_init();\n", "    spi_dma_init();\n    canfd_init();\n")
    r.set_version("hal", "1.2.0")
    r.set_dependency("hal", "driver", ">=1.1.0, <2.0.0")
    r.commit("hal: CAN FD に対応", "tanaka")
    r.tag("hal/v1.2.0")

    r.edit("components/app/src/main.c", "    comm_init();\n", "    comm_init();\n    canfd_config_load();\n")
    r.set_version("app", "1.2.0")
    r.set_dependency("app", "hal", ">=1.2.0, <2.0.0")
    r.set_dependency("app", "lib-comm", ">=1.1.0, <2.0.0")
    r.commit("app: CAN FD の設定を読み込む", "sato")
    r.tag("app/v1.2.0")

    # ---- release/1.2 と顧客系列 ---------------------------------------------
    r.checkout("release/1.2", new_from="main")
    r.write("RELEASE", "fw 1.2.0\n")
    r.commit("release: fw 1.2.0", "release")
    r.tag("fw/v1.2.0")

    r.checkout("customer/acme/1.2", new_from="release/1.2")
    r.write("components/hal/board/acme.cfg", "board = acme-x1\ni2c_clock = 400000\n")
    r.set_version("hal", "1.2.0+acme.1")
    r.commit("acme: ACME-X1 ボード定義を追加", "tanaka")
    r.tag("hal/v1.2.0+acme.1")
    r.tag("fw-acme/v1.2.0")

    r.checkout("customer/gamma/1.2", new_from="release/1.2")
    r.write("components/app/config/gamma.cfg", "customer = gamma\nbaudrate = 921600\n")
    r.write("RELEASE", "fw-gamma 1.2.0\n")
    r.commit("gamma: 顧客向け設定を追加", "suzuki")
    r.tag("fw-gamma/v1.2.0")

    r.checkout("main")
    r.write(".release/series.toml", series_toml([SERIES[s] for s in SERIES]))
    r.commit("chore: release/1.2 と顧客系列を追跡対象に追加", "sato")

    # ---- FIX-101: main で修正し、各系列へ -x で伝播 --------------------------
    r.edit("components/hal/src/i2c.c", "        return -1;\n", "        bus_reset();\n        return -1;\n")
    fix101 = r.commit("hal: I2C タイムアウト時にバスをリセットする\n\n"
                      "ACK 待ちでタイムアウトした後、バスがビジーのまま残ることがある。\n\n"
                      "Fix-ID: FIX-101\n", "suzuki")
    for branch in ("release/1.2", "release/1.1", "customer/beta/1.1", "customer/gamma/1.2"):
        r.checkout(branch)
        r.cherry_pick(fix101, "suzuki")
    # customer/acme/1.2 は release/1.2 のマージで取り込む

    # ---- FIX-102: release/1.2 で修正し main へ前方移植。gamma には漏れる ------
    r.checkout("release/1.2")
    r.edit("components/lib-comm/src/retry.c", "    retry_count++;\n",
           "    if (retry_count < UINT8_MAX) {\n        retry_count++;\n    }\n")
    fix102 = r.commit("lib-comm: 再送カウンタのオーバーフローを修正\n\n"
                      "Fix-ID: FIX-102\n", "suzuki")
    r.checkout("main")
    r.cherry_pick(fix102, "suzuki")
    r.write(".release/exclusions.toml", EXCLUSIONS_HEADER + EXCLUSION_FIX_102)
    r.commit("chore: FIX-102 を 1.1 系の対象外として宣言", "tanaka")

    # ---- FIX-105: 2コミットからなる fix。gamma には1コミット目だけ -------------
    r.edit("components/lib-comm/src/frame.c", "    out[0] = SOF;\n",
           "    if (len > FRAME_MAX) {\n        return 0;\n    }\n    out[0] = SOF;\n")
    fix105a = r.commit("lib-comm: フレーム長の上限チェックを追加\n\nFix-ID: FIX-105\n", "sato")
    r.edit("components/lib-comm/src/crc.c", "uint16_t crc = 0x0000;", "uint16_t crc = 0xFFFF;")
    fix105b = r.commit("lib-comm: CRC 初期値を仕様どおり 0xFFFF にする\n\nFix-ID: FIX-105\n", "sato")
    for branch in ("release/1.2", "release/1.1", "customer/beta/1.1"):
        r.checkout(branch)
        r.cherry_pick(fix105a, "sato")
        r.cherry_pick(fix105b, "sato")
    r.checkout("customer/gamma/1.2")
    r.cherry_pick(fix105a, "sato")

    # ---- release/1.2: fw 1.2.1 ----------------------------------------------
    r.checkout("release/1.2")
    r.set_version("hal", "1.2.1")
    r.set_version("lib-comm", "1.1.1")
    r.write("RELEASE", "fw 1.2.1\n")
    r.commit("release: fw 1.2.1", "release")
    for t in ("hal/v1.2.1", "lib-comm/v1.1.1", "fw/v1.2.1"):
        r.tag(t)

    # ---- customer/acme/1.2: release/1.2 をマージ（hal のバージョンが衝突） -----
    r.checkout("customer/acme/1.2")
    merge = r.git("merge", "--no-ff", "--no-commit", "release/1.2", author="tanaka", check=False)
    if merge.returncode not in (0, 1):
        raise RuntimeError(merge.stderr)
    r.write("components/hal/component.toml",
            component_toml("hal", "1.2.1+acme.1", {"driver": ">=1.1.0, <2.0.0"}))
    r.commit("Merge fw/v1.2.1 into customer/acme/1.2", "tanaka")
    r.tag("hal/v1.2.1+acme.1")
    r.tag("fw-acme/v1.2.1")

    # ---- FIX-103: release/1.1 へは -x もトレーラーもなしで移植 ----------------
    r.checkout("main")
    r.edit("components/driver/src/gpio.c", "#define DEBOUNCE_MS 5", "#define DEBOUNCE_MS 20")
    fix103 = r.commit("driver: GPIO のデバウンス時間を 20ms にする\n\n"
                      "5ms ではリレー接点のチャタリングを除去しきれない。\n\n"
                      "Fix-ID: FIX-103\n", "tanaka")
    r.checkout("release/1.2")
    fix103_r12 = r.cherry_pick(fix103, "tanaka")
    r.checkout("customer/acme/1.2")
    r.cherry_pick(fix103_r12, "tanaka")
    for branch in ("customer/gamma/1.2", "customer/beta/1.1"):
        r.checkout(branch)
        r.cherry_pick(fix103, "tanaka")
    r.checkout("release/1.1")
    r.cherry_pick_rewritten(fix103, "driver: デバウンス時間の修正を 1.1 系へ移植\n", "suzuki")

    # ---- FIX-104: release/1.2 へはトレーラーを消し -x の記録だけ残す ------------
    r.checkout("main")
    r.edit("components/app/src/watchdog.c", "    system_reset();\n",
           "    log_flush();\n    system_reset();\n")
    fix104 = r.commit("app: ウォッチドッグ再起動時にログが欠落する問題を修正\n\n"
                      "Fix-ID: FIX-104\n", "sato")
    r.checkout("release/1.2")
    fix104_r12 = r.cherry_pick_rewritten(
        fix104, f"app: WDT リセット前にログを書き出す（backport）\n\n"
                f"(cherry picked from commit {fix104})\n", "sato")
    r.checkout("customer/acme/1.2")
    r.cherry_pick(fix104_r12, "sato")
    for branch in ("customer/gamma/1.2", "release/1.1", "customer/beta/1.1"):
        r.checkout(branch)
        r.cherry_pick(fix104, "sato")

    # ---- 各系列のリリース ----------------------------------------------------
    r.checkout("release/1.1")
    for c, v in (("hal", "1.1.1"), ("driver", "1.0.1"), ("lib-comm", "1.0.1"), ("app", "1.1.1")):
        r.set_version(c, v)
    r.write("RELEASE", "fw 1.1.1\n")
    r.commit("release: fw 1.1.1", "release")
    for t in ("hal/v1.1.1", "driver/v1.0.1", "lib-comm/v1.0.1", "app/v1.1.1", "fw/v1.1.1"):
        r.tag(t)

    # customer/beta/1.1: app だけ CAN FD 設定をバックポートし、hal は 1.1.0 のまま（依存違反）
    r.checkout("customer/beta/1.1")
    r.edit("components/app/src/main.c", "    comm_init();\n", "    comm_init();\n    canfd_config_load();\n")
    r.set_version("app", "1.1.0+beta.1")
    r.set_dependency("app", "hal", ">=1.2.0, <2.0.0")
    r.commit("app: CAN FD 設定の読み込みをバックポート（Beta 社要望）", "tanaka")
    r.tag("app/v1.1.0+beta.1")
    r.write("RELEASE", "fw-beta 1.1.1\n")
    r.commit("release: fw-beta 1.1.1", "release")
    r.tag("fw-beta/v1.1.1")

    r.checkout("release/1.2")
    r.set_version("driver", "1.1.1")
    r.set_version("app", "1.2.1")
    r.write("RELEASE", "fw 1.2.2\n")
    r.commit("release: fw 1.2.2", "release")
    for t in ("driver/v1.1.1", "app/v1.2.1", "fw/v1.2.2"):
        r.tag(t)

    r.checkout("customer/acme/1.2")
    r.set_version("driver", "1.1.1")
    r.set_version("app", "1.2.1")
    r.write("RELEASE", "fw-acme 1.2.2\n")
    r.commit("release: fw-acme 1.2.2", "release")
    r.tag("fw-acme/v1.2.2")

    r.checkout("customer/gamma/1.2")
    r.set_version("hal", "1.2.1")
    r.write("RELEASE", "fw-gamma 1.2.1\n")
    r.commit("release: fw-gamma 1.2.1", "release")
    r.tag("fw-gamma/v1.2.1")

    # ---- main: 次の開発 -----------------------------------------------------
    r.checkout("main")
    r.edit("components/app/src/main.c", "    for (;;) app_loop();\n",
           "    ui_init();\n    for (;;) app_loop();\n")
    r.set_version("app", "1.3.0")
    r.commit("app: 設定画面を刷新", "sato")
    r.tag("app/v1.3.0")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", type=Path, help="生成先ディレクトリ")
    ap.add_argument("--force", action="store_true", help="生成先が存在すれば削除して作り直す")
    args = ap.parse_args(argv)

    if args.out.exists():
        if not args.force:
            print(f"{args.out} は既に存在します（--force で作り直し）", file=sys.stderr)
            return 2
        # ディレクトリ自体はエディタ等に掴まれていることがあるので、中身だけ消す
        for child in args.out.iterdir():
            if child.is_dir():
                shutil.rmtree(child, onerror=_force_remove)
            else:
                _force_remove(os.unlink, child, None)
    build(args.out)
    print(f"generated: {args.out}")
    return 0


def _force_remove(func, path, _exc):
    # Windows では .git/objects 配下が読み取り専用になっている
    os.chmod(path, 0o700)
    func(path)


if __name__ == "__main__":
    sys.exit(main())
