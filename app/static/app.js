// 확인 화면 스크립트: 서버(/api/*)에서 받은 데이터를 textContent로만 그려서 청크 본문이 HTML로 해석되지 않게 한다.
const $ = (sel, el = document) => el.querySelector(sel);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* JSON이 아닌 응답 */ }
  if (!res.ok) throw new Error((data && data.detail) || `요청 실패 (${res.status})`);
  return data;
}

const short = (id) => id
  .replace("hana-deposit-sheet__", "설명서:").replace("hana-deposit-terms__", "특약:")
  .replace("hana-deposit-basic-terms__", "기본:").replace("hana-lumpsum-terms__", "거치식:");

// ---------- 상태 표시줄 ----------
async function loadStatus() {
  const box = $("#status");
  box.replaceChildren();
  try {
    const s = await api("/api/status");
    box.append(
      h("span", { class: "pill " + (s.chunks ? "ok" : "bad") }, `청크 ${s.chunks}개`),
      h("span", { class: "pill " + (s.index ? "ok" : "bad") }, s.index == null ? "인덱스 없음" : `인덱스 ${s.index}개`),
      h("span", { class: "pill" }, s.embed_backend === "fake" ? "임베딩 fake(테스트)" : `임베딩 ${s.embed_model}`),
      h("span", { class: "pill" }, s.hints == null ? "검색용 설명 -" : `검색용 설명 ${s.hints}개 적용`),
    );
    return s;
  } catch (e) {
    box.append(h("span", { class: "pill bad" }, "서버 응답 없음"));
  }
}

// ---------- 탭 ----------
$("#tabs").addEventListener("click", (ev) => {
  const name = ev.target.dataset && ev.target.dataset.tab;
  if (!name) return;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b === ev.target));
  ["search", "chunks", "check"].forEach((t) => { $("#tab-" + t).hidden = t !== name; });
});

// ---------- 공통: 선택 상자 채우기 ----------
let META = { docs: [], chunk_types: [], products: [] };
async function loadMeta() {
  META = await api("/api/meta");
  const fill = (sel, items) => items.forEach((it) => sel.append(h("option", { value: it.value }, it.label)));
  fill($("#product"), META.products.map((p) => ({ value: p, label: p })));
  fill($("#ctype"), META.chunk_types.map((t) => ({ value: t, label: t })));
  fill($("#ctype2"), META.chunk_types.map((t) => ({ value: t, label: t })));
  fill($("#cdoc"), META.docs.map((d) => ({ value: d.id, label: d.title || d.id })));
}

// ---------- 검색 탭 ----------
function sourceLine(src) {
  const where = src.article || src.section || "";
  const page = src.page != null ? ` · ${src.page}쪽` : "";
  return `${src.doc_title || ""} · ${where}${page}`;
}
function versionLine(src) {
  const parts = [];
  if (src.review_no) parts.push(`심의 ${src.review_no}${src.review_date ? " (" + src.review_date + ")" : ""}`);
  if (src.revision_date) parts.push(`${src.revision_kind || "개정"} ${src.revision_date}`);
  if (src.effective_date) parts.push(`적용일 ${src.effective_date}`);
  if (src.as_of) parts.push(`기준일 ${src.as_of}`);
  return parts.join(" · ");
}
function textBlock(text) {
  const pre = h("pre", { class: "text" }, text);
  const long = text.length > 280 || text.split("\n").length > 7;
  const btn = h("button", { type: "button", class: "toggle", onclick: () => {
    pre.classList.toggle("open");
    btn.textContent = pre.classList.contains("open") ? "접기" : "전체 보기";
  } }, "전체 보기");
  return h("div", {}, pre, long ? btn : null);
}
function resultCard(r) {
  const sim = Math.max(0, Math.min(1, 1 - r.distance));
  return h("div", { class: "card" },
    h("header", {},
      h("span", { class: "rank" }, r.rank),
      h("span", { class: "tag" }, r.chunk_type || "-"),
      h("span", { class: "muted" }, short(r.chunk_id)),
      h("span", { class: "dist" }, `거리 ${r.distance.toFixed(3)}`, h("span", { class: "bar" }, h("i", { style: `width:${(sim * 100).toFixed(0)}%` }))),
    ),
    h("div", { class: "source" }, sourceLine(r.source)),
    h("div", { class: "ver" }, versionLine(r.source)),
    textBlock(r.text),
  );
}

async function runSearch(question) {
  const msg = $("#search-msg"), box = $("#results");
  msg.className = "msg"; msg.textContent = "검색 중..."; box.replaceChildren();
  const ctype = $("#ctype").value;
  try {
    const t0 = performance.now();
    const data = await api("/api/search", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: question, product: $("#product").value || null,
                             chunk_types: ctype ? [ctype] : null, k: Number($("#k").value) || 5 }),
    });
    const ms = Math.round(performance.now() - t0);
    if (data.status === "error") { msg.className = "msg err"; msg.textContent = data.message; return; }
    if (data.status === "not_found") { msg.textContent = `결과 없음 (not_found) · ${ms}ms`; return; }
    msg.textContent = `${data.results.length}개 결과 · ${ms}ms`;
    data.results.forEach((r) => box.append(resultCard(r)));
  } catch (e) {
    msg.className = "msg err"; msg.textContent = e.message;
  }
}
$("#search-form").addEventListener("submit", (ev) => { ev.preventDefault(); runSearch($("#q").value.trim()); });

