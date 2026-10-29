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

// ---------- 검색 설정 (검색 방식 · 리랭킹): 검색 · 일괄 확인 · 채팅 탭이 함께 쓴다 ----------
let STATUS = null;
function initSettings(s) {
  if (!initSettings.done) { $("#mode").value = s.defaults.mode; $("#rerank").value = s.defaults.rerank; initSettings.done = true; }
  const notes = [];
  if (!s.bm25) notes.push("키워드 인덱스 없음 (python rag/ingest.py 다시 실행)");
  if (!s.rerank_available.cross) notes.push("교차 인코더는 pip install fastembed 가 필요해요");
  $("#settings-note").textContent = notes.join(" · ");
}
const searchSettings = () => ({ mode: $("#mode").value, rerank: $("#rerank").value });
function scoreLine(sc) {
  if (!sc) return "";
  const p = [];
  if (sc.dense_rank != null) p.push(`벡터 ${sc.dense_rank}위`);
  if (sc.bm25_rank != null) p.push(`키워드 ${sc.bm25_rank}위 (${sc.bm25})`);
  if (sc.rrf != null) p.push(`RRF ${sc.rrf}`);
  if (sc.rerank != null) p.push(`리랭크 ${sc.rerank}`);
  return p.join(" · ");
}

// ---------- 상태 표시줄 ----------
async function loadStatus() {
  const box = $("#status");
  box.replaceChildren();
  try {
    const s = await api("/api/status");
    STATUS = s; initSettings(s);
    box.append(
      h("span", { class: "pill " + (s.chunks ? "ok" : "bad") }, `청크 ${s.chunks}개`),
      h("span", { class: "pill " + (s.index ? "ok" : "bad") }, s.index == null ? "인덱스 없음" : `인덱스 ${s.index}개`),
      h("span", { class: "pill" }, s.embed_backend === "fake" ? "임베딩 fake(테스트)" : `임베딩 ${s.embed_model}`),
      h("span", { class: "pill" }, s.hints == null ? "검색용 설명 -" : `검색용 설명 ${s.hints}개 적용`),
      h("span", { class: "pill" }, `답변 모델 ${s.chat_model}`),
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
  ["search", "chunks", "check", "chat", "calc", "tools", "agent"].forEach((t) => { $("#tab-" + t).hidden = t !== name; });
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
    scoreLine(r.scores) ? h("div", { class: "ver" }, scoreLine(r.scores)) : null,
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
                             chunk_types: ctype ? [ctype] : null, k: Number($("#k").value) || 5, ...searchSettings() }),
    });
    const ms = Math.round(performance.now() - t0);
    if (data.status === "error") { msg.className = "msg err"; msg.textContent = data.message; return; }
    if (data.status === "not_found") { msg.textContent = `결과 없음 (not_found) · ${ms}ms`; return; }
    const m = data.ms || {};
    const parts = [`임베딩 ${m.embed}ms`];
    if (m.dense != null) parts.push(`벡터 ${m.dense}ms`);
    if (m.bm25 != null) parts.push(`키워드 ${m.bm25}ms`);
    if (m.rerank != null) parts.push(`리랭크 ${m.rerank}ms`);
    msg.textContent = `${data.results.length}개 결과 (${data.mode}${data.rerank ? " + " + data.rerank : ""}) · ${parts.join(" · ")} · 총 ${ms}ms`;
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
    const sset = searchSettings();
    const data = await api(`/api/check?k=${Number($("#ck").value) || 5}&mode=${sset.mode}&rerank=${sset.rerank}`);
    msg.textContent = `검색 방식 ${data.mode} · 리랭킹 ${data.rerank}`;
    const s = data.summary;
    Object.entries(s.groups).forEach(([g, v]) => sum.append(h("div", { class: "sbox" },
      h("b", {}, `${g} (${v.n}문항)`), h("span", {}, `1위 ${v.top1}/${v.n}`), h("div", { class: "muted" }, `3위 안 ${v.top3}/${v.n} · MRR ${v.mrr}`))));
    sum.append(h("div", { class: "sbox" }, h("b", {}, "함정 청크가 앞선 질문"), h("span", {}, `${s.traps}개`),
      h("div", { class: "muted" }, "약정금리 이자계산 문장 등")));
    if (s.latency) sum.append(h("div", { class: "sbox" }, h("b", {}, "검색 시간 (중앙값)"), h("span", {}, `${s.latency.median_total}ms`),
      h("div", { class: "muted" }, `최대 ${s.latency.max_total}ms` + (s.latency.median_rerank != null ? ` · 리랭크 ${s.latency.median_rerank}ms` : ""))));
    const nd = s.no_answer_distances.filter((d) => d != null);
    sum.append(h("div", { class: "sbox" }, h("b", {}, "답 없는 질문의 가장 가까운 거리"), h("span", {}, nd.map((d) => d.toFixed(2)).join(" / ") || "-"),
      h("div", { class: "muted" }, s.limit == null ? "not_found 기준을 정할 때 사용" : `거리 한도 ${s.limit}: ${s.no_answer_rejected}/${nd.length}개 거부`)));
    const rows = data.rows.map((r) => {
      const mark = !r.has_answer ? ["·", ""] : r.rank === 1 ? ["✓", "m1"] : r.rank ? ["△", "m2"] : ["✗", "m0"];
      return h("tr", {},
        h("td", {}, r.group),
        h("td", {}, r.question),
        h("td", { class: "mark " + mark[1] }, mark[0]),
        h("td", {}, r.has_answer ? (r.rank ? `${r.rank}위` : `${data.k}위 밖`) : (r.nearest == null ? "결과 없음" : (s.limit == null ? "-" : (r.rejected ? "한도로 거부" : "한도 통과")))),
        h("td", {}, r.trapped ? "함정 앞섬" : ""),
        h("td", {}, r.top.map((t) => `${t.short} (${t.distance.toFixed(2)})`).join("  ·  ")));
    });
    tbl.append(h("table", {}, h("thead", {}, h("tr", {}, ["그룹", "질문", "결과", "순위", "함정", "상위 3개 (거리)"].map((x) => h("th", {}, x)))), h("tbody", {}, rows)));
  } catch (e) {
    msg.className = "msg err"; msg.textContent = e.message;
  }
}
$("#run-check").addEventListener("click", runCheck);


