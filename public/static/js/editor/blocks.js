/* The block workspace: registers the blocks from /api/blocks with Blockly, sets up the theme and the toolbox, and
   keeps the pieces that are not part of Blockly itself (variables, the file dropdown, problem markers, selection).
   Nothing here decides what a block does. The server says which blocks exist, and writes the Python from them. */
(function () {
  "use strict";

  var E = window.sffEditor;
  var B = window.Blockly;
  var el = E.el;

  /* The most a text block holds. The server's limit is the same number (tests/test_text_limits.py keeps them equal). */
  var TEXT_LIMIT = 2000;

  var payload = null;
  var workspace = null;
  var loading = false;
  var categories = {};
  var lockedTypes = [];
  var problemBlocks = {};

  // ------------------------------------------------------------------------------------------------ colours
  function channels(hex) {
    var value = hex.replace("#", "");
    return [parseInt(value.slice(0, 2), 16), parseInt(value.slice(2, 4), 16), parseInt(value.slice(4, 6), 16)];
  }

  function toHex(rgb) {
    return (
      "#" +
      rgb
        .map(function (part) {
          var n = Math.max(0, Math.min(255, Math.round(part)));
          return (n < 16 ? "0" : "") + n.toString(16);
        })
        .join("")
    );
  }

  /* `hex` blended towards `other` by `amount` (0 = hex, 1 = other). */
  function mix(hex, other, amount) {
    var a = channels(hex);
    var b = channels(other);
    return toHex(
      a.map(function (part, index) {
        return part + (b[index] - part) * amount;
      })
    );
  }

  // ------------------------------------------------------------------------------------------------ the theme
  function buildTheme() {
    var blockStyles = {};
    var categoryStyles = {};
    payload.categories.forEach(function (category) {
      blockStyles[category.id] = {
        colourPrimary: category.color,
        colourSecondary: mix(category.color, "#000000", 0.3),
        colourTertiary: mix(category.color, "#000000", 0.5),
      };
      categoryStyles[category.id] = { colour: category.color };
    });
    var bot = categories.bot.color;
    /* The two blocks that start and end every bot share the Bot colour. */
    blockStyles.config = {
      colourPrimary: bot,
      colourSecondary: mix(bot, "#000000", 0.3),
      colourTertiary: mix(bot, "#000000", 0.5),
    };
    /* Text and number boxes: white pills (the dark ink on top is set in CSS). */
    blockStyles.literal = { colourPrimary: "#ffffff", colourSecondary: "#e7e5ee", colourTertiary: "#9d99ad" };

    return B.Theme.defineTheme("sff", {
      name: "sff",
      base: B.Themes.Zelos,
      blockStyles: blockStyles,
      categoryStyles: categoryStyles,
      fontStyle: { family: '"IBM Plex Sans", system-ui, sans-serif', weight: "600", size: 12 },
      startHats: false,
      componentStyles: {
        workspaceBackgroundColour: "#0f0e11",
        toolboxBackgroundColour: "#17161b",
        toolboxForegroundColour: "#f3f2f6",
        flyoutBackgroundColour: "#1d1c22",
        flyoutForegroundColour: "#f3f2f6",
        flyoutOpacity: 1,
        scrollbarColour: "#4b4958",
        scrollbarOpacity: 0.55,
        insertionMarkerColour: "#ffffff",
        insertionMarkerOpacity: 0.3,
        markerColour: "#4589ff",
        cursorColour: "#4589ff",
        selectedGlowColour: "#4589ff",
        selectedGlowOpacity: 0.6,
        replacementGlowColour: "#ffffff",
        replacementGlowOpacity: 0.5,
      },
    });
  }

  // ------------------------------------------------------------------------------------------------ icons drawn inside blocks
  var glyphs = {};
  /* The character a Phosphor icon is drawn with, read from the icon font's own stylesheet. */
  function glyph(name) {
    if (glyphs[name] === undefined) {
      var probe = document.createElement("i");
      probe.className = "ph-fill ph-" + name;
      probe.style.cssText = "position:absolute;visibility:hidden";
      document.body.appendChild(probe);
      var content = window.getComputedStyle(probe, "::before").content;
      probe.remove();
      glyphs[name] = content && content !== "none" && content !== "normal" ? content.replace(/^["']|["']$/g, "") : "";
    }
    return glyphs[name];
  }

  function iconField(name) {
    return new B.FieldLabel(glyph(name), "sff-glyph");
  }

  // ------------------------------------------------------------------------------------------------ the file dropdown
  var UPLOAD = "__upload__";
  var NONE_CHOSEN = "choose a file";

  class FieldAsset extends B.FieldDropdown {
    constructor(kinds, value) {
      super(function () {
        return [[NONE_CHOSEN, ""]];
      });
      this.kinds = kinds || [];
      this.setValue(typeof value === "string" ? value : this.firstFile());
    }

    static fromJson(options) {
      return new FieldAsset(options.kinds, options.value);
    }

    firstFile() {
      var names = E.files ? E.files.names(this.kinds) : [];
      return names.length ? names[0] : "";
    }

    /* The files this block can use, plus the one it names now (even if that file is gone) so the choice is never lost. */
    getOptions() {
      var names = E.files ? E.files.names(this.kinds || []) : [];
      var options = names.map(function (name) {
        return [name, name];
      });
      var value = this.value_;
      if (!value) options.unshift([NONE_CHOSEN, ""]);
      else if (names.indexOf(value) === -1) options.push([value, value]);
      options.push(B.FieldDropdown.SEPARATOR, ["Upload a file…", UPLOAD]);
      this.generatedOptions = options;
      return options;
    }

    doClassValidation_(value) {
      return typeof value === "string" && value !== UPLOAD ? value : null;
    }

    onItemSelected_(menu, item) {
      if (item.getValue() === UPLOAD) {
        var field = this;
        E.files.pick(this.kinds).then(function (added) {
          if (added.length) field.setValue(added[added.length - 1]);
        });
        return;
      }
      super.onItemSelected_(menu, item);
    }

    getDisplayText_() {
      return this.value_ ? super.getDisplayText_() : NONE_CHOSEN;
    }

    render_() {
      super.render_();
      var group = this.fieldGroup_;
      if (!group) return;
      var missing = !!this.value_ && !(E.files && E.files.find(this.value_));
      var empty = !this.value_;
      group.classList.toggle("sff-missing", missing);
      group.classList.toggle("sff-empty", empty);
    }

    /* Called when files are added or removed: pick up the new list and redraw. */
    refresh() {
      this.generatedOptions = null;
      this.doValueUpdate_(this.value_);
      this.forceRerender();
    }
  }

  // ------------------------------------------------------------------------------------------------ the category buttons
  /* Blockly's category row, drawn as a coloured icon with its name underneath. */
  class SffCategory extends B.ToolboxCategory {
    createIconDom_() {
      var wrap = document.createElement("span");
      wrap.className = "sff-cat-icon";
      var name = this.toolboxItemDef_.icon;
      if (name) wrap.appendChild(E.icon(name));
      return wrap;
    }

    addColourBorder_(colour) {
      if (this.htmlDiv_) this.htmlDiv_.style.setProperty("--cat", colour || "#8b8998");
    }

    setSelected(selected) {
      if (!this.rowDiv_) return;
      this.rowDiv_.classList.toggle("blocklyToolboxSelected", !!selected);
      this.htmlDiv_.setAttribute("aria-selected", selected ? "true" : "false");
    }
  }

  // ------------------------------------------------------------------------------------------------ registering
  function defineBlocks() {
    B.fieldRegistry.register("field_sff_asset", FieldAsset);
    E.embeds.configure(payload.embed, payload.codenames);
    E.embeds.register();
    B.defineBlocksWithJsonArray(payload.blocks);

    /* A "hat" has a curved top and nothing can snap on above it. Blockly ignores a "hat" key in the JSON, so it is set
       when the block is made. */
    payload.hats.concat(["sff_bot_setup"]).forEach(function (type) {
      var definition = B.Blocks[type];
      var original = definition.init;
      definition.init = function () {
        original.call(this);
        this.hat = "cap";
      };
    });

    function addIcon(type, name) {
      var definition = B.Blocks[type];
      var original = definition.init;
      definition.init = function () {
        original.call(this);
        this.inputList[0].insertFieldAt(0, iconField(name));
      };
    }
    addIcon("sff_bot_setup", "robot");
    addIcon("sff_bot_login", "lock-key");
  }

  function trimMessages() {
    var Msg = B.Msg;
    Msg.NEW_VARIABLE = "Make a variable…";
    Msg.RENAME_VARIABLE = "Rename “%1”…";
    Msg.RENAME_VARIABLE_TITLE = "Rename “%1” to:";
    Msg.DELETE_VARIABLE = "Delete “%1”";
    Msg.DELETE_VARIABLE_CONFIRMATION = "%1 blocks use “%2”. Delete it and those blocks?";
    Msg.VARIABLE_ALREADY_EXISTS = "There is already something called “%1”.";
    Msg.VARIABLE_ALREADY_EXISTS_FOR_ANOTHER_TYPE = "There is already something called “%1”.";
    Msg.DELETE_ALL_BLOCKS = "Delete all %1 blocks?";
    Msg.CLEAN_UP = "Tidy up the blocks";
    Msg.COLLAPSE_ALL = "Collapse all blocks";
    Msg.EXPAND_ALL = "Expand all blocks";
    Msg.DUPLICATE_BLOCK = "Duplicate";
    Msg.DISABLE_BLOCK = "Turn this block off";
    Msg.ENABLE_BLOCK = "Turn this block on";
    Msg.DELETE_BLOCK = "Delete this block";
    Msg.DELETE_X_BLOCKS = "Delete %1 blocks";
    Msg.COLLAPSE_BLOCK = "Collapse this block";
    Msg.EXPAND_BLOCK = "Expand this block";
  }

  /* Things the saved blocks cannot hold (comments, "external inputs", help pages) are left out of the right-click menu. */
  function trimContextMenu() {
    var registry = B.ContextMenuRegistry.registry;
    ["blockComment", "blockInline", "blockHelp"].forEach(function (id) {
      try {
        registry.unregister(id);
      } catch (_) {
        /* already gone */
      }
    });
  }

  function hookDialogs() {
    B.dialog.setPrompt(function (message, defaultValue, callback) {
      /* On a touch screen, Blockly edits the text of a block in a dialog that asks "Change value:". That one is for
         text people write (a rule, a welcome), so it takes as much as a text block can hold and keeps it as typed.
         A box with a shorter limit of its own (a command name, say) still cuts the text to that limit. */
      var editingText = /change value/i.test(String(message));
      var options = editingText
        ? {
            title: "Edit text",
            label: "Text",
            value: defaultValue,
            maxLength: TEXT_LIMIT,
            trim: false,
            hint: "Up to " + TEXT_LIMIT + " characters.",
            confirmLabel: "Done",
            validate: function () { return ""; },
          }
        : { title: "Rename", label: message, value: defaultValue, validate: function (v) { return v ? "" : "Type a name first."; } };
      E.dialogs.prompt(options).then(callback);
    });
    B.dialog.setConfirm(function (message, callback) {
      E.dialogs.confirm({ title: "Are you sure?", message: message, confirmLabel: "Yes", danger: true }).then(callback);
    });
    B.dialog.setAlert(function (message, callback) {
      E.dialogs.alert({ title: "Heads up", message: message }).then(function () {
        if (callback) callback();
      });
    });
    B.dialog.setToast(function (_workspace, options) {
      window.sff.toast((options && options.message) || "", "info");
    });
  }

  // ------------------------------------------------------------------------------------------------ variables
  function variableMap() {
    return workspace.getVariableMap();
  }

  function normal(name) {
    return String(name).split(/\s+/).filter(Boolean).join(" ").toLowerCase();
  }

  /* The sentence to show if `name` can't be used for a new variable or list ("" if it's fine). */
  function nameProblem(name) {
    var trimmed = String(name || "").split(/\s+/).filter(Boolean).join(" ");
    if (!trimmed) return "Give it a name first.";
    if (trimmed.length > 60) return "That name is too long. Use 60 characters or fewer.";
    var taken = variableMap()
      .getAllVariables()
      .some(function (variable) {
        return normal(variable.getName()) === normal(trimmed);
      });
    if (taken) return "There is already a variable or list called “" + trimmed + "”. Pick another name.";
    return "";
  }

  async function makeVariable(kind) {
    var isList = kind === "List";
    /* The dialog takes the focus, and Blockly closes the open block list when that happens. Open it again afterwards,
       so the new variable's blocks are right there. */
    var toolbox = workspace.getToolbox();
    var shown = toolbox && toolbox.getSelectedItem();
    var name = await E.dialogs.prompt({
      title: isList ? "Make a list" : "Make a variable",
      label: "Name",
      hint: isList ? "A list keeps many values together, like a list of jokes." : "A variable remembers one value, like a score.",
      maxLength: 60,
      confirmLabel: isList ? "Make list" : "Make variable",
      validate: nameProblem,
    });
    var variable = null;
    if (name) variable = variableMap().createVariable(name.split(/\s+/).filter(Boolean).join(" "), kind);
    if (toolbox && shown) {
      try {
        if (toolbox.getSelectedItem() === shown) toolbox.refreshSelection();
        else toolbox.setSelectedItem(shown);
      } catch (_) {
        /* the list simply stays closed */
      }
    }
    return variable;
  }

  /* Delete one of the person's variables or lists, and the blocks that use it (after asking). */
  async function deleteVariable(kind) {
    var noun = kind === "List" ? "list" : "variable";
    var found = variablesOf([kind]);
    if (!found.length) return;
    var toolbox = workspace.getToolbox();
    var shown = toolbox && toolbox.getSelectedItem();
    var chosen = found[0];
    if (found.length > 1) {
      var id = await E.dialogs.choose({
        title: "Delete a " + noun,
        message: "Which one?",
        choices: found.map(function (variable) {
          return { id: variable.getId(), label: variable.getName(), kind: "ghost" };
        }),
        cancelLabel: "Cancel",
      });
      if (!id) return;
      chosen = variableMap().getVariableById(id);
    }
    if (!chosen) return;
    var uses = B.Variables.getVariableUsesById(workspace, chosen.getId()).length;
    var sentence = uses
      ? uses + (uses === 1 ? " block uses" : " blocks use") + " “" + chosen.getName() + "”. Delete it and " + (uses === 1 ? "that block?" : "those blocks?")
      : "Delete the " + noun + " “" + chosen.getName() + "”?";
    var yes = await E.dialogs.confirm({ title: "Delete " + noun, message: sentence, confirmLabel: "Delete", danger: true });
    if (yes) variableMap().deleteVariable(chosen);
    if (toolbox && shown) {
      try {
        if (toolbox.getSelectedItem() === shown) toolbox.refreshSelection();
        else toolbox.setSelectedItem(shown);
      } catch (_) {
        /* the list simply stays closed */
      }
    }
  }

  // ------------------------------------------------------------------------------------------------ the toolbox
  function variablesOf(kinds) {
    return variableMap()
      .getAllVariables()
      .filter(function (variable) {
        return kinds.indexOf(variable.getType()) !== -1;
      })
      .sort(function (a, b) {
        return a.getName().toLowerCase() < b.getName().toLowerCase() ? -1 : 1;
      });
  }

  /* Which kind of thing a block is waiting for: its own variable or a list. */
  function nounFor(kinds) {
    return kinds.indexOf("") !== -1 ? "variable" : "list";
  }

  /* What the flyout of one category shows. Blocks that need a variable get one of the person's own; if there is none the
     block waits until they have made one (opening a flyout never makes variables up). */
  function flyoutContents(categoryId) {
    var items = [];
    var category = categories[categoryId];
    var own = null; /* the thing this category already has a "Make a ..." button for */
    if (category.button) {
      own = category.button.kind === "List" ? "list" : "variable";
      items.push({ kind: "button", text: category.button.label, callbackKey: own === "list" ? "SFF_MAKE_LIST" : "SFF_MAKE_VARIABLE" });
      if (variablesOf([own === "list" ? "List" : ""]).length) {
        items.push({ kind: "button", text: "Delete a " + own, callbackKey: own === "list" ? "SFF_DELETE_LIST" : "SFF_DELETE_VARIABLE" });
      }
    }
    var waiting = { variable: false, list: false };
    (payload.toolbox[categoryId] || []).forEach(function (entry) {
      var block = { kind: "block", type: entry.type };
      if (entry.inputs) block.inputs = entry.inputs;
      if (!entry.variable) {
        items.push(block);
        return;
      }
      var found = variablesOf(entry.variable.kinds);
      if (entry.variable.each) {
        found.forEach(function (variable) {
          var copy = Object.assign({}, block);
          copy.fields = {};
          copy.fields[entry.variable.field] = { id: variable.getId() };
          items.push(copy);
        });
        return;
      }
      if (!found.length) {
        waiting[nounFor(entry.variable.kinds)] = true;
        return;
      }
      block.fields = {};
      block.fields[entry.variable.field] = { id: found[0].getId() };
      items.push(block);
    });
    var needed = ["variable", "list"].filter(function (noun) {
      return waiting[noun];
    });
    if (needed.length) {
      items.push({ kind: "label", text: "Make " + needed.map(function (noun) { return "a " + noun; }).join(" and ") + " to see more blocks here" });
      needed.forEach(function (noun) {
        if (noun !== own) {
          items.push({ kind: "button", text: "Make a " + noun, callbackKey: noun === "list" ? "SFF_MAKE_LIST" : "SFF_MAKE_VARIABLE" });
        }
      });
    }
    return items;
  }

  function toolboxDefinition() {
    return {
      kind: "categoryToolbox",
      contents: payload.categories.map(function (category) {
        return {
          kind: "category",
          name: category.name,
          categorystyle: category.id,
          custom: "SFF_" + category.id.toUpperCase(),
          icon: category.icon,
          toolboxitemid: "cat-" + category.id,
        };
      }),
    };
  }

  // ------------------------------------------------------------------------------------------------ the workspace
  function create(container) {
    var theme = buildTheme();
    var limits = {};
    lockedTypes.forEach(function (type) {
      limits[type] = 1;
    });

    workspace = B.inject(container, {
      toolbox: toolboxDefinition(),
      theme: theme,
      renderer: "zelos",
      sounds: false,
      media: "/static/vendor/blockly/media/",
      trashcan: true,
      comments: false,
      collapse: true,
      disable: true,
      maxInstances: limits,
      zoom: {
        controls: false,
        wheel: true,
        startScale: container.clientWidth < 700 ? 0.7 : 0.85, /* blocks start smaller on a phone */
        maxScale: 1.6,
        minScale: 0.35,
        scaleSpeed: 1.15,
        pinch: true,
      },
      move: { scrollbars: true, drag: true, wheel: true },
      grid: { spacing: 28, length: 2, colour: "#26242d", snap: false },
    });

    payload.categories.forEach(function (category) {
      workspace.registerToolboxCategoryCallback("SFF_" + category.id.toUpperCase(), function () {
        return flyoutContents(category.id);
      });
    });
    workspace.registerButtonCallback("SFF_MAKE_VARIABLE", function () {
      makeVariable("");
    });
    workspace.registerButtonCallback("SFF_MAKE_LIST", function () {
      makeVariable("List");
    });
    workspace.registerButtonCallback("SFF_DELETE_VARIABLE", function () {
      deleteVariable("");
    });
    workspace.registerButtonCallback("SFF_DELETE_LIST", function () {
      deleteVariable("List");
    });

    workspace.addChangeListener(onEvent);
    return workspace;
  }

  function onEvent(event) {
    if (event.type === B.Events.SELECTED) {
      E.bus.emit("select", event.newElementId || null);
      return;
    }
    if (event.type === B.Events.TOOLBOX_ITEM_SELECT) {
      E.bus.emit("flyout", !!event.newItem); /* a block list opened (true) or closed (false) */
      return;
    }
    if (loading) return;
    if (event.type === B.Events.VAR_CREATE || event.type === B.Events.VAR_DELETE || event.type === B.Events.VAR_RENAME) {
      workspace.refreshToolboxSelection();
    }
    if (event.isUiEvent) return;
    if (event.type === B.Events.FINISHED_LOADING) return;
    E.bus.emit("change", event);
  }

  /* Put `state` (what the server sent) into the workspace without it counting as a change. */
  function load(state) {
    loading = true;
    B.Events.disable();
    try {
      workspace.clear();
      B.serialization.workspaces.load(state, workspace);
    } finally {
      B.Events.enable();
      loading = false;
    }
    workspace.clearUndo();
    problemBlocks = {};
    refreshFiles();
    scrollToStart();
    E.bus.emit("undo-state");
  }

  function save() {
    var state = B.serialization.workspaces.save(workspace);
    if (!state.variables) state.variables = []; /* Blockly leaves the key out when there are none; the server always has it */
    return state;
  }

  /* Show the first block at the top left, with a little room around it. */
  function scrollToStart() {
    var box = workspace.getBlocksBoundingBox();
    if (!box || (box.right === box.left && box.bottom === box.top)) return;
    var scale = workspace.getScale();
    var margin = window.innerWidth < 900 ? 18 : 48; /* room left of the first block, less on a phone */
    workspace.scroll(margin - box.left * scale, 36 - box.top * scale);
  }

  // ------------------------------------------------------------------------------------------------ problems on blocks
  /* diagnostics: [{ level, message, block }]. A block with a problem gets a warning sign and a red outline. */
  function showProblems(diagnostics) {
    if (!workspace) return;
    var byBlock = {};
    (diagnostics || []).forEach(function (item) {
      if (!item.block) return;
      (byBlock[item.block] = byBlock[item.block] || []).push(item);
    });
    Object.keys(problemBlocks).forEach(function (id) {
      if (byBlock[id]) return;
      var gone = workspace.getBlockById(id);
      if (gone) {
        gone.setWarningText(null);
        gone.removeClass("sff-error");
      }
    });
    Object.keys(byBlock).forEach(function (id) {
      var block = workspace.getBlockById(id);
      if (!block) return;
      var text = byBlock[id]
        .map(function (item) {
          return item.message;
        })
        .join("\n");
      block.setWarningText(text);
      if (byBlock[id].some(function (item) { return item.level === "error"; })) block.addClass("sff-error");
      else block.removeClass("sff-error");
    });
    problemBlocks = byBlock;
  }

  // ------------------------------------------------------------------------------------------------ finding blocks
  function reveal(blockId) {
    var block = blockId && workspace.getBlockById(blockId);
    if (!block) return false;
    workspace.centerOnBlock(blockId, true);
    B.common.setSelected(block);
    return true;
  }

  var cursor = {};
  /* Select the next block that uses the file called `name` (again and again to go through them all). */
  function revealFile(name) {
    var users = workspace.getAllBlocks(true).filter(function (block) {
      return block.inputList.some(function (input) {
        return input.fieldRow.some(function (field) {
          return field instanceof FieldAsset && field.getValue() === name;
        });
      });
    });
    if (!users.length) return false;
    cursor[name] = ((cursor[name] === undefined ? -1 : cursor[name]) + 1) % users.length;
    return reveal(users[cursor[name]].id);
  }

  function refreshFiles() {
    if (!workspace) return;
    workspace.getAllBlocks(false).forEach(function (block) {
      block.inputList.forEach(function (input) {
        input.fieldRow.forEach(function (field) {
          if (field instanceof FieldAsset) field.refresh();
        });
      });
    });
  }

  // ------------------------------------------------------------------------------------------------ small controls
  function resize() {
    if (workspace) B.svgResize(workspace);
  }

  function zoom(direction) {
    workspace.zoomCenter(direction);
  }

  function fit() {
    workspace.zoomToFit();
  }

  function tidy() {
    workspace.cleanUp();
  }

  function undo(redo) {
    workspace.undo(!!redo);
  }

  function canUndo() {
    return workspace ? workspace.getUndoStack().length > 0 : false;
  }

  function canRedo() {
    return workspace ? workspace.getRedoStack().length > 0 : false;
  }

  // ------------------------------------------------------------------------------------------------ start
  function init(blocksPayload) {
    payload = blocksPayload;
    lockedTypes = payload.locked || [];
    payload.categories.forEach(function (category) {
      categories[category.id] = category;
    });
    trimMessages();
    trimContextMenu();
    B.registry.register(B.registry.Type.TOOLBOX_ITEM, B.ToolboxCategory.registrationName, SffCategory, true);
    defineBlocks();
    hookDialogs();
  }

  E.blocks = {
    init: init,
    create: create,
    load: load,
    save: save,
    showProblems: showProblems,
    reveal: reveal,
    revealFile: revealFile,
    refreshFiles: refreshFiles,
    resize: resize,
    zoom: zoom,
    fit: fit,
    tidy: tidy,
    undo: undo,
    canUndo: canUndo,
    canRedo: canRedo,
    makeVariable: makeVariable,
    workspace: function () {
      return workspace;
    },
  };

  /* The file list changed: the dropdowns on the blocks follow. */
  E.bus.on("files", refreshFiles);
})();
