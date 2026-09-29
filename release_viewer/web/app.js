"use strict";
// Release Viewer: release-viewer が出力した JSON（schema 1.x）をブラウザだけで表示する。
// 文字列はすべて textContent で入れる（コミットメッセージ等を HTML として解釈しない）。

const SUPPORTED_MAJOR = "1";

const STATE = {
  applied: { mark: "✔", label: "適用済み" },
  patch_id_only: { mark: "≈", label: "patch-idのみ一致" },
  missing: { mark: "✖", label: "未適用" },
  excluded: { mark: "−", label: "対象外（宣言）" },
  not_applicable: { mark: "n/a", label: "判定対象外" },
};
const METHOD = {
  ancestry: "祖先に含まれる",
  trailer: "Fix-ID トレーラー",
  cherry_pick_x: "cherry-pick -x の記録",
  patch_id: "patch-id 一致のみ",
};
const LANE_COLORS = ["#0969da", "#1a7f37", "#8250df", "#bc4c00", "#bf3989", "#1b7c83", "#4d2d00", "#57606a"];

let D = null;
const IDX = {};

// ---------------------------------------------------------------- DOM ヘルパ
function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  append(e, children);
  return e;
}
function append(parent, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    parent.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}
function svg(tag, attrs, ...children) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  append(e, children);
  return e;
}
const short = (sha) => (sha ? sha.slice(0, 10) : "");
const link = (hash, text, attrs) => el("a", { href: hash, ...(attrs || {}) }, text);
const tagHref = (name) => "#/release/" + encodeURIComponent(name);
const fixHref = (id) => "#/matrix/" + encodeURIComponent(id);
const seriesName = (id) => {
  const s = IDX.series.get(id);
  return s && s.label ? `${id}（${s.label}）` : id;
};

// ---------------------------------------------------------------- 読み込み
function init() {
  window.addEventListener("hashchange", route);
  const loader = document.getElementById("loader");
  document.getElementById("file").addEventListener("change", (ev) => readFile(ev.target.files[0]));
  loader.addEventListener("dragover", (ev) => { ev.preventDefault(); loader.classList.add("drag"); });
  loader.addEventListener("dragleave", () => loader.classList.remove("drag"));
  loader.addEventListener("drop", (ev) => {
    ev.preventDefault();
    loader.classList.remove("drag");
    readFile(ev.dataTransfer.files[0]);
  });
  if (window.RELEASE_DATA) load(window.RELEASE_DATA);
}

function readFile(file) {
  if (!file) return;
  file.text().then((text) => load(JSON.parse(text))).catch((e) => {
    document.getElementById("load-error").textContent = "読み込めません: " + e.message;
  });
}

function load(data) {
  const major = String(data.schema_version || "").split(".")[0];
  if (major !== SUPPORTED_MAJOR) {
    document.getElementById("load-error").textContent =
      `schema_version ${data.schema_version} には対応していません（対応: ${SUPPORTED_MAJOR}.x）`;
    return;
  }
  D = data;
  buildIndex();
  renderHeader();
  if (!location.hash) location.replace("#/tree");
  route();
}

function buildIndex() {
  IDX.series = new Map(D.series.map((s) => [s.id, s]));
  IDX.commit = new Map(D.commits.map((c) => [c.sha, c]));
  IDX.tag = new Map(D.tags.map((t) => [t.name, t]));
  IDX.fix = new Map(D.fixes.map((f) => [f.id, f]));

  // 系列を親子の深さ優先で並べる（レーン順）
  const children = new Map();
  for (const s of D.series) {
    const k = s.parent || "";
    if (!children.has(k)) children.set(k, []);
    children.get(k).push(s.id);
  }
  IDX.lanes = [];
  const walk = (id) => { IDX.lanes.push(id); (children.get(id) || []).forEach(walk); };
  (children.get("") || []).forEach(walk);
  IDX.laneColor = new Map(IDX.lanes.map((id, i) => [id, LANE_COLORS[i % LANE_COLORS.length]]));

  // 系列ごとの未適用 fix、fix ごとの未適用系列
  IDX.missingBySeries = new Map(D.series.map((s) => [s.id, []]));
  IDX.problemSeriesByFix = new Map();
  for (const f of D.fixes) {
    const probs = [];
    for (const [sid, st] of Object.entries(f.status)) {
      if (st.state === "missing") { IDX.missingBySeries.get(sid).push(f.id); probs.push({ sid, state: st.state }); }
      if (st.state === "patch_id_only") probs.push({ sid, state: st.state });
    }
    IDX.problemSeriesByFix.set(f.id, probs);
  }
  IDX.depsAt = new Map(D.dependency_checks.map((c) => [`${c.series}|${c.at.ref}`, c]));
}