// ---------- 채팅 탭 (SSE 스트리밍) ----------
// POST 요청이라 EventSource를 못 쓰고, fetch의 ReadableStream으로 직접 읽어 "event:/data:" 블록을 해석한다.
let chatAbort = null;

function parseSSE(block) {
  let name = "message";
  const data = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  return data.length ? { name, data: JSON.parse(data.join("\n")) } : null;
}

function sourceCards(results) {
  const box = h("details", { class: "chat-sources", open: true }, h("summary", {}, `근거 ${results.length}개`));
  results.forEach((r, i) => {
    const card = resultCard(r);
    card.querySelector(".rank").replaceWith(h("span", { class: "src-num" }, `[${i + 1}]`));
    box.append(card);
  });
  return box;
}

async function sendChat(question) {
  const log = $("#chat-log"), send = $("#chat-send"), stop = $("#chat-stop");
  if (log.querySelector(":scope > p.muted")) log.replaceChildren();
  const status = h("div", { class: "chat-status muted" }, "요청 중...");
  const answer = h("div", { class: "msg-bot streaming" });
  const timing = h("div", { class: "muted small" });
  const sources = h("div");
  log.append(h("div", { class: "msg-user" }, question), h("div", { class: "bot-wrap" }, status, answer, timing, sources));
  send.disabled = true; stop.hidden = false;
  chatAbort = new AbortController();
  const scroll = () => { if (answer.scrollIntoView) answer.scrollIntoView({ block: "nearest" }); };   // 스크롤 실패가 답변 출력을 끊지 않게

  const handlers = {
    status: (d) => { status.textContent = d.message; },
    sources: (d) => { sources.replaceChildren(d.results.length ? sourceCards(d.results) : ""); },
    token: (d) => { answer.append(document.createTextNode(d.text)); scroll(); },
    done: (d) => {
      status.textContent = "완료";
      const parts = d.search_ms != null ? [`검색 ${d.search_ms}ms`] : [];      // 가입금액 안내처럼 검색을 거치지 않는 답변은 검색 시간이 없다
      if (d.first_token_ms != null) parts.push(`첫 글자 ${d.first_token_ms}ms`);
      parts.push(`전체 ${d.elapsed_ms}ms`);
      if (d.model) parts.push(d.model);
      timing.textContent = parts.join(" · ");
    },
    error: (d) => { status.className = "chat-status err"; status.textContent = d.message; },
  };

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, ...searchSettings() }), signal: chatAbort.signal,
    });
    if (!res.ok) throw new Error(`요청 실패 (${res.status})`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {          // 이벤트 하나는 빈 줄로 끝난다
        const ev = parseSSE(buf.slice(0, idx));
        buf = buf.slice(idx + 2);
        if (ev && handlers[ev.name]) handlers[ev.name](ev.data);
      }
    }
  } catch (e) {
    status.className = "chat-status err";
    status.textContent = e.name === "AbortError" ? "중지했어요" : e.message;
  } finally {
    answer.classList.remove("streaming");
    send.disabled = false; stop.hidden = true; chatAbort = null;
  }
}
$("#chat-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = $("#chat-q").value.trim();
  if (!q || chatAbort) return;
  $("#chat-q").value = "";
  sendChat(q);
});
$("#chat-stop").addEventListener("click", () => { if (chatAbort) chatAbort.abort(); });

