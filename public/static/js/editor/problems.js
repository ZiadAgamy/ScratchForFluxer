/* The Problems tab: what is wrong with the blocks, in plain words. Clicking a problem finds the block. */
(function () {
  "use strict";

  var E = window.sffEditor;
  var el = E.el;

  var refs = {};
  var latest = [];

  function item(problem) {
    var isError = problem.level === "error";
    var node = el(
      problem.block ? "button" : "div",
      {
        class: "ed-problem is-" + (isError ? "error" : "warn"),
        type: problem.block ? "button" : null,
        title: problem.block ? "Show the block" : null,
      },
      [
        E.icon(isError ? "x-circle" : "warning"),
        el("span", { class: "ed-problem-text", text: problem.message }),
        problem.block ? E.icon("crosshair", "ed-problem-go") : null,
      ]
    );
    if (problem.block) {
      node.addEventListener("click", function () {
        E.bus.emit("problem:pick", problem.block);
      });
    }
    return node;
  }

  function counts(list) {
    var errors = list.filter(function (problem) {
      return problem.level === "error";
    }).length;
    return { errors: errors, warnings: list.length - errors };
  }

  function render(compile) {
    latest = compile.diagnostics || [];
    var total = latest.length;
    var number = counts(latest);

    E.clear(refs.list);
    if (!total) {
      refs.list.appendChild(
        el("div", { class: "ed-allgood" }, [
          E.icon("check-circle"),
          el("strong", { text: "No problems" }),
          el("p", { text: "Your blocks are ready to run." }),
        ])
      );
    } else {
      /* errors first, then warnings, each in the order the server found them */
      latest
        .filter(function (problem) { return problem.level === "error"; })
        .concat(latest.filter(function (problem) { return problem.level !== "error"; }))
        .forEach(function (problem) {
          refs.list.appendChild(item(problem));
        });
    }

    refs.badge.hidden = !total;
    refs.badge.textContent = String(total);
    refs.badge.classList.toggle("ed-count-error", number.errors > 0);

    refs.pill.hidden = !total;
    if (total) {
      refs.pill.textContent = "";
      refs.pill.appendChild(E.icon(number.errors ? "x-circle" : "warning"));
      refs.pill.appendChild(
        el("span", { text: number.errors ? E.plural(number.errors, "problem") : E.plural(number.warnings, "warning") })
      );
      refs.pill.classList.toggle("is-error", number.errors > 0);
    }
  }

  function init() {
    refs = {
      list: document.getElementById("problems-list"),
      badge: document.getElementById("problems-count"),
      pill: document.getElementById("ed-problems-pill"),
    };
    refs.pill.addEventListener("click", function () {
      E.bus.emit("tab", "problems");
    });
  }

  E.problems = {
    init: init,
    render: render,
    counts: function () {
      return counts(latest);
    },
  };
})();
