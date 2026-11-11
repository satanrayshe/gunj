/* Gunj results explorer. Vanilla JS, no build step.
   Every number on the page is read from data/*.json, which scripts/export_site_data.py
   writes from the repo's results files at build time. */
(function () {
  "use strict";

  var DATA = "data/";
  var NS = "http://www.w3.org/2000/svg";
  var reduceMotion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
  var state = { overview: null, clips: null, pages: {}, first: true };
  var resizers = [];

  /* ------------------------------------------------------------ DOM helpers */
  function el(tag, attrs) {
    var n = document.createElement(tag);
    setAttrs(n, attrs);
    for (var i = 2; i < arguments.length; i++) add(n, arguments[i]);
    return n;
  }
  function sv(tag, attrs) {
    var n = document.createElementNS(NS, tag);
    setAttrs(n, attrs);
    for (var i = 2; i < arguments.length; i++) add(n, arguments[i]);
    return n;
  }
  function setAttrs(n, attrs) {
    if (!attrs) return;
    for (var k in attrs) {
      if (!Object.prototype.hasOwnProperty.call(attrs, k)) continue;
      var v = attrs[k];
      if (v === null || v === undefined || v === false) continue;
      if (k === "text") n.textContent = v;
      else if (k === "on") { for (var ev in v) n.addEventListener(ev, v[ev]); }
      else n.setAttribute(k, v === true ? "" : String(v));
    }
  }
  function add(n, kid) {
    if (kid === null || kid === undefined || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(function (k) { add(n, k); }); return; }
    n.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
  }
  function T(cls, s) { return el("span", { class: cls || "t" }, s); }
  function fill(n) {
    n.textContent = "";
    for (var i = 1; i < arguments.length; i++) add(n, arguments[i]);
    return n;
  }

  /* ------------------------------------------------------------ formatting */
  var MINUS = "−";
  function fmtInt(n) { return n === null || n === undefined ? "n/a" : Number(n).toLocaleString("en-US"); }
  /* x with d decimals, rounded as Python's format(x, ".df") rounds, which the README, the report and the
     narration use: the exact binary value, ties to even. toFixed rounds an exact tie (6.25 at one decimal)
     away from zero, so only exact ties are changed here. */
  function pyFixed(x, d) {
    x = Number(x);
    var s = x.toFixed(d), a = Math.abs(x), y = a * Math.pow(2, d + 1);
    if (isFinite(y) && Math.floor(y) === y && y % 2 === 1) {
      var lo = Math.floor(a * Math.pow(10, d));
      if (lo % 2 === 0) s = (x < 0 ? "-" : "") + (lo / Math.pow(10, d)).toFixed(d);
    }
    return s;
  }
  function fmtF(x, d) { return x === null || x === undefined ? "n/a" : fixSign(pyFixed(x, d)); }
  function fixSign(s) { return s.charAt(0) === "-" ? MINUS + s.slice(1) : s; }
  function sgn(n) {
    if (n === null || n === undefined) return "n/a";
    n = Math.round(n);
    return n > 0 ? "+" + fmtInt(n) : n < 0 ? MINUS + fmtInt(-n) : "0";
  }
  function pct(x, d) { return x === null || x === undefined ? "n/a" : pyFixed(100 * x, d === undefined ? 1 : d) + "%"; }
  /* the share of k in n as the README computes it (100 * k / n, then rounded) */
  function pctKN(k, n) { return n ? pyFixed(100 * k / n, 1) + "%" : "n/a"; }
  function rateShare(r) { return typeof r.k === "number" && typeof r.n === "number" && r.n ? pctKN(r.k, r.n) : pct(r.rate); }
  function ciText(r) { return r && r.wilson95 ? "95% CI " + pyFixed(100 * r.wilson95[0], 1) + " to " + pct(r.wilson95[1]) : ""; }
  /* "25/90 (27.8%, 95% CI 19.6 to 37.8%)", the form the README uses */
  function fmtRate(r) {
    if (!r || !r.n) return "n/a";
    return r.k + "/" + r.n + " (" + rateShare(r) + (r.wilson95 ? ", " + ciText(r) : "") + ")";
  }
  /* a rate for a table cell: count and share, then the interval on its own line */
  function rateCell(r) {
    if (!r || !r.n) return "n/a";
    return [T("num", r.k + "/" + r.n), " ", T("num", "(" + rateShare(r) + ")"), r.wilson95 ? el("span", { class: "ci" }, ciText(r)) : null];
  }
  /* a length of time in words, the rule scripts/export_site_data.py and the README use */
  function durText(s) {
    s = Math.abs(s);
    if (s < 120) return Math.round(s) + " s";
    if (s < 7200) return Math.round(s / 60) + " minutes";
    if (s < 172800) return (s / 3600).toFixed(1) + " hours";
    return (s / 86400).toFixed(1) + " days";
  }
  /* "126 of 150" with only the numbers in the tabular face */
  function nOf(a, b) { return [T("num", String(a)), " of ", T("num", String(b))]; }
  function notComputed(file) { return el("span", { class: "muted" }, "not computed (", el("code", null, file), " not found)"); }
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  /* Times are IST wall-clock strings. They are parsed as UTC-naive numbers so that
     no browser time zone ever shifts them. */
  function parseNaive(s) {
    if (!s) return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})/.exec(s) || /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
    if (!m) return null;
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
  }
  function p2(n) { return (n < 10 ? "0" : "") + n; }
  function fmtDT(ms) {
    if (ms === null || ms === undefined) return "n/a";
    var d = new Date(ms);
    return d.getUTCFullYear() + "-" + p2(d.getUTCMonth() + 1) + "-" + p2(d.getUTCDate()) + " " +
      p2(d.getUTCHours()) + ":" + p2(d.getUTCMinutes()) + ":" + p2(d.getUTCSeconds());
  }
  function fmtDay(ms) { var d = new Date(ms); return d.getUTCDate() + " " + MON[d.getUTCMonth()] + " " + d.getUTCFullYear(); }
  function fmtHMS(ms) { var d = new Date(ms); return p2(d.getUTCHours()) + ":" + p2(d.getUTCMinutes()) + ":" + p2(d.getUTCSeconds()); }
  function fmtHM(ms) { var d = new Date(ms); return p2(d.getUTCHours()) + ":" + p2(d.getUTCMinutes()); }
  function ist(s) { var ms = parseNaive(s); return ms === null ? "n/a" : fmtDT(ms) + " IST"; }
  function dayTime(s) {
    var ms = parseNaive(s);
    if (ms === null) return "n/a";
    var d = new Date(ms);
    return d.getUTCDate() + " " + MON[d.getUTCMonth()] + " " + d.getUTCFullYear() + ", " + p2(d.getUTCHours()) + ":" + p2(d.getUTCMinutes()) + " IST";
  }
  function splitName(s) { return { dev: "Dev", calibration: "Calibration", test: "Test", practice: "PRACTICE" }[s] || s; }
  function scopeName(s) { return { full: "full 32 months", "90d": "±90 days", "1d": "±1 day" }[s] || s; }
  function venue(v) { return v || "n/a"; }

  /* reference index <-> time */
  function refT0(ov) { return parseNaive(ov.reference.t0_ist); }
  function idxToMs(ov, i) { return refT0(ov) + i * 1000; }
  function msToIdx(ov, ms) { return (ms - refT0(ov)) / 1000; }

  /* ------------------------------------------------------------ data */
  function getJSON(path) {
    return fetch(DATA + path, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(path + ": HTTP " + r.status);
      return r.json();
    });
  }
  function loadBase() {
    if (state.overview && state.clips) return Promise.resolve();
    return Promise.all([getJSON("overview.json"), getJSON("clips.json")]).then(function (a) {
      state.overview = a[0];
      state.clips = a[1];
    });
  }
  function loadClip(split, id) {
    var k = split + "/" + id;
    if (state.pages[k]) return Promise.resolve(state.pages[k]);
    return getJSON("clips/" + encodeURIComponent(split) + "/" + encodeURIComponent(id) + ".json").then(function (d) {
      state.pages[k] = d;
      return d;
    });
  }

  /* ------------------------------------------------------------ resize */
  var ro = window.ResizeObserver ? new ResizeObserver(function (entries) {
    entries.forEach(function (e) {
      var f = e.target.__draw;
      var w = Math.round(e.contentRect.width);
      if (f && w > 0 && w !== e.target.__w) { e.target.__w = w; f(w); }
    });
  }) : null;
  function chart(container, draw) {
    container.__draw = draw;
    resizers.push(container);
    if (ro) ro.observe(container);
    requestAnimationFrame(function () {
      var w = Math.round(container.clientWidth);
      if (w > 0 && w !== container.__w) { container.__w = w; draw(w); }
    });
    return container;
  }
  function clearCharts() {
    resizers.forEach(function (c) { if (ro) ro.unobserve(c); c.__draw = null; });
    resizers = [];
  }

  /* ------------------------------------------------------------ scales */
  function lin(d0, d1, r0, r1) {
    var k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0);
    return function (v) { return r0 + (v - d0) * k; };
  }
  function niceTicks(lo, hi, n) {
    var span = hi - lo;
    if (!(span > 0)) return [lo];
    var step = Math.pow(10, Math.floor(Math.log10(span / n)));
    var err = span / n / step;
    if (err >= 7.5) step *= 10; else if (err >= 3.5) step *= 5; else if (err >= 1.5) step *= 2;
    var out = [];
    for (var v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }
  function pathFrom(pts) {
    var s = "", pen = false;
    for (var i = 0; i < pts.length; i++) {
      var p = pts[i];
      if (!p) { pen = false; continue; }
      s += (pen ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1);
      pen = true;
    }
    return s;
  }
  function quantile(arr, q) {
    if (!arr.length) return null;
    var a = arr.slice().sort(function (x, y) { return x - y; });
    var i = (a.length - 1) * q, lo = Math.floor(i), hi = Math.ceil(i);
    return a[lo] + (a[hi] - a[lo]) * (i - lo);
  }

  /* ============================================================ CHARTS */

  /* 32-month strip: hourly min-max band of the reference, files, gaps, candidate ticks. */
  function drawStrip(host, ov, opts) {
    var api = { setScan: function () {}, setLocked: function () {}, showCands: function () {} };
    var rb = ov.ribbon, refN = ov.reference.seconds;
    var W = host.__w || host.clientWidth || 800;
    var H = opts.height || 120;
    var m = { l: 6, r: 6, t: opts.files ? 18 : 6, b: 20 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var ncol = Math.max(40, Math.floor(iw / 2));
    var nb = rb.n_bins, cols = [];
    var lo = Infinity, hi = -Infinity;
    for (var c = 0; c < ncol; c++) {
      var b0 = Math.floor(c * nb / ncol), b1 = Math.max(b0 + 1, Math.floor((c + 1) * nb / ncol));
      var mn = Infinity, mx = -Infinity;
      for (var b = b0; b < b1 && b < nb; b++) {
        if (rb.min[b] !== null && rb.min[b] < mn) mn = rb.min[b];
        if (rb.max[b] !== null && rb.max[b] > mx) mx = rb.max[b];
      }
      cols.push(mn === Infinity ? null : [mn, mx, b0, b1]);
      if (mn !== Infinity) { lo = Math.min(lo, mn); hi = Math.max(hi, mx); }
    }
    var xi = lin(0, refN, m.l, m.l + iw);
    var y = lin(hi, lo, m.t, m.t + ih);
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
      "aria-label": opts.label || "Hourly minimum and maximum of India's grid frequency over the reference span" });
    var g = sv("g");
    // band
    var top = [], bot = [], segs = [];
    function flush() {
      if (top.length) segs.push("M" + top.join("L") + "L" + bot.reverse().join("L") + "Z");
      top = []; bot = [];
    }
    cols.forEach(function (cl, i) {
      var x = m.l + (i + 0.5) * iw / ncol;
      if (!cl) { flush(); return; }
      top.push(x.toFixed(1) + "," + y(cl[1]).toFixed(1));
      bot.push(x.toFixed(1) + "," + y(cl[0]).toFixed(1));
    });
    flush();
    g.appendChild(sv("path", { class: "band", d: segs.join("") }));
    // 50 Hz line
    if (lo < 0 && hi > 0) g.appendChild(sv("line", { class: "filesep", x1: m.l, x2: m.l + iw, y1: y(0), y2: y(0), opacity: 0.5 }));
    // gaps (>= 10 min) as marks under the band
    (ov.reference.gaps_ge_10min || []).forEach(function (gp) {
      var x0 = xi(gp.start_index), x1 = xi(gp.start_index + gp.length_s);
      g.appendChild(sv("rect", { x: x0, y: m.t + ih + 2, width: Math.max(1.5, x1 - x0), height: 4, fill: "var(--faint)" },
        sv("title", { text: "gap " + gp.start_ist + " to " + gp.end_ist + " IST (" + fmtInt(gp.length_s) + " s)" })));
    });
    // files
    if (opts.files) {
      var bounds = [0].concat(ov.reference.junctions.map(function (j) { return j.handover_index; })).concat([refN]);
      var names = ["I", "II", "III", "IV", "V"];
      for (var k = 0; k < bounds.length - 1; k++) {
        if (k > 0) g.appendChild(sv("line", { class: "filesep", x1: xi(bounds[k]), x2: xi(bounds[k]), y1: m.t - 14, y2: m.t + ih }));
        g.appendChild(sv("text", { class: "filelab", x: (xi(bounds[k]) + xi(bounds[k + 1])) / 2, y: m.t - 5, "text-anchor": "middle",
          text: "file " + (names[k] || k + 1) }));
      }
    }
    // year ticks
    var ax = sv("g", { class: "ax" });
    for (var yr = 2021; yr <= 2023; yr++) {
      var ix = msToIdx(ov, Date.UTC(yr, 0, 1));
      if (ix < 0 || ix > refN) continue;
      ax.appendChild(sv("line", { x1: xi(ix), x2: xi(ix), y1: m.t + ih, y2: m.t + ih + 8 }));
      ax.appendChild(sv("text", { x: xi(ix) + 3, y: H - 4, text: String(yr) }));
    }
    g.appendChild(ax);
    // scope window
    if (opts.scope && opts.scope[1] - opts.scope[0] < refN * 0.5) {
      var sx0 = xi(opts.scope[0]), sx1 = xi(opts.scope[1]);
      g.appendChild(sv("rect", { x: sx0 - 1, y: m.t, width: Math.max(3, sx1 - sx0 + 2), height: ih, fill: "none", stroke: "var(--text)", "stroke-dasharray": "2 2" }));
    }
    // candidates
    var cg = sv("g", { class: "cands-g" });
    var lastX = -99;
    (opts.candidates || []).slice().reverse().forEach(function (cd) {
      var x = xi(cd.start_index);
      cg.appendChild(sv("line", { class: cd.rank === 1 ? "tick-top" : "tick-cand", x1: x, x2: x, y1: m.t - (opts.files ? 2 : 0), y2: m.t + ih },
        sv("title", { text: "#" + cd.rank + " " + cd.start_ist + " IST, r = " + fmtF(cd.r, 4) })));
    });
    var placed = [];
    (opts.candidates || []).forEach(function (cd) {
      var x = xi(cd.start_index);
      var ok = placed.every(function (p) { return Math.abs(p - x) > 11; });
      if (ok) {
        placed.push(x);
        cg.appendChild(sv("text", { x: x, y: m.t + ih - 3, "text-anchor": "middle", class: "rank", text: String(cd.rank),
          style: "fill: var(--text)", dx: cd.rank === 1 ? 0 : 0 }));
      }
    });
    g.appendChild(cg);
    // scan line
    var scan = sv("line", { class: "scan", x1: m.l, x2: m.l, y1: m.t, y2: m.t + ih, visibility: "hidden" });
    g.appendChild(scan);
    // cursor and readout
    var cur = sv("line", { class: "cursor", x1: 0, x2: 0, y1: m.t, y2: m.t + ih, visibility: "hidden" });
    g.appendChild(cur);
    svg.appendChild(g);
    host.replaceChildren(svg);

    api.setScan = function (f) {
      if (f === null) { scan.setAttribute("visibility", "hidden"); return; }
      var x = m.l + f * iw;
      scan.setAttribute("x1", x); scan.setAttribute("x2", x); scan.setAttribute("visibility", "visible");
    };
    api.showCands = function (on) { cg.setAttribute("visibility", on ? "visible" : "hidden"); };
    api.setLocked = function (on) { svg.classList.toggle("locked-svg", !!on); };

    if (opts.readout) {
      var ro_ = opts.readout, colIdx = -1;
      var show = function (i) {
        colIdx = Math.max(0, Math.min(ncol - 1, i));
        var cl = cols[colIdx];
        var x = m.l + (colIdx + 0.5) * iw / ncol;
        cur.setAttribute("x1", x); cur.setAttribute("x2", x); cur.setAttribute("visibility", "visible");
        var a = cl ? cl[2] : Math.floor(colIdx * nb / ncol), b = cl ? cl[3] : a + 1;
        var ms0 = idxToMs(ov, rb.first_bin_index + a * rb.bin_s), ms1 = idxToMs(ov, rb.first_bin_index + b * rb.bin_s);
        fill(ro_, T("num", fmtDay(ms0) + " " + fmtHM(ms0)), " to ", T("num", fmtDay(ms1) + " " + fmtHM(ms1)), " IST: ",
          cl ? [T("num", fmtF(50 + cl[0] / 1000, 3)), " to ", T("num", fmtF(50 + cl[1] / 1000, 3)), " Hz (hourly min and max)"] : "no data (gap)");
      };
      svg.addEventListener("pointermove", function (e) {
        var r = svg.getBoundingClientRect();
        var x = (e.clientX - r.left) * W / r.width;
        show(Math.floor((x - m.l) / iw * ncol));
      });
      svg.addEventListener("pointerleave", function () { cur.setAttribute("visibility", "hidden"); });
      host.onkeydown = function (e) {
        var step = e.shiftKey ? 10 : 1;
        if (e.key === "ArrowRight") { show((colIdx < 0 ? 0 : colIdx) + step); e.preventDefault(); }
        else if (e.key === "ArrowLeft") { show((colIdx < 0 ? ncol - 1 : colIdx) - step); e.preventDefault(); }
        else if (e.key === "Home") { show(0); e.preventDefault(); }
        else if (e.key === "End") { show(ncol - 1); e.preventDefault(); }
      };
    }
    return api;
  }

  /* Trace on reference: grey reference window, amber trace (solid where its frame weight is high). */
  function drawTrace(host, tr, rw, opts) {
    opts = opts || {};
    var W = host.__w || host.clientWidth || 800;
    var showW = !!opts.weights;
    var H = opts.height || 260;
    var wH = showW ? 26 : 0;
    var m = { l: 56, r: 10, t: 20, b: 26 + wH };
    if (W < 500) m.l = 48;
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var rs = rw.step_s, ts = tr.step_s, sc = tr.scale || 1e4;
    var refPts = rw.values, nRef = refPts.length;
    var span = nRef * rs;
    var x = lin(0, span, m.l, m.l + iw);
    var off = rw.trace_offset_s;
    var vals = [];
    refPts.forEach(function (v) { if (v !== null) vals.push(v); });
    if (!vals.length) { host.replaceChildren(el("p", { class: "muted" }, "No reference data in this window.")); return { setShift: function () {}, setLocked: function () {} }; }
    var solidVals = [];
    tr.freq.forEach(function (v, j) { if (v !== null && tr.weight[j] >= 25) solidVals.push(v); });
    var lo = Math.min(quantile(vals, 0), solidVals.length ? quantile(solidVals, 0.02) : Infinity);
    var hi = Math.max(quantile(vals, 1), solidVals.length ? quantile(solidVals, 0.98) : -Infinity);
    var pad = (hi - lo) * 0.08 || 10;
    lo -= pad; hi += pad;
    var y = lin(hi, lo, m.t, m.t + ih);
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
      "aria-label": opts.label || "Recording trace drawn over the matched reference window" });
    var cid = "clip" + Math.random().toString(36).slice(2, 8);
    svg.appendChild(sv("defs", null, sv("clipPath", { id: cid }, sv("rect", { x: m.l, y: m.t - 2, width: iw, height: ih + 4 }))));
    // y axis
    var ax = sv("g", { class: "ax" }), gr = sv("g", { class: "grid" });
    niceTicks(lo, hi, 4).forEach(function (v) {
      gr.appendChild(sv("line", { x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }));
      ax.appendChild(sv("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end", text: (50 + v / sc).toFixed(3) }));
    });
    ax.appendChild(sv("text", { x: 2, y: 10, text: "Hz" }));
    // x axis: IST on the reference's labels
    var ms0 = parseNaive(rw.start_ist);
    var stepMin = span / 60 > 150 ? (iw < 500 ? 60 : 30) : (iw < 500 ? 30 : 15);
    var first = Math.ceil((ms0 / 1000) / (stepMin * 60)) * stepMin * 60 - ms0 / 1000;
    for (var s = first; s <= span; s += stepMin * 60) {
      ax.appendChild(sv("line", { x1: x(s), x2: x(s), y1: m.t + ih, y2: m.t + ih + 5 }));
      ax.appendChild(sv("text", { x: x(s), y: m.t + ih + 17, "text-anchor": "middle", text: fmtHM(ms0 + s * 1000) }));
    }
    ax.appendChild(sv("line", { x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih }));
    svg.appendChild(gr);
    svg.appendChild(ax);
    // lock mark (behind the lines)
    var tEnd = off + tr.freq.length * ts;
    var lm = sv("g", { class: "lockmark", visibility: opts.locked ? "visible" : "hidden" },
      sv("rect", { x: x(off), y: m.t, width: Math.max(1, x(tEnd) - x(off)), height: ih }),
      sv("line", { x1: x(off), x2: x(off), y1: m.t, y2: m.t + ih }),
      sv("line", { x1: x(tEnd), x2: x(tEnd), y1: m.t, y2: m.t + ih }),
      sv("text", { x: x(off) + 5, y: m.t + 12, text: opts.lockLabel || "lock" }));
    svg.appendChild(lm);
    // reference
    var body = sv("g", { "clip-path": "url(#" + cid + ")" });
    var rp = refPts.map(function (v, j) { return v === null ? null : [x(j * rs + (rs - 1) / 2), y(v)]; });
    body.appendChild(sv("path", { class: "refline", d: pathFrom(rp) }));
    // trace (translated during the slide)
    var tg = sv("g");
    var fp = [], spts = [];
    tr.freq.forEach(function (v, j) {
      var px = x(off + j * ts + (ts - 1) / 2);
      var py = v === null ? null : y(v);
      fp.push(py === null ? null : [px, py]);
      spts.push(py === null || tr.weight[j] < 25 ? null : [px, py]);
    });
    tg.appendChild(sv("path", { class: "trace-faint", d: pathFrom(fp) }));
    tg.appendChild(sv("path", { class: "trace-solid", d: pathFrom(spts) }));
    body.appendChild(tg);
    svg.appendChild(body);
    // frame weights
    var wg = null;
    if (showW) {
      var y0 = H - 6, hW = wH - 8;
      wg = sv("g");
      var wpath = "";
      tr.weight.forEach(function (w, j) {
        if (!w) return;
        var px = x(off + j * ts);
        var hh = hW * w / 100;
        wpath += "M" + px.toFixed(1) + "," + y0 + "v" + (-hh).toFixed(1);
      });
      wg.appendChild(sv("path", { d: wpath, stroke: "var(--trace)", "stroke-width": Math.max(1, iw / tr.weight.length).toFixed(2), opacity: 0.6 }));
      svg.appendChild(sv("text", { x: 2, y: y0 - 2, text: "weight" }));
      svg.appendChild(wg);
    }
    host.replaceChildren(svg);
    var pxPerS = iw / span;
    return {
      setShift: function (sec) {
        var t = "translate(" + (sec * pxPerS).toFixed(2) + ",0)";
        tg.setAttribute("transform", t);
        if (wg) wg.setAttribute("transform", t);
      },
      setLocked: function (on) { lm.setAttribute("visibility", on ? "visible" : "hidden"); }
    };
  }

  /* Uniqueness curve (SYNTHETIC noise simulation). Tested noise levels in grey; the readings at the
     noise measured on the Test streams that lock (M4, results/m4_measured.json) in amber. */
  function drawUniq(host, u, scope, noise, meas) {
    var W = host.__w || host.clientWidth || 600, H = 270;
    var m = { l: 44, r: W < 480 ? 58 : 70, t: 12, b: 34 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var lengths = [5, 10, 20, 40, 60];
    var xpos = function (L) { return m.l + lengths.indexOf(L) / (lengths.length - 1) * iw; };
    var y = lin(1, 0, m.t, m.t + ih);
    var rows = u.rows.filter(function (r) { return r.scope === scope && (r.noise === noise || r.sigma_mhz === 0); });
    var levels = [];
    rows.forEach(function (r) { if (levels.indexOf(r.sigma_mhz) < 0) levels.push(r.sigma_mhz); });
    levels.sort(function (a, b) { return a - b; });
    var hl = null;
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
      "aria-label": "SYNTHETIC noise simulation: share of unique locks against excerpt length, scope " + scopeName(scope) +
        (meas ? "; amber: the readings at the measured noise level" : "") });
    var ax = sv("g", { class: "ax" }), gr = sv("g", { class: "grid" });
    [0, 0.25, 0.5, 0.75, 1].forEach(function (v) {
      gr.appendChild(sv("line", { x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }));
      ax.appendChild(sv("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end", text: Math.round(v * 100) + "%" }));
    });
    lengths.forEach(function (L) {
      ax.appendChild(sv("text", { x: xpos(L), y: m.t + ih + 16, "text-anchor": "middle", text: L + " min" }));
    });
    ax.appendChild(sv("text", { x: m.l + iw / 2, y: H - 2, "text-anchor": "middle", text: "excerpt length" }));
    svg.appendChild(gr);
    svg.appendChild(ax);
    svg.appendChild(sv("line", { class: "thr", x1: m.l, x2: m.l + iw, y1: y(0.9), y2: y(0.9) }));
    svg.appendChild(sv("text", { x: m.l + 4, y: y(0.9) - 4, text: "90%" }));
    var labY = [];
    levels.forEach(function (lv, i) {
      var pts = lengths.map(function (L) {
        var r = rows.filter(function (q) { return q.length_min === L && q.sigma_mhz === lv; })[0];
        return r ? { L: L, r: r } : null;
      }).filter(Boolean);
      var isHl = lv === hl;
      var col = isHl ? "var(--trace)" : "var(--ref)";
      var op = isHl ? 1 : (lv === 0 ? 0.45 : 0.45 + 0.55 * (i / Math.max(1, levels.length - 1)));
      var g = sv("g", { opacity: op });
      g.appendChild(sv("path", { class: "u-line", stroke: col, "stroke-dasharray": lv === 0 ? "3 3" : null,
        d: pathFrom(pts.map(function (p) { return [xpos(p.L), y(p.r.hit_rate)]; })) }));
      pts.forEach(function (p) {
        if (isHl && p.r.lo !== null) g.appendChild(sv("line", { class: "u-ci", stroke: col, x1: xpos(p.L), x2: xpos(p.L), y1: y(p.r.lo), y2: y(p.r.hi) }));
        g.appendChild(sv("circle", { class: "u-dot", fill: col, cx: xpos(p.L), cy: y(p.r.hit_rate), r: isHl ? 3.2 : 2.4 },
          sv("title", { text: (lv === 0 ? "no noise" : lv + " mHz") + ", " + p.L + " min: " + p.r.hits + "/" + p.r.N + " (" + pct(p.r.hit_rate) + ")" })));
      });
      var last = pts[pts.length - 1];
      var ly = y(last.r.hit_rate);
      // stack end labels that would collide
      labY.forEach(function (v) { if (Math.abs(v - ly) < 11) ly = v + 11; });
      labY.push(ly);
      g.appendChild(sv("text", { x: m.l + iw + 8, y: ly + 4, text: lv === 0 ? "no noise" : lv + " mHz", style: isHl ? "fill: var(--trace)" : null }));
      svg.appendChild(g);
    });
    // the readings at the measured noise level, interpolated by scripts/make_figures.py
    (meas || []).forEach(function (mc) {
      var pts = lengths.filter(function (L) { return mc.rates[String(L)] !== undefined; });
      var g = sv("g", { class: "u-meas" });
      g.appendChild(sv("path", { class: "u-line", stroke: "var(--trace)", "stroke-width": 2.2, "stroke-dasharray": mc.dash || null,
        d: pathFrom(pts.map(function (L) { return [xpos(L), y(mc.rates[String(L)])]; })) }));
      pts.forEach(function (L) {
        g.appendChild(sv("circle", { class: "u-dot", fill: "var(--trace)", cx: xpos(L), cy: y(mc.rates[String(L)]), r: 3.2 },
          sv("title", { text: mc.name + ", " + L + " min: " + pct(mc.rates[String(L)]) + " (interpolated)" })));
      });
      svg.appendChild(g);
    });
    host.replaceChildren(svg);
    return hl;
  }

  /* Label drift per weekly window, per reference file. */
  function drawDrift(host, ov) {
    var d = ov.drift, refN = ov.reference.seconds;
    var W = host.__w || host.clientWidth || 600, H = 250;
    var m = { l: 50, r: 10, t: 18, b: 26 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var lo = Infinity, hi = -Infinity;
    d.windows.forEach(function (w) { lo = Math.min(lo, w.shift_s); hi = Math.max(hi, w.shift_s); });
    lo = Math.min(lo, 0); hi = Math.max(hi, 0);
    var pad = (hi - lo) * 0.08;
    var x = lin(0, refN, m.l, m.l + iw), y = lin(hi + pad, lo - pad, m.t, m.t + ih);
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
      "aria-label": "Label drift of each reference file per 7-day window, in seconds" });
    var ax = sv("g", { class: "ax" }), gr = sv("g", { class: "grid" });
    niceTicks(lo, hi, 5).forEach(function (v) {
      gr.appendChild(sv("line", { x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }));
      ax.appendChild(sv("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end", text: fixSign(String(v)) + " s" }));
    });
    for (var yr = 2021; yr <= 2023; yr++) {
      var ix = msToIdx(ov, Date.UTC(yr, 0, 1));
      ax.appendChild(sv("line", { x1: x(ix), x2: x(ix), y1: m.t + ih, y2: m.t + ih + 6 }));
      ax.appendChild(sv("text", { x: x(ix) + 3, y: H - 6, text: String(yr) }));
    }
    svg.appendChild(gr);
    svg.appendChild(ax);
    var names = ["freq_I.csv", "freq_II.csv", "freq_III.csv", "freq_IV.csv", "freq_V.csv"];
    var short = ["I", "II", "III", "IV", "V"];
    var labEnd = -99;
    names.forEach(function (f, k) {
      var ws = d.windows.filter(function (w) { return w.file === f; });
      if (!ws.length) return;
      var pts = ws.map(function (w) { return [x(msToIdx(ov, parseNaive(w.mid))), y(w.shift_s)]; });
      svg.appendChild(sv("path", { class: "drift-line", d: pathFrom(pts) }));
      ws.forEach(function (w, i) {
        svg.appendChild(sv("circle", { class: "drift-pt" + (w.r < 0.5 ? " weak" : ""), cx: pts[i][0], cy: pts[i][1], r: 2.3 },
          sv("title", { text: "file " + short[k] + ", week of " + w.mid + ": " + sgn(w.shift_s) + " s, r = " + fmtF(w.r, 2) })));
      });
      var lab = (W < 640 ? "" : "file ") + short[k];
      var lx = Math.max(pts[0][0], labEnd + 6);
      svg.appendChild(sv("text", { x: lx, y: m.t - 5, text: lab }));
      labEnd = lx + lab.length * 6.8;
    });
    host.replaceChildren(svg);
  }

  /* Latency: the counted Calibration offsets o and L. */
  function drawLatency(host, lat) {
    var vals = lat.counted.values_s || [];
    var W = host.__w || host.clientWidth || 400, H = 86;
    var m = { l: 14, r: 14, t: 18, b: 24 };
    var iw = W - m.l - m.r;
    var lo = Math.min(-10, Math.min.apply(null, vals) - 2), hi = Math.max(10, Math.max.apply(null, vals) + 2);
    var x = lin(lo, hi, m.l, m.l + iw);
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
      "aria-label": "Counted Calibration offsets o: " + vals.join(", ") + " s; L = " + lat.L_s + " s" });
    var ax = sv("g", { class: "ax" });
    ax.appendChild(sv("line", { x1: m.l, x2: m.l + iw, y1: H - m.b, y2: H - m.b }));
    niceTicks(lo, hi, 6).forEach(function (v) {
      ax.appendChild(sv("line", { x1: x(v), x2: x(v), y1: H - m.b, y2: H - m.b + 5 }));
      ax.appendChild(sv("text", { x: x(v), y: H - 6, "text-anchor": "middle", text: fixSign(String(v)) }));
    });
    svg.appendChild(ax);
    svg.appendChild(sv("line", { class: "lat-L", x1: x(lat.L_s), x2: x(lat.L_s), y1: m.t - 6, y2: H - m.b }));
    svg.appendChild(sv("text", { x: x(lat.L_s) + 4, y: m.t, text: "L = " + lat.L_s + " s", style: "fill: var(--text)" }));
    var seen = {};
    vals.forEach(function (v) {
      seen[v] = (seen[v] || 0) + 1;
      svg.appendChild(sv("circle", { class: "lat-pt", cx: x(v), cy: H - m.b - 7 - (seen[v] - 1) * 9, r: 3.5 }, sv("title", { text: "o = " + v + " s" })));
    });
    host.replaceChildren(svg);
  }

  /* Hum: median peak-to-floor ratio per harmonic (log axis), threshold 3. */
  function drawHum(host, ratios, title) {
    var keys = Object.keys(ratios || {});
    var W = host.__w || host.clientWidth || 300;
    var rowH = 18, m = { l: 58, r: 46, t: 6, b: 20 };
    var H = m.t + m.b + rowH * Math.max(1, keys.length);
    var iw = W - m.l - m.r;
    var mx = 10;
    keys.forEach(function (k) { if (ratios[k] > mx) mx = ratios[k]; });
    var x = function (v) { return m.l + Math.log10(Math.max(1, v)) / Math.log10(mx) * iw; };
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
      "aria-label": title + ": " + keys.map(function (k) { return k + " " + ratios[k]; }).join(", ") });
    keys.forEach(function (k, i) {
      var v = ratios[k], yy = m.t + i * rowH;
      svg.appendChild(sv("text", { x: m.l - 6, y: yy + 12, "text-anchor": "end", text: k }));
      svg.appendChild(sv("rect", { class: "bar-h" + (v >= 3 ? "" : " off"), x: m.l, y: yy + 3, width: Math.max(1, x(v) - m.l), height: rowH - 7 }));
      svg.appendChild(sv("text", { x: x(v) + 4, y: yy + 12, text: fmtF(v, 2), style: "fill: var(--text)" }));
    });
    var tx = x(3);
    svg.appendChild(sv("line", { class: "thr", x1: tx, x2: tx, y1: m.t, y2: H - m.b + 2 }));
    svg.appendChild(sv("text", { x: tx, y: H - 4, "text-anchor": "middle", text: "3" }));
    host.replaceChildren(svg);
  }

  /* M2 signed error of the top candidate for one cell, from results/figures/figures.json. The axis is
     the one of scripts/make_figures.py: linear inside the +-30 s hit band, one unit per decade outside. */
  function errSeconds(f2, pos) {
    var a = Math.abs(pos), h = f2.band_display_half, b = f2.band_half_width_s;
    var s = a <= h ? a / h * b : b * Math.pow(10, a - h);
    return pos < 0 ? -s : s;
  }
  function shortDur(s) {
    var a = Math.abs(s), sign = s < 0 ? MINUS : "";
    if (a < 90) return sign + Math.round(a) + " s";
    if (a < 5400) return sign + Math.round(a / 60) + " min";
    if (a < 129600) return sign + Math.round(a / 3600) + " h";
    return sign + Math.round(a / 86400) + " d";
  }
  function drawSignedError(host, f2, key) {
    var c = f2.cells[key];
    var W = host.__w || host.clientWidth || 560, H = 212;
    var narrow = W < 460;
    var m = { l: 30, r: 8, t: 10, b: 42 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var E = f2.bin_edges_display, nb = E.length - 1, band = f2.band_display_half;
    var x = lin(E[0], E[nb], m.l, m.l + iw);
    var ymax = Math.max.apply(null, c.bin_counts.concat([1]));
    var step = ymax <= 10 ? 2 : ymax <= 30 ? 5 : 10;
    var top = step * Math.ceil((ymax + 1) / step);
    var y = lin(top, 0, m.t, m.t + ih);
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
      "aria-label": "Signed error of the top candidate, extractor " + f2.extractor + ", " + key.replace("/", " min, ") +
        ": " + c.hits + " of " + c.n + " within 30 s of T*" });
    var gr = sv("g", { class: "grid" }), ax = sv("g", { class: "ax" });
    for (var v = 0; v <= top; v += step) {
      gr.appendChild(sv("line", { x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }));
      ax.appendChild(sv("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end", text: String(v) }));
    }
    svg.appendChild(sv("rect", { class: "hitband", x: x(-band), y: m.t, width: x(band) - x(-band), height: ih }));
    svg.appendChild(gr);
    var bw = (x(E[1]) - x(E[0])) * 0.84;
    c.bin_counts.forEach(function (n, i) {
      if (!n) return;
      var ctr = (E[i] + E[i + 1]) / 2, inBand = Math.abs(ctr) < band;
      var a = errSeconds(f2, E[i]), b = errSeconds(f2, E[i + 1]);
      svg.appendChild(sv("rect", { class: inBand ? "eb-hit" : "eb-miss", x: x(ctr) - bw / 2, y: y(n), width: bw, height: y(0) - y(n) },
        sv("title", { text: n + (n === 1 ? " stream" : " streams") + ", e from " + shortDur(a) + " to " + shortDur(b) + (inBand ? " (hit)" : "") })));
    });
    f2.ticks.forEach(function (t) {
      ax.appendChild(sv("line", { x1: x(t.pos), x2: x(t.pos), y1: m.t + ih, y2: m.t + ih + 5 }));
      if (narrow && /min/.test(t.label)) return;
      ax.appendChild(sv("text", { x: x(t.pos), y: m.t + ih + 17, "text-anchor": "middle", text: t.label }));
    });
    ax.appendChild(sv("line", { x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih }));
    svg.appendChild(ax);
    svg.appendChild(sv("text", { x: m.l + iw / 2, y: H - 3, "text-anchor": "middle", text: "e = top candidate start − T*" }));
    host.replaceChildren(svg);
  }

  /* M3 reliability for the primary cell, from results/figures/figures.json: Test top-1 hit rate by the
     pre-registered confidence bins, and every excerpt's confidence on the same axis. */
  function confPos(cv, first) { return cv <= first ? cv / first : 1 + Math.log2(cv / first); }
  function swarm(xs, d) {
    var order = xs.map(function (v, i) { return i; }).sort(function (a, b) { return xs[a] - xs[b]; });
    var placed = [], off = new Array(xs.length);
    order.forEach(function (i) {
      for (var k = 0; k < 40; k++) {
        var cands = k === 0 ? [0] : [k * d * 0.9, -k * d * 0.9], ok = null;
        for (var j = 0; j < cands.length && ok === null; j++) {
          var yc = cands[j];
          if (placed.every(function (p) { return (xs[i] - p[0]) * (xs[i] - p[0]) + (yc - p[1]) * (yc - p[1]) >= d * d - 1e-9; })) ok = yc;
        }
        if (ok !== null) { off[i] = ok; placed.push([xs[i], ok]); return; }
      }
      off[i] = 0;
    });
    return off;
  }
  function drawReliability(host, f3) {
    var W = host.__w || host.clientWidth || 560;
    var narrow = W < 460;
    var rows = [
      { label: narrow ? "Test hits" : "Test, within 30 s", vals: f3.test_hits, cls: "rel-hit" },
      { label: narrow ? "Test misses" : "Test, over 30 s off", vals: f3.test_misses, cls: "rel-miss" },
      { label: narrow ? "real neg." : "real negatives", vals: f3.real.confidence, cls: "rel-real" },
      { label: narrow ? "SYNTH. neg." : "SYNTHETIC negatives", vals: f3.SYNTHETIC.confidence, cls: "rel-syn" }];
    var first = f3.first_bin_edge;
    var all = [];
    rows.forEach(function (r) { all = all.concat(r.vals); });
    var lastLo = f3.bins[f3.bins.length - 1].lo;
    var xmax = Math.max(6, Math.ceil(confPos(Math.max.apply(null, all.concat([lastLo * 2])), first) - 1e-9));
    var rowH = 20, topH = 120;
    var m = { l: narrow ? 112 : 172, r: 10, t: 22, b: 38 };
    var H = m.t + topH + 14 + rows.length * rowH + m.b;
    var iw = W - m.l - m.r;
    var x = lin(0, xmax, m.l, m.l + iw);
    var y = lin(1, 0, m.t, m.t + topH);
    var s0 = m.t + topH + 14;
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
      "aria-label": "Reliability of the confidence score: Test top-1 hit rate by confidence bin, " +
        f3.bins.map(function (b) { return b.bin + " " + b.k + "/" + b.n; }).join(", ") +
        "; real negatives at or above tau " + f3.real.n_at_or_above_tau + " of " + f3.real.n +
        ", SYNTHETIC negatives " + f3.SYNTHETIC.n_at_or_above_tau + " of " + f3.SYNTHETIC.n });
    var gr = sv("g", { class: "grid" }), ax = sv("g", { class: "ax" });
    [0, 0.5, 1].forEach(function (v) {
      gr.appendChild(sv("line", { x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }));
      ax.appendChild(sv("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end", text: Math.round(100 * v) + "%" }));
    });
    for (var t = 1; t < f3.bins.length; t++) gr.appendChild(sv("line", { x1: x(t), x2: x(t), y1: m.t, y2: s0 + rows.length * rowH }));
    svg.appendChild(gr);
    if (!narrow) svg.appendChild(sv("text", { x: 2, y: m.t + topH / 2 + 4, text: "hit rate" }));
    f3.bins.forEach(function (b, i) {
      var x0 = i, x1 = b.hi === null ? xmax : i + 1, xc = (x0 + x1) / 2;
      if (!b.n) { svg.appendChild(sv("text", { x: x(xc), y: y(0) - 4, "text-anchor": "middle", text: "n = 0" })); return; }
      var g = sv("g", null, sv("title", { text: "confidence " + b.bin + ": " + b.k + "/" + b.n + " within 30 s (" + pct(b.rate) + ", " + ciText(b) + ")" }));
      g.appendChild(sv("line", { class: "rel-ci", x1: x(xc), x2: x(xc), y1: y(b.wilson95[0]), y2: y(b.wilson95[1]) }));
      g.appendChild(sv("line", { class: "rel-rate", x1: x(x0 + 0.08), x2: x(x1 - 0.08), y1: y(b.rate), y2: y(b.rate) }));
      g.appendChild(sv("circle", { class: "rel-dot", cx: x(xc), cy: y(b.rate), r: 3.4 }));
      var up = b.wilson95[1] + 0.2 <= 1.0;
      g.appendChild(sv("text", { x: x(xc), y: up ? y(b.wilson95[1]) - 5 : y(b.wilson95[0]) + 13, "text-anchor": "middle",
        text: b.k + "/" + b.n, style: "fill: var(--text)" }));
      svg.appendChild(g);
    });
    // the dot rows
    var d = 6.2;
    rows.forEach(function (r, k) {
      var yc = s0 + k * rowH + rowH / 2;
      if (k) svg.appendChild(sv("line", { class: "rel-sep", x1: m.l, x2: m.l + iw, y1: s0 + k * rowH, y2: s0 + k * rowH }));
      svg.appendChild(sv("text", { x: m.l - 6, y: yc + 4, "text-anchor": "end", text: r.label + " (" + r.vals.length + ")" }));
      var xs = r.vals.map(function (v) { return x(confPos(v, first)); });
      var off = swarm(xs, d);
      var mx = Math.max.apply(null, off.map(Math.abs).concat([0]));
      var lim = rowH / 2 - 3;
      var sc = mx > lim ? lim / mx : 1;
      xs.forEach(function (px, i) {
        svg.appendChild(sv("circle", { class: "rel-pt " + r.cls, cx: px, cy: yc + off[i] * sc, r: 2.7 },
          sv("title", { text: r.label + ": confidence " + fmtF(r.vals[i], 4) })));
      });
    });
    // tau
    var xt = x(confPos(f3.tau, first));
    svg.appendChild(sv("line", { class: "thr", x1: xt, x2: xt, y1: m.t - 4, y2: s0 + rows.length * rowH }));
    svg.appendChild(sv("text", { x: xt, y: m.t - 8, "text-anchor": "middle", text: "tau " + fmtF(f3.tau, 4), style: "fill: var(--text)" }));
    // confidence axis: linear below 0.025, one step per doubling above
    var yb = s0 + rows.length * rowH;
    ax.appendChild(sv("line", { x1: m.l, x2: m.l + iw, y1: yb, y2: yb }));
    for (var j = 0; j <= xmax; j++) {
      var val = j === 0 ? 0 : first * Math.pow(2, j - 1);
      ax.appendChild(sv("line", { x1: x(j), x2: x(j), y1: yb, y2: yb + 5 }));
      if (narrow && j % 2) continue;                       // every other label where space is short
      ax.appendChild(sv("text", { x: x(j), y: yb + 17, "text-anchor": "middle", text: String(+val.toFixed(4)) }));
    }
    svg.appendChild(ax);
    svg.appendChild(sv("text", { x: m.l + iw / 2, y: H - 3, "text-anchor": "middle", text: "confidence = atanh(r1) − atanh(r2)" }));
    host.replaceChildren(svg);
  }

  /* ============================================================ SHARED BLOCKS */

  function candTable(cands, opts) {
    opts = opts || {};
    var tb = el("tbody");
    var rCls = opts.noErr ? "r num" : "r num hide-sm";        // on a phone the error column matters more than r
    cands.forEach(function (c, i) {
      tb.appendChild(el("tr", { class: i === 0 ? "is-top" : null },
        el("td", { class: "num" }, String(i + 1)),
        el("td", null, T("t", c.start_ist)),
        el("td", { class: rCls }, fmtF(c.r, 4)),
        opts.noErr ? null : el("td", { class: "r num" }, c.e_s === undefined || c.e_s === null ? "n/a" : sgn(c.e_s) + " s")));
    });
    return el("div", { class: "tbl-wrap" }, el("table", { class: "cands" },
      opts.caption ? el("caption", { class: "sr-only" }, opts.caption) : null,
      el("thead", null, el("tr", null, el("th", { scope: "col" }, "#"), el("th", { scope: "col" }, "start (IST)"),
        el("th", { scope: "col", class: rCls.replace(" num", "") }, "r"), opts.noErr ? null : el("th", { scope: "col", class: "r" }, "e vs T*"))), tb));
  }

  function srcLine(list) {
    if (!list || !list.length) return null;
    return el("p", { class: "figure-note" }, "Source: ", list.map(function (s, i) { return [i ? ", " : "", el("code", null, s)]; }));
  }

  /* ============================================================ OVERVIEW */

  function renderOverview() {
    var ov = state.overview;
    var root = el("div");
    root.appendChild(introBlock(ov));
    var sum = summaryBlock(ov);
    if (sum) root.appendChild(sum);
    if (ov.hero) root.appendChild(heroBlock(ov));
    root.appendChild(stripBlock(ov));
    root.appendChild(resultsBlock(ov));
    root.appendChild(uniqBlock(ov));
    root.appendChild(driftBlock(ov));
    root.appendChild(doesBlock(ov));
    return { node: root, title: "Gunj: overview" };
  }

  function introBlock(ov) {
    var r = ov.reference;
    return el("section", { class: "wrap intro" },
      el("h1", { tabindex: "-1" }, "Gunj dates Indian recordings from the hum of the national grid"),
      el("p", { class: "lede" },
        "India runs one synchronous AC grid, so mains hum in a recording follows the same frequency wander everywhere in the country. ",
        "Gunj extracts that wander from audio as a one-second trace and searches a public one-second log of India's grid frequency for the moment it fits."),
      el("p", { class: "muted small" }, "Reference: ", T("t", r.t0_ist), " to ", T("t", r.t_end_ist), " IST, ",
        T("num", fmtInt(r.seconds)), " seconds, ", T("num", fmtF(r.valid_pct, 2) + "%"), " valid. ",
        "Evaluation plan fixed in advance in ", repoLink("blob/main/PROTOCOL.md", el("code", null, "PROTOCOL.md")), ". ",
        "Code and README: ", repoLink("", "the GitHub repository"), ". Method and results in paper form: ",
        repoLink("blob/main/docs/gunj-report.pdf", "the report (PDF)"), "."));
  }

  /* a link into the public repository, or plain text if the export has no repository URL */
  function repoLink(path, label) {
    var base = (state.overview || {}).repo_url;
    if (!base) return label;
    return el("a", { href: base.replace(/\/$/, "") + (path ? "/" + path : "") }, label);
  }

  /* The blind Test result on the first screen, before the Calibration example. Every number is read from
     data/overview.json (results/evaluation.json, results/error_breakdown.json, results/gate_frozen.json). */
  function summaryBlock(ov) {
    var t = ov.test, res = ov.results || {};
    if (!t || !t.m2 || (t.mode && t.mode !== "real")) return null;
    var c2 = ((t.m2.v2 || {})["60"] || {}).full;
    if (!c2 || !c2.top1_hit) return null;
    var g = t.gate || {};
    var bd = res.breakdown;
    var conf = bd ? bd.cells[String(bd.primary.length_min)][bd.primary.scope].confident : null;
    var fmc = (((t.m3 || {}).v2 || {})["60"] || {}).full;
    var real = fmc ? fmc.real.false_match : null;
    function item(label, big, text) {
      return el("div", { class: "sum-item" }, el("div", { class: "label" }, label), el("div", { class: "sum-v" }, big), el("p", { class: "small" }, text));
    }
    var items = [
      item("Blind, pre-registered test", T("num", t.n_with_trace + " of " + t.n_selected),
        ["selected Indian livestreams were harvested before a YouTube bot check stopped downloads. The plan was fixed before any Test audio existed."]),
      item("Primary endpoint", T("num", c2.top1_hit.k + "/" + c2.top1_hit.n),
        ["60-min excerpts whose top candidate starts within 30 s of the expected time (", rateShare(c2.top1_hit), ", ", ciText(c2.top1_hit), ")."])];
    if (conf) {
      var wrong = conf.n - conf.within_30 - conf.no_candidate;
      items.push(item("Confident verdicts", T("num", String(conf.n)),
        [T("num", String(conf.within_30)), " within 30 s and ", T("num", String(wrong)), " more than 30 s off, which count as confident wrong; the other ",
          T("num", String(c2.n - conf.n)), " excerpts get no verdict."]));
    }
    if (real && real.n) {
      items.push(item("False matches", T("num", real.k + "/" + real.n),
        ["real foreign 50 Hz recordings reached the threshold (", rateShare(real), "). The pre-registered target of at most 1% is ",
          real.k / real.n > 0.01 ? "not met." : "not contradicted, though so few negatives cannot show it."]));
    }
    items.push(item("Gate (PROTOCOL section 15)", T("num", g.gate_met ? "met" : "not met"),
      g.gate_met ? ["Every condition held."] : [g.condition_1_2_met ? "The lock conditions held; " : "The lock conditions failed; ",
        g.condition_3_met ? "harvest health held." : "harvest health failed on the bot check. Work went on by the author's decision (PROTOCOL section 19)."]));
    return el("section", { class: "wrap summary", "aria-labelledby": "sum-h" },
      el("h2", { id: "sum-h", class: "sum-h" }, "What the blind test found"),
      el("div", { class: "sum-grid" }, items),
      el("p", { class: "small muted" }, "Extractor v2, the primary extractor, searched over the whole log. ",
        el("a", { href: "#res-h" }, "Every result, with its source file"), ", is further down; the example that follows is a Calibration stream, not a Test result."));
  }

  function heroBlock(ov) {
    var h = ov.hero;
    var srch = h.searches[h.shown_scope];
    var top = srch.candidates[0];
    var topMs = parseNaive(top.start_ist);
    var pubMs = parseNaive(h.public_start_ist);
    var locked = srch.verdict === "match";
    var sec = el("section", { class: "wrap section", "aria-labelledby": "hero-h" });
    sec.appendChild(el("div", { class: "section-head" },
      el("h2", { id: "hero-h" }, "Slide and lock"),
      el("span", { class: "badge" }, h.split_label + " split"),
      el("span", { class: "label" }, (h.length_min || 60) + "-min excerpt, extractor v2, scope: " + scopeName(h.shown_scope))));

    var counter = T("num", "0");
    var replay = el("button", { class: "btn", type: "button" }, "Replay");
    var stripHost = el("div", { class: "strip-wrap" });
    var traceHost = el("div");
    var p1 = el("div", { class: "panel" },
      el("div", { class: "panel-h" }, el("span", { class: "label" }, "Search: " + scopeName(h.shown_scope)),
        el("span", { class: "counter" }, counter, " candidate starts searched")),
      el("div", { class: "panel-b" }, stripHost));
    var p2 = el("div", { class: "panel" },
      el("div", { class: "panel-h" }, el("span", { class: "label" }, "Trace on the matched reference window"),
        el("span", { class: "legend", style: "margin-left:auto" },
          el("span", { class: "key" }, el("i", { class: "k-ref" }), "reference"),
          el("span", { class: "key" }, el("i", { class: "k-trace" }), "recording, high weight"),
          el("span", { class: "key" }, el("i", { class: "k-faint" }), "low weight"))),
      el("div", { class: "panel-b" }, traceHost,
        el("p", { class: "figure-note" }, "Both lines in Hz. The weighted correlation of extractor v2 relies on the solid parts of the trace; faint parts carry little weight. Time axis: the reference's own labels, IST.")));
    var plots = el("div", { class: "hero-plots" }, p1, p2);
    var desc = el("p", { class: "hero-desc prose muted small" },
      "Stream ", el("code", null, h.video_id), " (" + h.channel + ", " + venue(h.venue_type) + "). Chosen as the hero because it has the ", h.why, ". ",
      h.split_note);

    // lock card
    var state_ = el("span", { class: "lc-state" }, el("span", { class: "dot", "aria-hidden": "true" }),
      el("span", { class: "label" }, locked ? "Searching" : "No verdict"));
    var timeEl = el("div", { class: "lc-time t" }, "--:--:--");
    var dateEl = el("div", { class: "lc-date t" }, " ");
    var pub = el("div", { class: "reveal" },
      el("div", { class: "lc-pub" }, el("span", { class: "label" }, "Public start"), el("span", { class: "t" }, fmtDT(pubMs) + " IST")),
      el("p", { class: "calc" }, "Lock minus public start: ", T("num", sgn((topMs - pubMs) / 1000) + " s"),
        ". Expected from the label drift D (", T("num", sgn(h.D_s) + " s"), ") plus the start latency L (", T("num", sgn(h.L_s) + " s"),
        "): T* = ", T("num", fmtHMS(parseNaive(h.truth.T_star_ist))), ", so e = ", T("num", sgn(srch.candidates[0].e_s) + " s"), "."));
    var card = el("aside", { class: "lockcard", "aria-label": "Lock card" },
      el("div", { class: "lc-sec" }, state_,
        el("div", { class: "label", style: "margin-top:10px" }, "Implied recording start (reference labels)"), dateEl, timeEl, pub),
      el("div", { class: "lc-sec" }, el("div", { class: "label" }, "Top 5 candidates"),
        el("div", { class: "lc-hide" }, candTable(srch.candidates, { caption: "Top 5 candidates", noErr: true }))),
      el("div", { class: "lc-sec" },
        el("dl", { class: "kv lc-hide" },
          el("dt", null, "confidence"), el("dd", null, T("num", fmtF(srch.confidence, 3)), " ",
            el("span", { class: "muted small" }, "atanh(r1) − atanh(r2)")),
          el("dt", null, "tau"), el("dd", null, T("num", fmtF(srch.tau, 4)), " ", el("span", { class: "muted small" }, (h.length_min || 60) + " min, " + scopeName(h.shown_scope))),
          el("dt", null, "runner-up"), el("dd", null, srch.runner_up ? [T("t", srch.runner_up.start_ist), ", r ", T("num", fmtF(srch.runner_up.r, 4))] : "none"),
          el("dt", null, "hum flag"), el("dd", null, h.hum.trace_flag ? "set" : "not set", " ", el("span", { class: "muted small" },
            humSummary(h.hum.trace_ratios))),
          el("dt", null, "verdict"), el("dd", null, el("span", { class: locked ? "v-match" : "v-nv" }, srch.verdict))),
        el("p", { class: "calc lc-hide" }, "Searched ", T("num", fmtInt(srch.positions_searched)), " admissible start positions in the " + scopeName(h.shown_scope) + " scope. ",
          h.full_by_exporter && h.searches[h.base_scope] ? ["The pipeline's own " + scopeName(h.base_scope) + " search of this excerpt gives the same lock with confidence ",
            T("num", fmtF(h.searches[h.base_scope].confidence, 3)), " (tau ", T("num", fmtF(h.searches[h.base_scope].tau, 4)), ")."] : ""),
        h.full_by_exporter ? el("p", { class: "calc" }, "The full-scope search for this display was run by the site exporter with the frozen matcher; it is not part of any pre-registered metric.") : null),
      el("div", { class: "lc-sec" }, replay, " ", el("a", { href: "#/clip/" + h.split + "/" + encodeURIComponent(h.video_id) }, "clip page")));
    sec.appendChild(el("div", { class: "hero" }, plots, card, desc));

    var strip = null, tr = null;
    var candTicks = srch.candidates.map(function (c, i) { return { start_index: c.start_index, rank: i + 1, r: c.r, start_ist: c.start_ist }; });
    var scopeWin = h.scope_window[h.shown_scope];
    var animating = false;
    function finalState() {
      if (strip) { strip.setScan(null); strip.showCands(true); strip.setLocked(locked); }
      if (tr) { tr.setShift(0); tr.setLocked(locked); }
      counter.textContent = fmtInt(srch.positions_searched);
      dateEl.textContent = fmtDay(topMs);
      timeEl.textContent = fmtHMS(topMs) + " IST";
      card.classList.toggle("locked", locked);
      card.classList.remove("searching");          // the candidates and the confidence show only once the search ends
      state_.lastChild.textContent = locked ? "Lock: match" : "No verdict";
      pub.classList.add("on");
    }
    function startState() {
      if (strip) { strip.setScan(0); strip.showCands(false); strip.setLocked(false); }
      if (tr) { tr.setShift(-h.ref_window.trace_offset_s); tr.setLocked(false); }
      counter.textContent = "0";
      timeEl.textContent = "--:--:--";
      dateEl.textContent = " ";
      card.classList.remove("locked");
      card.classList.add("searching");
      state_.lastChild.textContent = "Searching";
      pub.classList.remove("on");
    }
    function play() {
      if (reduceMotion.matches || animating) { finalState(); return; }
      animating = true;
      startState();
      var D = 1200, t0 = null, shift0 = -h.ref_window.trace_offset_s;
      function frame(now) {
        if (t0 === null) t0 = now;
        var p = Math.min(1, (now - t0) / D);
        var e = 1 - Math.pow(1 - p, 3);
        var ps = Math.min(1, p / 0.85);
        if (strip) strip.setScan(ps);
        counter.textContent = fmtInt(Math.round(srch.positions_searched * ps));
        if (tr) tr.setShift(shift0 * (1 - e));
        if (p < 1) requestAnimationFrame(frame);
        else { animating = false; finalState(); }
      }
      requestAnimationFrame(frame);
    }
    replay.addEventListener("click", play);
    var played = false;
    chart(stripHost, function () {
      strip = drawStrip(stripHost, ov, { height: 74, candidates: candTicks, scope: scopeWin,
        label: "Search over " + scopeName(h.shown_scope) + "; top candidate " + top.start_ist + " IST" });
      if (!played) startState(); else finalState();
    });
    chart(traceHost, function () {
      tr = drawTrace(traceHost, h.trace, h.ref_window, { height: 250, lockLabel: "lock, r = " + fmtF(top.r, 4),
        label: "Recording trace of " + h.video_id + " over the reference from " + h.ref_window.start_ist + " IST" });
      if (!played) startState(); else finalState();
      if (!played) {
        played = true;
        var go = function () { setTimeout(play, 250); };
        if ("IntersectionObserver" in window && !reduceMotion.matches) {
          var io = new IntersectionObserver(function (ents) {
            if (ents.some(function (en) { return en.isIntersecting; })) { io.disconnect(); go(); }
          }, { threshold: 0 });
          io.observe(sec);
        } else { finalState(); }
      }
    });
    return sec;
  }

  function humSummary(r) {
    if (!r) return "";
    var ks = Object.keys(r).filter(function (k) { return r[k] >= 3; });
    if (!ks.length) return "(no harmonic at 3 or more)";
    return "(" + ks.map(function (k) { return k + " " + fmtF(r[k], 2); }).join(", ") + ")";
  }

  function stripBlock(ov) {
    var r = ov.reference;
    var h = ov.hero;
    var host = el("div", { class: "strip-wrap", tabindex: "0", "aria-label": "Reference strip. Use the arrow keys to read hourly values." });
    var readout = el("div", { class: "readout", "aria-live": "polite" }, "Point at the strip, or focus it and use the arrow keys, to read hourly values.");
    var cands = [];
    if (h) {
      var srch = h.searches.full || h.searches[h.shown_scope];
      cands = srch.candidates.map(function (c, i) { return { start_index: c.start_index, rank: i + 1, r: c.r, start_ist: c.start_ist }; });
    }
    var sec = el("section", { class: "wrap section", "aria-labelledby": "strip-h" },
      el("div", { class: "section-head" }, el("h2", { id: "strip-h" }, "32 months of grid frequency"),
        el("span", { class: "label" }, "hourly min and max, " + fmtInt(ov.ribbon.n_bins) + " hours")),
      el("div", { class: "panel" }, el("div", { class: "panel-b" }, host, readout,
        el("p", { class: "figure-note" },
          "Grey band: hourly minimum and maximum of the one-second reference (", T("num", fmtF(r.freq_min_hz, 3)), " to ", T("num", fmtF(r.freq_max_hz, 3)),
          " Hz overall). Marks under the band: gaps of 10 min or more (", T("num", String(r.gaps_ge_10min.length)), "). Dashed lines: hand-overs between the five source files. ",
          h ? ["Ticks: the hero's top 5 full-scope candidates; rank 1 is the lock."] : ""))),
      el("p", { class: "prose small muted", style: "margin-top:10px" },
        "Valid seconds: ", T("num", fmtInt(r.valid_seconds)), " of ", T("num", fmtInt(r.seconds)), " (", T("num", fmtF(r.valid_pct, 4) + "%"), "). ",
        "Days fully covered: ", T("num", String(r.days_full)), " of ", T("num", String(r.days_total)), ". Dataset: " + r.dataset + "."),
      srcLine(r.sources.concat([ov.ribbon.source])));
    chart(host, function () {
      var api = drawStrip(host, ov, { height: 150, files: true, candidates: cands, readout: readout });
      api.setLocked(h && (h.searches.full || h.searches[h.shown_scope]).verdict === "match");
    });
    return sec;
  }

  function resultsBlock(ov) {
    var t = ov.test, cal = ov.calibration, lat = ov.latency, tau = ov.tau, dev = ov.dev;
    var res = ov.results || {};
    var sec = el("section", { class: "wrap section", "aria-labelledby": "res-h" });
    sec.appendChild(el("div", { class: "section-head" }, el("h2", { id: "res-h" }, "Results"),
      t && t.gate_frozen ? el("span", { class: "label" }, "Test split, gate frozen " + dayTime(t.gate_frozen_ist)) : null));

    // Test
    var testPanel = el("div", { class: "panel", id: "test-panel" });
    var nTest = (ov.splits.test || {}).n;
    testPanel.appendChild(el("div", { class: "panel-h" }, el("h3", null, "Test split, scored blind"),
      el("span", { class: "badge" }, t && t.n_with_trace !== undefined ? t.n_with_trace + " of " + fmtInt(nTest) + " streams with audio" : fmtInt(nTest) + " streams"),
      t && t.m2 ? testStatusBadge(t) : null));
    var tb = el("div", { class: "panel-b" });
    if (t && t.m2) {
      tb.appendChild(el("p", { class: "small lead-note" }, "Indian livestreams drawn from metadata before any audio was downloaded, and searched blind after tau and L were committed (PROTOCOL section 14). ",
        (t.blocked_rows || []).length ? ["YouTube's bot check (stream ", el("code", null, t.blocked_rows[0]), ") stopped the harvest, and every stream harvested by then is counted. "] : "",
        "A hit is a top candidate within 30 s of the expected start T*. A verdict “match” needs the hum flag and a confidence at or above tau."));
      tb.appendChild(testTable(ov, t, res));
      tb.appendChild(breakdownBlock(res.breakdown));
      tb.appendChild(baselineNote(ov, t, res));
    } else {
      tb.appendChild(el("p", { class: "small" }, "Test results: ", notComputed("results/evaluation.json")));
    }
    testPanel.appendChild(tb);
    sec.appendChild(testPanel);

    sec.appendChild(el("div", { class: "cols" },
      el("div", { class: "col" }, m2Panel(res.figures), m5Panel(res.m5)),
      el("div", { class: "col" }, m3Panel(ov, t, res))));
    sec.appendChild(el("h3", { class: "subhead" }, "Measured before any Test search"));

    // Calibration, latency and tau, all fixed before any Test search
    var calRows = ["v2", "v1", "naive"].map(function (e) {
      var b = cal.by_extractor[e];
      return el("tr", null, el("td", null, e === "v2" ? "v2 (primary)" : e === "naive" ? "naive 50 Hz" : e),
        el("td", { class: "r num" }, b.within_30s_of_Tstar + "/" + b.n),
        el("td", { class: "r num" }, b.conf_ge_tau + "/" + b.n),
        el("td", { class: "r num" }, b.match_and_hit + "/" + b.n),
        el("td", { class: "r num" }, b.match_and_wrong + "/" + b.n),
        el("td", { class: "r num hide-sm" }, fmtF(b.tau, 4)));
    });
    var calPanel = el("div", { class: "panel" },
      el("div", { class: "panel-h" }, el("h3", null, "Calibration split"), el("span", { class: "badge" }, "in-sample for L")),
      el("div", { class: "panel-b" },
        el("p", { class: "small" }, T("num", String(cal.n_harvested)), " of ", T("num", String(cal.n_selected)), " streams harvested; ",
          T("num", String(cal.m1_hum)), " have a hum flag on all their audio (v1). Each 60-minute excerpt searched ±1 day around its public start. Same streams, three extractors:"),
        el("div", { class: "tbl-wrap" }, el("table", null,
          el("thead", null, el("tr", null, el("th", { scope: "col" }, "extractor"), el("th", { scope: "col", class: "r" }, "top within 30 s of T*"),
            el("th", { scope: "col", class: "r" }, "confidence ≥ tau"), el("th", { scope: "col", class: "r" }, "match and hit"),
            el("th", { scope: "col", class: "r" }, "match and wrong"), el("th", { scope: "col", class: "r hide-sm" }, "tau"))),
          el("tbody", null, calRows))),
        el("p", { class: "figure-note" }, cal.note + " A match needs the trace's hum flag and confidence at or above tau."),
        srcLine(cal.sources)));

    var latHost = el("div");
    var latPanel = el("div", { class: "panel" },
      el("div", { class: "panel-h" }, el("h3", null, "Start latency L"), el("span", { class: "badge" }, lat.status)),
      el("div", { class: "panel-b" }, latHost,
        el("p", { class: "small" }, "L is the delay between YouTube's release time and the first audio sample. It is the median offset o of the ",
          T("num", String(lat.counted.n)), " Calibration streams whose confidence reached tau (60 min, ±1 day) with |o| at most 600 s: o = ",
          T("num", lat.counted.values_s.map(function (v) { return sgn(v); }).join(", ")), " s, so L = ", T("num", lat.L_s + " s"),
          ". Range ", T("num", fmtF(lat.counted.range_width_s, 0) + " s"), (lat.counted.constant_latency_holds ? ", within the 60 s the protocol allows for one constant latency." : ", wider than the 60 s the protocol allows for one constant latency.")),
        srcLine(lat.sources)));
    chart(latHost, function () { drawLatency(latHost, lat); });

    var tauRows = ["10", "20", "40", "60"].map(function (m) {
      var c = tau.cells.v2[m] || {};
      return el("tr", null, el("td", null, m + " min"),
        ["full", "90d", "1d"].map(function (s) { return el("td", { class: "r num" }, c[s] ? fmtF(c[s].tau, 4) : "n/a"); }));
    });
    var nn = tau.n_negatives || {};
    var tauPanel = el("div", { class: "panel" },
      el("div", { class: "panel-h" }, el("h3", null, "Threshold tau (v2)")),
      el("div", { class: "panel-b" },
        el("table", null, el("thead", null, el("tr", null, el("th", { scope: "col" }, "excerpt"),
          el("th", { scope: "col", class: "r" }, "full"), el("th", { scope: "col", class: "r" }, "±90 d"), el("th", { scope: "col", class: "r" }, "±1 d"))),
          el("tbody", null, tauRows)),
        el("p", { class: "small", style: "margin-top:8px" }, "Set on the threshold half of the negatives only: ", T("num", String(nn.real_done)), " real foreign 50 Hz streams and ",
          T("num", String(nn.synthetic_done)), " SYNTHETIC negatives. Rule: " + tau.rule),
        srcLine(tau.sources)));

    var devPanel = el("div", { class: "panel" },
      el("div", { class: "panel-h" }, el("h3", null, "Extractor v2, tuned on Dev"), el("span", { class: "badge" }, "in-sample")),
      el("div", { class: "panel-b" },
        el("p", { class: "small" }, "Extractor v2 against v1 and a naive single-bin 50 Hz extractor on the 5 Dev streams (tuning data, never pooled with Test): objective J = ",
          T("num", fmtF(dev.J_v2, 3)), " against ", T("num", fmtF(dev.J_v1, 3)), " and ", T("num", fmtF(dev.J_naive, 3)), "; the top candidate fell within ",
          T("num", dev.true_region_s + " s"), " of the expected start in ", T("num", dev.cells_top_in_true_v2 + ", " + dev.cells_top_in_true_v1 + " and " + dev.cells_top_in_true_naive),
          " of ", T("num", String(dev.n_cells)), " stream × length cells."),
        srcLine(dev.sources)));

    sec.appendChild(el("div", { class: "two" }, calPanel, latPanel));
    sec.appendChild(el("div", { class: "two", style: "margin-top:16px" }, tauPanel, devPanel));
    return sec;
  }

  function testStatusBadge(t) {
    if (t.mode && t.mode !== "real") return el("span", { class: "badge badge-syn" }, "PRACTICE run, not Test");
    if (!t.gate_frozen) return null;
    var met = t.gate_frozen_met;
    return el("span", { class: "badge" }, "gate " + (met === true ? "met" : met === false ? "not met" : (t.gate_md_verdict || "").toLowerCase()));
  }

  function extName(e) { return e === "v2" ? "v2 (primary)" : e === "naive" ? "naive 50 Hz" : e; }

  function testTable(ov, t, res) {
    var rows = [];
    ["v2", "v1", "naive"].forEach(function (e) {
      var c = ((t.m2[e] || {})["60"] || {}).full;
      if (!c) return;
      rows.push(el("tr", { class: e === "v2" ? "is-top" : null }, el("th", { scope: "row" }, extName(e)),
        el("td", { class: "rate" }, rateCell(c.top1_hit)), el("td", { class: "rate" }, rateCell(c.confident_hit)),
        el("td", { class: "rate" }, rateCell(c.confident_wrong))));
    });
    var g = t.gate || {}, gv = (g.by_extractor || {}).v2 || {};
    var c2 = ((t.m2.v2 || {})["60"] || {}).full || {};
    var cnt = res.counts || {};
    var gateDd = [T("num", (g.gate_met ? "met" : "not met")), t.gate_frozen ? [" (frozen ", T("t", dayTime(t.gate_frozen_ist)), "). "] : ". ",
      "Conditions 1 and 2 ", g.condition_1_2_met ? "met" : "not met", "; condition 3, harvest health, ", g.condition_3_met ? "met" : "not met",
      ". PROTOCOL section 19 gives the decision to stay with Gunj."];
    return el("div", { class: "res-grid" },
      el("div", null,
        el("div", { class: "tbl-wrap" }, el("table", { class: "res-tbl" },
          el("caption", null, "Primary cell: 60-min excerpts, search over the full span. ", T("num", "n = " + (c2.n || "n/a")), " streams."),
          el("thead", null, el("tr", null, el("th", { scope: "col" }, "extractor"), el("th", { scope: "col" }, "top-1 within 30 s"),
            el("th", { scope: "col" }, "confident hit"), el("th", { scope: "col" }, "confident wrong"))),
          el("tbody", null, rows))),
        el("p", { class: "figure-note" }, "Top-1 within 30 s ignores tau; it is the primary endpoint for v2 (PROTOCOL section 13). Confident wrong: a verdict “match” more than 30 s from T*. ",
          (t.latency_line || "").replace(/`/g, ""))),
      el("dl", { class: "kv" },
        el("dt", null, "streams processed"), el("dd", null, nOf(fmtInt(g.N_processed), fmtInt(t.n_selected)), " selected Test streams have audio"),
        el("dt", null, "hum present (M1)"), el("dd", null, cnt.m1_hum_present ? fmtRate(cnt.m1_hum_present) : "n/a"),
        el("dt", null, "primary endpoint, hum present"), el("dd", null, fmtRate(c2.top1_hit_hum_present)),
        el("dt", null, "primary endpoint, D = 0 (S1)"), el("dd", null, fmtRate(c2.top1_hit_D0)),
        el("dt", null, "label-stable streams (S2)"), el("dd", null, cnt.s2_label_stable ? fmtRate(cnt.s2_label_stable) : "n/a"),
        el("dt", null, "verified channels (S3)"), el("dd", null, cnt.s3_verified_channels ? fmtRate(cnt.s3_verified_channels) : "n/a"),
        el("dt", null, "streams that lock (gate)"), el("dd", null, nOf(fmtInt(gv.n_streams_with_lock), fmtInt(g.N_processed)), ", ",
          T("num", fmtInt(gv.n_streams_with_lock_D0)), " with D = 0"),
        el("dt", null, "gate (section 15)"), el("dd", null, gateDd)),
      el("div", { class: "src-row" }, srcLine((t.sources || []).concat(cnt.source ? [cnt.source] : []))));
  }

  /* Confident verdicts and all top candidates by distance from T* (results/error_breakdown.json). */
  var CLASSES = [["within_30", "within 30 s", "sg-hit"], ["from_30_to_120", "30 s to 2 min", "sg-a1"],
    ["from_120_to_600", "2 to 10 min", "sg-a2"], ["beyond_600", "over 10 min", "sg-a3"], ["no_candidate", "no candidate", "sg-none"]];
  function stackBar(split, label, cls) {
    var bar = el("div", { class: "stack " + (cls || ""), role: "img",
      "aria-label": label + ": " + CLASSES.filter(function (c) { return split[c[0]]; }).map(function (c) { return split[c[0]] + " " + c[1]; }).join(", ") });
    CLASSES.forEach(function (c) {
      var n = split[c[0]];
      if (!n) return;
      bar.appendChild(el("span", { class: "sg " + c[2], style: "flex-grow:" + n, title: n + " " + c[1] }, el("span", { class: "sg-n" }, String(n))));
    });
    return bar;
  }
  function stackKey(split, words) {
    return el("ul", { class: "stack-key" }, CLASSES.filter(function (c) { return split[c[0]]; }).map(function (c) {
      return el("li", { class: "k-" + c[0] }, el("i", { class: "sw " + c[2], "aria-hidden": "true" }), T("num", String(split[c[0]])), " " + (words[c[0]] || c[1]));
    }));
  }
  function breakdownBlock(bd) {
    if (!bd) return el("p", { class: "small" }, "Confident verdicts by distance from T*: ", notComputed("results/error_breakdown.json"));
    var p = bd.primary, cell = bd.cells[String(p.length_min)][p.scope];
    var c = cell.confident, a = cell.top1;
    var far = p.beyond_600 || [];
    var wrong = c.n - c.within_30 - c.no_candidate;
    var words = { from_30_to_120: "at 30 s to 2 min", from_120_to_600: "at 2 to 10 min", beyond_600: "over 10 min off" };
    return el("div", { class: "breakdown", id: "breakdown" },
      el("div", { class: "bd-row", id: "bd-confident" },
        el("div", { class: "bd-h" }, el("span", { class: "label" }, "Confident verdicts"),
          el("span", { class: "small" }, nOf(c.n, a.n), " excerpts have a verdict “match”; ", T("num", String(a.n - c.n)), " have none")),
        stackBar(c, "Confident verdicts by distance from T*"),
        stackKey(c, words)),
      el("p", { class: "figure-note" }, "Only “within 30 s” is a hit, so ", T("num", String(wrong)), " of the ", T("num", String(c.n)), " confident verdicts count as confident wrong. ",
        far.length ? ["The ", T("num", String(far.length)), " over 10 min are off by ", far.map(function (f, i) { return [i ? " and " : "", T("num", f.words)]; }),
          " (", far.map(function (f, i) { return [i ? ", " : "", el("a", { href: "#/clip/test/" + encodeURIComponent(f.video_id) }, el("code", null, f.video_id))]; }), "). "] : "",
        "The 30 s, 2 min and 10 min classes are the pre-registered 30 s, 120 s and 600 s hit columns (PROTOCOL section 9)."),
      el("div", { class: "bd-row" },
        el("div", { class: "bd-h" }, el("span", { class: "label" }, "All top candidates"),
          el("span", { class: "small" }, "the same ", T("num", String(a.n)), " excerpts, whatever the verdict")),
        stackBar(a, "All top candidates by distance from T*", "stack-thin"),
        stackKey(a, words)),
      srcLine(bd.sources));
  }

  function m2Panel(fig) {
    var host = el("div");
    var st = { m: "60", s: "full" };
    var note = el("p", { class: "small", "aria-live": "polite" });
    var body = el("div", { class: "panel-b" });
    var panel = el("div", { class: "panel", id: "m2-panel" },
      el("div", { class: "panel-h" }, el("h3", null, "Signed error of the top candidate (M2)"), el("span", { class: "label", style: "margin-left:auto" }, "v2")), body);
    if (!fig) { body.appendChild(el("p", { class: "small" }, notComputed("results/figures/figures.json"))); return panel; }
    var f2 = fig.m2;
    function redraw() {
      var key = st.m + "/" + st.s, c = f2.cells[key];
      drawSignedError(host, f2, key);
      fill(note, st.m + "-min excerpts, scope " + scopeName(st.s) + ": ", nOf(c.hits, c.n), " top candidates within 30 s of T* (green)",
        c.no_candidate ? ["; ", T("num", String(c.no_candidate)), " excerpt", c.no_candidate > 1 ? "s have" : " has", " no candidate because the search could not run"] : "", ".");
    }
    function seg(name, key, opts) {
      var g = el("div", { class: "seg", role: "group", "aria-label": name });
      opts.forEach(function (o) {
        var b = el("button", { type: "button", "aria-pressed": String(st[key] === o[0]) }, o[1]);
        b.addEventListener("click", function () {
          st[key] = o[0];
          Array.prototype.forEach.call(g.children, function (x) { x.setAttribute("aria-pressed", String(x === b)); });
          redraw();
        });
        g.appendChild(b);
      });
      return g;
    }
    body.appendChild(el("div", { class: "controls" }, el("span", { class: "label" }, "excerpt"),
      seg("Excerpt length", "m", [["10", "10"], ["20", "20"], ["40", "40"], ["60", "60 min"]]),
      el("span", { class: "label" }, "scope"), seg("Search scope", "s", [["full", "full"], ["90d", "±90 d"], ["1d", "±1 d"]])));
    body.appendChild(host);
    body.appendChild(note);
    body.appendChild(el("p", { class: "figure-note" }, "Every Test excerpt's top candidate, by its error e. The axis is linear inside ±30 s (shaded) and logarithmic outside, as in ",
      el("code", null, fig.png.m2), "."));
    body.appendChild(srcLine(fig.sources));
    chart(host, function () { redraw(); });
    return panel;
  }

  function m3Panel(ov, t, res) {
    var body = el("div", { class: "panel-b" });
    var panel = el("div", { class: "panel", id: "m3-panel" },
      el("div", { class: "panel-h" }, el("h3", null, "False matches (M3)"), el("span", { class: "badge" }, "held-out negatives")), body);
    var cell = t && t.m3 && ((t.m3.v2 || {})["60"] || {}).full;
    if (!cell) { body.appendChild(el("p", { class: "small" }, notComputed("results/evaluation.json"))); return panel; }
    var mc = t.m3_counts || {}, rh = res.report_half_negatives || {};
    var tauP = ((t.m2.v2 || {})["60"] || {}).full.tau;
    var same = ["real", "SYNTHETIC"].every(function (g) { return cell[g].false_match.k === cell[g].false_match_with_hum_flag.k; });
    var fmRow = function (g, label, id) {
      return el("tr", { id: id }, el("th", { scope: "row" }, label), el("td", { class: "rate" }, rateCell(cell[g].false_match)));
    };
    body.appendChild(el("p", { class: "small" }, "Foreign 50 Hz recordings and SYNTHETIC negatives that tau was not set on, searched like the Test streams. A false match is a negative whose confidence reaches tau of its cell. The pre-registered target is at most 1% (PROTOCOL section 11)."));
    body.appendChild(el("div", { class: "tbl-wrap" }, el("table", { class: "res-tbl fm-tbl" },
      el("caption", null, "v2, 60 min, full span, tau ", T("num", fmtF(tauP, 4))),
      el("thead", null, el("tr", null, el("th", { scope: "col" }, "report-half negatives"), el("th", { scope: "col" }, "at or above tau"))),
      el("tbody", null, fmRow("real", "real foreign 50 Hz streams", "fm-real"), fmRow("SYNTHETIC", "SYNTHETIC noise and hum", "fm-syn"),
        fmRow("together", "real and SYNTHETIC together", "fm-all")))));
    var tgt = cell.real.false_match;
    body.appendChild(el("p", { class: "small", id: "fm-target" }, "Target of at most 1%: ", T("num", tgt.k === 0 ? "no false match in this cell" : "not met"),
      tgt.k === 0 ? ". With this few negatives the interval cannot show a rate under 1%. "
        : [". ", nOf(tgt.k, tgt.n), " real negatives" + (tgt.k === 1 ? " reaches" : " reach") + " tau. "],
      "Searched: ", nOf(mc.real === undefined ? "n/a" : mc.real, rh.real_selected === undefined ? "n/a" : rh.real_selected), " real and ",
      nOf(mc.SYNTHETIC === undefined ? "n/a" : mc.SYNTHETIC, rh.SYNTHETIC_selected === undefined ? "n/a" : rh.SYNTHETIC_selected),
      " SYNTHETIC report-half negatives; a stream counts in a cell only if it is long enough. ",
      same ? "With the hum-flag condition added the counts are the same." : ""));
    var m1n = res.m1_negatives;
    if (m1n && m1n.all && m1n.all.n) {
      body.appendChild(el("p", { class: "small" }, "M1 hum flag on the real negatives (description only): ", fmtRate(m1n.all), " of the harvested streams, ",
        T("num", m1n.threshold.k + "/" + m1n.threshold.n), " in the threshold half and ", T("num", m1n.report.k + "/" + m1n.report.n),
        " in the report half (", el("code", null, m1n.source), ")."));
    }
    // every v2 cell
    var trs = [];
    ["10", "20", "40", "60"].forEach(function (m) {
      ["full", "90d", "1d"].forEach(function (s) {
        var c = ((t.m3.v2 || {})[m] || {})[s];
        if (!c) return;
        var mt = (((t.m2.v2 || {})[m] || {})[s] || {}).tau;
        trs.push(el("tr", null, el("td", { class: "num" }, m + " min"), el("td", null, scopeName(s)), el("td", { class: "r num" }, fmtF(mt, 4)),
          ["real", "SYNTHETIC", "together"].map(function (g) {
            var r = c[g].false_match;
            return el("td", { class: "r num", title: fmtRate(r) }, r.k + "/" + r.n);
          })));
      });
    });
    body.appendChild(el("details", null, el("summary", null, "Every cell of v2"),
      el("div", { class: "tbl-wrap" }, el("table", null,
        el("thead", null, el("tr", null, el("th", { scope: "col" }, "excerpt"), el("th", { scope: "col" }, "scope"), el("th", { scope: "col", class: "r" }, "tau"),
          el("th", { scope: "col", class: "r" }, "real"), el("th", { scope: "col", class: "r" }, "SYNTHETIC"), el("th", { scope: "col", class: "r" }, "together"))),
        el("tbody", null, trs))),
      el("p", { class: "figure-note" }, "False matches / negatives searched in the cell. Hover a count for its Wilson 95% interval.")));
    var fig = res.figures;
    if (fig) {
      var host = el("div", { class: "rel-host" });
      body.appendChild(el("div", { class: "label", style: "margin-top:14px" }, "Reliability of the confidence score"));
      body.appendChild(host);
      body.appendChild(el("p", { class: "figure-note" }, "Top: Test top-1 hit rate in each pre-registered confidence bin (v2, 60 min, full span), with hits / excerpts and the Wilson 95% interval. ",
        "Below: every excerpt's confidence on the same axis: Test hits (green), Test misses (amber), real negatives (filled grey), SYNTHETIC negatives (open grey). The axis is linear below 0.025 and doubles at each step above, as in ",
        el("code", null, fig.png.m3), ".", fig.m3.n_without_confidence ? [" ", T("num", String(fig.m3.n_without_confidence)), " of the ", T("num", String(fig.m3.n_excerpts)), " Test excerpts has no confidence because its search could not run."] : ""));
      chart(host, function () { drawReliability(host, fig.m3); });
    }
    body.appendChild(srcLine(["results/evaluation.json"].concat(fig ? fig.sources.slice(0, 1) : [])));
    return panel;
  }

  function m5Panel(m5) {
    var body = el("div", { class: "panel-b" });
    var panel = el("div", { class: "panel", id: "m5-panel" },
      el("div", { class: "panel-h" }, el("h3", null, "Edit detection (M5)"), el("span", { class: "badge badge-syn" }, "SYNTHETIC cuts")), body);
    if (!m5) { body.appendChild(el("p", { class: "small" }, notComputed("results/m5.json"))); return panel; }
    var by = m5.by_cut_length || [];
    var lo = by.length ? by[0].cut_s : null, hi = by.length ? by[by.length - 1].cut_s : null;
    var per = m5.n_streams ? m5.n_cuts / m5.n_streams : null;
    body.appendChild(el("p", { class: "small" }, "Every cut is SYNTHETIC: at harvest time a stretch of ", T("num", lo + " to " + hi + " s"),
      " was removed from the 60-min excerpt audio of the ", T("num", String(m5.n_streams)), " Test streams whose uncut excerpt is a confident hit, ",
      T("num", String(per)), " cuts each. Each cut trace is located in the ±1-day scope and passed to the frozen edit finder. No recording with a real edit was tested."));
    body.appendChild(el("dl", { class: "kv" },
      el("dt", null, "SYNTHETIC cuts found"), el("dd", null, fmtRate(m5.detected), el("span", { class: "muted small" }, " within " + m5.detection_window_s + " s of the true position")),
      el("dt", null, "false alarms"), el("dd", null, fmtRate(m5.false_alarms), el("span", { class: "muted small" }, " uncut excerpts of the same streams with an edit reported")),
      el("dt", null, "duration error"), el("dd", null, T("num", "median " + m5.dur_err.median_s + " s, max " + m5.dur_err.max_s + " s"),
        el("span", { class: "muted small" }, " over the " + m5.dur_err.n + " SYNTHETIC cuts found"))));
    body.appendChild(el("details", null, el("summary", null, "By SYNTHETIC cut length"),
      el("div", { class: "tbl-wrap" }, el("table", null,
        el("thead", null, el("tr", null, el("th", { scope: "col" }, "SYNTHETIC cut"), el("th", { scope: "col", class: "r" }, "cuts"),
          el("th", { scope: "col", class: "r" }, "found"), el("th", { scope: "col", class: "r" }, "duration error, median / max"))),
        el("tbody", null, by.map(function (b) {
          return el("tr", null, el("td", { class: "num" }, b.cut_s + " s"), el("td", { class: "r num" }, String(b.n_cuts)),
            el("td", { class: "r num", title: fmtRate(b.detected) }, b.detected.k + "/" + b.detected.n),
            el("td", { class: "r num" }, b.dur_err_median_s === null ? "none found" : b.dur_err_median_s + " s / " + b.dur_err_max_s + " s"));
        }))))));
    body.appendChild(srcLine(m5.sources));
    return panel;
  }

  /* Baselines on the same Test streams (PROTOCOL section 12), under the primary table. */
  function baselineNote(ov, t, res) {
    var rg = ov.random_guess || {};
    var cnt = res.counts || {}, pr = cnt.paired_60_full || {};
    var lh = res.libhum || {};
    var pairLine = function (e) {
      var p = pr[e];
      if (!p) return null;
      return el("li", null, extName(e), " on the same ", T("num", String(p.n)), " streams: both within 30 s ", T("num", String(p.both)),
        ", v2 only ", T("num", String(p.v2_only)), ", ", e, " only ", T("num", String(p.baseline_only)), ", neither ", T("num", String(p.neither)), ".");
    };
    return el("div", { class: "base-note", id: "baselines" },
      el("div", { class: "label" }, "Baselines"),
      el("ul", { class: "small tight" }, pairLine("v1"), pairLine("naive"),
        el("li", null, "Random guess within 30 s, 60-min excerpt: 1 in ", T("num", fmtInt(Math.round((rg.full_60min || {}).one_in))), " over the full span, 1 in ",
          T("num", fmtInt(Math.round((rg["90d_60min"] || {}).one_in))), " for ±90 days, 1 in ", T("num", fmtInt(Math.round((rg["1d_60min"] || {}).one_in))),
          " for ±1 day (", el("code", null, "results/splits_summary.json"), ")."),
        el("li", null, "libhum (PROTOCOL section 12): ", T("num", lh.status || "not run"),
          lh.reason ? [". ", lh.source, ": “", lh.reason, "”"] : ".")),
      el("p", { class: "figure-note" }, "No significance test is run (PROTOCOL section 13)."));
  }

  function uniqBlock(ov) {
    var u = ov.uniqueness, mm = (ov.results || {}).m4_measured;
    var st = { scope: "full", noise: "hann16" };
    var host = el("div");
    var summary = el("p", { class: "small u-summary", "aria-live": "polite" });
    function segBtns(name, opts, key) {
      var g = el("div", { class: "seg", role: "group", "aria-label": name });
      opts.forEach(function (o) {
        var b = el("button", { type: "button", "aria-pressed": String(st[key] === o[0]) }, o[1]);
        b.addEventListener("click", function () {
          st[key] = o[0];
          Array.prototype.forEach.call(g.children, function (c) { c.setAttribute("aria-pressed", String(c === b)); });
          redraw();
        });
        g.appendChild(b);
      });
      return g;
    }
    var tableHost = el("div", { class: "tbl-wrap" });
    var readHost = el("div", { class: "tbl-wrap" });
    var levels = [];
    u.rows.forEach(function (r) { if (r.sigma_mhz > 0 && levels.indexOf(r.sigma_mhz) < 0) levels.push(r.sigma_mhz); });
    var probe = u.probe_sigma_mhz ? u.probe_sigma_mhz.median : null;
    var probeLevel = null;
    levels.forEach(function (lv) { if (probe !== null && (probeLevel === null || Math.abs(lv - probe) < Math.abs(probeLevel - probe))) probeLevel = lv; });
    var PRE = (mm && mm.preregistered_reading) || "B";
    var READ = [[PRE, PRE + ": " + (PRE === "B" ? "unweighted r, as in probe_noise.py" : "r as scored") + " (pre-registered)", null]];
    var OTHER = PRE === "B" ? "A" : "B";
    READ.push([OTHER, OTHER + ": " + (OTHER === "A" ? "r as scored" : "unweighted r") + " (EXPLORATORY, post hoc)", "5 4"]);
    function reading(key, noise, scope) { return mm ? ((((mm.readings || {})[key] || {})[noise] || {})[scope] || null) : null; }
    function shortest(r) {
      if (!r) return "n/a";
      if (r.status !== "read" || r.shortest_length_min === null || r.shortest_length_min === undefined) return "not read";
      return r.shortest_length_min + " min" + (r.rate_at_shortest === null ? "" : " (" + pct(r.rate_at_shortest) + ")");
    }
    function previous(r) {      /* the tested length before the shortest that reaches 90%, with its share */
      var p = r && r.previous_tested_min;
      if (p === null || p === undefined || !r.rates_by_length_min || r.rates_by_length_min[String(p)] === undefined) return null;
      return [T("num", p + " min"), " gives ", T("num", pct(r.rates_by_length_min[String(p)]))];
    }
    function redraw() {
      var meas = [];
      READ.forEach(function (k) {
        var r = reading(k[0], st.noise, st.scope);
        if (r) meas.push({ name: "reading " + k[0] + " at " + fmtF(r.sigma_mhz, 1) + " mHz", rates: r.rates_by_length_min, dash: k[2], r: r, key: k[0] });
      });
      drawUniq(host, u, st.scope, st.noise, meas);
      var nk = st.noise === "hann16" ? "16-s smoothed" : "white";
      if (meas.length) {
        fill(summary, "With ", nk, " SYNTHETIC noise at the measured level, scope " + scopeName(st.scope) + ", the shortest tested length with at least 90% unique locks is ",
          meas.map(function (mc, i) {
            var pv = mc.key === PRE ? previous(mc.r) : null;
            return [i ? "; the EXPLORATORY (post hoc) reading " + mc.key + " gives " : "", T("num", shortest(mc.r).replace(/ \(.*/, "")),
              i ? "" : " under reading " + mc.key + ", the pre-registered one", " (", T("num", fmtF(mc.r.sigma_mhz, 1) + " mHz"),
              mc.r.rate_at_shortest === null ? "" : [", ", T("num", pct(mc.r.rate_at_shortest))], pv ? ["; ", pv] : "", ")"];
          }), ".");
      } else {
        var rows = u.rows.filter(function (r) { return r.scope === st.scope && r.noise === st.noise && r.sigma_mhz === probeLevel; });
        rows.sort(function (a, b) { return a.length_min - b.length_min; });
        var need = rows.filter(function (r) { return r.hit_rate >= 0.9; })[0];
        fill(summary, "At ", T("num", probeLevel + " mHz"), " of ", nk, " SYNTHETIC noise (the tested level nearest the probe recording), scope " + scopeName(st.scope) + ": ",
          need ? ["the shortest tested length with at least 90% unique locks is ", T("num", need.length_min + " min"), "."] : "no tested length reaches 90%.",
          " The reading at the measured noise level is ", notComputed("results/m4_measured.json"), ".");
      }
      if (mm) {
        readHost.replaceChildren(el("table", { class: "res-tbl" },
          el("caption", null, "Shortest tested length with at least 90% unique locks, " + nk + " SYNTHETIC noise, at the median measured noise"),
          el("thead", null, el("tr", null, el("th", { scope: "col" }, "reading"), el("th", { scope: "col", class: "hide-sm" }, "noise, median (Q1 to Q3)"),
            ["full", "90d", "1d"].map(function (s) { return el("th", { scope: "col", class: "r" }, scopeName(s)); }))),
          el("tbody", null, READ.map(function (k) {
            var dd = (mm.distribution || {})[k[0]] || {};
            return el("tr", null, el("th", { scope: "row" }, el("span", { class: "rd-key" + (k[2] ? " rd-dash" : ""), "aria-hidden": "true" }), k[1]),
              el("td", { class: "num hide-sm" }, fmtF(dd.median_mhz, 1) + " mHz (" + fmtF(dd.q1_mhz, 1) + " to " + fmtF(dd.q3_mhz, 1) + ")"),
              ["full", "90d", "1d"].map(function (s) { return el("td", { class: "r num" + (s === st.scope ? " on" : "") }, shortest(reading(k[0], st.noise, s))); }));
          }))));
      }
      var trs = u.rows.filter(function (r) { return r.scope === st.scope && (r.noise === st.noise || r.sigma_mhz === 0); })
        .sort(function (a, b) { return a.sigma_mhz - b.sigma_mhz || a.length_min - b.length_min; })
        .map(function (r) {
          return el("tr", null, el("td", { class: "num" }, r.sigma_mhz === 0 ? "none" : r.sigma_mhz + " mHz"), el("td", { class: "num" }, r.length_min + " min"),
            el("td", { class: "r num" }, r.hits + "/" + r.N), el("td", { class: "r num" }, pctKN(r.hits, r.N)), el("td", { class: "r num" }, pct(r.lo) + " to " + pct(r.hi)));
        });
      tableHost.replaceChildren(el("table", null, el("thead", null, el("tr", null, el("th", { scope: "col" }, "SYNTHETIC noise"), el("th", { scope: "col" }, "length"),
        el("th", { scope: "col", class: "r" }, "unique locks"), el("th", { scope: "col", class: "r" }, "rate"), el("th", { scope: "col", class: "r" }, "Wilson 95%"))),
        el("tbody", null, trs)));
    }
    var sec = el("section", { class: "wrap section", "aria-labelledby": "u-h" },
      el("div", { class: "section-head" }, el("h2", { id: "u-h" }, "How much audio a unique lock needs (M4)"), el("span", { class: "badge badge-syn" }, "SYNTHETIC noise simulation")),
      el("div", { class: "prose small" },
        el("p", null, u.label + " Real excerpts of the reference get SYNTHETIC Gaussian noise added and are searched with the same matcher. ",
          "A lock is unique when the top candidate lies within ", T("num", u.hit_tol_s + " s"), " of the excerpt's true position. ",
          "Smoothed noise is correlated over 16 s like real extractor error and is the harder case."),
        mm ? el("p", null, "PROTOCOL section 11 reads the curve at the noise measured on the Test streams that lock, computed as in ",
          el("code", null, "scripts/probe_noise.py"), ": here the ", T("num", String(mm.n_streams)),
          " streams the gate counts as locks, one median over each stream's locking excerpts (", T("num", String(mm.n_excerpts)), " in all). ",
          "That script uses the plain (unweighted) correlation r of a trace with the window it matched, which is reading B, the pre-registered reading (solid amber line). ",
          "Reading A puts v2's weighted correlation, as scored, into the same formula; it was added after the Test results were known and is EXPLORATORY, post hoc (dashed). ",
          "Both are interpolated linearly between the tested levels. ",
          "Before any Test search the curve was read at ", T("num", probeLevel + " mHz"), ", the tested level nearest the 5 Nov probe recording's ",
          T("num", fmtF(probe, 1) + " mHz"), ".") : null),
      el("div", { class: "panel", id: "u-panel" }, el("div", { class: "panel-b" },
        el("div", { class: "controls" }, el("span", { class: "label" }, "scope"),
          segBtns("Search scope", [["full", "full"], ["90d", "±90 d"], ["1d", "±1 d"]], "scope"),
          el("span", { class: "label" }, "noise"), segBtns("Noise kind", [["hann16", "16-s smoothed"], ["white", "white"]], "noise"),
          mm ? el("span", { class: "legend u-legend" },
            el("span", { class: "key" }, el("i", { class: "k-trace" }), "reading " + PRE + " (pre-registered)"),
            el("span", { class: "key" }, el("i", { class: "k-trace k-dash" }), "reading " + OTHER + " (EXPLORATORY)"),
            el("span", { class: "key" }, el("i", { class: "k-ref" }), "tested SYNTHETIC levels")) : null),
        host, summary, readHost,
        el("details", null, el("summary", null, "Every tested level for this view"), tableHost))),
      srcLine(u.sources.concat(mm ? mm.sources : [])));
    chart(host, function () { redraw(); });
    return sec;
  }

  function driftBlock(ov) {
    var d = ov.drift;
    var host = el("div");
    var offs = d.overlaps.map(function (o) { return o.label_offset_s; });
    // Roman-numeral order I to V (a sort by name length would give I, V, II, IV, III).
    var fileKeys = ["freq_I.csv", "freq_II.csv", "freq_III.csv", "freq_IV.csv", "freq_V.csv"].filter(function (k) { return d.per_file[k]; });
    var lasts = fileKeys.map(function (k) { return d.per_file[k].last_window_shift_s; });
    function andList(a) { return a.length < 2 ? a.join("") : a.slice(0, -1).join(", ") + " and " + a[a.length - 1]; }
    var sec = el("section", { class: "wrap section", "aria-labelledby": "d-h" },
      el("div", { class: "section-head" }, el("h2", { id: "d-h" }, "The reference's own clock drifts")),
      el("div", { class: "two" },
        el("div", { class: "prose" },
          el("p", null, "The dataset's time labels are not consistent. Overlapping source files label the same values ",
            T("num", andList(offs)), " s apart. Inside each file, a check against the grid's 15-minute scheduling clock finds the labels drifting late in steps: between the first and the last week of files I to V they drift late by ",
            T("num", andList(lasts)), " s."),
          el("p", null, "So a recording's true start and its position on the reference's labels can differ by minutes. For each stream Gunj reads the drift D at the stream's start from this curve and expects the lock at T* = public start + L + D. ",
            "For ", T("num", String(d.test_abs_D_gt_30s)), " of the ", T("num", String(d.test_n)), " Test streams |D| is over 30 s."),
          el("p", { class: "muted small" }, "The check is relative: it assumes each file's first week is on time. D is an estimate with unknown error, so results are also reported with D = 0. Hollow points: weeks whose profile correlation r is under 0.5.")),
        el("div", { class: "panel" }, el("div", { class: "panel-b" }, host, gateCallout(ov)))),
      srcLine(d.sources));
    chart(host, function () { drawDrift(host, ov); });
    return sec;
  }

  /* What the drift correction is worth in the blind test: the gate count with D and with D = 0. */
  function gateCallout(ov) {
    var t = ov.test;
    var g = t && t.gate, v2 = g && (g.by_extractor || {}).v2;
    if (!v2) return null;
    var c2 = ((t.m2.v2 || {})["60"] || {}).full || {};
    return el("div", { class: "callout", id: "drift-callout" },
      el("div", { class: "label" }, "Test streams that lock blind, any excerpt length (gate count)"),
      el("div", { class: "callout-row" },
        el("div", null, el("span", { class: "big-n" }, String(v2.n_streams_with_lock)), " ", el("span", { class: "small" }, "of " + g.N_processed + " with the drift correction")),
        el("div", null, el("span", { class: "big-n" }, String(v2.n_streams_with_lock_D0)), " ", el("span", { class: "small" }, "without it (D = 0)"))),
      el("p", { class: "figure-note" }, "Primary endpoint the same way: ", fmtRate(c2.top1_hit), " with D, ", fmtRate(c2.top1_hit_D0), " with D = 0. Source: ",
        el("code", null, "results/gate_frozen.json"), ", ", el("code", null, "results/evaluation.json"), "."));
  }

  function wrongLine(ov) {
    var t = ov.test, c2 = t && t.m2 && ((t.m2.v2 || {})["60"] || {}).full;
    var m3 = t && t.m3 && ((t.m3.v2 || {})["60"] || {}).full;
    if (!c2 || !m3) return null;
    var cw = c2.confident_wrong, ch = c2.confident_hit, fm = m3.real.false_match;
    return el("li", null, "Give certain verdicts. In the blind test ", nOf(cw.k, cw.k + ch.k), " confident verdicts (60 min, full span) were more than 30 s off, and ",
      nOf(fm.k, fm.n), " real foreign recordings reached tau.");
  }

  function doesBlock(ov) {
    var r = ov.reference;
    return el("section", { class: "wrap section", "aria-labelledby": "does-h" },
      el("div", { class: "section-head" }, el("h2", { id: "does-h" }, "What it does and does not do")),
      el("div", { class: "does" },
        el("div", { class: "panel" }, el("div", { class: "panel-h" }, el("h3", null, "Does")), el("div", { class: "panel-b" }, el("ul", null,
          el("li", null, "Reports when a recording started, at one-second resolution on the reference's own labels, by matching its mains-hum trace against India's one-second grid log.", accuracyLine(ov)),
          el("li", null, "Reports the top 5 candidates and a margin over the best candidate at least 60 s away, with a threshold set in advance on foreign 50 Hz streams and SYNTHETIC negatives."),
          el("li", null, "Says “no verdict” when the trace has no hum flag or the margin is below the threshold."),
          el("li", null, "Publishes its evaluation plan before the Test results, with every later change to the method, data, metrics and thresholds listed in PROTOCOL.md section 19.")))),
        el("div", { class: "panel" }, el("div", { class: "panel-h" }, el("h3", null, "Does not")), el("div", { class: "panel-b" }, el("ul", null,
          el("li", null, "Give a location. The whole grid shares one frequency, so Gunj reports time and grid only."),
          el("li", null, "Date recordings outside the public reference, ", T("t", r.t0_ist.slice(0, 10)), " to ", T("t", r.t_end_ist.slice(0, 10)), ". Grid-India measures this every second; a public log of later years would extend it."),
          el("li", null, "Prove a recording is genuine. A matching hum is evidence about time, and someone holding the grid record could add a fake hum. A missing hum says nothing either way."),
          el("li", null, "Remove the reference's label drift. D corrects it per stream as an estimate."),
          wrongLine(ov),
          el("li", null, "Count Dev or Calibration streams as evidence. Dev tuned extractor v2 and Calibration set L; only the Test split is the independent test."))))));
  }

  /* how close the confident verdicts of the blind test came (primary cell), for the Does list */
  function accuracyLine(ov) {
    var t = ov.test, c2 = t && t.m2 && ((t.m2.v2 || {})["60"] || {}).full;
    if (!c2 || !c2.confident_hit || !c2.confident_wrong) return "";
    var ch = c2.confident_hit.k, cw = c2.confident_wrong.k;
    return [" In the blind test ", nOf(ch, ch + cw), " confident verdicts on 60-min excerpts were within 30 s of the expected start and ",
      T("num", String(cw)), " were further off."];
  }

  /* ============================================================ CLIP LIST */

  function renderClips() {
    var ov = state.overview, list = state.clips.clips;
    var f = { split: "all", verdict: "all" };
    var tbody = el("tbody");
    var countEl = el("p", { class: "small muted", "aria-live": "polite" });
    function draw() {
      var rows = list.filter(function (c) {
        return (f.split === "all" || c.split === f.split) && (f.verdict === "all" || (f.verdict === "match" ? c.verdict === "match" : c.verdict !== "match"));
      });
      tbody.replaceChildren.apply(tbody, rows.map(clipRow));
      if (!rows.length) tbody.appendChild(el("tr", null, el("td", { colspan: "9" }, "No clips match these filters.")));
      countEl.textContent = rows.length + " of " + list.length + " clips shown.";
    }
    function seg(name, key, opts) {
      var g = el("div", { class: "seg", role: "group", "aria-label": name });
      opts.forEach(function (o) {
        var b = el("button", { type: "button", "aria-pressed": String(f[key] === o[0]) }, o[1]);
        b.addEventListener("click", function () {
          f[key] = o[0];
          Array.prototype.forEach.call(g.children, function (c) { c.setAttribute("aria-pressed", String(c === b)); });
          draw();
        });
        g.appendChild(b);
      });
      return g;
    }
    var cc = ov.clip_counts || {};
    var hasTest = (cc.test || 0) > 0;
    var nNever = list.filter(function (c) { return c.split === "test" && c.harvest === "not attempted"; }).length;
    var selTest = (ov.splits.test || {}).n;
    var root = el("section", { class: "wrap intro" },
      el("h1", { tabindex: "-1" }, "Clips"),
      el("p", { class: "prose" }, "Each clip is one public livestream. Its page shows the 60-minute v2 trace over the reference window of its top candidate, the top 5 candidates, and the verdict with its reason. ",
        "Dev streams tuned extractor v2 and Calibration streams set L, so neither is an independent test. ",
        hasTest ? ["Test streams are listed from ", el("code", null, "results/test_results.csv"), ", searched blind",
          ov.test && ov.test.gate_frozen ? ["; the gate was frozen ", dayTime(ov.test.gate_frozen_ist), " and these results are final."] : ".",
          " The list holds ", T("num", String(cc.test)), " of the ", T("num", String(selTest)), " selected Test streams",
          nNever ? ["; the ", T("num", String(nNever)), " that no download ever attempted come from the flow of ", el("code", null, "results/evaluation.json"),
            " (", el("code", null, "results/flow_table.md"), ")."] : "."]
          : ["Test streams (", T("num", fmtInt((ov.splits.test || {}).n)), "): ", notComputed("results/test_results.csv"), "."]),
      el("div", { class: "filters" }, el("span", { class: "label" }, "split"),
        seg("Split", "split", [["all", "all"], ["dev", "Dev"], ["calibration", "Calibration"], ["test", "Test"]].concat(cc.practice ? [["practice", "PRACTICE"]] : [])),
        el("span", { class: "label" }, "verdict"), seg("Verdict", "verdict", [["all", "all"], ["match", "match"], ["nv", "no verdict"]])),
      countEl,
      el("div", { class: "tbl-wrap" }, el("table", { class: "clip-tbl" },
        el("caption", { class: "sr-only" }, "Clips with their verdicts"),
        el("thead", null, el("tr", null,
          el("th", { scope: "col" }, "split"), el("th", { scope: "col" }, "video"), el("th", { scope: "col", class: "hide-sm" }, "channel"),
          el("th", { scope: "col", class: "hide-sm" }, "venue"), el("th", { scope: "col", class: "hide-sm" }, "public start (IST)"),
          el("th", { scope: "col" }, "hum"), el("th", { scope: "col" }, "verdict"), el("th", { scope: "col", class: "r hide-sm" }, "conf / tau"),
          el("th", { scope: "col", class: "r" }, "e"))),
        tbody)));
    draw();
    return { node: root, title: "Gunj: clips" };
  }

  function clipRow(c) {
    var href = "#/clip/" + c.split + "/" + encodeURIComponent(c.video_id);
    var wrong = c.verdict === "match" && c.hit_30s === false;
    var v = c.verdict === "match" && !wrong ? el("span", { class: "v-match", title: c.verdict_reason || null }, "match")
      : wrong ? el("span", { class: "v-wrong", title: "match verdict, but the top candidate is more than 30 s from T*" },
        el("span", { class: "hide-sm" }, "match, wrong lock"), el("span", { class: "show-sm" }, "wrong lock"))
      : el("span", { class: "v-nv", title: c.verdict_reason || null }, c.verdict || "n/a");
    return el("tr", null,
      el("td", null, el("span", { class: "hide-sm" }, splitName(c.split)), el("span", { class: "show-sm" }, { dev: "Dev", calibration: "Cal", test: "Test", practice: "Prac" }[c.split] || c.split)),
      el("td", null, c.page ? el("a", { href: href }, el("code", null, c.video_id)) : el("code", null, c.video_id)),
      el("td", { class: "hide-sm" }, c.channel || ""),
      el("td", { class: "hide-sm" }, c.venue_type || ""),
      el("td", { class: "hide-sm" }, T("t", c.release_time_ist ? fmtDT(parseNaive(c.release_time_ist)) : "n/a")),
      el("td", null, c.hum_m1 === null || c.hum_m1 === undefined ? "n/a" : c.hum_m1 ? "yes" : "no"),
      el("td", null, v),
      el("td", { class: "r num hide-sm" }, c.confidence === null || c.confidence === undefined ? "" : fmtF(c.confidence, 3) + " / " + fmtF(c.tau, 3)),
      el("td", { class: "r num" }, c.e_s === null || c.e_s === undefined ? "" :
        [el("span", { class: "hide-sm" }, sgn(c.e_s) + " s"), el("span", { class: "show-sm", title: sgn(c.e_s) + " s" }, shortDur(c.e_s))]));
  }

  /* ============================================================ CLIP PAGE */

  function renderClip(split, id) {
    return loadClip(split, id).then(function (c) {
      var ov = state.overview;
      var s = c.search;
      var root = el("section", { class: "wrap intro" });
      root.appendChild(el("p", { class: "crumb" }, el("a", { href: "#/clips" }, "Clips"), " / " + splitName(c.split) + " / ", el("code", null, c.video_id)));
      root.appendChild(el("h1", { tabindex: "-1" }, el("code", null, c.video_id), " ", el("span", { class: "muted", style: "font-size:0.7em" }, (c.channel || "") + (c.venue_type ? ", " + c.venue_type : ""))));
      root.appendChild(el("p", { class: "note" }, c.split_note));
      if (c.split === "test") root.appendChild(el("p", { class: "small muted", style: "max-width:80ch" },
        "This page shows the stream's longest excerpt in the full scope. The gate (PROTOCOL section 15) counts a lock at any excerpt length in the full or ±90-day scope, so a stream can count for the gate and still show no lock here. Every search is in the table further down."));
      var wrongLock = c.verdict === "match" && c.truth && c.truth.hit_30s === false;
      var isMatch = c.verdict === "match" && !wrongLock;
      root.appendChild(el("div", { class: "verdict-line" },
        el("span", { class: "big " + (isMatch ? "v-match" : wrongLock ? "v-wrong" : "v-nv") },
          isMatch ? "MATCH" : wrongLock ? "MATCH, WRONG LOCK" : (c.verdict || "no verdict").toUpperCase()),
        el("span", null, wrongLock ? (c.verdict_reason || "") + "; the top candidate is " + sgn(c.truth.e_s) + " s from T*, " + distWords(c.truth) : (c.verdict_reason || "")),
        s ? el("span", { class: "label" }, s.length_min + "-min excerpt, " + s.extractor + ", scope " + scopeName(s.scope)) : null));

      if (!s) {
        root.appendChild(el("dl", { class: "kv" },
          el("dt", null, "public start"), el("dd", null, T("t", c.release_time_ist ? ist(c.release_time_ist) : "n/a")),
          el("dt", null, "harvest"), el("dd", null, ((c.harvest || {}).status || "n/a") + ((c.harvest || {}).reason ? ": " + c.harvest.reason : ""))));
        return { node: root, title: "Gunj: " + c.video_id };
      }
      var top = s.candidates[0];
      var facts = el("dl", { class: "kv" },
        el("dt", null, "public start"), el("dd", null, T("t", ist(c.release_time_ist))),
        el("dt", null, "top candidate"), el("dd", null, top ? T("t", top.start_ist + " IST") : "none", " ", el("span", { class: "muted small" }, "implied recording start, reference labels")),
        el("dt", null, "T*"), el("dd", null, c.truth && c.truth.T_star_ist ? [T("t", c.truth.T_star_ist + " IST"), " ", el("span", { class: "muted small" }, "public start + L (" + sgn(c.truth.L_s) + " s) + D (" + sgn(c.truth.D_s) + " s)")] : "n/a"),
        el("dt", null, "error e"), el("dd", null, c.truth && c.truth.e_s !== null && c.truth.e_s !== undefined ? [T("num", sgn(c.truth.e_s) + " s"), " (" + distWords(c.truth) + ")"] : "n/a"),
        el("dt", null, "confidence"), el("dd", null, T("num", fmtF(s.confidence, 3)), " against tau ", T("num", fmtF(s.tau, 4)), s.conf_ge_tau ? " (at or above)" : " (below)"),
        el("dt", null, "positions searched"), el("dd", null, T("num", fmtInt(s.positions_searched)), " in " + scopeName(s.scope)),
        el("dt", null, "label drift D"), el("dd", null, T("num", sgn(c.D_s) + " s"), c.label_stable === null ? "" : c.label_stable ? ", label-stable" : ", not label-stable"),
        el("dt", null, "hum flag (all audio, v1)"), el("dd", null, c.hum && c.hum.m1_present !== null && c.hum.m1_present !== undefined ? (c.hum.m1_present ? "set" : "not set") + (c.hum.m1_max_ratio ? ", largest median ratio " + fmtF(c.hum.m1_max_ratio, 2) : "") : "n/a"),
        el("dt", null, "licence"), el("dd", null, c.licence || "n/a"),
        el("dt", null, "public video"), el("dd", null, el("a", { href: "https://www.youtube.com/watch?v=" + encodeURIComponent(c.video_id), rel: "noopener noreferrer", target: "_blank" }, "youtube.com/watch?v=" + c.video_id),
          " ", el("span", { class: "muted small" }, "(no audio or video is hosted here)")));
      var traceHost = el("div");
      var plot = el("div", { class: "panel", style: "margin-top:16px" },
        el("div", { class: "panel-h" }, el("span", { class: "label" }, isMatch ? "Trace on the locked reference window"
          : wrongLock ? "Trace on the top candidate's window (wrong lock)" : "Trace on the top candidate's window (no verdict)"),
          el("span", { class: "legend", style: "margin-left:auto" },
            el("span", { class: "key" }, el("i", { class: "k-ref" }), "reference"),
            el("span", { class: "key" }, el("i", { class: "k-trace" }), "recording, high weight"),
            el("span", { class: "key" }, el("i", { class: "k-faint" }), "low weight"))),
        el("div", { class: "panel-b" }, traceHost,
          el("p", { class: "figure-note" }, "Trace: ", el("code", null, (c.trace || {}).source || "n/a"), ". Bars under the plot: the trace's frame weight in the weighted correlation.")));
      var humHost1 = el("div"), humHost2 = el("div");
      var humPanel = el("div", { class: "panel" },
        el("div", { class: "panel-h" }, el("h3", null, "Hum"), el("span", { class: "muted small" }, c.hum ? c.hum.rule : "")),
        el("div", { class: "panel-b" },
          c.hum && c.hum.trace_ratios && Object.keys(c.hum.trace_ratios).length ? [el("div", { class: "label" }, "this excerpt (" + s.extractor + ")"), humHost2] : null,
          c.hum && c.hum.m1_ratios && Object.keys(c.hum.m1_ratios).length ? [el("div", { class: "label", style: "margin-top:8px" }, "all downloaded audio (v1, M1)"), humHost1] : null,
          el("p", { class: "figure-note" }, "Median peak-to-floor ratio per harmonic, log axis. Dashed line: 3.")));
      var candPanel = el("div", { class: "panel" },
        el("div", { class: "panel-h" }, el("h3", null, "Top 5 candidates"), el("span", { class: "muted small" }, scopeName(s.scope))),
        el("div", { class: "panel-b" }, candTable(s.candidates, { caption: "Top 5 candidates" }),
          el("p", { class: "figure-note" }, s.source)));
      root.appendChild(el("div", { class: "two", style: "margin-top:12px" }, el("div", null, facts), candPanel));
      root.appendChild(plot);
      root.appendChild(el("div", { class: "two", style: "margin-top:16px" }, humPanel, searchesPanel(c)));
      if (c.trace && c.ref_window) chart(traceHost, function () {
        drawTrace(traceHost, c.trace, c.ref_window, { height: 280, weights: true, locked: isMatch, lockLabel: top ? "lock, r = " + fmtF(top.r, 4) : "lock",
          label: "Trace of " + c.video_id + " over the reference window starting " + c.ref_window.start_ist + " IST" });
      });
      else traceHost.appendChild(el("p", { class: "muted" }, "No trace data in this export."));
      if (c.hum && c.hum.m1_ratios) chart(humHost1, function () { drawHum(humHost1, c.hum.m1_ratios, "Hum, all audio"); });
      if (c.hum && c.hum.trace_ratios) chart(humHost2, function () { drawHum(humHost2, c.hum.trace_ratios, "Hum, this excerpt"); });
      return { node: root, title: "Gunj: " + c.video_id };
    });
  }

  /* the top candidate's distance from T* in words, with the pre-registered 30 s, 120 s and 600 s columns */
  function distWords(tr) {
    if (!tr || tr.e_s === null || tr.e_s === undefined) return "n/a";
    if (tr.hit_30s) return "within 30 s";
    if (tr.hit_120s === undefined || tr.hit_120s === null) return "outside 30 s";
    if (tr.hit_120s) return "outside 30 s, within 2 min";
    if (tr.hit_600s) return "outside 2 min, within 10 min";
    return "over 10 min off: " + durText(tr.e_s);
  }

  function searchesPanel(c) {
    var rows = (c.searches || []).map(function (q) {
      return el("tr", null, el("td", null, q.extractor), el("td", { class: "num" }, q.length_min + " min"), el("td", null, scopeName(q.scope)),
        el("td", { class: "r num hide-sm" }, fmtF(q.confidence, 3)), el("td", { class: "r num hide-sm" }, fmtF(q.tau, 4)),
        el("td", { class: "r num" }, q.e_s === null || q.e_s === undefined ? "n/a" : sgn(q.e_s) + " s"),
        el("td", null, q.verdict ? q.verdict : (q.confidence !== null && q.tau !== null && q.confidence >= q.tau ? (q.trace_hum_flag ? "match" : "no hum flag") : "below tau")));
    });
    var srcs = [];
    (c.searches || []).forEach(function (q) { if (q.source && srcs.indexOf(q.source) < 0) srcs.push(q.source); });
    return el("div", { class: "panel" },
      el("div", { class: "panel-h" }, el("h3", null, "Every search on record")),
      el("div", { class: "panel-b" }, el("div", { class: "tbl-wrap" }, el("table", null,
        el("thead", null, el("tr", null, el("th", { scope: "col" }, "ext."), el("th", { scope: "col" }, "length"), el("th", { scope: "col" }, "scope"),
          el("th", { scope: "col", class: "r hide-sm" }, "conf"), el("th", { scope: "col", class: "r hide-sm" }, "tau"), el("th", { scope: "col", class: "r" }, "e"),
          el("th", { scope: "col" }, "result"))),
        el("tbody", null, rows.length ? rows : el("tr", null, el("td", { colspan: "7" }, "none"))))),
        el("p", { class: "figure-note" }, "e = top candidate start minus T*. On a narrow screen the confidence and tau columns are left out. Sources: ",
          srcs.map(function (s, i) { return [i ? "; " : "", el("code", null, s)]; }))));
  }

  /* ============================================================ FOOTER, ROUTER */

  function renderFooter() {
    var ov = state.overview;
    var f = document.getElementById("foot");
    if (!f) return;
    var cr = ov.reference.credit;
    f.replaceChildren(
      cr ? el("p", null, "Reference data: ", cr.text, " ", el("a", { href: cr.doi_url }, cr.doi_url.replace(/^https:\/\//, "")), ". Licence: ",
        el("a", { href: cr.licence_url }, cr.licence), ". ", cr.changes)
        : el("p", null, "Reference data: ", ov.reference.dataset, "."),
      el("p", null, "Stream metadata: public YouTube pages. No audio or video is hosted on this site; clip pages show video IDs, public metadata and derived one-second traces only."),
      el("p", null, "Built by Shrey Sati (GitHub satanrayshe) for Threws Odyssey 2026. Code and README: ", repoLink("", "GitHub repository"),
        ". Report: ", repoLink("blob/main/docs/gunj-report.pdf", "docs/gunj-report.pdf"), ". Method, splits, seeds and every later change to them: ",
        repoLink("blob/main/PROTOCOL.md", el("code", null, "PROTOCOL.md")), "."),
      el("p", { class: "small" }, "Data exported ", T("t", ov.written_ist || ""), ov.git_head ? [" from commit ", el("code", null, ov.git_head)] : "", " by ", el("code", null, ov.exporter), "."));
  }

  function setNav(name) {
    Array.prototype.forEach.call(document.querySelectorAll(".nav a"), function (a) {
      if (a.getAttribute("data-route") === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }

  function mount(res) {
    var main = document.getElementById("main");
    clearCharts();
    main.replaceChildren(res.node);
    document.title = res.title;
    var live = document.getElementById("live");
    if (live) live.textContent = res.title.replace(/^Gunj: /, "") + " page loaded";
    if (!state.first) {
      var h1 = main.querySelector("h1");
      window.scrollTo(0, 0);
      if (h1) h1.focus({ preventScroll: true });
    }
    state.first = false;
  }

  function showError(err) {
    var main = document.getElementById("main");
    main.replaceChildren(el("section", { class: "wrap" }, el("div", { class: "error", role: "alert" },
      el("p", null, "Could not load the explorer data: ", el("code", null, String(err && err.message || err))),
      el("p", { class: "small" }, "Open this folder through a web server (for example ", el("code", null, "python -m http.server"), " inside site/), since browsers block data files opened from disk."))));
  }

  function route() {
    var h = location.hash || "#/";
    if (h.indexOf("#/") !== 0) { if (!state.first) return; h = "#/"; }
    var parts = h.slice(2).split("/").filter(Boolean).map(function (p) { try { return decodeURIComponent(p); } catch (e) { return p; } });
    loadBase().then(function () {
      renderFooter();
      if (!parts.length) { setNav("overview"); mount(renderOverview()); return; }
      if (parts[0] === "clips") { setNav("clips"); mount(renderClips()); return; }
      if (parts[0] === "clip" && parts.length >= 3) {
        setNav("clips");
        return renderClip(parts[1], parts[2]).then(mount);
      }
      setNav("");
      mount({ node: el("section", { class: "wrap intro" }, el("h1", { tabindex: "-1" }, "Not found"), el("p", null, el("a", { href: "#/" }, "Back to the overview"))), title: "Gunj: not found" });
    }).catch(showError);
  }

  /* theme */
  function initTheme() {
    var b = document.getElementById("theme-toggle");
    if (!b) return;
    function sync() {
      var t = document.documentElement.getAttribute("data-theme") || "dark";
      b.textContent = t === "dark" ? "Light" : "Dark";
      b.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
    }
    b.addEventListener("click", function () {
      var t = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", t);
      try { localStorage.setItem("gunj-theme", t); } catch (e) {}
      sync();
    });
    sync();
  }

  window.addEventListener("hashchange", route);
  document.addEventListener("DOMContentLoaded", function () { initTheme(); route(); });
})();
