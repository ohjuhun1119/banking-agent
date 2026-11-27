(function () {
  "use strict";

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function won(n) { return Number(n).toLocaleString("ko-KR"); }

  var memorySid = null;
  function sessionId() {
    try {
      var v = sessionStorage.getItem("sid");
      if (!v) { v = crypto.randomUUID(); sessionStorage.setItem("sid", v); }
      return v;
    } catch (e) {
      if (!memorySid) memorySid = "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
      return memorySid;
    }
  }
  function newSession() {
    try { sessionStorage.removeItem("sid"); } catch (e) { /* 저장소를 못 쓰면 메모리 값만 바꾼다 */ }
    memorySid = null;
  }

  function call(path, options) {
    var init = Object.assign({ credentials: "same-origin", headers: { "Content-Type": "application/json" } }, options || {});
    return fetch(path, init).then(function (res) {
      if (res.status === 401) { window.location.href = "/login"; return null; }
      return res.json().then(function (data) { return { status: res.status, data: data }; }, function () { return { status: res.status, data: null }; });
    });
  }

  function rich(parent, text) {
    String(text || "").split(/\n+/).forEach(function (raw) {
      var line = raw.trim();
      if (!line) return;
      var isItem = /^([-*•]|\d+\.)\s+/.test(line);
      var p = el("p", isItem ? "item" : "");
      line = line.replace(/^#{1,6}\s*/, "").replace(/^([-*•])\s+/, "· ").replace(/`/g, "");
      line.split(/(\*\*[^*]+\*\*)/).forEach(function (part) {
        if (/^\*\*[^*]+\*\*$/.test(part)) p.appendChild(el("strong", "", part.slice(2, -2)));
        else if (part) p.appendChild(document.createTextNode(part));
      });
      parent.appendChild(p);
    });
  }

  function evidence(parent, text) {
    var lines = String(text || "").split("\n").map(function (s) { return s.trim(); })
      .filter(function (s) { return s && s !== "근거"; })
      .map(function (s) { return s.replace(/^-\s*/, ""); });
    if (!lines.length) return;
    var box = el("details");
    box.appendChild(el("summary", "", "근거 약관 " + lines.length + "건"));
    var list = el("ul");
    lines.forEach(function (l) { list.appendChild(el("li", "", l)); });
    box.appendChild(list);
    parent.appendChild(box);
  }

  var messages = $("messages");
  var question = $("question");
  var busy = false;

  function scrollDown() { window.scrollTo(0, document.body.scrollHeight); }
  function clearEmpty() { var e = messages.querySelector(".empty"); if (e) e.remove(); }
  function addUser(text) { clearEmpty(); var m = el("div", "msg-user", text); messages.appendChild(m); scrollDown(); return m; }
  function addBot(cls) { clearEmpty(); var m = el("div", "msg-bot" + (cls ? " " + cls : "")); messages.appendChild(m); scrollDown(); return m; }

  function failureText(result) {
    var err = result && result.data && result.data.error;
    if (typeof err === "string" && err.indexOf("한도") !== -1) return err;
    if (result && result.status === 429) return "요청이 너무 많아요. 잠시 후 다시 시도해 주세요.";
    if (result && result.status === 413) return "질문이 너무 길어요. 짧게 줄여 주세요.";
    return "답변을 가져오지 못했어요. 잠시 후 다시 시도해 주세요.";
  }

  $("chat-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var q = question.value.trim();
    if (!q || busy) return;
    busy = true;
    question.value = "";
    addUser(q);
    var bot = addBot("pending");
    bot.textContent = "답변을 준비하고 있어요";
    call("/chat", { method: "POST", body: JSON.stringify({ question: q, session_id: sessionId() }) }).then(function (res) {
      bot.className = "msg-bot";
      bot.textContent = "";
      var d = res && res.data;
      if (res && res.status === 200 && d && d.answer) {
        rich(bot, d.answer);
        evidence(bot, d.evidence_text);
      } else {
        bot.className = "msg-bot problem";
        bot.textContent = failureText(res);
      }
    }, function () {
      bot.className = "msg-bot problem";
      bot.textContent = failureText(null);
    }).then(function () { busy = false; scrollDown(); question.focus(); });
  });

  $("reset").addEventListener("click", function () {
    var old = sessionId();
    call("/chat/" + encodeURIComponent(old), { method: "DELETE" }).then(function () {}, function () {});
    newSession();
    messages.textContent = "";
    var empty = el("p", "empty");
    empty.appendChild(document.createTextNode("궁금한 점을 입력해 주세요."));
    empty.appendChild(document.createElement("br"));
    empty.appendChild(document.createTextNode("예) 중도해지하면 이자는 어떻게 돼요?"));
    messages.appendChild(empty);
    $("calc-result").textContent = "";
    question.focus();
  });

  var tabs = [["tab-chat", "panel-chat"], ["tab-calc", "panel-calc"]];
  function selectTab(index) {
    tabs.forEach(function (pair, i) {
      $(pair[0]).setAttribute("aria-selected", i === index ? "true" : "false");
      $(pair[1]).hidden = i !== index;
    });
  }
  tabs.forEach(function (pair, i) {
    $(pair[0]).addEventListener("click", function () { selectTab(i); });
    $(pair[0]).addEventListener("keydown", function (ev) {
      if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") { var next = (i + 1) % tabs.length; selectTab(next); $(tabs[next][0]).focus(); }
    });
  });

  function list(parent, summary, lines) {
    if (!lines || !lines.length) return;
    var box = el("details");
    box.appendChild(el("summary", "", summary));
    var ul = el("ul");
    lines.forEach(function (l) { ul.appendChild(el("li", "", l)); });
    box.appendChild(ul);
    parent.appendChild(box);
  }

  $("calc-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var out = $("calc-result");
    var digits = $("principal").value.replace(/[^0-9]/g, "");
    var rate = $("base_rate").value.trim();
    var body = {
      principal: digits ? Number(digits) : null,
      start_date: $("start_date").value || null,
      maturity_date: $("maturity_date").value || null,
      termination_date: $("termination_date").value || null,
      base_rate: rate ? rate : null
    };
    out.textContent = "";
    call("/api/calc", { method: "POST", body: JSON.stringify(body) }).then(function (res) {
      var r = res && res.data;
      if (!r) { out.appendChild(el("p", "alert", "계산하지 못했어요. 잠시 후 다시 시도해 주세요.")); return; }
      if (r.status === "ok") {
        out.appendChild(el("div", "label", "세전 이자"));
        out.appendChild(el("div", "amount", won(r.interest_krw) + "원"));
        out.appendChild(el("p", "sub", "연 " + r.rate_pct + "% · 경과 " + r.elapsed_days + "일 · " + r.section));
        out.appendChild(el("p", "sub", "원금과 이자 합계 " + won(r.payout_krw) + "원 (세전)"));
        list(out, "계산식 보기", r.formula);
        list(out, "계산 기준 보기", r.assumptions);
      } else if (r.status === "need_base_rate") {
        out.appendChild(el("p", "guide", r.message));
        $("base_rate").focus();
      } else {
        out.appendChild(el("p", "alert", r.message || "입력값을 확인해 주세요."));
      }
    }, function () { out.appendChild(el("p", "alert", "계산하지 못했어요. 잠시 후 다시 시도해 주세요.")); });
  });
})();