// ---------- 이자 계산 탭 ----------
const CALC_STATUS = {
  ok: ["계산 완료", "ok"], need_base_rate: ["기본금리가 필요해요", "warn"], missing_info: ["값이 더 필요해요", "warn"],
  invalid_input: ["입력값을 확인해 주세요", "bad"], not_early_termination: ["중도해지가 아니에요", "warn"],
};
let CALC_EXPECTED = null;                                   // 기준 사례(A~G)를 불러왔을 때만 기대값과 비교한다
const won = (n) => `${Number(n).toLocaleString("ko-KR")}원`;

function renderCalc(data) {
  const box = $("#calc-result");
  box.replaceChildren();
  const [label, tone] = CALC_STATUS[data.status] || [data.status, "warn"];
  box.append(h("div", { class: "calc-status " + tone }, label), h("p", {}, data.message || data.summary || ""));
  if (data.errors && data.errors.length) box.append(h("ul", {}, data.errors.map((e) => h("li", {}, `${e.label}: ${e.message}`))));
  if (data.missing && data.missing.length) box.append(h("p", { class: "muted" }, "필요한 값: " + data.missing.join(", ")));
  if (data.status !== "ok") {
    if (data.section) box.append(h("p", { class: "muted" }, `경과 ${data.elapsed_days}일 (${data.completed_months}개월) · ${data.section}`));
    return;
  }
  const cells = [["세전 이자", won(data.interest_krw)], ["세전 지급 예상액", won(data.payout_krw)], ["중도해지금리", `연 ${data.rate_pct}%`],
                 ["구간", data.section], ["경과 / 계약", `${data.elapsed_days}일 / ${data.contract_days}일 (${data.completed_months}개월)`]];
  box.append(h("div", { class: "summary" }, cells.map(([k, v]) => h("div", { class: "sbox" }, h("b", {}, k), h("span", {}, v)))));
  if (CALC_EXPECTED) {
    const same = data.interest_krw === CALC_EXPECTED.interest;
    box.append(h("p", { class: same ? "calc-ok" : "calc-bad" }, `케이스 ${CALC_EXPECTED.id} 기대값 ${won(CALC_EXPECTED.interest)} → ${same ? "일치 ✓" : "불일치 ✗"}`));
  }
  box.append(h("h4", {}, "계산식"), h("pre", { class: "text open" }, data.formula.join("\n")),
             h("h4", {}, "사용한 가정"), h("ul", {}, data.assumptions.map((a) => h("li", {}, a))));
  if (data.warnings.length) box.append(h("h4", {}, "주의"), h("ul", {}, data.warnings.map((w) => h("li", {}, w))));
}