async function loadExamples() {
  const box = $("#examples");
  const items = await api("/api/examples");
  items.forEach((it) => box.append(h("button", { type: "button", class: "chip g-" + it.group, title: it.group,
    onclick: () => { $("#q").value = it.question; runSearch(it.question); } }, it.question)));
}

// ---------- 청크 탭 ----------
async function loadChunkList() {
  const params = new URLSearchParams({ q: $("#cq").value, doc: $("#cdoc").value, type: $("#ctype2").value });
  const data = await api("/api/chunks?" + params);
  $("#ctotal").textContent = `${data.total}개`;
  const list = $("#chunk-list");
  list.replaceChildren();
  data.items.forEach((it) => list.append(h("li", { "data-id": it.chunk_id, onclick: (ev) => showChunk(it.chunk_id, ev.currentTarget) },
    `${short(it.chunk_id)}`, h("small", {}, `${it.chunk_type || ""} · ${it.label} · ${it.length}자`))));
}
async function showChunk(id, li) {
  document.querySelectorAll("#chunk-list li").forEach((x) => x.classList.toggle("sel", x === li));
  const c = await api("/api/chunks/" + encodeURIComponent(id));
  const dl = h("dl", { class: "kv" });
  Object.entries(c.metadata).forEach(([k, v]) => dl.append(h("dt", {}, k), h("dd", {}, String(v))));
  $("#chunk-detail").replaceChildren(h("h3", {}, c.chunk_id), dl, h("pre", { class: "text open" }, c.text));
}
["cq", "cdoc", "ctype2"].forEach((id) => $("#" + id).addEventListener("input", loadChunkList));

// ---------- 일괄 확인 탭 ----------
async function runCheck() {
  const msg = $("#check-msg"), sum = $("#check-summary"), tbl = $("#check-table");
  msg.className = "msg"; msg.textContent = "질문을 검색하는 중..."; sum.replaceChildren(); tbl.replaceChildren();
  try {
    const data = await api("/api/check?k=" + (Number($("#ck").value) || 5));
    msg.textContent = "";
    const s = data.summary;
    Object.entries(s.groups).forEach(([g, v]) => sum.append(h("div", { class: "sbox" },
      h("b", {}, `${g} (${v.n}문항)`), h("span", {}, `1위 ${v.top1}/${v.n}`), h("div", { class: "muted" }, `3위 안 ${v.top3}/${v.n} · MRR ${v.mrr}`))));
    sum.append(h("div", { class: "sbox" }, h("b", {}, "함정 청크가 앞선 질문"), h("span", {}, `${s.traps}개`),
      h("div", { class: "muted" }, "약정금리 이자계산 문장 등")));
    const nd = s.no_answer_distances.filter((d) => d != null);
    sum.append(h("div", { class: "sbox" }, h("b", {}, "답 없는 질문의 가장 가까운 거리"), h("span", {}, nd.map((d) => d.toFixed(2)).join(" / ") || "-"),
      h("div", { class: "muted" }, s.limit == null ? "not_found 기준을 정할 때 사용" : `거리 한도 ${s.limit}: ${s.no_answer_rejected}/${nd.length}개 거부`)));
    const rows = data.rows.map((r) => {
      const mark = !r.has_answer ? ["·", ""] : r.rank === 1 ? ["✓", "m1"] : r.rank ? ["△", "m2"] : ["✗", "m0"];
      return h("tr", {},
        h("td", {}, r.group),
        h("td", {}, r.question),
        h("td", { class: "mark " + mark[1] }, mark[0]),
        h("td", {}, r.has_answer ? (r.rank ? `${r.rank}위` : `${data.k}위 밖`) : (s.limit == null ? "-" : (r.rejected ? "한도로 거부" : "한도 통과"))),
        h("td", {}, r.trapped ? "함정 앞섬" : ""),
        h("td", {}, r.top.map((t) => `${t.short} (${t.distance.toFixed(2)})`).join("  ·  ")));
    });
    tbl.append(h("table", {}, h("thead", {}, h("tr", {}, ["그룹", "질문", "결과", "순위", "함정", "상위 3개 (거리)"].map((x) => h("th", {}, x)))), h("tbody", {}, rows)));
  } catch (e) {
    msg.className = "msg err"; msg.textContent = e.message;
  }
}
$("#run-check").addEventListener("click", runCheck);

// ---------- 시작 ----------
(async function init() {
  await loadStatus();
  try { await loadMeta(); await loadChunkList(); await loadExamples(); }
  catch (e) { $("#search-msg").className = "msg err"; $("#search-msg").textContent = e.message; }
})();
