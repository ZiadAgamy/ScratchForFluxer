/* The /docs guide: highlight where you are in the side menu, search the blocks, copy code, and open an answer when a
   link points at it. No dependencies. The page reads fine without any of this. */
(function () {
  "use strict";

  var nav = document.getElementById("docs-nav");
  var main = document.getElementById("docs-main");
  if (!nav || !main) return;

  /* ------------------------------------------------------------------ the menu on small screens */

  var mirror = document.querySelector(".docs-toc-mobile [data-mirror]");
  var mobile = document.querySelector(".docs-toc-mobile");
  if (mirror) {
    Array.prototype.forEach.call(nav.children, function (child) {
      if (child.classList.contains("docs-search")) return;
      mirror.appendChild(child.cloneNode(true));
    });
    mirror.addEventListener("click", function (event) {
      if (event.target.closest && event.target.closest("a") && mobile) mobile.open = false;
    });
  }

  /* ------------------------------------------------------------------ where you are */

  var links = Array.prototype.slice.call(document.querySelectorAll('.docs-nav a[href^="#"], .docs-toc-body a[href^="#"]'));
  var byId = {};
  links.forEach(function (link) {
    var id = link.getAttribute("href").slice(1);
    (byId[id] = byId[id] || []).push(link);
  });

  var sections = Object.keys(byId)
    .map(function (id) {
      return document.getElementById(id);
    })
    .filter(Boolean);
  var current = null;

  function mark(id) {
    if (id === current) return;
    current = id;
    links.forEach(function (link) {
      if (link.getAttribute("href") === "#" + id) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    });
    var active = byId[id] && byId[id][0];
    if (active && nav.scrollHeight > nav.clientHeight) {
      var box = nav.getBoundingClientRect();
      var rect = active.getBoundingClientRect();
      if (rect.top < box.top + 40 || rect.bottom > box.bottom - 40) active.scrollIntoView({ block: "center" });
    }
  }

  function whereAmI() {
    var line = window.innerHeight * 0.3;
    var best = sections[0];
    sections.forEach(function (section) {
      if (section.getBoundingClientRect().top <= line) best = section;
    });
    if (best) mark(best.id);
  }

  var waiting = false;
  window.addEventListener(
    "scroll",
    function () {
      if (waiting) return;
      waiting = true;
      window.requestAnimationFrame(function () {
        waiting = false;
        whereAmI();
      });
    },
    { passive: true }
  );
  whereAmI();

  /* ------------------------------------------------------------------ answers that open from a link */

  function openTarget() {
    var id = decodeURIComponent((location.hash || "").slice(1));
    var target = id && document.getElementById(id);
    if (!target) return;
    var box = target.closest("details");
    if (box) {
      box.open = true;
      target.scrollIntoView();
    }
  }
  window.addEventListener("hashchange", openTarget);
  openTarget();

  /* ------------------------------------------------------------------ copy buttons on code */

  Array.prototype.forEach.call(document.querySelectorAll(".codebox"), function (box) {
    var code = box.querySelector("code");
    if (!code) return;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "copy-btn";
    var icon = document.createElement("i");
    icon.className = "ph-fill ph-copy";
    icon.setAttribute("aria-hidden", "true");
    var label = document.createElement("span");
    label.textContent = "Copy";
    button.appendChild(icon);
    button.appendChild(label);
    button.addEventListener("click", function () {
      var text = code.textContent;
      function done() {
        label.textContent = "Copied";
        window.setTimeout(function () {
          label.textContent = "Copy";
        }, 1600);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {
          label.textContent = "Press Ctrl+C";
        });
      } else {
        var range = document.createRange();
        range.selectNodeContents(code);
        var selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        label.textContent = "Press Ctrl+C";
      }
    });
    box.appendChild(button);
  });

  /* ------------------------------------------------------------------ search the blocks */

  var input = document.getElementById("docs-search-input");
  var none = document.getElementById("docs-no-results");
  var families = Array.prototype.slice.call(document.querySelectorAll(".family"));
  var jumped = false;

  function search() {
    var terms = input.value
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean);
    var anything = false;
    families.forEach(function (family) {
      var shown = 0;
      Array.prototype.forEach.call(family.querySelectorAll(".block-item"), function (item) {
        var words = item.getAttribute("data-words") || "";
        var match = terms.every(function (term) {
          return words.indexOf(term) !== -1;
        });
        item.hidden = !match;
        if (match) shown += 1;
      });
      /* with no search, every family shows; with one, only the families that have a match */
      family.hidden = terms.length > 0 && shown === 0;
      if (!family.hidden) anything = true;
      var id = family.getAttribute("data-family");
      links.forEach(function (link) {
        if (link.getAttribute("data-family") === id && link.parentElement) link.parentElement.hidden = family.hidden;
      });
    });
    if (none) none.hidden = terms.length === 0 || anything;
    if (terms.length && !jumped) {
      jumped = true;
      var reference = document.getElementById("blocks-reference");
      if (reference) reference.scrollIntoView();
    }
    if (!terms.length) jumped = false;
  }

  if (input) input.addEventListener("input", search);
})();
