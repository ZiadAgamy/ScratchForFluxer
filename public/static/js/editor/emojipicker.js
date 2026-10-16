/* The emoji picker for the text of a card: ordinary emoji, plus the custom emoji of the communities the bot is in
   (asked from our server the first time the picker opens). Picking one writes it into the text box at the cursor.
   A community emoji is written <:name:123456789> (or <a:name:...> when it moves), which is how Fluxer reads it. */
(function () {
  "use strict";

  var E = window.sffEditor;
  var el = E.el;

  var loaded = null; /* { communities: [...], media: "https://..." } once asked */
  var asking = null;
  var counter = 0;

  function botId() {
    var root = document.getElementById("editor");
    return root ? root.dataset.botId : "";
  }

  /* Ask for the community emoji once, and again if the first try failed. Resolves to {communities, media} or throws a message. */
  function load() {
    if (loaded) return Promise.resolve(loaded);
    if (asking) return asking;
    asking = fetch("/api/bots/" + encodeURIComponent(botId()) + "/emojis", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          if (!response.ok) throw new Error(body.detail || "Couldn't get the community emoji.");
          loaded = { communities: body.communities || [], media: body.media || "" };
          return loaded;
        });
      })
      .catch(function (error) {
        throw new Error(error && error.message ? error.message : "Couldn't get the community emoji.");
      })
      .then(
        function (value) { asking = null; return value; },
        function (error) { asking = null; throw error; }
      );
    return asking;
  }

  function emojiUrl(id, animated) {
    return (loaded ? loaded.media : "") + "/emojis/" + id + "." + (animated ? "gif" : "png");
  }

  function written(entry) {
    return "<" + (entry.animated ? "a" : "") + ":" + entry.name + ":" + entry.id + ">";
  }

  var CUSTOM = /<(a?):([A-Za-z0-9_]{1,64}):(\d{5,32})>/g;

  /* Text for the card preview: community emoji become small pictures (once we know where they live), the rest stays text. */
  function nodes(text) {
    var out = [];
    var last = 0;
    String(text).replace(CUSTOM, function (whole, animated, name, id, index) {
      if (index > last) out.push(document.createTextNode(text.slice(last, index)));
      if (loaded && loaded.media) {
        var img = el("img", { class: "emoji-inline", alt: ":" + name + ":", title: ":" + name + ":", src: emojiUrl(id, animated === "a"), loading: "lazy" });
        img.addEventListener("error", function () {
          img.replaceWith(document.createTextNode(":" + name + ":"));
        });
        out.push(img);
      } else {
        out.push(document.createTextNode(":" + name + ":"));
      }
      last = index + whole.length;
      return whole;
    });
    if (last < text.length) out.push(document.createTextNode(text.slice(last)));
    return out;
  }

  /* A button and a panel for one text box. Returns {button, panel}; put both next to the box. */
  function attach(control, afterInsert) {
    counter += 1;
    var uid = "emoji" + counter;
    var open = false;
    var search = el("input", { class: "input emoji-search", type: "text", placeholder: "Search emoji…", "aria-label": "Search emoji", autocomplete: "off", spellcheck: "false" });
    var list = el("div", { class: "emoji-list", id: uid + "-list", role: "group", "aria-label": "Emoji" });
    var status = el("p", { class: "emoji-status", role: "status" });
    var panel = el("div", { class: "emoji-panel", hidden: true }, [search, list, status]);
    var button = el("button", { class: "btn btn-ghost btn-sm emoji-toggle", type: "button", "aria-expanded": "false", "aria-controls": uid + "-list", title: "Add an emoji" }, [E.icon("smiley"), el("span", { text: "Emoji" })]);

    function insert(word) {
      var start = typeof control.selectionStart === "number" ? control.selectionStart : control.value.length;
      var end = typeof control.selectionEnd === "number" ? control.selectionEnd : start;
      var next = control.value.slice(0, start) + word + control.value.slice(end);
      var max = parseInt(control.getAttribute("maxlength") || "0", 10);
      if (max && next.length > max) {
        status.textContent = "There isn't room for that in this text.";
        return;
      }
      status.textContent = "";
      control.value = next;
      control.focus();
      try { control.setSelectionRange(start + word.length, start + word.length); } catch (e) { /* not a text box */ }
      control.dispatchEvent(new Event("input", { bubbles: true }));
      if (afterInsert) afterInsert(word);
    }

    function heading(text) {
      return el("h4", { class: "emoji-heading", text: text });
    }

    function pick(word, label, child) {
      return el("button", { class: "emoji-pick", type: "button", title: label, "aria-label": label, on: { click: function () { insert(word); } } }, [child]);
    }

    function draw() {
      E.clear(list);
      var wanted = search.value.trim().toLowerCase();
      var shown = 0;
      if (loaded) {
        loaded.communities.forEach(function (community) {
          var matches = community.emojis.filter(function (entry) { return !wanted || entry.name.toLowerCase().indexOf(wanted) !== -1; });
          if (!matches.length) return;
          list.appendChild(heading(community.name));
          var grid = el("div", { class: "emoji-grid" });
          matches.forEach(function (entry) {
            var img = el("img", { class: "emoji-custom", alt: ":" + entry.name + ":", src: emojiUrl(entry.id, entry.animated), loading: "lazy" });
            img.addEventListener("error", function () {
              img.replaceWith(el("span", { class: "emoji-fallback", text: entry.name }));
            });
            grid.appendChild(pick(written(entry), ":" + entry.name + ":", img));
            shown += 1;
          });
          list.appendChild(grid);
        });
      }
      E.emojiData.forEach(function (group) {
        var matches = group.emojis.filter(function (entry) { return !wanted || entry.words.indexOf(wanted) !== -1; });
        if (!matches.length) return;
        list.appendChild(heading(group.name));
        var grid = el("div", { class: "emoji-grid" });
        matches.forEach(function (entry) {
          grid.appendChild(pick(entry.char, entry.words.split(" ")[0], el("span", { class: "emoji-char", text: entry.char, "aria-hidden": "true" })));
          shown += 1;
        });
        list.appendChild(grid);
      });
      if (!shown) list.appendChild(el("p", { class: "emoji-none", text: "No emoji matches “" + search.value.trim() + "”." }));
    }

    function toggle(force) {
      open = typeof force === "boolean" ? force : !open;
      panel.hidden = !open;
      button.setAttribute("aria-expanded", open ? "true" : "false");
      if (!open) return;
      draw();
      search.focus();
      if (!loaded) {
        status.textContent = "Getting your communities' emoji…";
        load().then(
          function (result) {
            status.textContent = result.communities.length ? "" : "No community emoji yet. Add some in your community's settings and they show up here.";
            if (open) draw();
            control.dispatchEvent(new Event("input", { bubbles: true })); /* the preview can show them now */
          },
          function (error) {
            status.textContent = error.message + " The ordinary emoji still work.";
          }
        );
      }
    }

    button.addEventListener("click", function () { toggle(); });
    search.addEventListener("input", draw);
    panel.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        event.stopPropagation(); /* close the picker, not the whole pop-up */
        event.preventDefault();
        toggle(false);
        button.focus();
      } else if (event.key === "Enter" && event.target === search) {
        event.preventDefault();
        var first = list.querySelector(".emoji-pick");
        if (first) first.click();
      }
    });
    return { button: button, panel: panel };
  }

  E.emojis = { attach: attach, nodes: nodes, load: load, written: written };
})();
