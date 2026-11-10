/* Jobs browser over the analysed postings (metadata only; descriptions stay on the employers' sites). */
(function () {
  "use strict";
  var P = window.PREP_POSTINGS || [], N = window.PREP_NAMES || {}, SK = window.PREP_SKILLS || { areas: [], cards: {} };
  var root = document.getElementById("jobs-app");
  if (!root) return;
  var skillName = {}, skillArea = {};
  SK.areas.forEach(function (a) { a.skills.forEach(function (s) { skillName[s.id] = s.name; skillArea[s.id] = a.id; }); });

  // per-skill mastery from the review app's saved progress
  var prog = {};
  try { prog = (JSON.parse(localStorage.getItem("prep.v1") || "{}").cards) || {}; } catch (e) { prog = {}; }
  var agg = {};
  Object.keys(SK.cards || {}).forEach(function (id) {
    var s = SK.cards[id], r = prog[id];
    agg[s] = agg[s] || [0, 0];
    agg[s][0] += r ? (r.ivl >= 7 ? 1 : 0.5) : 0; agg[s][1] += 1;
  });
  var hasProgress = Object.keys(prog).length > 0;
  function m(s) { return agg[s] ? agg[s][0] / agg[s][1] : 0; }
  function tech(p) { return p.s.filter(function (s) { return skillArea[s] && skillArea[s] !== "senior"; }); }
  P.forEach(function (p) { var t = tech(p); p.fit = t.length ? t.reduce(function (a, s) { return a + m(s); }, 0) / t.length : 0; });

  var f = { k: "", a: "", g: "", l: "", us: false, skill: "", q: "", sort: hasProgress ? "fit" : "new" }, limit = 40;
  function h(tag, attrs, kids) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "class") e.className = attrs[k]; else if (k === "text") e.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== null && attrs[k] !== false) e.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (k) { if (k) e.appendChild(typeof k === "string" ? document.createTextNode(k) : k); });
    return e;
  }
  function select(label, opts, val, on) {
    var s = h("select", { "aria-label": label, onchange: function () { on(s.value); } }, opts.map(function (o) { var op = h("option", { value: o[0], text: o[1] }); if (o[0] === val) op.selected = true; return op; }));
    return h("label", { class: "rv-field" }, [h("span", { text: label }), s]);
  }
  function opts(map, all) { return [["", all]].concat(Object.keys(map || {}).map(function (k) { return [k, map[k]]; })); }
  function match(p) {
    if (f.k && p.k !== f.k) return false;
    if (f.a && p.a !== f.a) return false;
    if (f.g && p.g !== f.g) return false;
    if (f.l && p.l !== f.l) return false;
    if (f.us && !p.us) return false;
    if (f.skill && p.s.indexOf(f.skill) < 0) return false;
    if (f.q) { var q = f.q.toLowerCase(); if ((p.t + " " + p.c + " " + p.loc.join(" ")).toLowerCase().indexOf(q) < 0) return false; }
    return true;
  }
  function money(v) { return "$" + Math.round(v / 1000) + "k"; }
  var listEl, countEl;
  function draw() {
    var rows = P.filter(match);
    rows.sort(f.sort === "fit" ? function (a, b) { return b.fit - a.fit; } : f.sort === "pay" ? function (a, b) { return (b.p ? b.p[1] : 0) - (a.p ? a.p[1] : 0); } : function (a, b) { return (b.d || "").localeCompare(a.d || ""); });
    countEl.textContent = rows.length + " of " + P.length + " postings" + (hasProgress ? "" : ". Review some cards and this list can rank postings by how much of each one you already know.");
    listEl.innerHTML = "";
    rows.slice(0, limit).forEach(function (p) {
      var t = tech(p).sort(function (a, b) { return m(b) - m(a); });
      var sub = [p.c, N.level && N.level[p.l] !== undefined && p.l !== "unlevelled" ? N.level[p.l] : null, p.y ? p.y + "+ yrs" : null, p.loc.slice(0, 2).join(" · ") || null, p.p ? money(p.p[0]) + "–" + money(p.p[1]) : null, p.d ? "posted " + p.d : null].filter(Boolean).join("  ·  ");
      listEl.appendChild(h("div", { class: "job" }, [
        hasProgress ? h("span", { class: "job-fit", title: "Share of this posting's technical skills you know, from your review progress", text: Math.round(100 * p.fit) + "% known" }) : null,
        h("a", { class: "job-title", href: p.u, target: "_blank", rel: "noopener", text: p.t }),
        h("div", { class: "job-sub", text: sub }),
        h("div", { class: "job-skills" }, t.slice(0, 14).map(function (s) {
          return h("a", { class: "tag" + (m(s) >= 0.5 ? " have" : ""), href: "../skills/" + skillArea[s] + ".html#" + s, text: skillName[s] });
        }).concat(t.length > 14 ? [h("span", { class: "tag", text: "+" + (t.length - 14) })] : []))
      ]));
    });
    if (rows.length > limit) listEl.appendChild(h("button", { class: "rv-btn", style: "margin-top:1rem", text: "Show more", onclick: function () { limit += 60; draw(); } }));
  }
  var skillOpts = [["", "Any skill"]];
  SK.areas.forEach(function (a) { a.skills.forEach(function (s) { skillOpts.push([s.id, a.short + ": " + s.name]); }); });
  var inp = h("input", { type: "search", placeholder: "Title, company or city", "aria-label": "Search postings" }), t;
  inp.addEventListener("input", function () { clearTimeout(t); t = setTimeout(function () { f.q = inp.value.trim(); limit = 40; draw(); }, 200); });
  var us = h("input", { type: "checkbox", onchange: function () { f.us = us.checked; limit = 40; draw(); } });
  function on(key) { return function (v) { f[key] = v; limit = 40; draw(); }; }
  root.appendChild(h("div", { class: "rv-filters" }, [
    select("Track", opts(N.track, "All tracks"), f.k, on("k")), select("Role type", opts(N.archetype, "All role types"), f.a, on("a")),
    select("Employer", opts(N.group, "All employers"), f.g, on("g")), select("Level", opts(N.level, "Any level"), f.l, on("l")),
    select("Requires skill", skillOpts, f.skill, on("skill")),
    select("Sort", [["new", "Newest first"], ["pay", "Top of pay range"]].concat(hasProgress ? [["fit", "Best fit to what I know"]] : []), f.sort, on("sort")),
    h("label", { class: "rv-field rv-search" }, [h("span", { text: "Search" }), inp]),
    h("label", { class: "rv-field", style: "flex-direction:row;align-items:center;gap:.4rem;padding-bottom:.45rem" }, [us, h("span", { text: "US only" })])
  ]));
  countEl = h("p", { class: "rv-hint" }); listEl = h("div");
  root.appendChild(countEl); root.appendChild(listEl);
  draw();
})();
