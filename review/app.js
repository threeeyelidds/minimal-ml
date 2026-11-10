/* Review app: spaced repetition over the question bank.
   Cards are rendered by Quarto into #rv-bank (hidden); this script reads them from the DOM,
   so Markdown, code and math are already formatted. Progress lives in localStorage. */
(function () {
  "use strict";
  var KEY = "prep.v1";
  var DAY = 86400000;
  var SK = window.PREP_SKILLS || { n: 0, areas: [] };
  var root = document.getElementById("rv-app");
  var bankEl = document.getElementById("rv-bank");
  if (!root || !bankEl) return;

  /* ---------- data ---------- */
  var skillInfo = {}, areaInfo = {}, areaOrder = [];
  SK.areas.forEach(function (a) {
    areaInfo[a.id] = a; areaOrder.push(a.id);
    a.skills.forEach(function (s) { s.area = a.id; skillInfo[s.id] = s; });
  });
  var cards = Array.prototype.map.call(bankEl.querySelectorAll(".rv-card"), function (el) {
    var d = el.dataset;
    return {
      id: d.id, area: d.area, skill: d.skill, also: (d.also || "").split(" ").filter(Boolean),
      type: d.type, level: d.level, tracks: (d.tracks || "").split(" ").filter(Boolean), el: el,
      text: (el.querySelector(".rv-q") || el).textContent.trim()
    };
  });
  var byId = {};
  cards.forEach(function (c) { byId[c.id] = c; });

  var TRACKS = [["all", "All tracks"], ["mle", "ML engineer"], ["ai", "AI engineer"], ["re", "Research engineer"]];
  var TYPES = [["concept", "Concept"], ["tradeoff", "Trade-off"], ["design", "Design"], ["debug", "Debugging"], ["coding", "Coding"],
               ["derivation", "Derivation"], ["behavioral", "Behavioural"], ["case", "Design case"]];
  var LEVELS = [["core", "Core"], ["senior", "Senior"], ["staff", "Staff"]];
  var LEVEL_RANK = { core: 0, senior: 1, staff: 2 };

  /* ---------- storage ---------- */
  function load() {
    try {
      var s = JSON.parse(localStorage.getItem(KEY) || "{}");
      s.cards = s.cards || {}; s.log = s.log || {}; s.set = s.set || {};
      return s;
    } catch (e) { return { cards: {}, log: {}, set: {} }; }
  }
  var store = load(), storageOk = true;
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(store)); } catch (e) { storageOk = false; }
  }
  function today() { var d = new Date(); return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2); }

  /* ---------- scheduling (SM-2, simplified) ---------- */
  function next(rec, grade) {
    var r = rec ? { ivl: rec.ivl, ease: rec.ease, reps: rec.reps, lapses: rec.lapses } : { ivl: 0, ease: 2.5, reps: 0, lapses: 0 };
    if (grade === 0) { r.ivl = 0; r.reps = 0; r.lapses += 1; r.ease = Math.max(1.3, r.ease - 0.2); }
    else if (grade === 1) { r.ivl = Math.max(1, Math.round(r.ivl * 1.2)); r.ease = Math.max(1.3, r.ease - 0.15); r.reps += 1; }
    else if (grade === 2) { r.ivl = r.reps === 0 ? 1 : r.reps === 1 ? 3 : Math.round(Math.max(r.ivl + 1, r.ivl * r.ease)); r.reps += 1; }
    else { r.ivl = r.reps === 0 ? 4 : Math.round(Math.max(r.ivl + 2, r.ivl * r.ease * 1.3)); r.ease += 0.15; r.reps += 1; }
    r.due = Date.now() + (r.ivl === 0 ? 10 * 60000 : r.ivl * DAY);
    r.last = Date.now();
    return r;
  }
  function fmtIvl(ivl) { return ivl === 0 ? "10 min" : ivl < 30 ? ivl + " d" : ivl < 365 ? Math.round(ivl / 30) + " mo" : (ivl / 365).toFixed(1) + " y"; }
  function status(id) {
    var r = store.cards[id];
    if (!r) return "new";
    return r.ivl >= 7 ? "known" : "learning";
  }
  function mastery(id) { var s = status(id); return s === "known" ? 1 : s === "learning" ? 0.5 : 0; }
  function isDue(id) { var r = store.cards[id]; return !!r && r.due <= Date.now(); }
  function grade(card, g) {
    var isNew = !store.cards[card.id];
    store.cards[card.id] = next(store.cards[card.id], g);
    var t = today();
    store.log[t] = store.log[t] || { n: 0, fresh: 0 };
    store.log[t].n += 1;
    if (isNew) store.log[t].fresh += 1;
    save();
  }

  /* ---------- filters ---------- */
  var f = { track: store.set.track || "all", areas: [], skill: "", level: "", type: "", status: "", q: "" };
  function readHash() {
    var h = location.hash.replace(/^#/, "");
    h.split("&").forEach(function (kv) {
      var p = kv.split("=");
      if (p[0] === "area" && p[1]) f.areas = p[1].split(",").filter(function (a) { return areaInfo[a]; });
      if (p[0] === "skill" && skillInfo[p[1]]) { f.skill = p[1]; f.areas = [skillInfo[p[1]].area]; }
      if (p[0] === "track" && p[1]) f.track = p[1];
      if (p[0] === "view" && p[1]) view = p[1];
    });
  }
  function demand(card) {
    var s = skillInfo[card.skill];
    if (!s) return 50;
    return f.track === "all" ? s.pct : s[f.track];
  }
  function match(c) {
    if (f.track !== "all" && c.tracks.indexOf(f.track) < 0) return false;
    if (f.areas.length && f.areas.indexOf(c.area) < 0) return false;
    if (f.skill && c.skill !== f.skill && c.also.indexOf(f.skill) < 0) return false;
    if (f.level && c.level !== f.level) return false;
    if (f.type && c.type !== f.type) return false;
    if (f.status) {
      var st = status(c.id);
      if (f.status === "due") { if (!isDue(c.id)) return false; }
      else if (st !== f.status) return false;
    }
    if (f.q) {
      var q = f.q.toLowerCase();
      var hay = (c.text + " " + (skillInfo[c.skill] ? skillInfo[c.skill].name : "") + " " + c.el.textContent).toLowerCase();
      if (hay.indexOf(q) < 0) return false;
    }
    return true;
  }
  function filtered() { return cards.filter(match); }

  /* ---------- helpers ---------- */
  function h(tag, attrs, kids) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "class") e.className = attrs[k];
      else if (k === "text") e.textContent = attrs[k];
      else if (k === "html") e.innerHTML = attrs[k];
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) e.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (k) { if (k) e.appendChild(typeof k === "string" ? document.createTextNode(k) : k); });
    return e;
  }
  function typeset(el) { if (window.MathJax && window.MathJax.typesetPromise) window.MathJax.typesetPromise([el]).catch(function () {}); }
  function part(card, cls) {
    var src = card.el.querySelector("." + cls);
    if (!src) return null;
    var c = src.cloneNode(true);
    c.querySelectorAll("[id]").forEach(function (n) { n.removeAttribute("id"); });
    return c;
  }
  function shuffle(a) { for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)); var t = a[i]; a[i] = a[j]; a[j] = t; } return a; }
  function tags(card) {
    var s = skillInfo[card.skill], a = areaInfo[card.area];
    var wrap = h("div", { class: "rv-meta" });
    wrap.appendChild(h("a", { class: "rv-crumb", href: "../skills/" + card.area + ".html#" + (s ? card.skill : card.id), text: (a ? a.short : card.area) + (s ? " › " + s.name : "") }));
    wrap.appendChild(h("span", { class: "tag tag-" + card.level, text: card.level }));
    wrap.appendChild(h("span", { class: "tag tag-type", text: (TYPES.filter(function (t) { return t[0] === card.type; })[0] || [0, card.type])[1].toLowerCase() }));
    if (s) wrap.appendChild(h("span", { class: "tag tag-demand", title: "Share of postings naming this skill" + (f.track === "all" ? "" : " in the selected track"), text: Math.round(demand(card)) + "% of postings" }));
    wrap.appendChild(h("span", { class: "tag tag-status st-" + status(card.id), text: status(card.id) }));
    return wrap;
  }

  /* ---------- views ---------- */
  var view = "today";
  var session = null;  // {queue:[card], i, shown, done:[{card,grade}], mock:{title,start}}

  function buildQueue() {
    var pool = filtered();
    var due = pool.filter(function (c) { return isDue(c.id); }).sort(function (a, b) { return store.cards[a.id].due - store.cards[b.id].due; });
    var limit = store.set.dailyNew || 15;
    var doneToday = (store.log[today()] || {}).fresh || 0;
    var room = Math.max(0, limit - doneToday);
    var fresh = pool.filter(function (c) { return !store.cards[c.id]; }).sort(function (a, b) {
      return (LEVEL_RANK[a.level] - LEVEL_RANK[b.level]) || (demand(b) - demand(a));
    });
    // interleave areas a little so a session is not one topic
    var picked = [], seenSkill = {};
    fresh.forEach(function (c) { if (picked.length < room && !seenSkill[c.skill]) { picked.push(c); seenSkill[c.skill] = 1; } });
    fresh.forEach(function (c) { if (picked.length < room && picked.indexOf(c) < 0) picked.push(c); });
    return { due: due, fresh: picked, freshTotal: fresh.length, room: room };
  }

  function startSession(queue, mock) {
    session = { queue: queue, i: 0, shown: false, done: [], mock: mock || null, start: Date.now() };
    view = "session"; render();
  }

  function renderTabs() {
    var tabs = [["today", "Today"], ["browse", "Browse"], ["mock", "Mock interview"], ["progress", "Progress"]];
    return h("div", { class: "rv-tabs", role: "tablist" }, tabs.map(function (t) {
      return h("button", { class: "rv-tab" + ((view === t[0] || (view === "session" && t[0] === (session && session.mock ? "mock" : "today"))) ? " on" : ""), role: "tab", onclick: function () { view = t[0]; session = null; render(); }, text: t[1] });
    }));
  }

  function select(label, opts, val, on) {
    var s = h("select", { "aria-label": label, onchange: function () { on(s.value); } }, opts.map(function (o) {
      var op = h("option", { value: o[0], text: o[1] }); if (o[0] === val) op.selected = true; return op;
    }));
    return h("label", { class: "rv-field" }, [h("span", { text: label }), s]);
  }

  function renderFilters(onChange) {
    var box = h("div", { class: "rv-filters" });
    box.appendChild(select("Track", TRACKS, f.track, function (v) { f.track = v; store.set.track = v; save(); onChange(); }));
    var skillOpts = [["", "All skills"]];
    (f.areas.length ? f.areas : areaOrder).forEach(function (a) {
      (areaInfo[a] ? areaInfo[a].skills : []).forEach(function (s) { skillOpts.push([s.id, (f.areas.length === 1 ? "" : areaInfo[a].short + ": ") + s.name]); });
    });
    box.appendChild(select("Skill", skillOpts, f.skill, function (v) { f.skill = v; onChange(); }));
    box.appendChild(select("Level", [["", "Any level"]].concat(LEVELS), f.level, function (v) { f.level = v; onChange(); }));
    box.appendChild(select("Type", [["", "Any type"]].concat(TYPES), f.type, function (v) { f.type = v; onChange(); }));
    box.appendChild(select("Status", [["", "Any status"], ["new", "New"], ["learning", "Learning"], ["known", "Known"], ["due", "Due now"]], f.status, function (v) { f.status = v; onChange(); }));
    var inp = h("input", { type: "search", placeholder: "Search cards", value: f.q, "aria-label": "Search cards" });
    var t; inp.addEventListener("input", function () { clearTimeout(t); t = setTimeout(function () { f.q = inp.value.trim(); onChange(true); }, 200); });
    box.appendChild(h("label", { class: "rv-field rv-search" }, [h("span", { text: "Search" }), inp]));
    var chips = h("div", { class: "rv-chips" });
    chips.appendChild(h("button", { class: "rv-chip" + (f.areas.length ? "" : " on"), text: "All areas", onclick: function () { f.areas = []; f.skill = ""; onChange(); } }));
    areaOrder.forEach(function (a) {
      var n = cards.filter(function (c) { return c.area === a; }).length;
      if (!n) return;
      chips.appendChild(h("button", { class: "rv-chip" + (f.areas.indexOf(a) >= 0 ? " on" : ""), text: areaInfo[a].short + " " + n, onclick: function () {
        var i = f.areas.indexOf(a); if (i >= 0) f.areas.splice(i, 1); else f.areas.push(a);
        f.skill = ""; onChange();
      } }));
    });
    return h("div", { class: "rv-filterwrap" }, [box, chips]);
  }

  function renderToday() {
    var wrap = h("div");
    wrap.appendChild(renderFilters(function () { render(); }));
    var q = buildQueue();
    var pool = filtered();
    var known = pool.filter(function (c) { return status(c.id) === "known"; }).length;
    var learning = pool.filter(function (c) { return status(c.id) === "learning"; }).length;
    var stats = h("div", { class: "rv-stats" }, [
      stat(q.due.length, "due now"), stat(q.fresh.length, "new today", q.freshTotal + " unseen in this selection"),
      stat(learning, "learning"), stat(known, "known", "of " + pool.length + " cards")
    ]);
    wrap.appendChild(stats);
    var total = q.due.length + q.fresh.length;
    var actions = h("div", { class: "rv-actions" });
    actions.appendChild(h("button", { class: "rv-btn primary", disabled: total ? null : "disabled", text: total ? "Start review (" + total + " cards)" : "Nothing due in this selection", onclick: function () { startSession(q.due.concat(q.fresh)); } }));
    if (q.freshTotal > q.fresh.length) actions.appendChild(h("button", { class: "rv-btn", text: "Add 10 more new cards", onclick: function () {
      store.set.dailyNew = (store.set.dailyNew || 15) + 10; save(); render();
    } }));
    actions.appendChild(h("button", { class: "rv-btn", text: "Cram this selection (" + pool.length + ")", title: "Go through every card in the selection, ignoring the schedule", disabled: pool.length ? null : "disabled", onclick: function () {
      startSession(pool.slice().sort(function (a, b) { return mastery(a.id) - mastery(b.id) || demand(b) - demand(a); }));
    } }));
    wrap.appendChild(actions);
    wrap.appendChild(h("p", { class: "rv-hint", html: "New cards come in order of level (core first) and of how often postings name the skill" + (f.track === "all" ? "" : " for the selected track") + ". Keys: <kbd>space</kbd> show answer, <kbd>1</kbd>–<kbd>4</kbd> grade, <kbd>s</kbd> skip." }));
    if (!storageOk) wrap.appendChild(h("p", { class: "rv-warn", text: "This browser is not saving progress (storage is blocked). Reviews work, but will not be remembered." }));
    return wrap;
  }
  function stat(n, label, sub) { return h("div", { class: "rv-stat" }, [h("div", { class: "n", text: String(n) }), h("div", { class: "l", text: label }), sub ? h("div", { class: "s", text: sub }) : null]); }

  function renderSession() {
    var s = session, wrap = h("div", { class: "rv-session" });
    if (s.i >= s.queue.length) return renderSummary();
    var card = s.queue[s.i];
    var head = h("div", { class: "rv-sessionhead" }, [
      h("div", { class: "rv-progress" }, [h("div", { class: "rv-progressbar", style: "width:" + (100 * s.i / s.queue.length) + "%" })]),
      h("span", { class: "rv-count", text: (s.i + 1) + " / " + s.queue.length + (s.mock ? " · " + s.mock.title : "") }),
      s.mock ? h("span", { class: "rv-timer", id: "rv-timer", text: "0:00" }) : null,
      h("button", { class: "rv-btn small", text: "End", onclick: function () { s.i = s.queue.length; render(); } })
    ]);
    wrap.appendChild(head);
    var stage = h("div", { class: "rv-stage" });
    stage.appendChild(tags(card));
    var q = part(card, "rv-q"); q.className = "rv-question"; stage.appendChild(q);
    if (!s.shown) {
      stage.appendChild(h("div", { class: "rv-actions" }, [
        h("button", { class: "rv-btn primary", text: "Show answer", onclick: function () { s.shown = true; render(); } }),
        h("button", { class: "rv-btn", text: "Skip", onclick: skip })
      ]));
      stage.appendChild(h("p", { class: "rv-hint", text: card.type === "case" || card.type === "design" ? "Sketch the design out loud or on paper first." : card.type === "coding" ? "Write the code before you look." : "Answer out loud first." }));
    } else {
      var a = part(card, "rv-a"); a.className = "rv-answer"; stage.appendChild(a);
      var p = part(card, "rv-probes");
      if (p) { p.className = "rv-probesbox"; p.insertBefore(h("div", { class: "rv-probetitle", text: "Follow-ups an interviewer would ask" }), p.firstChild); stage.appendChild(p); }
      var rec = store.cards[card.id];
      var labels = [["Again", "missed it"], ["Hard", "partly"], ["Good", "got it"], ["Easy", "cold"]];
      stage.appendChild(h("div", { class: "rv-grades" }, labels.map(function (l, g) {
        return h("button", { class: "rv-grade g" + g, onclick: function () { doGrade(g); } }, [h("b", { text: (g + 1) + " · " + l[0] }), h("span", { text: l[1] + " · " + fmtIvl(next(rec, g).ivl) })]);
      })));
    }
    wrap.appendChild(stage);
    setTimeout(function () { typeset(stage); }, 0);
    return wrap;
  }
  function doGrade(g) {
    var s = session; if (!s || !s.shown) return;
    var card = s.queue[s.i];
    grade(card, g);
    s.done.push({ card: card, grade: g });
    if (g === 0 && !s.mock) s.queue.push(card);
    s.i += 1; s.shown = false; render();
    window.scrollTo({ top: root.getBoundingClientRect().top + window.scrollY - 80 });
  }
  function skip() { var s = session; if (!s) return; s.i += 1; s.shown = false; render(); }

  function renderSummary() {
    var s = session, wrap = h("div", { class: "rv-summary" });
    var n = s.done.length, good = s.done.filter(function (d) { return d.grade >= 2; }).length;
    var mins = Math.max(1, Math.round((Date.now() - s.start) / 60000));
    wrap.appendChild(h("h3", { text: s.mock ? s.mock.title + ": debrief" : "Session done" }));
    wrap.appendChild(h("p", { text: n ? n + " cards graded in " + mins + " min; " + good + " answered well (" + Math.round(100 * good / n) + "%)." : "No cards graded." }));
    var weak = s.done.filter(function (d) { return d.grade < 2; });
    if (weak.length) {
      wrap.appendChild(h("p", { text: "Worth another look:" }));
      var seen = {};
      wrap.appendChild(h("ul", null, weak.filter(function (d) { if (seen[d.card.id]) return false; seen[d.card.id] = 1; return true; }).map(function (d) {
        var sk = skillInfo[d.card.skill];
        return h("li", null, [h("a", { href: "../skills/" + d.card.area + ".html#" + d.card.id, text: d.card.text.slice(0, 140) + (d.card.text.length > 140 ? "…" : "") }), sk ? h("span", { class: "muted", text: "  " + sk.name }) : null]);
      })));
    }
    wrap.appendChild(h("div", { class: "rv-actions" }, [
      h("button", { class: "rv-btn primary", text: "Back to today", onclick: function () { session = null; view = "today"; render(); } }),
      h("button", { class: "rv-btn", text: "See progress", onclick: function () { session = null; view = "progress"; render(); } })
    ]));
    return wrap;
  }

  var browseLimit = 40;
  function renderBrowse(keepFocus) {
    var wrap = h("div");
    wrap.appendChild(renderFilters(function (typing) { browseLimit = 40; render(typing); }));
    var pool = filtered();
    wrap.appendChild(h("p", { class: "rv-hint", text: pool.length + " cards match." }));
    var list = h("div", { class: "rv-list" });
    pool.slice(0, browseLimit).forEach(function (c) {
      var det = h("details", { class: "qa" });
      var sum = h("summary");
      sum.appendChild(h("span", { class: "dot st-" + status(c.id), title: status(c.id) }));
      sum.appendChild(h("span", { class: "tag tag-" + c.level, text: c.level }));
      var q = part(c, "rv-q"); var span = h("span", { class: "rv-qinline" });
      while (q.firstChild) { var n = q.firstChild; if (n.nodeName === "P") { while (n.firstChild) span.appendChild(n.firstChild); q.removeChild(n); } else span.appendChild(n); }
      sum.appendChild(span);
      det.appendChild(sum);
      det.addEventListener("toggle", function () {
        if (det.open && !det.dataset.filled) {
          det.dataset.filled = "1";
          det.appendChild(tags(c));
          var a = part(c, "rv-a"); a.className = "rv-answer"; det.appendChild(a);
          var p = part(c, "rv-probes"); if (p) { p.className = "rv-probesbox"; p.insertBefore(h("div", { class: "rv-probetitle", text: "Follow-ups" }), p.firstChild); det.appendChild(p); }
          typeset(det);
        }
      });
      list.appendChild(det);
    });
    wrap.appendChild(list);
    if (pool.length > browseLimit) wrap.appendChild(h("button", { class: "rv-btn", text: "Show more", onclick: function () { browseLimit += 60; render(); } }));
    setTimeout(function () { typeset(list); }, 0);
    return wrap;
  }

  var ROUNDS = [
    { id: "design", title: "ML / AI system design", desc: "One design case, 40 minutes. Talk through it before opening the answer.", pick: function (t) { return draw(t, { type: ["case"] }, 1); } },
    { id: "breadth", title: "ML breadth, rapid fire", desc: "Ten concept and trade-off questions across fundamentals, deep learning and evaluation.", pick: function (t) { return draw(t, { areas: ["mlfund", "dl", "eval", "llm"], type: ["concept", "tradeoff", "derivation"] }, 10); } },
    { id: "coding", title: "Coding and ML coding", desc: "Three implementation questions. Write the code before looking.", pick: function (t) { return draw(t, { type: ["coding"] }, 3); } },
    { id: "llmapp", title: "AI engineering deep dive", desc: "Eight questions on agents, retrieval, evaluation and serving LLM products.", pick: function (t) { return draw(t, { areas: ["llmapp", "eval", "serving"], level: ["senior", "staff"] }, 8); } },
    { id: "ranking", title: "Search and recommendation deep dive", desc: "Eight senior questions on retrieval, ranking, ads and experimentation.", pick: function (t) { return draw(t, { areas: ["recsys"], level: ["senior", "staff"] }, 8); } },
    { id: "research", title: "Research engineering deep dive", desc: "Eight questions on training, research practice and infrastructure.", pick: function (t) { return draw(t, { areas: ["research", "llm", "dl", "infra", "rl"], level: ["senior", "staff"] }, 8); } },
    { id: "debug", title: "Debugging round", desc: "Six \"this is broken, what do you do\" questions.", pick: function (t) { return draw(t, { type: ["debug"] }, 6); } },
    { id: "behavioral", title: "Behavioural and leadership", desc: "Five questions. Answer each with a real story in under three minutes.", pick: function (t) { return draw(t, { areas: ["senior"], type: ["behavioral"] }, 5); } },
    { id: "loop", title: "Full loop", desc: "One design case, two coding, six breadth, three behavioural. About two and a half hours.", pick: function (t) {
      return draw(t, { type: ["case"] }, 1).concat(draw(t, { type: ["coding"] }, 2), draw(t, { areas: ["mlfund", "dl", "eval", "llm", "llmapp", "recsys"], type: ["concept", "tradeoff", "debug"] }, 6), draw(t, { areas: ["senior"], type: ["behavioral"] }, 3));
    } }
  ];
  function draw(track, spec, k) {
    var pool = cards.filter(function (c) {
      if (track !== "all" && c.tracks.indexOf(track) < 0) return false;
      if (spec.areas && spec.areas.indexOf(c.area) < 0) return false;
      if (spec.type && spec.type.indexOf(c.type) < 0) return false;
      if (spec.level && spec.level.indexOf(c.level) < 0) return false;
      return true;
    });
    // weight towards cards not yet known
    pool = shuffle(pool).sort(function (a, b) { return mastery(a.id) - mastery(b.id) + (Math.random() - 0.5) * 0.8; });
    return pool.slice(0, k);
  }
  function renderMock() {
    var wrap = h("div");
    wrap.appendChild(h("div", { class: "rv-filters" }, [select("Track", TRACKS, f.track, function (v) { f.track = v; store.set.track = v; save(); render(); })]));
    wrap.appendChild(h("p", { class: "rv-hint", text: "Each round draws cards you have not mastered, runs a timer, and grades feed the review schedule." }));
    var grid = h("div", { class: "rv-rounds" });
    ROUNDS.forEach(function (r) {
      var q = r.pick(f.track);
      grid.appendChild(h("div", { class: "rv-round" }, [
        h("h4", { text: r.title }), h("p", { text: r.desc }),
        h("button", { class: "rv-btn primary", disabled: q.length ? null : "disabled", text: q.length ? "Start (" + q.length + ")" : "No cards yet", onclick: function () { startSession(r.pick(f.track), { title: r.title }); } })
      ]));
    });
    wrap.appendChild(grid);
    return wrap;
  }

  function readiness(track) {
    var num = 0, den = 0, rows = [];
    Object.keys(skillInfo).forEach(function (sid) {
      var s = skillInfo[sid];
      var cs = cards.filter(function (c) { return c.skill === sid && (track === "all" || c.tracks.indexOf(track) >= 0); });
      var w = track === "all" ? s.pct : s[track];
      if (!cs.length || !w) return;
      var m = cs.reduce(function (acc, c) { return acc + mastery(c.id); }, 0) / cs.length;
      num += w * m; den += w;
      rows.push({ s: s, w: w, m: m, n: cs.length });
    });
    return { score: den ? num / den : 0, rows: rows };
  }
  function renderProgress() {
    var wrap = h("div");
    var seenAny = Object.keys(store.cards).length > 0;
    var tr = h("div", { class: "rv-stats" });
    [["mle", "ML engineer"], ["ai", "AI engineer"], ["re", "Research engineer"]].forEach(function (t) {
      var r = readiness(t[0]);
      tr.appendChild(h("div", { class: "rv-stat" + (f.track === t[0] ? " sel" : ""), role: "button", tabindex: "0", onclick: function () { f.track = t[0]; store.set.track = t[0]; save(); render(); } }, [
        h("div", { class: "n", text: Math.round(100 * r.score) + "%" }), h("div", { class: "l", text: t[1] + " readiness" }),
        h("div", { class: "meter" }, [h("div", { style: "width:" + (100 * r.score) + "%" })])
      ]));
    });
    wrap.appendChild(tr);
    wrap.appendChild(h("p", { class: "rv-hint", text: "Readiness is the share of cards you know, weighted by how often that track's postings name each skill. A card counts half once seen and fully once its interval reaches a week. Click a track to rank the gaps for it." }));

    var track = f.track;
    var r = readiness(track);
    var gaps = r.rows.filter(function (x) { return x.m < 1; }).sort(function (a, b) { return b.w * (1 - b.m) - a.w * (1 - a.m); }).slice(0, 12);
    wrap.appendChild(h("h3", { text: "Biggest gaps" + (track === "all" ? "" : " for " + TRACKS.filter(function (t) { return t[0] === track; })[0][1].toLowerCase()) }));
    var tb = h("table", { class: "rv-table" }, [h("thead", null, [h("tr", null, ["Skill", "Area", "Postings", "Known", ""].map(function (x) { return h("th", { text: x }); }))])]);
    var body = h("tbody");
    gaps.forEach(function (g) {
      body.appendChild(h("tr", null, [
        h("td", null, [h("a", { href: "../skills/" + g.s.area + ".html#" + g.s.id, text: g.s.name })]),
        h("td", { class: "muted", text: areaInfo[g.s.area].short }),
        h("td", { text: Math.round(g.w) + "%" }),
        h("td", null, [h("div", { class: "meter" }, [h("div", { style: "width:" + (100 * g.m) + "%" })])]),
        h("td", null, [h("button", { class: "rv-btn small", text: "Drill " + g.n, onclick: function () {
          f.skill = g.s.id; f.areas = [g.s.area];
          startSession(cards.filter(function (c) { return c.skill === g.s.id && (track === "all" || c.tracks.indexOf(track) >= 0); }).sort(function (a, b) { return mastery(a.id) - mastery(b.id); }));
        } })])
      ]));
    });
    tb.appendChild(body); wrap.appendChild(h("div", { class: "rv-tablewrap" }, [tb]));

    wrap.appendChild(h("h3", { text: "By area" }));
    var tb2 = h("table", { class: "rv-table" }, [h("thead", null, [h("tr", null, ["Area", "Cards", "New / learning / known", ""].map(function (x) { return h("th", { text: x }); }))])]);
    var b2 = h("tbody");
    areaOrder.forEach(function (a) {
      var cs = cards.filter(function (c) { return c.area === a && (track === "all" || c.tracks.indexOf(track) >= 0); });
      if (!cs.length) return;
      var k = cs.filter(function (c) { return status(c.id) === "known"; }).length, l = cs.filter(function (c) { return status(c.id) === "learning"; }).length;
      var stack = h("div", { class: "stack", title: (cs.length - k - l) + " new, " + l + " learning, " + k + " known" }, [
        h("div", { class: "k", style: "width:" + (100 * k / cs.length) + "%" }), h("div", { class: "l", style: "width:" + (100 * l / cs.length) + "%" })
      ]);
      b2.appendChild(h("tr", null, [
        h("td", null, [h("a", { href: "../skills/" + a + ".html", text: areaInfo[a].name })]), h("td", { text: String(cs.length) }),
        h("td", null, [stack, h("span", { class: "muted", text: " " + (cs.length - k - l) + " / " + l + " / " + k })]),
        h("td", null, [h("button", { class: "rv-btn small", text: "Review", onclick: function () { f.areas = [a]; f.skill = ""; view = "today"; render(); } })])
      ]));
    });
    tb2.appendChild(b2); wrap.appendChild(h("div", { class: "rv-tablewrap" }, [tb2]));

    wrap.appendChild(h("h3", { text: "Last 14 days" }));
    var days = h("div", { class: "rv-days" }), max = 1, arr = [];
    for (var i = 13; i >= 0; i--) {
      var d = new Date(Date.now() - i * DAY);
      var key = d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2);
      var n = (store.log[key] || {}).n || 0; arr.push([key, n]); max = Math.max(max, n);
    }
    arr.forEach(function (x) { days.appendChild(h("div", { class: "day", title: x[0] + ": " + x[1] + " reviews" }, [h("div", { class: "col", style: "height:" + Math.round(100 * x[1] / max) + "%" }), h("span", { text: x[0].slice(8) })])); });
    wrap.appendChild(days);
    var streak = 0;
    for (var j = arr.length - 1; j >= 0; j--) { if (arr[j][1] > 0) streak++; else if (j < arr.length - 1) break; }
    wrap.appendChild(h("p", { class: "rv-hint", text: seenAny ? Object.keys(store.cards).length + " of " + cards.length + " cards seen; current streak " + streak + " day" + (streak === 1 ? "" : "s") + "." : "No reviews yet. Start on the Today tab." }));

    var file = h("input", { type: "file", accept: "application/json", style: "display:none" });
    file.addEventListener("change", function () {
      var fr = new FileReader();
      fr.onload = function () { try { var s = JSON.parse(fr.result); if (s && s.cards) { store = s; store.log = store.log || {}; store.set = store.set || {}; save(); render(); } } catch (e) { /* ignore a bad file */ } };
      if (file.files[0]) fr.readAsText(file.files[0]);
    });
    wrap.appendChild(h("div", { class: "rv-actions" }, [
      h("button", { class: "rv-btn", text: "Export progress", title: "Progress is stored in this browser only; export it to move to another device", onclick: function () {
        var a = h("a", { href: URL.createObjectURL(new Blob([JSON.stringify(store)], { type: "application/json" })), download: "ml-prep-progress-" + today() + ".json" });
        document.body.appendChild(a); a.click(); a.remove();
      } }),
      h("button", { class: "rv-btn", text: "Import progress", onclick: function () { file.click(); } }), file,
      h("button", { class: "rv-btn danger", text: "Reset", onclick: function (e) {
        var b = e.currentTarget;
        if (b.dataset.arm) { store = { cards: {}, log: {}, set: store.set }; save(); render(); }
        else { b.dataset.arm = "1"; b.textContent = "Click again to erase all progress"; }
      } })
    ]));
    return wrap;
  }

  /* ---------- render loop ---------- */
  var timer = null;
  function render(keepFocus) {
    var active = keepFocus && document.activeElement && document.activeElement.type === "search";
    var pos = active ? document.activeElement.selectionStart : 0;
    root.innerHTML = "";
    root.appendChild(renderTabs());
    var body = view === "session" ? renderSession() : view === "browse" ? renderBrowse() : view === "mock" ? renderMock() : view === "progress" ? renderProgress() : renderToday();
    root.appendChild(body);
    if (active) { var i = root.querySelector('input[type="search"]'); if (i) { i.focus(); try { i.setSelectionRange(pos, pos); } catch (e) { /* not supported */ } } }
    clearInterval(timer);
    if (view === "session" && session && session.mock) {
      var tick = function () { var el = document.getElementById("rv-timer"); if (!el || !session) return; var s = Math.floor((Date.now() - session.start) / 1000); el.textContent = Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2); };
      tick(); timer = setInterval(tick, 1000);
    }
  }
  document.addEventListener("keydown", function (e) {
    if (view !== "session" || !session || session.i >= session.queue.length) return;
    var t = e.target.tagName;
    if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT" || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === " " || e.key === "Enter") { if (!session.shown) { e.preventDefault(); session.shown = true; render(); } }
    else if (e.key === "s") { skip(); }
    else if (session.shown && e.key >= "1" && e.key <= "4") { doGrade(Number(e.key) - 1); }
  });
  window.addEventListener("hashchange", function () { readHash(); session = null; if (view === "session") view = "today"; render(); });
  readHash();
  if (!cards.length) { root.appendChild(h("p", { text: "No cards found." })); return; }
  render();
})();