function fixBadge(id, plain) {
  if (plain) return link(fixHref(id), id, { class: "badge fix" });
  const probs = IDX.problemSeriesByFix.get(id) || [];
  const missing = probs.filter((p) => p.state === "missing");
  const cls = missing.length ? "badge fix missing" : probs.length ? "badge fix warn" : "badge fix";
  const title = probs.map((p) => `${p.sid}: ${STATE[p.state].label}`).join("\n") || "全系列に適用済み／対象外";
  return link(fixHref(id), missing.length ? `${id} ⚠ 未伝播${missing.length}` : id, { class: cls, title });
}
function tagBadge(name) {
  const t = IDX.tag.get(name);
  return link(tagHref(name), name, { class: "badge " + (t && t.kind === "release" ? "tag-release" : "tag-component") });
}

// ---------------------------------------------------------------- ヘッダと違反一覧
function renderHeader() {
  const r = D.repository;
  document.getElementById("repo-meta").textContent =
    `${r.name} ・ 設定 ${r.config_ref || "(--config-dir)"}@${short(r.config_commit)} ・ 生成 ${D.generated_at} ・ schema ${D.schema_version}`;
  const btn = document.getElementById("violations-toggle");
  const { errors, warnings } = D.summary;
  btn.hidden = false;
  btn.textContent = `違反 ${errors} ／ 警告 ${warnings}`;
  btn.classList.toggle("has-error", errors > 0);
  const box = document.getElementById("violations");
  box.replaceChildren(el("ul", null, D.violations.map((v) => el("li", null,
    el("span", { class: "sev-" + v.severity }, v.severity === "error" ? "ERROR" : "WARN"), " ",
    el("code", null, v.kind), " ", violationLink(v)))));
  btn.onclick = () => { box.hidden = !box.hidden; };
  box.hidden = true;  // 件数はボタンで分かるので既定は閉じる。開閉は画面遷移（route）で保持される
}

function violationLink(v) {
  const r = v.refs || {};
  if (r.fix && IDX.fix.has(r.fix)) return link(fixHref(r.fix), v.message);
  if (r.tag) return link(tagHref(r.tag), v.message);
  if (r.series && v.kind === "dependency_violation") return link("#/deps/" + encodeURIComponent(r.series), v.message);
  return v.message;
}