async function runCalc() {
  try {
    const data = await api("/api/calc", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ principal: $("#c-principal").value, start_date: $("#c-start").value, maturity_date: $("#c-maturity").value,
                             termination_date: $("#c-termination").value, base_rate: $("#c-base").value }),
    });
    renderCalc(data);
  } catch (e) {
    $("#calc-result").replaceChildren(h("p", { class: "msg err" }, e.message));
  }
}
function fillCalc(c) {
  $("#c-principal").value = c.principal; $("#c-start").value = c.start_date; $("#c-maturity").value = c.maturity_date;
  $("#c-termination").value = c.termination_date; $("#c-base").value = c.base_rate == null ? "" : c.base_rate;
  CALC_EXPECTED = c;
  runCalc();
}
["c-principal", "c-start", "c-maturity", "c-termination", "c-base"].forEach((id) => $("#" + id).addEventListener("input", () => { CALC_EXPECTED = null; }));
$("#calc-form").addEventListener("submit", (ev) => { ev.preventDefault(); runCalc(); });
async function loadCalcCases() {
  const cases = await api("/api/calc/cases");
  cases.forEach((c) => $("#calc-cases").append(h("button", { type: "button", class: "chip", onclick: () => fillCalc(c) }, `케이스 ${c.id} · ${c.label}`)));
}

// ---------- 도구 호출 탭 (5일차) ----------
let TOOL_CASE = null;                                       // 확인용 질문을 골랐을 때만 채점한다
const ERR_TEXT = {
  missing_call: "불러야 할 도구를 안 부름", extra_call: "부르면 안 되는 도구를 부름", unknown_tool: "없는 도구 이름",
  invalid_call: "인자를 읽을 수 없음", empty_query: "검색어가 비어 있음", extra_arg: "질문에 없는 값을 넣음(지어냄)",
  missing_arg: "질문에 있는 값을 빠뜨림", date_not_iso: "날짜가 YYYY-MM-DD가 아님", unparsable_principal: "원금을 숫자로 못 읽음",
};
const errText = (e) => (ERR_TEXT[e.code] || (e.code.startsWith("wrong_") ? "값이 다름" : e.code)) + (e.field ? ` (${e.field})` : "")
  + (e.detail != null ? `: ${JSON.stringify(e.detail)}` : "");

function expectText(exp) {
  if (exp.tool === null) return "기대: 도구를 부르지 않음";
  if (exp.tool === "search_terms") return "기대: search_terms (검색)";
  if (exp.strict) return "기대: calc_interest " + JSON.stringify(exp.args);
  return "기대: 되묻기, 또는 calc_interest에 아는 값만 " + JSON.stringify(exp.allowed || {});
}

