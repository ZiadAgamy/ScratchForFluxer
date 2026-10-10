/* The Python tab: shows the code the server wrote for the blocks, lights up the lines of the selected block, and
   selects the block that wrote a line you click. The server says which lines belong to which block (compile.lines). */
(function () {
  "use strict";

  var E = window.sffEditor;
  var el = E.el;

  var refs = {};
  var current = { code: null, lines: {}, diagnostics: [] };
  var rows = [];
  var lit = [];
  var selected = null;
  var copyTimer = null;

  function buildRow(number, text) {
    var code = el("span", { class: "ct" });
    code.appendChild(E.python.colourise(text));
    if (!text) code.appendChild(document.createTextNode("​"));
    return el("div", { class: "cl", data: { line: String(number) } }, [el("span", { class: "cn", text: String(number) }), code]);
  }

  function buildRows(code) {
    E.clear(refs.view);
    rows = [];
    if (!code) {
      refs.view.appendChild(el("p", { class: "ed-empty", text: "Your blocks haven't written any Python yet." }));
      return;
    }
    var lines = code.replace(/\n$/, "").split("\n");
    refs.view.style.setProperty("--cn-digits", String(String(lines.length).length));
    var fragment = document.createDocumentFragment();
    lines.forEach(function (text, index) {
      var row = buildRow(index + 1, text);
      rows.push(row);
      fragment.appendChild(row);
    });
    refs.view.appendChild(fragment);
  }

  /* The block whose lines are the tightest fit around `number`. */
  function blockForLine(number) {
    var best = null;
    var size = Infinity;
    Object.keys(current.lines).forEach(function (id) {
      var range = current.lines[id];
      if (number < range[0] || number > range[1]) return;
      if (range[1] - range[0] < size) {
        best = id;
        size = range[1] - range[0];
      }
    });
    return best;
  }

  function clearLit() {
    lit.forEach(function (row) {
      row.classList.remove("is-lit", "is-lit-first", "is-lit-last");
    });
    lit = [];
  }

  /* Light up the lines the block (or, for a small value block, the block it sits in) wrote. */
  function highlight(blockId, resolveOwner) {
    selected = blockId;
    clearLit();
    if (!blockId) return;
    var owner = blockId;
    if (!current.lines[owner] && resolveOwner) owner = resolveOwner(blockId);
    var range = owner && current.lines[owner];
    if (!range) return;
    for (var n = range[0]; n <= range[1]; n++) {
      var row = rows[n - 1];
      if (!row) continue;
      row.classList.add("is-lit");
      if (n === range[0]) row.classList.add("is-lit-first");
      if (n === range[1]) row.classList.add("is-lit-last");
      lit.push(row);
    }
    reveal(rows[range[0] - 1], rows[range[1] - 1]);
  }

  /* Scroll the code view (and only the code view: scrollIntoView would also move the page around it) so the first line
     of the block is showing, along with as many of its other lines as fit. */
  function reveal(first, last) {
    if (!first) return;
    var view = refs.view;
    var margin = 10;
    var box = view.getBoundingClientRect();
    var top = first.getBoundingClientRect().top;
    var bottom = (last || first).getBoundingClientRect().bottom;
    if (top < box.top + margin) {
      view.scrollTop -= box.top + margin - top;
    } else if (bottom > box.bottom - margin) {
      view.scrollTop += Math.min(bottom - (box.bottom - margin), top - (box.top + margin));
    }
  }

  function markProblems() {
    rows.forEach(function (row) {
      row.classList.remove("has-error", "has-warning");
      row.removeAttribute("title");
    });
    current.diagnostics.forEach(function (item) {
      var range = item.block && current.lines[item.block];
      var row = range && rows[range[0] - 1];
      if (!row) return;
      row.classList.add(item.level === "error" ? "has-error" : "has-warning");
      row.title = item.message;
    });
  }

  function showBanner(compile) {
    var errors = compile.diagnostics.filter(function (item) { return item.level === "error"; }).length;
    var warnings = compile.diagnostics.length - errors;
    E.clear(refs.banner);
    if (!errors && !warnings) {
      refs.banner.hidden = true;
      return;
    }
    refs.banner.hidden = false;
    refs.banner.className = "ed-banner " + (errors ? "is-error" : "is-warn");
    var text = errors
      ? "This bot can't run yet: " + E.plural(errors, "problem") + " to fix."
      : E.plural(warnings, "warning") + " to look at.";
    refs.banner.appendChild(E.icon("warning"));
    refs.banner.appendChild(el("span", { text: text }));
    refs.banner.appendChild(
      el("button", { class: "ed-link", type: "button", text: "Show", on: { click: function () { E.bus.emit("tab", "problems"); } } })
    );
  }

  /* compile: { code, ok, diagnostics, lines } from the server. */
  function render(compile) {
    var changed = compile.code !== current.code;
    current = { code: compile.code, lines: compile.lines || {}, diagnostics: compile.diagnostics || [] };
    if (changed) buildRows(compile.code);
    var count = compile.code ? compile.code.replace(/\n$/, "").split("\n").length : 0;
    refs.meta.textContent = count ? "· " + E.plural(count, "line") : "";
    refs.copy.disabled = !compile.code;
    showBanner(compile);
    markProblems();
    if (selected) highlight(selected, refs.resolveOwner);
  }

  function copy() {
    if (!current.code) return;
    window.sff.copyText(current.code).then(function (ok) {
      var label = refs.copy.querySelector("span");
      label.textContent = ok ? "Copied" : "Couldn't copy";
      clearTimeout(copyTimer);
      copyTimer = setTimeout(function () {
        label.textContent = "Copy";
      }, 1600);
    });
  }

  /* resolveOwner(blockId) walks up from a block with no lines of its own to the one that has them. */
  function init(resolveOwner) {
    refs = {
      view: document.getElementById("code-view"),
      meta: document.getElementById("code-meta"),
      banner: document.getElementById("code-banner"),
      copy: document.getElementById("code-copy"),
      resolveOwner: resolveOwner,
    };
    refs.copy.addEventListener("click", copy);
    refs.view.addEventListener("click", function (event) {
      var row = event.target.closest(".cl");
      if (!row || String(window.getSelection())) return; /* dragging to copy some text is not a click on a line */
      var id = blockForLine(Number(row.dataset.line));
      if (id) E.bus.emit("code:pick", id);
    });
  }

  E.code = { init: init, render: render, highlight: highlight };
})();
