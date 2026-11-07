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
  function fmtF(x, d) { return x === null || x === undefined ? "n/a" : fixSign(Number(x).toFixed(d)); }
  function fixSign(s) { return s.charAt(0) === "-" ? MINUS + s.slice(1) : s; }
  function sgn(n) {
    if (n === null || n === undefined) return "n/a";
    n = Math.round(n);
    return n > 0 ? "+" + fmtInt(n) : n < 0 ? MINUS + fmtInt(-n) : "0";
  }
  function pct(x, d) { return x === null || x === undefined ? "n/a" : (100 * x).toFixed(d === undefined ? 1 : d) + "%"; }
  function fmtRate(r) {
    if (!r || !r.n) return "n/a";
    var s = r.k + "/" + r.n + " (" + pct(r.rate) + ")";
    if (r.wilson95) s += ", 95% CI " + pct(r.wilson95[0]) + " to " + pct(r.wilson95[1]);
    return s;
  }
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
  function gateText(ov) {
    var ms = parseNaive(ov && ov.decision_time);
    if (ms === null) return "the gate";
    var d = new Date(ms);
    return d.getUTCDate() + " " + MON[d.getUTCMonth()] + " " + p2(d.getUTCHours()) + ":" + p2(d.getUTCMinutes()) + " IST";
  }
  function pending(ov) { return el("span", { class: "pending" }, "[pending: gate " + gateText(ov) + "]"); }
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

  /* Uniqueness curve (SYNTHETIC noise simulation). */
  function drawUniq(host, u, scope, noise) {
    var W = host.__w || host.clientWidth || 600, H = 270;
    var m = { l: 44, r: 70, t: 12, b: 34 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var lengths = [5, 10, 20, 40, 60];
    var xpos = function (L) { return m.l + lengths.indexOf(L) / (lengths.length - 1) * iw; };
    var y = lin(1, 0, m.t, m.t + ih);
    var rows = u.rows.filter(function (r) { return r.scope === scope && (r.noise === noise || r.sigma_mhz === 0); });
    var levels = [];
    rows.forEach(function (r) { if (levels.indexOf(r.sigma_mhz) < 0) levels.push(r.sigma_mhz); });
    levels.sort(function (a, b) { return a - b; });
    var probe = u.probe_sigma_mhz ? u.probe_sigma_mhz.median : null;
    var hl = null;
    if (probe !== null) levels.forEach(function (lv) { if (lv > 0 && (hl === null || Math.abs(lv - probe) < Math.abs(hl - probe))) hl = lv; });
    var svg = sv("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
      "aria-label": "SYNTHETIC noise simulation: share of unique locks against excerpt length, scope " + scopeName(scope) });
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

  /* ============================================================ SHARED BLOCKS */

  function candTable(cands, opts) {
    opts = opts || {};
    var tb = el("tbody");
    cands.forEach(function (c, i) {
      tb.appendChild(el("tr", { class: i === 0 ? "is-top" : null },
        el("td", { class: "num" }, String(i + 1)),
        el("td", null, T("t", c.start_ist)),
        el("td", { class: "r num" }, fmtF(c.r, 4)),
        opts.noErr ? null : el("td", { class: "r num" }, c.e_s === undefined || c.e_s === null ? "n/a" : sgn(c.e_s) + " s")));
    });
    return el("div", { class: "tbl-wrap" }, el("table", { class: "cands" },
      opts.caption ? el("caption", { class: "sr-only" }, opts.caption) : null,
      el("thead", null, el("tr", null, el("th", { scope: "col" }, "#"), el("th", { scope: "col" }, "start (IST)"),
        el("th", { scope: "col", class: "r" }, "r"), opts.noErr ? null : el("th", { scope: "col", class: "r" }, "e vs T*"))), tb));
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
        "Evaluation plan fixed in advance in ", el("code", null, "PROTOCOL.md"), "."));
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
        candTable(srch.candidates, { caption: "Top 5 candidates", noErr: true })),
      el("div", { class: "lc-sec" },
        el("dl", { class: "kv" },
          el("dt", null, "confidence"), el("dd", null, T("num", fmtF(srch.confidence, 3)), " ",
            el("span", { class: "muted small" }, "atanh(r1) − atanh(r2)")),
          el("dt", null, "tau"), el("dd", null, T("num", fmtF(srch.tau, 4)), " ", el("span", { class: "muted small" }, (h.length_min || 60) + " min, " + scopeName(h.shown_scope))),
          el("dt", null, "runner-up"), el("dd", null, srch.runner_up ? [T("t", srch.runner_up.start_ist), ", r ", T("num", fmtF(srch.runner_up.r, 4))] : "none"),
          el("dt", null, "hum flag"), el("dd", null, h.hum.trace_flag ? "set" : "not set", " ", el("span", { class: "muted small" },
            humSummary(h.hum.trace_ratios))),
          el("dt", null, "verdict"), el("dd", null, el("span", { class: locked ? "v-match" : "v-nv" }, srch.verdict))),
        el("p", { class: "calc" }, "Searched ", T("num", fmtInt(srch.positions_searched)), " admissible start positions in the " + scopeName(h.shown_scope) + " scope. ",
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
    var sec = el("section", { class: "wrap section", "aria-labelledby": "res-h" });
    sec.appendChild(el("div", { class: "section-head" }, el("h2", { id: "res-h" }, "Results")));

    // Test
    var testPanel = el("div", { class: "panel" });
    var nTest = (ov.splits.test || {}).n;
    testPanel.appendChild(el("div", { class: "panel-h" }, el("h3", null, "Test split"), el("span", { class: "badge" }, fmtInt(nTest) + " streams"),
      t && t.m2 ? testStatusBadge(ov, t) : pending(ov)));
    var tb = el("div", { class: "panel-b" });
    tb.appendChild(el("p", { class: "small" }, "Indian livestreams drawn from metadata before any audio was downloaded, searched blind after tau and L were committed. ",
      "Primary endpoint: top-1 hit rate (|e| ≤ 30 s against T*) of extractor v2 for 60-minute excerpts in the full scope."));
    if (t && t.m2) tb.appendChild(testTable(ov, t));
    else {
      tb.appendChild(el("dl", { class: "kv" },
        el("dt", null, "primary endpoint"), el("dd", null, pending(ov)),
        el("dt", null, "confident hits and wrong locks"), el("dd", null, pending(ov)),
        el("dt", null, "false matches (held-out negatives)"), el("dd", null, pending(ov)),
        el("dt", null, "gate"), el("dd", null, pending(ov))));
      tb.appendChild(el("p", { class: "figure-note" }, "The gate (PROTOCOL section 15): at least 5 independent Test streams lock blind within 30 s of T* with confidence at or above tau, and the harvest met no block. Test numbers appear here when ",
        el("code", null, "results/evaluation.json"), " exists."));
    }
    testPanel.appendChild(tb);
    sec.appendChild(testPanel);

    // Known now
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
      el("div", { class: "panel-h" }, el("h3", null, "Calibration split, known now"), el("span", { class: "badge" }, "in-sample for L")),
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

    var rg = ov.random_guess || {};
    var rgP = el("div", { class: "panel" },
      el("div", { class: "panel-h" }, el("h3", null, "Baselines")),
      el("div", { class: "panel-b" },
        el("p", { class: "small" }, "Random guess within 30 s of the truth, 60-minute excerpt: full scope 1 in ", T("num", fmtInt(Math.round((rg.full_60min || {}).one_in))),
          ", ±90 days 1 in ", T("num", fmtInt(Math.round((rg["90d_60min"] || {}).one_in))), ", ±1 day 1 in ", T("num", fmtInt(Math.round((rg["1d_60min"] || {}).one_in))),
          " (", el("code", null, "results/splits_summary.json"), ")."),
        el("p", { class: "small" }, "Extractor v2 against v1 and a naive single-bin 50 Hz extractor on the 5 Dev streams (tuning data, in-sample): objective J = ",
          T("num", fmtF(dev.J_v2, 3)), " against ", T("num", fmtF(dev.J_v1, 3)), " and ", T("num", fmtF(dev.J_naive, 3)), "; the top candidate fell within ",
          T("num", dev.true_region_s + " s"), " of the expected start in ", T("num", dev.cells_top_in_true_v2 + ", " + dev.cells_top_in_true_v1 + " and " + dev.cells_top_in_true_naive),
          " of ", T("num", String(dev.n_cells)), " stream × length cells."),
        el("p", { class: "small" }, "libhum on the same inputs (PROTOCOL section 12): ", el("span", { class: "pending" }, "[pending: not run yet]")),
        srcLine(dev.sources)));

    sec.appendChild(el("div", { class: "two", style: "margin-top:16px" }, calPanel, latPanel));
    sec.appendChild(el("div", { class: "two", style: "margin-top:16px" }, tauPanel, rgP));
    return sec;
  }

  function testStatusBadge(ov, t) {
    if (t.mode && t.mode !== "real") return el("span", { class: "badge badge-syn" }, "PRACTICE run, not Test");
    if (t.gate_frozen) return el("span", { class: "badge" }, "gate frozen " + (t.gate_frozen_ist || "") + ": " + (t.gate_md_verdict || ""));
    return el("span", { class: "pending" }, "INTERIM, written " + (t.written_ist || "") + "; gate " + gateText(ov));
  }

  function testTable(ov, t) {
    var rows = [];
    ["v2", "v1", "naive"].forEach(function (e) {
      var c = ((t.m2[e] || {})["60"] || {}).full;
      if (!c) return;
      rows.push(el("tr", null, el("td", null, e === "v2" ? "v2 (primary)" : e),
        el("td", { class: "num" }, fmtRate(c.top1_hit)), el("td", { class: "num" }, fmtRate(c.confident_hit)),
        el("td", { class: "num" }, fmtRate(c.confident_wrong))));
    });
    var g = t.gate || {}, gv = (g.by_extractor || {}).v2 || {};
    var m3 = (((t.m3 || {}).v2 || {})["60"] || {}).full;
    return el("div", null,
      el("div", { class: "tbl-wrap" }, el("table", null,
        el("thead", null, el("tr", null, el("th", { scope: "col" }, "60 min, full scope"), el("th", { scope: "col" }, "top-1 hit"),
          el("th", { scope: "col" }, "confident hit"), el("th", { scope: "col" }, "confident wrong"))),
        el("tbody", null, rows))),
      el("dl", { class: "kv", style: "margin-top:10px" },
        el("dt", null, "streams processed"), el("dd", null, T("num", fmtInt(g.N_processed))),
        el("dt", null, "streams that lock (gate count)"), el("dd", null, T("num", fmtInt(gv.n_streams_with_lock)), " (with D = 0: ", T("num", fmtInt(gv.n_streams_with_lock_D0)), ")"),
        el("dt", null, "gate"), el("dd", null, t.gate_frozen ? [t.gate_md_verdict || (g.gate_met ? "met" : "not met"), " (frozen ", T("t", t.gate_frozen_ist || ""), ")"]
          : ["interim reading: ", t.gate_md_verdict || (g.gate_met ? "met" : "not met"), ". Decided at ", gateText(ov), " from the streams harvested by then; this is not the gate result."]),
        el("dt", null, "false matches, real negatives"), el("dd", null, m3 ? fmtRate(m3.real.false_match) : "n/a"),
        el("dt", null, "false matches, SYNTHETIC negatives"), el("dd", null, m3 ? fmtRate(m3.SYNTHETIC.false_match) : "n/a")),
      el("p", { class: "figure-note" }, (t.latency_line || "").replace(/`/g, "")),
      srcLine(t.sources));
  }

  function uniqBlock(ov) {
    var u = ov.uniqueness;
    var st = { scope: "full", noise: "hann16" };
    var host = el("div");
    var summary = el("p", { class: "small", "aria-live": "polite" });
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
    function redraw() {
      var hl = drawUniq(host, u, st.scope, st.noise);
      var rows = u.rows.filter(function (r) { return r.scope === st.scope && r.noise === st.noise && r.sigma_mhz === hl; });
      rows.sort(function (a, b) { return a.length_min - b.length_min; });
      var need = rows.filter(function (r) { return r.hit_rate >= 0.9; })[0];
      fill(summary, "At ", T("num", hl + " mHz"), " of ", st.noise === "hann16" ? "16-s smoothed" : "white", " SYNTHETIC noise (the tested level nearest the probe recording's ",
        T("num", fmtF(u.probe_sigma_mhz.median, 1) + " mHz"), "), scope " + scopeName(st.scope) + ": ",
        need ? ["the shortest tested length with at least 90% unique locks is ", T("num", need.length_min + " min"), " (", T("num", need.hits + "/" + need.N), ")."]
          : "no tested length reaches 90%.");
      var trs = u.rows.filter(function (r) { return r.scope === st.scope && (r.noise === st.noise || r.sigma_mhz === 0); })
        .sort(function (a, b) { return a.sigma_mhz - b.sigma_mhz || a.length_min - b.length_min; })
        .map(function (r) {
          return el("tr", null, el("td", { class: "num" }, r.sigma_mhz === 0 ? "none" : r.sigma_mhz + " mHz"), el("td", { class: "num" }, r.length_min + " min"),
            el("td", { class: "r num" }, r.hits + "/" + r.N), el("td", { class: "r num" }, pct(r.hit_rate)), el("td", { class: "r num" }, pct(r.lo) + " to " + pct(r.hi)));
        });
      tableHost.replaceChildren(el("table", null, el("thead", null, el("tr", null, el("th", { scope: "col" }, "SYNTHETIC noise"), el("th", { scope: "col" }, "length"),
        el("th", { scope: "col", class: "r" }, "unique locks"), el("th", { scope: "col", class: "r" }, "rate"), el("th", { scope: "col", class: "r" }, "Wilson 95%"))),
        el("tbody", null, trs)));
    }
    var sec = el("section", { class: "wrap section", "aria-labelledby": "u-h" },
      el("div", { class: "section-head" }, el("h2", { id: "u-h" }, "How much audio does a lock need?"), el("span", { class: "badge badge-syn" }, "SYNTHETIC noise simulation")),
      el("p", { class: "prose small" }, u.label + " Real excerpts of the reference get SYNTHETIC Gaussian noise added and are searched with the same matcher. ",
        "A lock is unique when the top candidate lies within ", T("num", u.hit_tol_s + " s"), " of the excerpt's true position. ",
        "Smoothed noise is correlated over 16 s like real extractor error and is the harder case. The probe recording of 5 Nov had an equivalent noise of ",
        T("num", fmtF(u.probe_sigma_mhz.median, 1) + " mHz"), " (median of ", T("num", String(u.probe_sigma_mhz.n)), " excerpts, range ",
        T("num", fmtF(u.probe_sigma_mhz.min, 1)), " to ", T("num", fmtF(u.probe_sigma_mhz.max, 1)), " mHz)."),
      el("div", { class: "panel" }, el("div", { class: "panel-b" },
        el("div", { class: "controls" }, el("span", { class: "label" }, "scope"),
          segBtns("Search scope", [["full", "full"], ["90d", "±90 d"], ["1d", "±1 d"]], "scope"),
          el("span", { class: "label" }, "noise"), segBtns("Noise kind", [["hann16", "16-s smoothed"], ["white", "white"]], "noise")),
        host, summary,
        el("details", null, el("summary", null, "Table for this view"), tableHost))),
      srcLine(u.sources));
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
        el("div", { class: "panel" }, el("div", { class: "panel-b" }, host))),
      srcLine(d.sources));
    chart(host, function () { drawDrift(host, ov); });
    return sec;
  }

  function doesBlock(ov) {
    var r = ov.reference;
    return el("section", { class: "wrap section", "aria-labelledby": "does-h" },
      el("div", { class: "section-head" }, el("h2", { id: "does-h" }, "What it does and does not do")),
      el("div", { class: "does" },
        el("div", { class: "panel" }, el("div", { class: "panel-h" }, el("h3", null, "Does")), el("div", { class: "panel-b" }, el("ul", null,
          el("li", null, "Estimates when a recording started, to the second on the reference's own labels, by matching its mains-hum trace against India's one-second grid log."),
          el("li", null, "Reports the top 5 candidates and a margin over the best candidate at least 60 s away, with a threshold set in advance on foreign 50 Hz streams and SYNTHETIC negatives."),
          el("li", null, "Says “no verdict” when the trace has no hum flag or the margin is below the threshold."),
          el("li", null, "Publishes its evaluation plan before the Test results, with every later change listed in PROTOCOL.md section 19.")))),
        el("div", { class: "panel" }, el("div", { class: "panel-h" }, el("h3", null, "Does not")), el("div", { class: "panel-b" }, el("ul", null,
          el("li", null, "Give a location. The whole grid shares one frequency, so Gunj reports time and grid only."),
          el("li", null, "Date recordings outside the public reference, ", T("t", r.t0_ist.slice(0, 10)), " to ", T("t", r.t_end_ist.slice(0, 10)), ". Grid-India measures this every second; a public log of later years would extend it."),
          el("li", null, "Prove a recording is genuine. A matching hum is evidence about time, and someone holding the grid record could add a fake hum. A missing hum says nothing either way."),
          el("li", null, "Remove the reference's label drift. D corrects it per stream as an estimate."),
          el("li", null, "Count Dev or Calibration streams as evidence. Dev tuned extractor v2 and Calibration set L; only the Test split is the independent test."))))));
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
      if (!rows.length) tbody.appendChild(el("tr", null, el("td", { colspan: "8" }, f.split === "test" ? ["Test clips: ", pending(ov)] : "No clips match these filters.")));
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
    var root = el("section", { class: "wrap intro" },
      el("h1", { tabindex: "-1" }, "Clips"),
      el("p", { class: "prose" }, "Each clip is one public livestream. Its page shows the 60-minute v2 trace over the reference window of its top candidate, the top 5 candidates, and the verdict with its reason. ",
        "Dev streams tuned extractor v2 and Calibration streams set L, so neither is an independent test. ",
        hasTest ? ["Test streams are listed from ", el("code", null, "results/test_results.csv"),
          ov.test && !ov.test.gate_frozen ? ["; their numbers are interim until the gate is frozen after ", gateText(ov), "."] : "."] : ["Test streams (", T("num", fmtInt((ov.splits.test || {}).n)), ") appear here after scoring: ", pending(ov), "."]),
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
      : wrong ? el("span", { class: "v-wrong", title: "match verdict, but the top candidate is more than 30 s from T*" }, "match, wrong lock")
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
      el("td", { class: "r num" }, c.e_s === null || c.e_s === undefined ? "" : sgn(c.e_s) + " s"));
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
        el("span", null, wrongLock ? (c.verdict_reason || "") + "; the top candidate is " + sgn(c.truth.e_s) + " s from T*, outside 30 s" : (c.verdict_reason || "")),
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
        el("dt", null, "error e"), el("dd", null, c.truth && c.truth.e_s !== null && c.truth.e_s !== undefined ? [T("num", sgn(c.truth.e_s) + " s"), c.truth.hit_30s ? " (within 30 s)" : " (outside 30 s)"] : "n/a"),
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

  function searchesPanel(c) {
    var rows = (c.searches || []).map(function (q) {
      return el("tr", null, el("td", null, q.extractor), el("td", { class: "num" }, q.length_min + " min"), el("td", null, scopeName(q.scope)),
        el("td", { class: "r num" }, fmtF(q.confidence, 3)), el("td", { class: "r num" }, fmtF(q.tau, 4)),
        el("td", { class: "r num" }, q.e_s === null || q.e_s === undefined ? "n/a" : sgn(q.e_s) + " s"),
        el("td", null, q.verdict ? q.verdict : (q.confidence !== null && q.tau !== null && q.confidence >= q.tau ? (q.trace_hum_flag ? "match" : "no hum flag") : "below tau")));
    });
    var srcs = [];
    (c.searches || []).forEach(function (q) { if (q.source && srcs.indexOf(q.source) < 0) srcs.push(q.source); });
    return el("div", { class: "panel" },
      el("div", { class: "panel-h" }, el("h3", null, "Every search on record")),
      el("div", { class: "panel-b" }, el("div", { class: "tbl-wrap" }, el("table", null,
        el("thead", null, el("tr", null, el("th", { scope: "col" }, "ext."), el("th", { scope: "col" }, "length"), el("th", { scope: "col" }, "scope"),
          el("th", { scope: "col", class: "r" }, "conf"), el("th", { scope: "col", class: "r" }, "tau"), el("th", { scope: "col", class: "r" }, "e"),
          el("th", { scope: "col" }, "result"))),
        el("tbody", null, rows.length ? rows : el("tr", null, el("td", { colspan: "7" }, "none"))))),
        el("p", { class: "figure-note" }, "e = top candidate start minus T*. Sources: ", srcs.map(function (s, i) { return [i ? "; " : "", el("code", null, s)]; }))));
  }

  /* ============================================================ FOOTER, ROUTER */

  function renderFooter() {
    var ov = state.overview;
    var f = document.getElementById("foot");
    if (!f) return;
    f.replaceChildren(
      el("p", null, "Reference data: ", ov.reference.dataset, ". Stream metadata: public YouTube pages. No audio or video is hosted on this site; clip pages show video IDs, public metadata and derived one-second traces only."),
      el("p", null, "Built by Shrey Sati (GitHub satanrayshe) for Threws Odyssey 2026. The code, the analysis scripts and the protocol were written with AI coding assistants, under Shrey's direction. Method, splits, seeds and every deviation: ",
        ov.repo_url ? el("a", { href: ov.repo_url.replace(/\/$/, "") + "/blob/main/PROTOCOL.md" }, "PROTOCOL.md") : el("code", null, "PROTOCOL.md"), "."),
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
