/* Landing page: the live demo in the hero, and the block explorer. No dependencies.

   The demo is a small model of the real thing: editing a block rewrites the Python, and the bot only
   uses your changes after you press Restart, just like in the editor. */
(function () {
  "use strict";

  var reduceMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  var MAX_COMMAND = 24; // the longest command name the editor keeps

  /* ------------------------------------------------------------------ Python text */

  // Names a command function must not take, or it would shadow something the generated file uses (Python's keywords and
  // built-in names, and the names the generated file defines itself). The server sends the list: it is the compiler's own.
  var RESERVED = new Set();

  function pyStr(value) {
    // JSON string syntax is valid Python string syntax for everything we allow here.
    return JSON.stringify(String(value));
  }

  /** The function name for a command, and whether the command needs an explicit name="..." to keep its text.
      The same rules as compiler.command_function_name, step for step. */
  function pyCommand(name) {
    var ident = name.normalize("NFKC").replace(/[\s-]+/g, "_");
    if (!ident || !/^\p{L}/u.test(ident)) ident = "cmd_" + ident; // digits and underscores can't start the name
    if (RESERVED.has(ident) || ident.indexOf("on_") === 0) ident += "_cmd";
    ident = Array.from(ident)
      .map(function (ch) {
        return /^\p{ID_Continue}$/u.test(ch) ? ch : "_";
      })
      .join("");
    return { ident: ident, name: name, rename: ident !== name };
  }

  /** The whole file, as [block id, line text] pairs. Keep in step with the compiler (see content.DEMO). */
  function buildLines(v) {
    var c = pyCommand(v.command);
    return [
      ["setup", "import os"],
      ["setup", ""],
      ["setup", "import fluxer"],
      ["setup", ""],
      ["setup", "bot = fluxer.Bot("],
      ["setup", "    command_prefix=" + pyStr(v.prefix) + ","],
      ["setup", "    intents=fluxer.Intents.default(),"],
      ["setup", ")"],
      ["setup", ""],
      ["setup", ""],
      ["ready", "@bot.event"],
      ["ready", "async def on_ready():"],
      ["print", "    print(" + pyStr(v.ready) + ")"],
      ["setup", ""],
      ["setup", ""],
      ["cmd", "@bot.command" + (c.rename ? "(name=" + pyStr(c.name) + ")" : "()")],
      ["cmd", "async def " + c.ident + "(ctx):"],
      ["reply", "    await ctx.reply(" + pyStr(v.reply) + ")"],
      ["setup", ""],
      ["setup", ""],
      ["run", 'bot.run(os.environ["FLUXER_TOKEN"])'],
    ];
  }

  var TOKEN =
    /("(?:[^"\\]|\\.)*")|(#.*$)|(@[\p{L}_][\p{L}\p{N}_.]*)|\b(def)(\s+)([\p{L}_][\p{L}\p{N}_]*)|\b(import|from|async|await|return|if|else|elif|for|in|while|not|and|or|True|False|None)\b|\b(print)\b|(\b\d+(?:\.\d+)?\b)/gu;

  /** Colours one line of Python. Builds DOM nodes (never innerHTML), so what people type can't inject markup. */
  function appendTokens(parent, text) {
    var last = 0;
    var m;
    function plain(s) {
      if (s) parent.appendChild(document.createTextNode(s));
    }
    function span(cls, s) {
      var el = document.createElement("span");
      el.className = cls;
      el.textContent = s;
      parent.appendChild(el);
    }
    TOKEN.lastIndex = 0;
    while ((m = TOKEN.exec(text)) !== null) {
      plain(text.slice(last, m.index));
      if (m[1] !== undefined) span("tok-s", m[1]);
      else if (m[2] !== undefined) span("tok-c", m[2]);
      else if (m[3] !== undefined) span("tok-d", m[3]);
      else if (m[4] !== undefined) {
        span("tok-k", m[4]);
        plain(m[5]);
        span("tok-f", m[6]);
      } else if (m[7] !== undefined) span("tok-k", m[7]);
      else if (m[8] !== undefined) span("tok-b", m[8]);
      else span("tok-n", m[9]);
      last = m.index + m[0].length;
    }
    plain(text.slice(last));
  }

  function lineNode(block, text) {
    var code = document.createElement("code");
    code.className = "ln";
    code.setAttribute("data-block", block);
    appendTokens(code, text);
    return code;
  }

  function replaceChildren(parent, fragment) {
    while (parent.firstChild) parent.removeChild(parent.firstChild);
    parent.appendChild(fragment);
  }

  /* ------------------------------------------------------------------ the demo */

  function initDemo(root) {
    var $ = function (selector) {
      return root.querySelector(selector);
    };

    var prefix = root.getAttribute("data-prefix") || "!";
    try {
      RESERVED = new Set(JSON.parse(root.getAttribute("data-reserved") || "[]"));
    } catch (_) {
      RESERVED = new Set(); // the demo still works; only the rarest command names would be written differently
    }
    var fields = { ready: $('[data-field="ready"]'), command: $('[data-field="command"]'), reply: $('[data-field="reply"]') };
    var codeEl = $('[data-role="code"]');
    var chatLog = $('[data-role="chat"]');
    var chatForm = $('[data-role="chat-form"]');
    var chatInput = chatForm.elements.message;
    var chatSend = chatForm.querySelector('[type="submit"]');
    var consoleEl = $('[data-role="console"]');
    var statusEl = $('[data-role="status"]');
    var statusText = $('[data-role="status-text"]');
    var hintEl = $('[data-role="hint"]');
    var buttons = { run: $('[data-act="run"]'), restart: $('[data-act="restart"]'), stop: $('[data-act="stop"]') };

    var state = { status: "stopped", running: null, timers: [], lastCommand: fields.command.value, chatEdited: false };
    var linked = null;

    /* ---- values + Python pane ---- */
    function values() {
      return { prefix: prefix, ready: fields.ready.value, command: state.lastCommand, reply: fields.reply.value };
    }

    function renderCode() {
      var fragment = document.createDocumentFragment();
      buildLines(values()).forEach(function (line) {
        fragment.appendChild(lineNode(line[0], line[1]));
      });
      replaceChildren(codeEl, fragment);
      applyLink();
    }

    function syncSize(input) {
      var size = input.parentNode.querySelector(".blk-size");
      if (size) size.textContent = input.value || "​"; // a zero-width space keeps the pill's height when empty
    }

    /* ---- block <-> line highlighting ---- */
    function applyLink() {
      root.querySelectorAll(".is-linked").forEach(function (el) {
        el.classList.remove("is-linked");
      });
      if (!linked || linked === "setup") return;
      root.querySelectorAll('[data-block="' + linked + '"]').forEach(function (el) {
        el.classList.add("is-linked");
      });
    }

    function linkFrom(target) {
      var el = target && target.closest ? target.closest("[data-block]") : null;
      linked = el ? el.getAttribute("data-block") : null;
      applyLink();
    }

    root.addEventListener("pointerover", function (event) {
      linkFrom(event.target);
    });
    root.addEventListener("pointerleave", function () {
      linkFrom(null);
    });
    root.addEventListener("focusin", function (event) {
      linkFrom(event.target);
    });
    root.addEventListener("focusout", function () {
      linkFrom(null);
    });

    /* ---- console + chat ---- */
    function clock() {
      return new Date().toLocaleTimeString("en-GB", { hour12: false });
    }

    function log(kind, text) {
      var empty = consoleEl.querySelector(".console-empty");
      if (empty) empty.remove();
      var row = document.createElement("div");
      row.className = "log log-" + kind;
      var time = document.createElement("time");
      time.textContent = clock();
      var message = document.createElement("span");
      message.textContent = text;
      row.appendChild(time);
      row.appendChild(message);
      consoleEl.appendChild(row);
      while (consoleEl.children.length > 60) consoleEl.removeChild(consoleEl.firstChild);
      consoleEl.scrollTop = consoleEl.scrollHeight;
    }

    function icon(name) {
      var el = document.createElement("i");
      el.className = "ph-fill ph-" + name;
      el.setAttribute("aria-hidden", "true");
      return el;
    }

    function messageRow(who) {
      var row = document.createElement("div");
      row.className = "msg msg-" + who;
      var avatar = document.createElement("span");
      avatar.className = "msg-avatar";
      avatar.appendChild(icon(who === "bot" ? "robot" : "user"));
      var body = document.createElement("div");
      var name = document.createElement("div");
      name.className = "msg-name";
      name.appendChild(document.createTextNode(who === "bot" ? "My first bot" : "You"));
      if (who === "bot") {
        var tag = document.createElement("span");
        tag.className = "msg-tag";
        tag.textContent = "Bot";
        name.appendChild(tag);
      }
      body.appendChild(name);
      row.appendChild(avatar);
      row.appendChild(body);
      return { row: row, body: body };
    }

    function trimChat() {
      while (chatLog.children.length > 40) chatLog.removeChild(chatLog.firstChild);
      chatLog.scrollTop = chatLog.scrollHeight;
    }

    function clearChatEmpty() {
      var empty = chatLog.querySelector(".chat-empty");
      if (empty) empty.remove();
    }

    function addMessage(who, text) {
      clearChatEmpty();
      var parts = messageRow(who);
      var line = document.createElement("div");
      line.className = "msg-text";
      line.textContent = text;
      parts.body.appendChild(line);
      chatLog.appendChild(parts.row);
      trimChat();
    }

    function addNote(text) {
      clearChatEmpty();
      var note = document.createElement("p");
      note.className = "chat-note";
      note.textContent = text;
      chatLog.appendChild(note);
      trimChat();
    }

    function showTyping() {
      clearChatEmpty();
      var parts = messageRow("bot");
      parts.row.setAttribute("data-typing", "");
      var dots = document.createElement("div");
      dots.className = "typing";
      dots.setAttribute("aria-label", "typing");
      for (var i = 0; i < 3; i++) dots.appendChild(document.createElement("i"));
      parts.body.appendChild(dots);
      chatLog.appendChild(parts.row);
      trimChat();
    }

    function hideTyping() {
      chatLog.querySelectorAll("[data-typing]").forEach(function (el) {
        el.remove();
      });
    }

    /* ---- the bot ---- */
    function later(fn, ms) {
      var id = setTimeout(function () {
        state.timers = state.timers.filter(function (t) {
          return t !== id;
        });
        fn();
      }, ms);
      state.timers.push(id);
    }

    function clearTimers() {
      state.timers.forEach(clearTimeout);
      state.timers = [];
    }

    function isDirty() {
      var run = state.running;
      if (!run) return false;
      var now = values();
      return run.ready !== now.ready || run.command !== now.command || run.reply !== now.reply;
    }

    function updateHint() {
      var dirty = isDirty();
      hintEl.hidden = !dirty;
      if (dirty) buttons.restart.classList.add("is-hint");
      else buttons.restart.classList.remove("is-hint");
    }

    function setStatus(next) {
      state.status = next;
      statusEl.setAttribute("data-status", next);
      statusText.textContent = { stopped: "Offline", starting: "Starting…", online: "Online" }[next];
      buttons.run.disabled = next !== "stopped";
      buttons.stop.disabled = next === "stopped";
      buttons.restart.disabled = next === "stopped";
      updateHint();
    }

    function run(done) {
      if (state.status !== "stopped") return;
      state.running = values();
      var snapshot = state.running;
      setStatus("starting");
      log("system", "Starting your bot…");
      later(
        function () {
          setStatus("online");
          log("info", snapshot.ready);
          if (done) done();
        },
        reduceMotion ? 0 : 900
      );
    }

    function stop() {
      if (state.status === "stopped") return;
      clearTimers();
      hideTyping();
      state.running = null;
      setStatus("stopped");
      log("system", "Your bot stopped.");
    }

    function restart() {
      if (state.status === "stopped") return;
      stop();
      later(run, reduceMotion ? 0 : 350);
    }

    function send(raw) {
      var text = String(raw || "").trim();
      if (!text) return;
      addMessage("you", text);
      if (state.status !== "online") {
        addNote(state.status === "starting" ? "Your bot is still starting." : "Your bot is offline, so nobody answers. Press Run.");
        return;
      }
      var snapshot = state.running;
      // fluxer.py runs a command when the text after the prefix starts with the command's name.
      if (text.indexOf(snapshot.prefix) !== 0 || text.slice(snapshot.prefix.length).indexOf(snapshot.command) !== 0) return;
      showTyping();
      later(
        function () {
          hideTyping();
          if (snapshot.reply) addMessage("bot", snapshot.reply);
        },
        reduceMotion ? 0 : 650
      );
    }

    /* ---- autoplay: one run-through when the demo scrolls into view, abandoned at the first touch ---- */
    var autoOn = true;
    var autoTimers = [];

    function auto(fn, ms) {
      if (!autoOn) return;
      autoTimers.push(setTimeout(fn, ms));
    }

    function cancelAuto() {
      autoOn = false;
      autoTimers.forEach(clearTimeout);
      autoTimers = [];
    }

    function startAutoplay() {
      root.classList.add("is-live"); // the entry animation is over; later redraws must not replay it
      if (!autoOn) return;
      run(function () {
        auto(function () {
          if (state.status === "online") send(chatInput.value);
        }, reduceMotion ? 0 : 900);
      });
    }

    function whenVisible(callback) {
      if (!("IntersectionObserver" in window)) {
        callback();
        return;
      }
      var observer = new IntersectionObserver(
        function (entries) {
          if (entries.some(function (entry) {
            return entry.isIntersecting;
          })) {
            observer.disconnect();
            callback();
          }
        },
        { threshold: 0.4 }
      );
      observer.observe(root);
    }

    ["pointerdown", "keydown", "focusin"].forEach(function (name) {
      root.addEventListener(name, cancelAuto);
    });

    /* ---- wiring ---- */
    /* What the box may hold while someone types: letters, digits, "_", "-" and single spaces (compiler.clean_command_name). */
    function typedCommand(value) {
      return value
        .replace(/\s+/g, " ")
        .replace(/[^\p{L}\p{N}\p{M}_\- ]/gu, "")
        .replace(/^ /, "");
    }

    /* The command itself: the same, without the spaces at the ends, and no longer than the editor allows. */
    function cleanCommand(value) {
      return typedCommand(value).slice(0, MAX_COMMAND).trim();
    }

    Object.keys(fields).forEach(function (key) {
      var input = fields[key];
      input.addEventListener("input", function () {
        root.classList.add("is-live");
        if (key === "command") {
          var shown = typedCommand(input.value);
          if (shown !== input.value) {
            var caret = Math.min(input.selectionStart || 0, shown.length);
            input.value = shown;
            input.setSelectionRange(caret, caret);
          }
          var cleaned = cleanCommand(shown);
          if (cleaned) {
            var previous = state.lastCommand;
            state.lastCommand = cleaned;
            if (!state.chatEdited && chatInput.value === prefix + previous) chatInput.value = prefix + cleaned;
          }
        }
        syncSize(input);
        renderCode();
        updateHint();
      });
      input.addEventListener("change", function () {
        if (key === "command") {
          input.value = cleanCommand(input.value) || state.lastCommand; // a command needs a name: put the last one back
          syncSize(input);
        }
      });
      input.addEventListener("keydown", function (event) {
        if (event.key === "Enter") input.blur();
      });
    });

    chatInput.addEventListener("input", function () {
      state.chatEdited = true;
    });

    chatForm.addEventListener("submit", function (event) {
      event.preventDefault();
      send(chatInput.value);
      chatInput.select();
    });

    buttons.run.addEventListener("click", function () {
      run();
    });
    buttons.stop.addEventListener("click", stop);
    buttons.restart.addEventListener("click", restart);

    chatSend.disabled = false;
    setStatus("stopped");
    whenVisible(function () {
      setTimeout(startAutoplay, reduceMotion ? 0 : 2000);
    });
  }

  /* ------------------------------------------------------------------ block explorer */

  function initExplorer(root) {
    var tabs = Array.prototype.slice.call(root.querySelectorAll(".tab"));
    var panels = tabs.map(function (tab) {
      return document.getElementById(tab.getAttribute("data-target"));
    });
    if (!tabs.length || panels.some(function (panel) {
      return !panel;
    })) return;

    var list = root.querySelector('[data-role="tabs"]');
    var wide = window.matchMedia ? window.matchMedia("(min-width: 52rem)") : null;
    function orient() {
      list.setAttribute("aria-orientation", wide && wide.matches ? "vertical" : "horizontal");
    }
    orient();
    if (wide && wide.addEventListener) wide.addEventListener("change", orient);

    root.classList.add("is-enhanced");
    list.setAttribute("role", "tablist");
    tabs.forEach(function (tab, i) {
      tab.id = "tab-" + panels[i].id;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-controls", panels[i].id);
      panels[i].setAttribute("role", "tabpanel");
      panels[i].setAttribute("aria-labelledby", tab.id);
      panels[i].tabIndex = 0;
    });

    function select(index, focus) {
      tabs.forEach(function (tab, i) {
        var on = i === index;
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.tabIndex = on ? 0 : -1;
        panels[i].hidden = !on;
      });
      if (focus) tabs[index].focus();
    }

    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () {
        select(i, false);
      });
      tab.addEventListener("keydown", function (event) {
        var next = null;
        if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (i + 1) % tabs.length;
        else if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = tabs.length - 1;
        if (next === null) return;
        event.preventDefault();
        select(next, true);
      });
    });

    select(0, false);
  }

  var demo = document.getElementById("demo");
  if (demo) initDemo(demo);
  var explorer = document.querySelector("[data-explorer]");
  if (explorer) initExplorer(explorer);
})();
