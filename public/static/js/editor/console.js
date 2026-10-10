/* The console: what the running bot writes (messages, warnings, errors). It only shows lines it is given, so it
   works the same for a bot running here and for the messages the editor itself has to say.

   A line is { level: "info" | "warning" | "error" | "system", text, time?, source?, hint? }.
   - A Python error (a "Traceback") is folded: the line that says what went wrong stays, the rest opens on request.
   - `hint` is a plain-language help from the runner, shown under the line.
   - If the error points at a line of main.py that one of the blocks wrote, a "Show block" button finds that block. */
(function () {
  "use strict";

  var E = window.sffEditor;
  var el = E.el;

  var MAX_LINES = 1000;
  var TRACEBACK = "Traceback (most recent call last):";
  var MAIN_FRAME = /File "main\.py", line (\d+)/g;
  var QUIET_SOURCES = { print: 1, system: 1, python: 1, sff: 1, bot: 1, root: 1 };
  var refs = {};
  var state = { filter: "all", entries: [], counts: { warning: 0, error: 0 } };
  var locator = null;
  var HINT_IDLE = "Press Run to start your bot. Its messages, warnings and errors show up here.";
  var hint = HINT_IDLE;

  var LABEL = { info: "INFO", warning: "WARN", error: "ERROR", system: "" };

  function clock(time) {
    var when = time ? new Date(time) : new Date();
    if (isNaN(when.getTime())) when = new Date();
    var two = function (n) { return (n < 10 ? "0" : "") + n; };
    return two(when.getHours()) + ":" + two(when.getMinutes()) + ":" + two(when.getSeconds());
  }

  /* "Error in command 'x'\nTraceback (most recent call last):\n ...\nValueError: boom" ->
     the words before the traceback, the last line (what went wrong), and the whole traceback. */
  function splitTraceback(text) {
    var at = text.indexOf(TRACEBACK);
    if (at === -1) return null;
    var lines = text.split("\n");
    var last = lines.length - 1;
    while (last > 0 && !lines[last].trim()) last -= 1;
    return { head: text.slice(0, at).replace(/\s+$/, ""), summary: lines[last].trim(), details: text.slice(at).replace(/\s+$/, "") };
  }

  /* The line of main.py where the error came through the bot's own code (the last main.py in the traceback). */
  function lastMainLine(text) {
    var found = null;
    var match;
    MAIN_FRAME.lastIndex = 0;
    while ((match = MAIN_FRAME.exec(text)) !== null) found = parseInt(match[1], 10);
    return found;
  }

  function actionButton(label, onClick) {
    return el("button", { class: "log-action", type: "button", text: label, on: { click: onClick } });
  }

  function bodyNode(entry) {
    var body = el("div", { class: "log-body" });
    var trace = entry.level === "info" ? null : splitTraceback(entry.text);
    var actions = [];

    var first = trace ? trace.head : entry.text;
    var named = entry.source && !QUIET_SOURCES[entry.source];
    if (first || named) {
      var text = el("span", { class: "log-text" });
      if (named) text.appendChild(el("span", { class: "log-source", text: entry.source }));
      text.appendChild(document.createTextNode(first));
      body.appendChild(text);
    }
    if (trace) body.appendChild(el("span", { class: "log-summary", text: trace.summary }));
    if (entry.hint) {
      body.appendChild(el("p", { class: "log-note" }, [E.icon("lightbulb"), el("span", { text: entry.hint })]));
    }

    var blockId = null;
    if (trace && locator) {
      var line = lastMainLine(entry.text);
      if (line !== null) blockId = locator(line);
    }
    if (blockId) {
      actions.push(actionButton("Show block", function () {
        E.bus.emit("code:pick", blockId);
      }));
    }
    if (trace) {
      var details = el("pre", { class: "log-details", text: trace.details, hidden: true });
      var toggle = el("button", { class: "log-action", type: "button", text: "Show details", "aria-expanded": "false" });
      toggle.addEventListener("click", function () {
        var open = details.hidden;
        details.hidden = !open;
        toggle.textContent = open ? "Hide details" : "Show details";
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) details.scrollIntoView({ block: "nearest" });
      });
      actions.push(toggle);
      actions.push(actionButton("Copy", function () {
        window.sff.copyText(entry.text).then(function (ok) {
          window.sff.toast(ok ? "Copied the error" : "Couldn't copy. Open the details and copy them by hand.", ok ? "ok" : "error", 1800);
        });
      }));
      body.appendChild(el("div", { class: "log-actions" }, actions));
      body.appendChild(details);
    } else if (actions.length) {
      body.appendChild(el("div", { class: "log-actions" }, actions));
    }
    return body;
  }

  function lineNode(entry) {
    return el("div", { class: "log-line lvl-" + entry.level, data: { level: entry.level } }, [
      el("span", { class: "log-time", text: clock(entry.time) }),
      el("span", { class: "log-level", text: LABEL[entry.level] === undefined ? entry.level.toUpperCase() : LABEL[entry.level] }),
      bodyNode(entry),
    ]);
  }

  function nearBottom() {
    var log = refs.log;
    return log.scrollHeight - log.scrollTop - log.clientHeight < 48;
  }

  function showHint() {
    var empty = !state.entries.length;
    var existing = refs.log.querySelector(".log-hint");
    if (empty && !existing) refs.log.appendChild(el("p", { class: "log-hint", text: hint }));
    if (!empty && existing) existing.remove();
    if (empty && existing) existing.textContent = hint;
  }

  function updateCounts() {
    refs.filters.forEach(function (button) {
      var kind = button.dataset.filter;
      var badge = button.querySelector(".ed-count");
      if (badge) badge.remove();
      if (kind !== "all" && state.counts[kind]) {
        button.appendChild(el("span", { class: "ed-count ed-count-" + (kind === "error" ? "error" : "warn"), text: String(state.counts[kind]) }));
      }
    });
  }

  function trim() {
    while (state.entries.length > MAX_LINES) {
      var old = state.entries.shift();
      if (state.counts[old.level] !== undefined) state.counts[old.level] -= 1;
      var first = refs.log.querySelector(".log-line");
      if (first) first.remove();
    }
  }

  function normalise(entry) {
    return {
      level: LABEL[entry.level] === undefined ? "info" : entry.level,
      text: String(entry.text === undefined ? "" : entry.text),
      time: entry.time,
      source: typeof entry.source === "string" ? entry.source : "",
      hint: typeof entry.hint === "string" ? entry.hint : "",
    };
  }

  /* Put lines at the end, and stay at the bottom only if the person was already there. */
  function append(list) {
    if (!list.length) return;
    var stick = nearBottom();
    var fragment = document.createDocumentFragment();
    list.forEach(function (raw) {
      var entry = normalise(raw);
      state.entries.push(entry);
      if (state.counts[entry.level] !== undefined) state.counts[entry.level] += 1;
      fragment.appendChild(lineNode(entry));
    });
    var hintNode = refs.log.querySelector(".log-hint");
    if (hintNode) hintNode.remove();
    refs.log.appendChild(fragment);
    trim();
    updateCounts();
    if (stick) refs.log.scrollTop = refs.log.scrollHeight;
    else refs.jump.hidden = false;
  }

  function add(entry) {
    append([entry]);
  }

  function addMany(entries) {
    append(entries);
  }

  function clear() {
    state.entries = [];
    state.counts = { warning: 0, error: 0 };
    E.clear(refs.log);
    refs.jump.hidden = true;
    showHint();
    updateCounts();
  }

  function setFilter(kind) {
    state.filter = kind;
    refs.log.dataset.filter = kind;
    refs.filters.forEach(function (button) {
      button.setAttribute("aria-pressed", button.dataset.filter === kind ? "true" : "false");
    });
    refs.log.scrollTop = refs.log.scrollHeight;
  }

  /* The little label next to the title: "Not running", "Running", ... */
  function setState(name, text, title) {
    refs.state.dataset.state = name;
    refs.state.textContent = text;
    if (title) refs.state.title = title;
    else refs.state.removeAttribute("title");
  }

  function setHint(text) {
    hint = text || HINT_IDLE;
    showHint();
  }

  /* fn(lineNumberInMainPy) -> id of the block that wrote that line, or null */
  function setLocator(fn) {
    locator = fn;
  }

  function init() {
    refs = {
      log: document.getElementById("console-log"),
      state: document.getElementById("run-state"),
      clear: document.getElementById("console-clear"),
      filters: Array.prototype.slice.call(document.querySelectorAll(".ed-filter")),
      jump: el("button", { class: "log-jump", type: "button", hidden: true }, [E.icon("arrow-down"), "Latest"]),
    };
    refs.log.parentNode.appendChild(refs.jump);
    refs.jump.addEventListener("click", function () {
      refs.log.scrollTop = refs.log.scrollHeight;
      refs.jump.hidden = true;
    });
    refs.log.addEventListener("scroll", function () {
      if (nearBottom()) refs.jump.hidden = true;
    });
    refs.clear.addEventListener("click", clear);
    refs.filters.forEach(function (button) {
      button.addEventListener("click", function () {
        setFilter(button.dataset.filter);
      });
    });
    refs.log.dataset.filter = "all";
    showHint();
  }

  E.console = {
    init: init,
    add: add,
    addMany: addMany,
    clear: clear,
    setState: setState,
    setHint: setHint,
    setLocator: setLocator,
  };
})();