function renderTools(data) {
  const box = $("#tools-result");
  box.replaceChildren();
  if (data.grade) {
    box.append(h("p", { class: data.grade.pass ? "calc-ok" : "calc-bad" }, `케이스 ${TOOL_CASE.id}: ${data.grade.pass ? "통과 ✓" : "실패 ✗"}`));
    if (data.grade.errors.length) box.append(h("ul", {}, data.grade.errors.map((e) => h("li", {}, errText(e)))));
    if (data.grade.notes.length) box.append(h("p", { class: "muted small" }, data.grade.notes.join(" · ")));
  }
  if (TOOL_CASE) box.append(h("p", { class: "muted small" }, expectText(TOOL_CASE.expect)));
  if (!data.tool_calls.length) box.append(h("div", { class: "tool-call" }, h("b", {}, "도구 호출 없음"), h("pre", {}, data.content || "(빈 답변)")));
  data.tool_calls.forEach((c, i) => {
    const card = h("div", { class: "tool-call" }, h("b", {}, c.name), h("pre", {}, JSON.stringify(c.args, null, 2)));
    const r = data.tool_results && data.tool_results[i];
    if (r) card.append(h("p", { class: "muted small" }, "도구 결과 (status: " + r.result.status + ")"), h("pre", { class: "text" }, JSON.stringify(r.result, null, 2)));
    box.append(card);
  });
  (data.invalid_calls || []).forEach((c) => box.append(h("div", { class: "tool-call" }, h("b", {}, "읽을 수 없는 호출"), h("pre", {}, JSON.stringify(c, null, 2)))));
  box.append(h("p", { class: "muted small" }, `설명 문구 ${data.variant} · 모델 응답 ${data.ms}ms`));
}

