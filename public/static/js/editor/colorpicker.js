/* A colour picker that looks the same in every browser: a square for how bright and how strong the colour is, a rainbow
   slider for the hue, number boxes for red, green and blue, and a box for the code (#ff8800). When the browser has an
   eyedropper (Chrome and Edge do) a button picks any colour from the screen. The last colours used are kept in this
   browser. Everything is built with the DOM (no HTML strings), and the keyboard does everything the mouse does. */
(function () {
  "use strict";

  var E = window.sffEditor;
  var el = E.el;
  var RECENT_KEY = "sff.recentColors";
  var RECENT_MAX = 8;

  /* ------------------------------------------------------------------------------------------ colour maths */

  function clamp(n, lo, hi) {
    return Math.min(hi, Math.max(lo, n));
  }

  function hexToRgb(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim());
    if (!m) return null;
    var n = parseInt(m[1], 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
  }

  function rgbToHex(r, g, b) {
    return "#" + [r, g, b].map(function (v) { return ("0" + clamp(Math.round(v), 0, 255).toString(16)).slice(-2); }).join("");
  }

  function rgbToHsv(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    var max = Math.max(r, g, b);
    var min = Math.min(r, g, b);
    var d = max - min;
    var h = 0;
    if (d) {
      if (max === r) h = ((g - b) / d) % 6;
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
      if (h < 0) h += 360;
    }
    return { h: h, s: max ? d / max : 0, v: max };
  }

  function hsvToRgb(h, s, v) {
    var c = v * s;
    var x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    var m = v - c;
    var r = 0, g = 0, b = 0;
    if (h < 60) { r = c; g = x; }
    else if (h < 120) { r = x; g = c; }
    else if (h < 180) { g = c; b = x; }
    else if (h < 240) { g = x; b = c; }
    else if (h < 300) { r = x; b = c; }
    else { r = c; b = x; }
    return { r: Math.round((r + m) * 255), g: Math.round((g + m) * 255), b: Math.round((b + m) * 255) };
  }

  /* ------------------------------------------------------------------------------------------ the colours used last */

  function readRecent() {
    try {
      var list = JSON.parse(window.localStorage.getItem(RECENT_KEY) || "[]");
      return Array.isArray(list) ? list.filter(function (c) { return typeof c === "string" && hexToRgb(c); }).slice(0, RECENT_MAX) : [];
    } catch (e) {
      return [];
    }
  }

  function remember(hex) {
    try {
      var list = readRecent().filter(function (c) { return c !== hex; });
      list.unshift(hex);
      window.localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX)));
    } catch (e) {
      /* storage is blocked: the picker works without it */
    }
  }

  /* ------------------------------------------------------------------------------------------ the picker */

  /* create({ value: "#176dff", onInput: function (hex) {} }) -> { node, set(hex), get() }
     onInput runs on every change, including while the mouse is dragging. */
  function create(options) {
    var onInput = options.onInput || function () {};
    var start = hexToRgb(options.value) || { r: 23, g: 109, b: 255 };
    var hsv = rgbToHsv(start.r, start.g, start.b);
    var id = "cp" + Math.random().toString(36).slice(2, 8);

    var handle = el("span", { class: "cp-handle", "aria-hidden": "true" });
    var square = el("div", {
      class: "cp-square",
      tabindex: "0",
      role: "group",
      "aria-label": "Shade. Use the arrow keys to move: left and right for how strong, up and down for how bright.",
    }, [handle]);

    var hue = el("input", { class: "cp-hue", type: "range", min: "0", max: "360", step: "1", value: "0", "aria-label": "Hue (the basic colour)" });
    var fields = {};
    var row = el("div", { class: "cp-inputs" });
    [["r", "R", "Red"], ["g", "G", "Green"], ["b", "B", "Blue"]].forEach(function (spec) {
      var input = el("input", { class: "input cp-num", id: id + spec[0], type: "number", min: "0", max: "255", step: "1", inputmode: "numeric", "aria-label": spec[2] + " from 0 to 255" });
      fields[spec[0]] = input;
      row.appendChild(el("label", { class: "cp-field", for: id + spec[0] }, [input, el("span", { text: spec[1] })]));
    });
    var hexBox = el("input", { class: "input cp-hex", id: id + "hex", type: "text", maxlength: "7", autocomplete: "off", spellcheck: "false", "aria-label": "Colour code, like #176dff" });
    row.appendChild(el("label", { class: "cp-field cp-field-hex", for: id + "hex" }, [hexBox, el("span", { text: "Hex" })]));

    var preview = el("span", { class: "cp-preview", "aria-hidden": "true" });
    var recentRow = el("div", { class: "cp-recent", role: "group", "aria-label": "Colours you used lately" });
    var top = el("div", { class: "cp-top" }, [preview]);
    var dropper = null;
    if (window.EyeDropper) {
      dropper = el("button", { class: "btn btn-ghost btn-sm cp-dropper", type: "button", title: "Pick a colour from anywhere on the screen" }, [E.icon("eyedropper"), " Pick from screen"]);
      top.appendChild(dropper);
    }

    var node = el("div", { class: "cp", "aria-label": "Colour picker" }, [square, hue, row, top, recentRow]);

    var current = rgbToHex(start.r, start.g, start.b);

    /* `typing` is the box the person is typing in ("rgb" or "hex"): the others follow it, that one is left alone. */
    function paint(typing) {
      var rgb = hsvToRgb(hsv.h, hsv.s, hsv.v);
      current = rgbToHex(rgb.r, rgb.g, rgb.b);
      node.style.setProperty("--cp-hue", "hsl(" + Math.round(hsv.h) + " 100% 50%)");
      node.style.setProperty("--cp-colour", current);
      handle.style.left = hsv.s * 100 + "%";
      handle.style.top = (1 - hsv.v) * 100 + "%";
      hue.value = String(Math.round(hsv.h));
      if (typing !== "rgb") {
        fields.r.value = rgb.r;
        fields.g.value = rgb.g;
        fields.b.value = rgb.b;
      }
      if (typing !== "hex") hexBox.value = current;
    }

    function changed(typing) {
      paint(typing);
      onInput(current);
    }

    function setFromRgb(r, g, b, typing) {
      var next = rgbToHsv(r, g, b);
      if (next.s === 0 || next.v === 0) next.h = hsv.h; /* grey and black have no hue: keep the slider where it was */
      hsv = next;
      changed(typing);
    }

    function drawRecent() {
      E.clear(recentRow);
      var list = readRecent();
      recentRow.hidden = !list.length;
      list.forEach(function (hex) {
        var b = el("button", { class: "cp-recent-swatch", type: "button", title: hex, "aria-label": "Use " + hex });
        b.style.setProperty("--swatch", hex);
        b.addEventListener("click", function () {
          var c = hexToRgb(hex);
          setFromRgb(c.r, c.g, c.b);
          remember(hex); /* the row is not redrawn now: it must not move under the person's mouse */
        });
        recentRow.appendChild(b);
      });
    }

    /* A colour the person settled on is kept, but the row of last colours is only redrawn when the picker opens
       again, so a swatch never moves while someone is about to press it. */
    function commit() {
      remember(current);
    }

    /* the square: mouse, finger and pen */
    function fromPointer(event) {
      var box = square.getBoundingClientRect();
      hsv.s = clamp((event.clientX - box.left) / box.width, 0, 1);
      hsv.v = 1 - clamp((event.clientY - box.top) / box.height, 0, 1);
      changed(false);
    }
    square.addEventListener("pointerdown", function (event) {
      if (event.button !== undefined && event.button > 0) return;
      event.preventDefault();
      square.focus();
      try { square.setPointerCapture(event.pointerId); } catch (e) { /* not capturing: the move still works over the square */ }
      square.classList.add("is-dragging");
      fromPointer(event);
    });
    square.addEventListener("pointermove", function (event) {
      if (square.classList.contains("is-dragging")) fromPointer(event);
    });
    function endDrag() {
      if (!square.classList.contains("is-dragging")) return;
      square.classList.remove("is-dragging");
      commit();
    }
    square.addEventListener("pointerup", endDrag);
    square.addEventListener("pointercancel", endDrag);
    square.addEventListener("keydown", function (event) {
      var step = event.shiftKey ? 0.1 : 0.01;
      var moved = true;
      if (event.key === "ArrowLeft") hsv.s = clamp(hsv.s - step, 0, 1);
      else if (event.key === "ArrowRight") hsv.s = clamp(hsv.s + step, 0, 1);
      else if (event.key === "ArrowUp") hsv.v = clamp(hsv.v + step, 0, 1);
      else if (event.key === "ArrowDown") hsv.v = clamp(hsv.v - step, 0, 1);
      else moved = false;
      if (!moved) return;
      event.preventDefault();
      changed(false);
    });
    square.addEventListener("keyup", function (event) {
      if (event.key.indexOf("Arrow") === 0) commit();
    });

    hue.addEventListener("input", function () {
      hsv.h = clamp(Number(hue.value) || 0, 0, 360);
      changed(false);
    });
    hue.addEventListener("change", commit);

    ["r", "g", "b"].forEach(function (key) {
      fields[key].addEventListener("input", function () {
        if (fields[key].value === "") return;
        var v = {};
        ["r", "g", "b"].forEach(function (k) { v[k] = clamp(Math.round(Number(fields[k].value) || 0), 0, 255); });
        setFromRgb(v.r, v.g, v.b, "rgb");
      });
      fields[key].addEventListener("change", function () {
        paint(false);
        commit();
      });
    });

    hexBox.addEventListener("input", function () {
      var value = hexBox.value.trim();
      if (value.charAt(0) !== "#") value = "#" + value;
      var rgb = hexToRgb(value);
      hexBox.setAttribute("aria-invalid", rgb ? "false" : "true");
      if (rgb) setFromRgb(rgb.r, rgb.g, rgb.b, "hex");
    });
    hexBox.addEventListener("change", function () {
      hexBox.setAttribute("aria-invalid", "false");
      paint(false);
      commit();
    });

    if (dropper) {
      dropper.addEventListener("click", function () {
        new window.EyeDropper().open().then(function (result) {
          var rgb = hexToRgb(result.sRGBHex);
          if (!rgb) return;
          setFromRgb(rgb.r, rgb.g, rgb.b);
          commit();
        }).catch(function () { /* the person pressed Escape */ });
      });
    }

    paint(false);
    drawRecent();

    return {
      node: node,
      get: function () { return current; },
      /* Shows the colours used lately (call it when the picker opens). */
      refresh: drawRecent,
      /* Sets the colour without calling onInput (the caller already knows). */
      set: function (hex) {
        var rgb = hexToRgb(hex);
        if (!rgb || hex.toLowerCase() === current) return;
        var next = rgbToHsv(rgb.r, rgb.g, rgb.b);
        if (next.s === 0 || next.v === 0) next.h = hsv.h;
        hsv = next;
        paint(false);
      },
    };
  }

  E.colorPicker = { create: create, hexToRgb: hexToRgb, rgbToHex: rgbToHex, rgbToHsv: rgbToHsv, hsvToRgb: hsvToRgb };
})();