// ---------------------------------------------------------------- ルーティング
function route() {
  if (!D) return;
  const raw = location.hash.replace(/^#\/?/, "");
  const i = raw.indexOf("/");
  const view = i < 0 ? raw : raw.slice(0, i);
  const arg = i < 0 ? "" : decodeURIComponent(raw.slice(i + 1));
  const views = { tree: renderTree, matrix: renderMatrix, release: renderRelease, deps: renderDeps, search: renderSearch };
  const fn = views[view] || renderTree;
  for (const a of document.querySelectorAll("#tabs a")) a.classList.toggle("active", a.dataset.view === (views[view] ? view : "tree"));
  closePanel();
  const root = document.getElementById("view");
  root.replaceChildren();
  fn(root, arg);
}

function openPanel(...children) {
  const p = document.getElementById("panel");
  p.replaceChildren(el("button", { class: "close", type: "button", title: "閉じる", onclick: closePanel }, "×"), ...children);
  p.hidden = false;
}
function closePanel() { document.getElementById("panel").hidden = true; }

// ---------------------------------------------------------------- 1. 系列ツリー
function renderTree(root) {
  const showPicks = el("input", { type: "checkbox", id: "show-picks", checked: true });
  const onlyProblems = el("input", { type: "checkbox", id: "only-problems" });
  const holder = el("div");
  const redraw = () => holder.replaceChildren(drawTree(showPicks.checked, onlyProblems.checked));
  showPicks.addEventListener("change", redraw);
  onlyProblems.addEventListener("change", redraw);

  root.append(
    el("h2", null, "系列ツリー"),
    el("p", { class: "muted" }, "タグ・hotfix・分岐点・マージ・HEAD のコミットだけを表示（…n は省略したコミット数）。上が新しい。"),
    laneLegend(),
    el("div", { class: "controls" },
      el("label", null, showPicks, " cherry-pick の対応線を表示"),
      el("label", null, onlyProblems, " 未伝播・patch-idのみの fix の起点だけ強調（× は未伝播の系列）")),
    holder);
  redraw();
}

function laneLegend() {
  return el("table", { class: "lane-legend" },
    el("tr", null, el("th"), el("th", null, "系列"), el("th", null, "種別／状態"), el("th", null, "HEAD の構成"), el("th", null, "未伝播の fix")),
    IDX.lanes.map((id) => {
      const s = IDX.series.get(id);
      const missing = IDX.missingBySeries.get(id);
      return el("tr", null,
        el("td", null, el("span", { class: "swatch", style: `background:${IDX.laneColor.get(id)}` })),
        el("td", null, el("code", null, id), s.label ? ` ${s.label}` : "", s.parent ? el("div", { class: "muted" }, `親: ${s.parent} ／ 分岐点 `, el("code", null, short(s.fork_point))) : ""),
        el("td", null, `${s.kind} ／ ${s.status}`),
        el("td", { class: "mono" }, Object.entries(s.head_snapshot).map(([c, v]) => `${c} ${v}`).join(", ")),
        el("td", null, missing.length ? missing.map((f) => [fixBadge(f), " "]) : el("span", { class: "ok" }, "なし")));
    }));
}

function drawTree(showPicks, onlyProblems) {
  const LANE_W = 30, ROW_H = 26, LEFT = 18, TOP = 84;  // TOP: レーン名（-60° 回転）の分
  const nodes = [...D.commits].sort((a, b) => b.seq - a.seq);
  const laneIdx = new Map(IDX.lanes.map((id, i) => [id, i]));
  const rowOf = new Map(nodes.map((n, i) => [n.sha, i]));
  const X = (sha) => LEFT + (laneIdx.get(IDX.commit.get(sha).series) ?? 0) * LANE_W;
  const Y = (sha) => TOP + rowOf.get(sha) * ROW_H + ROW_H / 2;
  const width = LEFT * 2 + (IDX.lanes.length - 1) * LANE_W;
  const height = TOP + nodes.length * ROW_H + 8;

  // 強調は fix の起点コミット（units）だけに付ける。伝播先のコミットまで赤くすると漏れの場所が埋もれる
  const unitFixes = (n) => n.fix_ids.filter((f) => IDX.fix.get(f)?.units.includes(n.sha));
  const problems = (n) => unitFixes(n).flatMap((f) => (IDX.problemSeriesByFix.get(f) || []).map((p) => ({ ...p, fix: f })));
  const hasProblem = (n) => problems(n).length > 0;
  const hasMissing = (n) => problems(n).some((p) => p.state === "missing");

  const g = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}` });
  IDX.lanes.forEach((id, i) => g.append(svg("line", {
    x1: LEFT + i * LANE_W, x2: LEFT + i * LANE_W, y1: TOP - 4, y2: height,
    stroke: IDX.laneColor.get(id), "stroke-opacity": 0.12, "stroke-width": 10,
  }, svg("title", null, id))));
  // レーン名。右上へ伸びる分は、行リストの padding-top（= TOP）の空白にはみ出して描く
  IDX.lanes.forEach((id, i) => {
    const x = LEFT + i * LANE_W, y = TOP - 8;
    const label = IDX.series.get(id)?.kind === "customer" ? id.replace(/^customer\//, "") : id;
    g.append(svg("text", { x, y, class: "lane-label", fill: IDX.laneColor.get(id), transform: `rotate(-60 ${x} ${y})` },
      label, svg("title", null, id)));
  });

  for (const e of D.graph.edges) {
    if (!rowOf.has(e.from) || !rowOf.has(e.to)) continue;
    if (e.kind === "cherry_pick" && !showPicks) continue;
    const x1 = X(e.from), y1 = Y(e.from), x2 = X(e.to), y2 = Y(e.to);
    let d;
    if (x1 === x2) d = `M${x1},${y1} L${x2},${y2}`;
    else if (e.kind === "merge") d = `M${x1},${y1} L${x1},${y2 + ROW_H} C${x1},${y2 + ROW_H / 2} ${x2},${y2 + ROW_H / 2} ${x2},${y2}`;
    else d = `M${x1},${y1} C${x1},${y1 - ROW_H / 2} ${x2},${y1 - ROW_H / 2} ${x2},${y1 - ROW_H} L${x2},${y2}`;
    const colorOf = e.kind === "merge" ? e.from : e.to;
    g.append(svg("path", {
      d, fill: "none",
      stroke: e.kind === "cherry_pick" ? "#8c959f" : IDX.laneColor.get(IDX.commit.get(colorOf).series),
      "stroke-width": e.kind === "cherry_pick" ? 1 : 2,
      "stroke-dasharray": e.kind === "cherry_pick" ? "2 3" : e.kind === "merge" ? "6 3" : null,
    }, svg("title", null, `${e.kind}${e.hidden_commits ? `（省略 ${e.hidden_commits} コミット）` : ""}`)));
    if (e.hidden_commits > 0 && e.kind !== "cherry_pick") {
      g.append(svg("text", { x: x2 + 5, y: (Math.min(y1, y2 + ROW_H) + y2) / 2 + 8, class: "hidden-count" }, `…${e.hidden_commits}`));
    }
  }

  for (const n of nodes) {
    const x = X(n.sha), y = Y(n.sha);
    const color = IDX.laneColor.get(n.series) || "#57606a";
    const isFix = n.roles.includes("fix");
    const isRelease = n.tags.some((t) => IDX.tag.get(t)?.kind === "release");
    const shape = isFix
      ? svg("rect", { x: x - 6, y: y - 6, width: 12, height: 12, transform: `rotate(45 ${x} ${y})`, fill: color })
      : svg("circle", { cx: x, cy: y, r: isRelease ? 7 : 5, fill: n.tags.length ? color : "#fff", stroke: color, "stroke-width": 2 });
    const grp = svg("g", { class: "node", onclick: () => showCommit(n) }, shape, svg("title", null, `${short(n.sha)} ${n.subject}`));
    if (hasMissing(n)) grp.append(svg("circle", { cx: x, cy: y, r: 11, fill: "none", stroke: "#cf222e", "stroke-width": 3 }));
    else if (hasProblem(n)) grp.append(svg("circle", { cx: x, cy: y, r: 11, fill: "none", stroke: "#9a6700", "stroke-width": 2, "stroke-dasharray": "3 2" }));
    g.append(grp);
    // 起点コミットの行で、未伝播の系列のレーンに × を置く
    for (const p of problems(n)) {
      if (p.state !== "missing" || !laneIdx.has(p.sid)) continue;
      const mx = LEFT + laneIdx.get(p.sid) * LANE_W;
      g.append(svg("g", null,
        svg("path", { d: `M${mx - 6},${y - 6} L${mx + 6},${y + 6} M${mx + 6},${y - 6} L${mx - 6},${y + 6}`, stroke: "#cf222e", "stroke-width": 3 }),
        svg("title", null, `${p.fix} が ${p.sid} に未伝播`)));
    }
  }

  const rows = el("div", { class: "rows", style: `padding-top:${TOP}px` }, nodes.map((n) => {
    const dim = onlyProblems && !hasProblem(n);
    return el("div", {
      class: "row", style: `height:${ROW_H}px;${dim ? "opacity:.35" : ""}`,
      onclick: (ev) => { if (ev.target.tagName !== "A") showCommit(n); },
    },
      el("code", { class: "sha" }, short(n.sha).slice(0, 7)),
      n.tags.map((t) => tagBadge(t)),
      n.fix_ids.map((f) => fixBadge(f, !unitFixes(n).includes(f))),
      el("span", { class: "subject", title: n.subject }, n.subject),
      el("span", { class: "date" }, n.date.slice(0, 10)));
  }));
  return el("div", { class: "tree" }, g, rows);
}

function showCommit(n) {
  openPanel(
    el("h3", null, "コミット"),
    el("dl", { class: "kv" },
      el("dt", null, "SHA"), el("dd", { class: "mono" }, n.sha),
      el("dt", null, "件名"), el("dd", null, n.subject),
      el("dt", null, "系列"), el("dd", null, seriesName(n.series)),
      el("dt", null, "作者"), el("dd", null, n.author),
      el("dt", null, "日時"), el("dd", null, n.date),
      el("dt", null, "コンポーネント"), el("dd", null, n.components.join(", ") || "—"),
      el("dt", null, "役割"), el("dd", null, n.roles.join(", ")),
      el("dt", null, "タグ"), el("dd", null, n.tags.length ? n.tags.map((t) => [tagBadge(t), " "]) : "—"),
      el("dt", null, "fix"), el("dd", null, n.fix_ids.length ? n.fix_ids.map((f) => [fixBadge(f), " "]) : "—"),
      el("dt", null, "cherry-pick 元"), el("dd", { class: "mono" }, n.cherry_picked_from.length ? n.cherry_picked_from.map((s) => el("div", null, s)) : "—"),
      el("dt", null, "親"), el("dd", { class: "mono" }, n.parents.map((s) => el("div", null, s)))));
}

// ---------------------------------------------------------------- 2. hotfix マトリクス
function renderMatrix(root, focusFix) {
  const onlyMissing = el("input", { type: "checkbox" });
  const includeWarn = el("input", { type: "checkbox" });
  const holder = el("div");
  const redraw = () => holder.replaceChildren(matrixTable(onlyMissing.checked, includeWarn.checked, focusFix));
  onlyMissing.addEventListener("change", redraw);
  includeWarn.addEventListener("change", redraw);
  root.append(
    el("h2", null, "hotfix マトリクス"),
    el("div", { class: "legend" }, Object.entries(STATE).map(([k, v]) => el("span", null, el("b", null, v.mark), " ", v.label))),
    el("div", { class: "controls" },
      el("label", null, onlyMissing, " 未適用のある fix だけ表示"),
      el("label", null, includeWarn, " patch-idのみ一致も含める")),
    holder);
  redraw();
  if (focusFix && IDX.fix.has(focusFix)) {
    showFix(IDX.fix.get(focusFix));
    document.getElementById("fixrow-" + focusFix)?.scrollIntoView({ block: "center" });
  }
}

function matrixTable(onlyMissing, includeWarn, focusFix) {
  const fixes = D.fixes.filter((f) => {
    if (!onlyMissing) return true;
    return Object.values(f.status).some((st) => st.state === "missing" || (includeWarn && st.state === "patch_id_only"));
  });
  if (!fixes.length) return el("p", { class: "ok" }, "該当する fix はありません。");
  return el("table", { class: "matrix" },
    el("tr", null, el("th", null, "Fix-ID"), el("th", null, "件名"), el("th", null, "コンポーネント"), el("th", null, "起点"),
      IDX.lanes.map((sid) => el("th", { title: seriesName(sid) }, el("span", { class: "swatch", style: `background:${IDX.laneColor.get(sid)}` }), " ", sid))),
    fixes.map((f) => el("tr", { id: "fixrow-" + f.id, class: f.id === focusFix ? "highlight" : null },
      el("td", null, el("a", { href: fixHref(f.id), class: "mono" }, f.id)),
      el("td", null, f.title),
      el("td", null, f.components.join(", ")),
      el("td", null, el("code", null, f.origin.series)),
      IDX.lanes.map((sid) => {
        const st = f.status[sid];
        const partial = st.units_total ? ` ${st.units_matched}/${st.units_total}` : "";
        const tip = [STATE[st.state].label, st.method && METHOD[st.method], st.reason,
          st.state === "excluded" && st.exclusion.reason].filter(Boolean).join(" ／ ");
        return el("td", {
          class: `cell st-${st.state}`, title: tip,
          onclick: () => showFix(f, sid),
        }, STATE[st.state].mark, st.state === "missing" ? partial : "",
          st.method && st.method !== "ancestry" && st.state === "applied" ? el("div", { class: "muted", style: "font-size:11px" }, st.method === "trailer" ? "trailer" : "-x") : "");
      }))));
}

function showFix(f, focusSeries) {
  const origin = IDX.commit.get(f.origin.commit);
  openPanel(
    el("h3", null, f.id, " ", f.title),
    el("dl", { class: "kv" },
      el("dt", null, "コンポーネント"), el("dd", null, f.components.join(", ") || "—"),
      el("dt", null, "起点"), el("dd", null, el("code", null, f.origin.series), " ", el("code", null, short(f.origin.commit))),
      el("dt", null, "コミット数"), el("dd", null, `${f.units.length}（すべて揃った系列だけを適用済みと判定）`),
      el("dt", null, "起点の作者"), el("dd", null, origin ? `${origin.author} ${origin.date}` : "—")),
    el("table", null,
      el("tr", null, el("th", null, "系列"), el("th", null, "状態"), el("th", null, "根拠")),
      IDX.lanes.map((sid) => {
        const st = f.status[sid];
        return el("tr", { class: sid === focusSeries ? "highlight" : null },
          el("td", null, el("code", null, sid)),
          el("td", { class: `st-${st.state}` }, STATE[st.state].mark, " ", STATE[st.state].label,
            st.units_total ? ` (${st.units_matched}/${st.units_total})` : ""),
          el("td", null,
            st.method ? el("div", null, METHOD[st.method]) : "",
            (st.commits || []).map((s) => el("div", { class: "mono" }, short(s), " ", IDX.commit.get(s)?.subject || "")),
            st.reason ? el("div", null, "理由: ", st.reason) : "",
            st.state === "excluded" ? [
              el("div", null, "理由: ", st.exclusion.reason),
              st.exclusion.by || st.exclusion.decided ? el("div", { class: "muted" }, [st.exclusion.by, st.exclusion.decided].filter(Boolean).join(" ")) : ""] : "",
            st.exclusion && st.state !== "excluded" ? el("div", { class: "sev-warning" }, "除外宣言あり（ただし適用済み）: ", st.exclusion.reason) : ""));
      })),
    el("h3", null, "この fix を含むリリースタグ"),
    el("div", null, D.tags.filter((t) => t.kind === "release" && t.fixes_added.includes(f.id)).map((t) => [tagBadge(t.name), " "])));
}

// ---------------------------------------------------------------- 3. リリース詳細
function renderRelease(root, name) {
  const current = IDX.tag.get(name) || D.tags.filter((t) => t.kind === "release").slice(-1)[0];
  const list = el("div", { class: "taglist" }, el("h3", null, "タグ"),
    IDX.lanes.map((sid) => {
      const tags = D.tags.filter((t) => t.series === sid).reverse();
      const rel = tags.filter((t) => t.kind === "release");
      const comp = tags.filter((t) => t.kind === "component");
      if (!tags.length) return null;
      const item = (t) => el("li", null, link(tagHref(t.name), t.name, { class: t === current ? "current" : null }));
      return el("div", null,
        el("div", null, el("span", { class: "swatch", style: `background:${IDX.laneColor.get(sid)}` }), " ", el("b", null, sid)),
        el("ul", null, rel.map(item)),
        comp.length ? el("details", { open: comp.includes(current) }, el("summary", null, `コンポーネントタグ（${comp.length}）`), el("ul", null, comp.map(item))) : null);
    }));
  root.append(el("h2", null, "リリース詳細"), el("div", { class: "split" }, list, current ? releaseDetail(current) : el("p", null, "タグがありません。")));
}

// 含まれる fix の赤バッジ（未伝播）は他の系列でのこと。このリリースに入っていないと誤読されないよう注記する
function missingNote(ids) {
  const red = ids.some((id) => (IDX.problemSeriesByFix.get(id) || []).some((p) => p.state === "missing"));
  return red ? el("p", { class: "muted" }, "赤いバッジは他の系列で未伝播の fix（このリリースには含まれる）") : null;
}

function releaseDetail(t) {
  const prev = t.previous ? IDX.tag.get(t.previous) : null;
  const changed = new Set(t.diff.map((d) => d.component));
  const comps = [...new Set([...Object.keys(t.snapshot), ...Object.keys(prev ? prev.snapshot : {})])].sort();
  const dep = IDX.depsAt.get(`${t.series}|${t.name}`);
  const fixRow = (id) => {
    const f = IDX.fix.get(id);
    return el("li", null, fixBadge(id), " ", f ? f.title : "");
  };
  // この系列で missing の fix のうち、このタグにも入っていないもの
  const notIn = (IDX.missingBySeries.get(t.series) || []).filter((id) => !t.fixes_included.includes(id));
  return el("div", null,
    el("h3", null, t.name),
    el("dl", { class: "kv" },
      el("dt", null, "種別"), el("dd", null, t.kind === "release" ? "製品リリース" : `コンポーネント（${t.component}）`),
      el("dt", null, "系列"), el("dd", null, t.series ? seriesName(t.series) : "（追跡外）"),
      el("dt", null, "コミット"), el("dd", { class: "mono" }, t.commit),
      el("dt", null, "日時"), el("dd", null, t.date, t.annotated ? "" : el("span", { class: "sev-warning" }, "（軽量タグ）")),
      el("dt", null, "前版"), el("dd", null, prev ? tagBadge(prev.name) : "なし")),
    el("h3", null, "コンポーネント構成"),
    el("table", null,
      el("tr", null, el("th", null, "コンポーネント"), el("th", null, "バージョン"), el("th", null, prev ? `前版（${prev.name}）` : "前版")),
      comps.map((c) => el("tr", { class: changed.has(c) ? "changed" : null },
        el("td", null, c), el("td", { class: "mono" }, t.snapshot[c] ?? "（なし）"),
        el("td", { class: "mono" }, prev ? (prev.snapshot[c] ?? "（なし）") : "—")))),
    el("h3", null, `前版から追加された fix（${t.fixes_added.length}）`),
    missingNote(t.fixes_added),
    t.fixes_added.length ? el("ul", null, t.fixes_added.map(fixRow)) : el("p", { class: "muted" }, "なし"),
    el("h3", null, `含まれる fix（${t.fixes_included.length}）`),
    missingNote(t.fixes_included),
    t.fixes_included.length ? el("ul", null, t.fixes_included.map(fixRow)) : el("p", { class: "muted" }, "なし"),
    notIn.length ? [el("h3", { class: "sev-error" }, "この系列で未適用の fix（このタグにも含まれない）"), el("ul", null, notIn.map(fixRow))] : null,
    dep ? [el("h3", null, "依存チェック（このタグ時点）"), depTable(dep)] : null);
}

// ---------------------------------------------------------------- 4. 依存チェック
function renderDeps(root, focusSeries) {
  const comps = D.components.map((c) => c.name);
  const headVio = new Set();
  for (const c of D.dependency_checks) {
    if (c.at.kind !== "head") continue;
    for (const r of c.results) if (!r.ok) { headVio.add(`${c.series}|${r.component}`); headVio.add(`${c.series}|${r.dependency}`); }
  }
  root.append(
    el("h2", null, "依存チェック"),
    el("h3", null, "系列 × コンポーネント（HEAD 時点のバージョン）"),
    el("table", null,
      el("tr", null, el("th", null, "系列"), comps.map((c) => el("th", null, c)), el("th", null, "依存")),
      IDX.lanes.map((sid) => {
        const s = IDX.series.get(sid);
        const head = IDX.depsAt.get(`${sid}|${s.branch}`);
        return el("tr", null,
          el("td", null, el("a", { href: "#/deps/" + encodeURIComponent(sid) }, sid)),
          comps.map((c) => el("td", { class: "mono " + (headVio.has(`${sid}|${c}`) ? "vio" : "") }, s.head_snapshot[c] ?? "—")),
          el("td", null, !head ? "—" : head.ok ? el("span", { class: "ok" }, "OK") : el("span", { class: "ng" }, "違反")));
      })));

  for (const sid of IDX.lanes) {
    const checks = D.dependency_checks.filter((c) => c.series === sid);
    if (!checks.length) continue;
    const ng = checks.filter((c) => !c.ok);
    const card = el("div", { class: "card" + (ng.length ? " ng" : ""), id: "deps-" + sid },
      el("h3", null, el("span", { class: "swatch", style: `background:${IDX.laneColor.get(sid)}` }), " ", seriesName(sid), " ",
        ng.length ? el("span", { class: "ng" }, `違反 ${ng.length} 時点`) : el("span", { class: "ok" }, "すべて充足")),
      checks.map((c) => el("details", { open: !c.ok || c.at.kind === "head" },
        el("summary", null, c.at.kind === "head" ? "HEAD " : "タグ ",
          c.at.kind === "tag" ? tagBadge(c.at.ref) : el("code", null, c.at.ref), " ",
          el("code", { class: "muted" }, short(c.at.commit)), " ",
          c.ok ? el("span", { class: "ok" }, "OK") : el("span", { class: "ng" }, "違反")),
        depTable(c))));
    root.append(card);
  }
  if (focusSeries) document.getElementById("deps-" + focusSeries)?.scrollIntoView({ block: "start" });
}

function depTable(check) {
  if (!check.results.length) return el("p", { class: "muted" }, "依存制約なし");
  return el("table", null,
    el("tr", null, el("th", null, "コンポーネント"), el("th", null, "依存先"), el("th", null, "制約"), el("th", null, "実際"), el("th", null, "結果")),
    check.results.map((r) => el("tr", { class: r.ok ? null : "ng" },
      el("td", null, r.component), el("td", null, r.dependency),
      el("td", { class: "mono" }, r.constraint), el("td", { class: "mono" }, r.actual ?? "—"),
      el("td", null, r.ok ? el("span", { class: "ok" }, "✔") : el("span", { class: "ng" }, "✖ ", r.error || "制約を満たさない")))));
}

// ---------------------------------------------------------------- 5. 検索
function renderSearch(root, q) {
  const input = el("input", { id: "q", type: "search", value: q, placeholder: "コンポーネント名・バージョン・Fix-ID・系列（空白区切りで AND）", autocomplete: "off" });
  const results = el("div", { class: "results" });
  const update = () => {
    history.replaceState(null, "", "#/search/" + encodeURIComponent(input.value));
    results.replaceChildren(searchResults(input.value));
  };
  input.addEventListener("input", update);
  root.append(el("h2", null, "検索"), input, results);
  results.replaceChildren(searchResults(q));
  input.focus();
}

function searchResults(q) {
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return el("p", { class: "muted" }, "例: hal 1.2.1 ／ FIX-102 ／ gamma ／ acme lib-comm");
  const match = (...fields) => {
    const hay = fields.filter(Boolean).join(" ").toLowerCase();
    return terms.every((t) => hay.includes(t));
  };
  const hl = (text) => {
    const s = String(text);
    const lower = s.toLowerCase();
    const out = [];
    let i = 0;
    while (i < s.length) {
      let best = -1, len = 0;
      for (const t of terms) {
        const j = lower.indexOf(t, i);
        if (j >= 0 && (best < 0 || j < best)) { best = j; len = t.length; }
      }
      if (best < 0) { out.push(s.slice(i)); break; }
      out.push(s.slice(i, best), el("mark", null, s.slice(best, best + len)));
      i = best + len;
    }
    return out;
  };

  const series = D.series.filter((s) => match(s.id, s.label, s.customer, s.kind, s.status));
  // 系列 HEAD のコンポーネント構成（「hal 1.2.1」で、どの系列に入っているかを引ける）
  const placements = [];
  for (const s of D.series) {
    for (const [c, v] of Object.entries(s.head_snapshot)) {
      if (match(c, v, s.id, s.label, s.customer)) placements.push({ s, c, v });
    }
  }
  const tags = D.tags.filter((t) => match(t.name, t.component, t.version, t.series,
    ...Object.entries(t.snapshot).map(([c, v]) => `${c}@${v}`)));
  const fixes = D.fixes.filter((f) => match(f.id, f.title, ...f.components, f.origin.series));

  const section = (title, items) => el("section", null, el("h3", null, `${title}（${items.length}）`),
    items.length ? el("ul", null, items.slice(0, 200)) : el("p", { class: "muted" }, "該当なし"),
    items.length > 200 ? el("p", { class: "muted" }, "先頭 200 件のみ表示") : null);

  return el("div", null,
    section("系列", series.map((s) => el("li", null, link("#/deps/" + encodeURIComponent(s.id), hl(s.id)), " ", s.label ? hl(s.label) : "", ` ／ ${s.kind} ／ ${s.status}`))),
    section("系列に入っているコンポーネント（HEAD）", placements.map(({ s, c, v }) => el("li", null,
      el("code", null, hl(s.id)), " : ", hl(c), " ", el("code", null, hl(v))))),
    section("タグ", tags.map((t) => el("li", null, link(tagHref(t.name), hl(t.name)), " ",
      el("span", { class: "muted" }, t.series || "", " ", t.date.slice(0, 10))))),
    section("Fix", fixes.map((f) => {
      const probs = IDX.problemSeriesByFix.get(f.id) || [];
      return el("li", null, link(fixHref(f.id), hl(f.id)), " ", hl(f.title), " ",
        el("span", { class: "muted" }, `[${f.components.join(", ")}]`), " ",
        probs.length ? probs.map((p) => el("span", { class: p.state === "missing" ? "sev-error" : "sev-warning" }, ` ${p.sid}: ${STATE[p.state].label}`)) : el("span", { class: "ok" }, "伝播済み"));
    })));
}

init();