async function askTools() {
  const q = $("#t-q").value.trim();
  if (!q) return;
  const btn = $("#t-send");
  btn.disabled = true;
  $("#tools-result").replaceChildren(h("p", { class: "msg" }, "모델이 도구를 고르는 중..."));
  try {
    const body = { question: q, variant: $("#t-variant").value, execute: $("#t-exec").checked };
    if (TOOL_CASE && TOOL_CASE.question === q) body.case_id = TOOL_CASE.id;
    renderTools(await api("/api/tools/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
  } catch (e) {
    $("#tools-result").replaceChildren(h("p", { class: "msg err" }, e.message));
  } finally {
    btn.disabled = false;
  }
}
async function loadToolSpec() {
  const spec = await api("/api/tools/spec?variant=" + encodeURIComponent($("#t-variant").value));
  $("#tools-spec-text").textContent = "[시스템 프롬프트]\n" + spec.system_prompt.replace("{today}", spec.today) + "\n\n[도구]\n" + JSON.stringify(spec.tools, null, 2);
}
async function loadToolCases() {
  const data = await api("/api/tools/cases");
  $("#tools-today").textContent = data.today;
  data.cases.forEach((c) => $("#tools-cases").append(h("button", {
    type: "button", class: "chip", title: c.question,
    onclick: (ev) => {
      document.querySelectorAll("#tools-cases .chip").forEach((b) => b.classList.toggle("sel", b === ev.currentTarget));
      TOOL_CASE = c; $("#t-q").value = c.question; askTools();
    },
  }, `${c.id} · ${c.group}`)));
  await loadToolSpec();
}
$("#t-q").addEventListener("input", () => { if (TOOL_CASE && $("#t-q").value.trim() !== TOOL_CASE.question) { TOOL_CASE = null; document.querySelectorAll("#tools-cases .chip").forEach((b) => b.classList.remove("sel")); } });
$("#t-variant").addEventListener("change", loadToolSpec);
$("#tools-form").addEventListener("submit", (ev) => { ev.preventDefault(); askTools(); });

// ---------- 에이전트 탭 (6일차) ----------
const AGENT_SESSION = "web-" + Math.random().toString(36).slice(2, 10);       // 서버가 이 이름으로 대화를 저장한다
const TRACE_TONE = { ok: "", not_found: "warn", missing_info: "warn", need_base_rate: "warn", invalid_input: "warn", duplicate: "warn", error: "bad", unknown_tool: "bad" };

const STOP_LABEL = { tool_limit: "중단: 도구 호출 한도", model_error: "중단: 모델 오류", handoff_not_ready: "안내: 상담원 연결은 아직 지원하지 않음" };
const FIELD_LABEL = { principal: "원금", start_date: "신규일", maturity_date: "만기일", termination_date: "해지일", base_rate: "기본금리" };

function agentTrace(trace) {
  return h("div", { class: "agent-trace" }, trace.map((t) => {
    const dropped = Object.entries(t.dropped || {}), filled = Object.entries(t.filled || {});
    return h("div", { class: TRACE_TONE[t.status] || "" },
      `🔧 ${t.name}(${Object.entries(t.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")}) → ${t.status}`
      + (t.forced ? " · 코드가 먼저 검색" : "") + (t.retried ? " · 재시도함" : "") + (t.duplicate ? " · 같은 호출 반복, 실행 안 함" : "")
      + (dropped.length ? ` · 말하지 않은 값 제외: ${dropped.map(([k, v]) => `${FIELD_LABEL[k] || k} ${v}`).join(", ")}` : "")
      + (filled.length ? ` · 질문에서 채움: ${filled.map(([k, v]) => `${FIELD_LABEL[k] || k} ${v}`).join(", ")}` : "") + ` · ${t.ms}ms`);
  }));
}

function renderAgentTurn(data, pending) {
  const bubble = pending.querySelector(".msg-bot");
  pending.querySelectorAll(".agent-trace").forEach((el) => el.remove());
  if (data.trace && data.trace.length) pending.insertBefore(agentTrace(data.trace), bubble);
  bubble.classList.remove("streaming");
  if (data.error) { bubble.classList.add("failed"); bubble.textContent = data.error; }
  else {
    bubble.textContent = data.answer || "(빈 답변)";
    if (data.stop_reason) bubble.classList.add("stopped");
  }
  pending.append(h("div", { class: "muted small" }, `도구 ${data.tool_calls}회 · 모델 ${data.model_calls}회 · ${data.ms}ms` + (data.stop_reason ? ` · ${STOP_LABEL[data.stop_reason] || "중단: " + data.stop_reason}` : "")));
  $("#agent-confirmed").textContent = data.confirmed_text ? "확인된 값: " + data.confirmed_text : "";
}

async function sendAgent() {
  const input = $("#agent-q"), q = input.value.trim();
  if (!q) return;
  const log = $("#agent-log");
  if (log.querySelector("p.muted")) log.replaceChildren();
  log.append(h("div", { class: "msg-user" }, q));
  const pending = h("div", { class: "bot-wrap" }, h("div", { class: "msg-bot streaming" }, "생각하는 중..."));
  log.append(pending);
  input.value = "";
  $("#agent-send").disabled = true;
  try {
    renderAgentTurn(await api("/api/agent/ask", { method: "POST", headers: { "Content-Type": "application/json" },
                                                  body: JSON.stringify({ question: q, session_id: AGENT_SESSION }) }), pending);
  } catch (e) {
    renderAgentTurn({ error: e.message, tool_calls: 0, model_calls: 0, ms: 0, trace: [], confirmed_text: "" }, pending);
  } finally {
    $("#agent-send").disabled = false;
    input.focus();
  }
}
$("#agent-form").addEventListener("submit", (ev) => { ev.preventDefault(); sendAgent(); });
$("#agent-reset").addEventListener("click", async () => {
  try { await api("/api/agent/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: AGENT_SESSION }) }); }
  catch (e) { /* 서버에 대화가 없어도 화면은 비운다 */ }
  $("#agent-log").replaceChildren(h("p", { class: "muted" }, "새 대화를 시작했습니다."));
  $("#agent-confirmed").textContent = "";
});

// ---------- 시작 ----------
(async function init() {
  await loadStatus();
  try { await loadMeta(); await loadChunkList(); await loadExamples(); await loadCalcCases(); await loadToolCases(); }
  catch (e) { $("#search-msg").className = "msg err"; $("#search-msg").textContent = e.message; }
})();
