/* Finding blocks by typing: one search box in the toolbar that looks through every block, and one over the top of each
   family's block list that narrows just that family. The matching itself lives in blocks.js. */
(function () {
  "use strict";

  var E = window.sffEditor;
  var sff = window.sff;
  var el = E.el;

  var input = document.getElementById("ed-search-input");
  if (!input) return;
  var list = document.getElementById("ed-search-list");
  var box = document.getElementById("ed-flyout-search");
  var finput = document.getElementById("ed-flyout-input");
  var fclear = document.getElementById("ed-flyout-clear");
  var stage = document.getElementById("view-blocks");

  var hits = [];
  var active = -1;
  var family = null; /* the family whose list is open */

  // ------------------------------------------------------------------------------------------------ the toolbar search
  function colourOf(id) {
    var payload = E.blocks.payload && E.blocks.payload();
    var found = payload && payload.categories.filter(function (c) { return c.id === id; })[0];
    return found ? found.color : "#888";
  }

  function closeList() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  }

  function mark(index) {
    var rows = list.children;
    for (var i = 0; i < rows.length; i++) rows[i].classList.toggle("is-active", i === index);
    active = index;
    if (rows[index]) {
      input.setAttribute("aria-activedescendant", rows[index].id);
      rows[index].scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function render() {
    E.clear(list);
    var query = input.value.trim();
    if (!query) return closeList();
    hits = E.blocks.search(query, 30);
    if (!hits.length) {
      list.appendChild(el("li", { class: "ed-search-none", role: "presentation", text: "No block matches “" + query + "”." }));
    }
    hits.forEach(function (hit, index) {
      list.appendChild(
        el("li", { class: "ed-search-row", role: "option", id: "ed-hit-" + index, data: { type: hit.type }, on: {
          mousedown: function (event) { event.preventDefault(); },
          mousemove: function () { if (active !== index) mark(index); },
          click: function () { choose(index); },
        } }, [
          el("span", { class: "ed-search-dot", style: "background:" + colourOf(hit.family) }),
          el("span", { class: "ed-search-name", text: hit.label }),
          el("span", { class: "ed-search-family", text: hit.familyName }),
        ])
      );
    });
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    mark(hits.length ? 0 : -1);
  }

  function choose(index) {
    var hit = hits[index];
    if (!hit) return;
    var problem = E.blocks.addBlock(hit.type);
    if (problem) {
      sff.toast(problem, "info", 3200);
      return;
    }
    input.value = "";
    closeList();
    input.blur();
  }

  input.addEventListener("input", render);
  input.addEventListener("focus", function () {
    if (input.value.trim()) render();
  });
  input.addEventListener("blur", function () {
    window.setTimeout(closeList, 120);
  });
  input.addEventListener("keydown", function (event) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (list.hidden) render();
      if (!hits.length) return;
      event.preventDefault();
      var step = event.key === "ArrowDown" ? 1 : -1;
      mark((active + step + hits.length) % hits.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (active >= 0) choose(active);
    } else if (event.key === "Escape") {
      if (input.value) input.value = "";
      closeList();
      input.blur();
    }
  });

  /* "/" or Ctrl+K from anywhere in the editor that isn't a text box. */
  document.addEventListener("keydown", function (event) {
    var target = event.target;
    var typing = target && (/^(input|textarea|select)$/i.test(target.tagName) || target.isContentEditable);
    var wants = (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k");
    if (!wants || document.querySelector("dialog[open]")) return;
    var visible = input.offsetParent !== null;
    if (!visible) return;
    event.preventDefault();
    input.focus();
    input.select();
  });

  // ------------------------------------------------------------------------------------------------ the family search
  function place() {
    var rect = E.blocks.flyoutShape();
    if (!rect) return;
    var home = stage.getBoundingClientRect();
    box.style.left = rect.left - home.left + "px";
    box.style.top = rect.top - home.top + "px";
    box.style.width = rect.width + "px";
  }

  var typing = false; /* the family box has (or just had) focus: Blockly closes its list when focus leaves it, so we reopen it */

  function showFor() {
    family = E.blocks.currentFamily();
    if (!family) return hideBox();
    var refocus = typing;
    E.blocks.resetFilters(family);
    finput.value = E.blocks.filterOf(family);
    fclear.hidden = !finput.value;
    box.hidden = false;
    place();
    window.requestAnimationFrame(place);
    if (refocus) {
      finput.focus();
      finput.setSelectionRange(finput.value.length, finput.value.length);
      typing = false;
    }
  }

  function hideBox() {
    box.hidden = true;
    if (family) E.blocks.setFilter(family, "", true);
    family = null;
    finput.value = "";
  }

  E.bus.on("flyout", function (open) {
    if (open) {
      window.setTimeout(showFor, 0);
      return;
    }
    if (typing && family) {
      var again = family;
      window.setTimeout(function () {
        E.blocks.selectFamily(again);
      }, 0);
      return;
    }
    hideBox();
  });
  finput.addEventListener("pointerdown", function () {
    typing = true;
  });
  finput.addEventListener("focus", function () {
    typing = true;
  });
  finput.addEventListener("blur", function () {
    window.setTimeout(function () {
      if (document.activeElement !== finput) typing = false;
    }, 250);
  });
  window.addEventListener("resize", function () {
    if (!box.hidden) place();
  });

  finput.addEventListener("input", function () {
    fclear.hidden = !finput.value;
    if (family) E.blocks.setFilter(family, finput.value.trim());
    window.requestAnimationFrame(place);
  });
  finput.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      if (finput.value) {
        finput.value = "";
        finput.dispatchEvent(new Event("input"));
      } else {
        typing = false; /* a real close: don't bring the list back */
        finput.blur();
        E.blocks.closeFlyout();
      }
    }
  });
  fclear.addEventListener("click", function () {
    finput.value = "";
    finput.dispatchEvent(new Event("input"));
    finput.focus();
  });
})();
