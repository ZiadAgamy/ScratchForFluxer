/* A small Python colouriser for the code view. It only has to read what our compiler writes (short lines,
   double-quoted strings, no continuation tricks), but it copes with anything: unknown text is shown as it is. */
(function () {
  "use strict";

  var E = window.sffEditor;

  var KEYWORDS = {};
  (
    "and as assert async await break class continue def del elif else except finally for from global if import in is " +
    "lambda nonlocal not or pass raise return try while with yield"
  )
    .split(" ")
    .forEach(function (word) {
      KEYWORDS[word] = true;
    });

  var CONSTANTS = { True: true, False: true, None: true };

  var BUILTINS = {};
  (
    "print len str int float bool list dict set tuple range min max sum abs round sorted enumerate zip isinstance " +
    "open super type repr any all reversed map filter"
  )
    .split(" ")
    .forEach(function (word) {
      BUILTINS[word] = true;
    });

  var NUMBER = /^(?:0[xX][0-9a-fA-F_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?|\.\d[\d_]*(?:[eE][+-]?\d+)?)/;
  var IDENT = /^[A-Za-z_][A-Za-z0-9_]*/;
  var STRING_PREFIX = /^(?:[rRbBuUfF]{1,2})(?=["'])/;

  /* The end of the string that starts at `start` (a quote), or the end of the line if it never closes. */
  function stringEnd(line, start) {
    var quote = line.charAt(start);
    var triple = line.substr(start, 3) === quote + quote + quote;
    var i = start + (triple ? 3 : 1);
    while (i < line.length) {
      var ch = line.charAt(i);
      if (ch === "\\") {
        i += 2;
        continue;
      }
      if (triple ? line.substr(i, 3) === quote + quote + quote : ch === quote) return i + (triple ? 3 : 1);
      i += 1;
    }
    return line.length;
  }

  /* [kind, text] pairs for one line. Kinds: kw, const, builtin, str, num, com, fn, deco, op, plain. */
  function tokens(line) {
    var out = [];
    var i = 0;
    var previousWord = "";
    var afterDot = false;
    while (i < line.length) {
      var rest = line.slice(i);
      var ch = line.charAt(i);
      var match;

      if (ch === "#") {
        out.push(["com", rest]);
        break;
      }
      if (ch === " " || ch === "\t") {
        var spaces = /^[ \t]+/.exec(rest)[0];
        out.push(["plain", spaces]);
        i += spaces.length;
        continue;
      }
      if (ch === '"' || ch === "'" || (STRING_PREFIX.test(rest) && (match = STRING_PREFIX.exec(rest)))) {
        var from = i;
        if (ch !== '"' && ch !== "'") i += match[0].length;
        var end = stringEnd(line, i);
        out.push(["str", line.slice(from, end)]);
        i = end;
        previousWord = "";
        afterDot = false;
        continue;
      }
      if ((match = NUMBER.exec(rest)) && !afterDot) {
        out.push(["num", match[0]]);
        i += match[0].length;
        previousWord = "";
        continue;
      }
      if ((match = IDENT.exec(rest))) {
        var word = match[0];
        var next = line.charAt(i + word.length);
        var kind = "plain";
        if (previousWord === "def" || previousWord === "class") kind = "fn";
        else if (previousWord === "@" || (afterDot && previousWord === "@.")) kind = "deco";
        else if (!afterDot && CONSTANTS[word]) kind = "const";
        else if (!afterDot && KEYWORDS[word]) kind = "kw";
        else if (!afterDot && BUILTINS[word] && next === "(") kind = "builtin";
        else if (next === "(") kind = "call";
        out.push([kind, word]);
        i += word.length;
        previousWord = afterDot && previousWord === "@" ? "@." : word;
        if (kind === "deco") previousWord = "@.";
        afterDot = false;
        continue;
      }
      /* punctuation: one character, and remember "@" and "." for the next word */
      out.push(["op", ch]);
      if (ch === "@") previousWord = "@";
      else if (ch === ".") {
        afterDot = true;
        if (previousWord === "@" || previousWord === "@.") previousWord = "@.";
        else previousWord = ".";
      } else previousWord = "";
      if (ch !== ".") afterDot = false;
      i += 1;
    }
    return out;
  }

  /* One line as a DocumentFragment of coloured spans. */
  function colourise(line) {
    var fragment = document.createDocumentFragment();
    tokens(line).forEach(function (token) {
      if (token[0] === "plain") {
        fragment.appendChild(document.createTextNode(token[1]));
      } else {
        var span = document.createElement("span");
        span.className = "py-" + token[0];
        span.textContent = token[1];
        fragment.appendChild(span);
      }
    });
    return fragment;
  }

  E.python = { tokens: tokens, colourise: colourise };
})();
