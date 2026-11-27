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
  var faq = $("faq");
  function clearEmpty() { faq.remove(); }
  function addUser(text) { clearEmpty(); var m = el("div", "msg-user", text); messages.appendChild(m); scrollDown(); return m; }
  function addBot(cls) { clearEmpty(); var m = el("div", "msg-bot" + (cls ? " " + cls : "")); messages.appendChild(m); scrollDown(); return m; }

  var HISTORY_KEY = "chat_history";
  function loadHistory() {
    try { var v = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]"); return Array.isArray(v) ? v : []; } catch (e) { return []; }
  }
  function saveHistory(list) {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, 20))); } catch (e) { /* 저장소를 못 쓰면 이번 기록만 건너뛴다 */ }
  }
  function remember(q, a) {
    var sid = sessionId();
    var list = loadHistory();
    var conv = list.filter(function (c) { return c.id === sid; })[0];
    if (!conv) { conv = { id: sid, at: Date.now(), items: [] }; list.unshift(conv); }
    conv.items = conv.items.concat([{ q: q, a: a }]).slice(-50);
    saveHistory(list);
  }
  function renderHistory() {
    var box = $("history-list");
    box.textContent = "";
    var past = loadHistory().filter(function (c) { return c.id !== sessionId() && c.items && c.items.length; });
    if (!past.length) { box.appendChild(el("p", "help", "지난 문의가 없어요.")); return; }
    past.forEach(function (c) {
      var d = el("details");
      var when = new Date(c.at).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
      var first = String(c.items[0].q);
      d.appendChild(el("summary", "", when + " · " + (first.length > 24 ? first.slice(0, 24) + "…" : first)));
      c.items.forEach(function (it) {
        d.appendChild(el("p", "q", String(it.q)));
        var a = el("div", "a");
        rich(a, it.a);
        d.appendChild(a);
      });
      box.appendChild(d);
    });
  }
  $("history-open").addEventListener("click", function () {
    var box = $("history-box");
    box.hidden = !box.hidden;
    if (!box.hidden) renderHistory();
  });
  $("history-clear").addEventListener("click", function () {
    try { localStorage.removeItem(HISTORY_KEY); } catch (e) { /* 지울 게 없다 */ }
    renderHistory();
  });

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
    var started = Date.now();
    bot.textContent = "답변을 준비하고 있어요";
    var timer = setInterval(function () {
      var sec = Math.floor((Date.now() - started) / 1000);
      if (sec >= 1) bot.textContent = "답변을 준비하고 있어요 (" + sec + "초)";
    }, 1000);
    call("/chat", { method: "POST", body: JSON.stringify({ question: q, session_id: sessionId() }) }).then(function (res) {
      clearInterval(timer);
      bot.className = "msg-bot";
      bot.textContent = "";
      var d = res && res.data;
      if (res && res.status === 200 && d && d.answer) {
        rich(bot, d.answer);
        evidence(bot, d.evidence_text);
        remember(q, d.answer);
      } else {
        bot.className = "msg-bot problem";
        bot.textContent = failureText(res);
      }
    }, function () {
      clearInterval(timer);
      bot.className = "msg-bot problem";
      bot.textContent = failureText(null);
    }).then(function () { busy = false; scrollDown(); question.focus(); });
  });

  Array.prototype.forEach.call(document.querySelectorAll(".faq-item"), function (button) {
    button.addEventListener("click", function () {
      question.value = button.getAttribute("data-q");
      var form = $("chat-form");
      if (form.requestSubmit) form.requestSubmit();
      else form.dispatchEvent(new Event("submit", { cancelable: true }));
    });
  });

  $("reset").addEventListener("click", function () {
    var old = sessionId();
    call("/chat/" + encodeURIComponent(old), { method: "DELETE" }).then(function () {}, function () {});
    newSession();
    messages.textContent = "";
    messages.appendChild(faq);
    $("calc-result").textContent = "";
    question.focus();
  });

  var tabs = [["tab-home", "panel-home"], ["tab-chat", "panel-chat"], ["tab-calc", "panel-calc"], ["tab-handoff", "panel-handoff"]];
  function selectTab(index) {
    tabs.forEach(function (pair, i) {
      $(pair[0]).setAttribute("aria-selected", i === index ? "true" : "false");
      $(pair[1]).hidden = i !== index;
    });
    window.scrollTo(0, 0);
  }
  tabs.forEach(function (pair, i) {
    $(pair[0]).addEventListener("click", function () { selectTab(i); });
    $(pair[0]).addEventListener("keydown", function (ev) {
      if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") { var next = (i + 1) % tabs.length; selectTab(next); $(tabs[next][0]).focus(); }
    });
  });

  Array.prototype.forEach.call(document.querySelectorAll("[data-go]"), function (button) {
    button.addEventListener("click", function () {
      var target = button.getAttribute("data-go");
      tabs.forEach(function (pair, i) { if (pair[0] === target) selectTab(i); });
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

  var HANDOFF_PREFIX = "상담원 연결을 요청합니다. 문의 내용: ";
  var handoffConfirm = $("handoff-confirm");
  var handoffStart = $("handoff-start");
  var handoffText = $("handoff-text");
  var handoffResult = $("handoff-result");
  var handoffBusy = false;

  function handoffMessage(cls, text) {
    handoffResult.textContent = "";
    handoffResult.appendChild(el("p", cls, text));
  }

  handoffStart.addEventListener("click", function () {
    var note = handoffText.value.trim();
    handoffResult.textContent = "";
    if (!note) {
      handoffMessage("alert", "상담받고 싶은 내용을 입력해 주세요.");
      handoffText.focus();
      return;
    }
    $("handoff-preview").textContent = note;
    handoffConfirm.hidden = false;
    handoffStart.hidden = true;
  });

  $("handoff-cancel").addEventListener("click", function () {
    handoffConfirm.hidden = true;
    handoffStart.hidden = false;
  });

  $("handoff-save").addEventListener("click", function () {
    var note = handoffText.value.trim();
    if (!note || handoffBusy) return;
    handoffBusy = true;
    handoffMessage("guide", "저장하고 있어요");
    call("/chat", { method: "POST", body: JSON.stringify({ question: HANDOFF_PREFIX + note, session_id: sessionId() }) }).then(function (res) {
      var d = res && res.data;
      handoffResult.textContent = "";
      if (res && res.status === 200 && d && d.answer) {
        rich(handoffResult, d.answer);
        if (/접수번호/.test(d.answer)) handoffText.value = "";
      } else {
        handoffMessage("alert", "저장하지 못했어요. 하나은행 고객센터나 영업점에 직접 문의해 주세요.");
      }
    }, function () {
      handoffMessage("alert", "저장하지 못했어요. 하나은행 고객센터나 영업점에 직접 문의해 주세요.");
    }).then(function () {
      handoffBusy = false;
      handoffConfirm.hidden = true;
      handoffStart.hidden = false;
    });
  });
})();
