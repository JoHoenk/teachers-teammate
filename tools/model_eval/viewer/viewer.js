/* The evaluation report: every number comes from the data files, this file only shows them.
 *
 * The page is one HTML file plus data files that assign to window.REPORT, so it opens from
 * file:// without a server.  Nothing here computes a metric: the scorecard, the heat map, the
 * error table and the F-score of a version are all in the data.
 */
(function () {
  "use strict";

  var REPORT = window.REPORT || {};
  var INDEX = REPORT.index || {
    inputs: [],
    variants: [],
    numbers: {},
    texts: [],
    cells: {},
    errors: [],
    eval_versions: [],
    deciding: "de+en",
    beta: 0.5,
    eval_notes: {},
    prompts: {},
  };
  // A text's data file adds itself to REPORT.text when it is loaded, so the table is the page's
  // own and exists before the first file does.
  REPORT.text = REPORT.text || {};
  var TEXTS = REPORT.text;

  var MEASURES = [
    { key: "recall", label: "Recall", digits: 2, scale: "share" },
    { key: "precision", label: "Precision", digits: 2, scale: "share" },
    { key: "f_beta", label: "F", digits: 2, scale: "share" },
    { key: "gleu", label: "GLEU", digits: 2, scale: "share" },
    { key: "alone", label: "Left alone", digits: 2, scale: "share" },
    { key: "explained", label: "Explained", digits: 0, scale: "share" },
    { key: "seconds_per_100_words", label: "s/100w", digits: 1, scale: "time" },
    { key: "failed", label: "Failed", digits: 0, scale: "count" },
  ];

  /* The language key of the row over the languages that decide: the mean of German and English. */
  var DECIDING = INDEX.deciding || "de+en";
  /* The F-score's weight, as the label of its column says it (0.5 reads F0.5). */
  var F_LABEL = "F" + (INDEX.beta === undefined ? 0.5 : INDEX.beta);

  MEASURES.forEach(function (measure) {
    if (measure.key === "f_beta") measure.label = F_LABEL;
  });

  var state = {
    screen: "overview",
    variants: {},
    language: DECIDING,
    evalVersion: null,
    diffOnly: false,
    band: "all",
    repeat: "all",
    mode: "read",
    layout: "stack",
    text: null,
    row: 0,
    filter: "all",
    drawer: null,
    expanded: {},
    pageVariants: [],
    errorGroup: null,
    sort: { key: "recall", desc: true },
    info: false,
    menuOpen: false,
  };

  if (INDEX.eval_versions && INDEX.eval_versions.length) {
    state.evalVersion = INDEX.eval_versions[INDEX.eval_versions.length - 1];
  }
  /* Only the newest eval version is ticked to begin with; the others are one click away in the
   * Variants menu. */
  INDEX.variants.forEach(function (variant) {
    state.variants[variant.id] =
      state.evalVersion === null || variant.eval_version === state.evalVersion;
  });
  /* One repeat at a time to begin with: every repeat of every variant as its own row buries the
   * variants.  The menu still offers "all", and the marks column says how many repeats agreed. */
  var firstRepeat = null;
  INDEX.variants.forEach(function (variant) {
    variant.repeats.forEach(function (repeat) {
      if (firstRepeat === null || repeat < firstRepeat) firstRepeat = repeat;
    });
  });
  if (firstRepeat !== null) state.repeat = String(firstRepeat);

  /* ── helpers ───────────────────────────────────────────────────────── */

  var LANGUAGE_NAMES = { de: "German", en: "English", fr: "French" };

  /* What a language key reads as: "German", or "German + English (these decide)" for de+en. */
  function languageLabel(key) {
    if (key.indexOf("+") >= 0) {
      return key.split("+").map(languageLabel).join(" + ") + " (these decide)";
    }
    return LANGUAGE_NAMES[key] || key;
  }

  /* How the two server states read: the fact is "isolated" or "shared" in the data. */
  var SERVER_STATE = { isolated: "idle server", shared: "shared server" };

  function shown(key, value) {
    return key === "isolated" ? SERVER_STATE[value] || value : value;
  }

  /* The prompts of the variants are 8-character fingerprints; a reader only needs to know which
   * variants used the same one, so they are named A, B, C … in the order they first appear. */
  var PROMPT_LETTERS = {};
  INDEX.variants.forEach(function (variant) {
    var hash = (variant.facts || {}).prompt8;
    if (hash && !(hash in PROMPT_LETTERS)) {
      PROMPT_LETTERS[hash] = String.fromCharCode(65 + Object.keys(PROMPT_LETTERS).length);
    }
  });

  function promptName(hash) {
    return PROMPT_LETTERS[hash] || hash;
  }

  function el(tag, attrs) {
    var node = document.createElement(tag);
    var extra = Array.prototype.slice.call(arguments, 2);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined || value === false) return;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key.slice(0, 2) === "on") node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value);
      });
    }
    append(node, extra);
    return node;
  }

  function append(node, children) {
    children.forEach(function (child) {
      if (child === null || child === undefined || child === false) return;
      if (Array.isArray(child)) append(node, child);
      else if (child.nodeType) node.appendChild(child);
      else node.appendChild(document.createTextNode(String(child)));
    });
  }

  function num(value, digits) {
    if (value === null || value === undefined) return "–";
    return Number(value).toFixed(digits === undefined ? 2 : digits);
  }

  function share(value) {
    if (value === null || value === undefined) return "–";
    return Math.round(Number(value) * 100) + "%";
  }

  function fileName(id) {
    return String(id).replace(/[^A-Za-z0-9._-]/g, "_");
  }

  function variantById(id) {
    for (var i = 0; i < INDEX.variants.length; i++) {
      if (INDEX.variants[i].id === id) return INDEX.variants[i];
    }
    return null;
  }

  /* The model a variant runs.  It is what groups the rows of the text screen: one block per model,
   * the variants of that model inside it. */
  function modelOf(variant) {
    return (variant.facts && variant.facts.model) || variant.label;
  }

  /* The variants of *variants* in blocks of one model each, in the order they were given. */
  function groupsOf(variants) {
    var order = [];
    var byModel = {};
    variants.forEach(function (variant) {
      var model = modelOf(variant);
      if (!byModel[model]) {
        byModel[model] = [];
        order.push(model);
      }
      byModel[model].push(variant);
    });
    return order.map(function (model) {
      return byModel[model];
    });
  }

  /* The facts that tell the variants of one model apart, in the order a reader needs them.  A fact
   * every variant of the block shares is left out: the model line already says it. */
  var DIFFERING = [
    ["mode", "mode"],
    ["temperature", "T"],
    ["eval_version", "eval"],
    ["answer_format_version", "format"],
    ["prompt8", "prompt"],
    ["step1_prompt", "step ① prompt"],
    ["profile", "profile"],
    ["num_ctx", "ctx"],
    ["explainer", "explainer"],
    ["machine", "machine"],
  ];

  function labelOf(variant, group, withServer) {
    var facts = variant.facts || {};
    var parts = [];
    DIFFERING.concat(withServer ? [["isolated", ""]] : []).forEach(function (pair) {
      var key = pair[0];
      var mine = facts[key] === undefined ? "" : String(facts[key]);
      if (!mine || mine === "-") return;
      var shared = group.every(function (one) {
        var theirs = (one.facts || {})[key];
        return (theirs === undefined ? "" : String(theirs)) === mine;
      });
      if (shared) return;
      var text = key === "prompt8" ? promptName(mine) : shown(key, mine);
      parts.push(pair[1] ? pair[1] + " " + text : text);
    });
    return parts.join(" · ");
  }

  /* The label of a variant among the variants of its model.  The server state does not change
   * what is found, so it is named only where two variants would otherwise read the same. */
  function shortLabel(variant, group) {
    var plain = labelOf(variant, group, false);
    var clash = group.some(function (one) {
      return one !== variant && labelOf(one, group, false) === plain;
    });
    var found = clash ? labelOf(variant, group, true) : plain;
    return found || variant.label;
  }

  /* What every variant of a model shares: the quantisation and the size, which the model line
   * carries instead of repeating them on every row. */
  function sharedFacts(variant) {
    var facts = variant.facts || {};
    var parts = [];
    if (facts.quant) parts.push(facts.quant);
    if (facts.params) parts.push(facts.params);
    return parts.join(" · ");
  }

  /* The ticked variants.  The tables of numbers (scorecard, heat map, error types) hold one eval
   * version at a time, because two eval versions are never compared in one table; the text screen
   * only reads, so it shows every ticked variant of any eval version. */
  function shownVariants() {
    return INDEX.variants.filter(function (variant) {
      if (!state.variants[variant.id]) return false;
      if (state.screen === "text" || state.evalVersion === null) return true;
      return variant.eval_version === state.evalVersion;
    });
  }

  /* Every variant of the eval version on screen, ticked or not: the scorecard lists them all, so a
   * variant that was unticked can be ticked again, and the dominated mark compares them all. */
  function tabVariants() {
    return INDEX.variants.filter(function (variant) {
      return state.evalVersion === null || variant.eval_version === state.evalVersion;
    });
  }

  function languages() {
    var found = [];
    INDEX.texts.forEach(function (text) {
      if (found.indexOf(text.language) < 0) found.push(text.language);
    });
    return found.sort();
  }

  function languageChoices() {
    return [DECIDING].concat(languages()).map(function (key) {
      return [key, languageLabel(key)];
    });
  }

  function inLanguage(code) {
    return state.language === code || state.language.split("+").indexOf(code) >= 0;
  }

  function bands() {
    var found = [];
    INDEX.texts.forEach(function (text) {
      if (text.band && found.indexOf(text.band) < 0) found.push(text.band);
    });
    return found.sort();
  }

  function shownTexts() {
    return INDEX.texts.filter(function (text) {
      if (!inLanguage(text.language)) return false;
      if (state.band !== "all" && text.band !== state.band) return false;
      return true;
    });
  }

  function rowOf(variantId, language, band) {
    var found = INDEX.numbers[variantId];
    if (!found) return null;
    return found.rows[language + "/" + (band || "all")] || null;
  }

  function measureOf(variantId, key) {
    var row = rowOf(variantId, state.language, state.band);
    return row ? row.measures[key] : null;
  }

  function repeatsOf(variantId) {
    var variant = variantById(variantId);
    return variant ? variant.repeats : [];
  }

  function allRepeats() {
    var found = [];
    shownVariants().forEach(function (variant) {
      variant.repeats.forEach(function (repeat) {
        if (found.indexOf(repeat) < 0) found.push(repeat);
      });
    });
    return found.sort(function (first, second) {
      return first - second;
    });
  }

  /* The texts whose file did not arrive, by id, with the file that was asked for.  Without this the
   * screen asks again on every render and stays at "Loading …" for ever. */
  var FAILED = {};

  function loadText(id, done) {
    if (TEXTS[id]) {
      done(TEXTS[id]);
      return;
    }
    if (FAILED[id]) {
      done(null);
      return;
    }
    var script = document.createElement("script");
    // The build's stamp, so a rebuilt report is not read from the browser's cache.
    script.src = "data/text-" + fileName(id) + ".js" + (INDEX.stamp ? "?" + INDEX.stamp : "");
    script.onload = function () {
      // A file that loaded but did not register its text is as broken as a missing one.
      if (!TEXTS[id]) FAILED[id] = script.src;
      done(TEXTS[id] || null);
    };
    script.onerror = function () {
      FAILED[id] = script.src;
      done(null);
    };
    document.head.appendChild(script);
  }

  /* ── the range bar ─────────────────────────────────────────────────── */

  function rangeBar(measure, scale) {
    if (!measure || measure.mean === null) return null;
    var low = measure.low === null ? measure.mean : measure.low;
    var high = measure.high === null ? measure.mean : measure.high;
    var span = scale.max - scale.min || 1;
    function at(value) {
      return Math.max(0, Math.min(100, ((value - scale.min) / span) * 100));
    }
    var bar = el("span", { class: "range" });
    if (measure.low !== null && measure.high !== null) {
      bar.appendChild(
        el("span", {
          class: "bar",
          style: "left:" + at(low) + "%;width:" + Math.max(1, at(high) - at(low)) + "%",
        })
      );
    }
    bar.appendChild(el("span", { class: "dot", style: "left:" + at(measure.mean) + "%" }));
    return bar;
  }

  /* One small vertical bar per repeat, so a variant of three repeats shows its three values. */
  function repeatBars(measure, scale) {
    if (!measure || measure.values.length < 2) return null;
    var span = scale.max - scale.min || 1;
    var spark = el("span", { class: "vbars spark", title: "one bar per repeat" });
    measure.values.forEach(function (value, index) {
      var height = value === null ? 0 : Math.max(4, ((value - scale.min) / span) * 100);
      spark.appendChild(
        el("i", {
          class: "vbar repeat",
          style: "height:" + Math.min(100, height) + "%",
          title: "r" + (index + 1) + ": " + (value === null ? "–" : num(value, 2)),
        })
      );
    });
    return spark;
  }

  function scaleFor(key) {
    var most = 0;
    if (key === "seconds_per_100_words" || key === "failed") {
      tabVariants().forEach(function (variant) {
        var measure = measureOf(variant.id, key);
        if (measure && measure.high !== null) most = Math.max(most, measure.high);
      });
      return { min: 0, max: most || 1 };
    }
    return { min: 0, max: 1 };
  }

  function measureCell(variantId, measure) {
    var found = measureOf(variantId, measure.key);
    if (!found || found.mean === null) return el("td", { class: "num" }, "–");
    var cell = el(
      "td",
      { class: "num" },
      measure.scale === "share" ? share(found.mean) : num(found.mean, measure.digits)
    );
    var bar = rangeBar(found, scaleFor(measure.key));
    if (bar) cell.appendChild(bar);
    var spark = repeatBars(found, scaleFor(measure.key));
    if (spark) cell.appendChild(spark);
    return cell;
  }

  /* ── dominance: a variant another one beats on every figure ────────── */

  function dominated(variantId) {
    // A variant that has not answered every text has means over other texts than the rest.
    if (!variantById(variantId).complete) return false;
    var mine = {
      recall: measureOf(variantId, "recall"),
      precision: measureOf(variantId, "precision"),
      alone: measureOf(variantId, "alone"),
      seconds: measureOf(variantId, "seconds_per_100_words"),
    };
    return tabVariants().some(function (other) {
      if (other.id === variantId || !other.complete) return false;
      var theirs = {
        recall: measureOf(other.id, "recall"),
        precision: measureOf(other.id, "precision"),
        alone: measureOf(other.id, "alone"),
        seconds: measureOf(other.id, "seconds_per_100_words"),
      };
      return (
        notWorse(theirs.recall, mine.recall) &&
        notWorse(theirs.precision, mine.precision) &&
        notWorse(theirs.alone, mine.alone) &&
        notSlower(theirs.seconds, mine.seconds) &&
        (better(theirs.recall, mine.recall) ||
          better(theirs.precision, mine.precision) ||
          better(theirs.alone, mine.alone) ||
          faster(theirs.seconds, mine.seconds))
      );
    });
  }

  function edge(measure, high) {
    if (!measure || measure.mean === null) return null;
    if (high) return measure.high === null ? measure.mean : measure.high;
    return measure.low === null ? measure.mean : measure.low;
  }

  function notWorse(theirs, mine) {
    var mineEdge = edge(mine, true);
    return theirs && theirs.mean !== null && mineEdge !== null && theirs.mean >= mineEdge;
  }

  function better(theirs, mine) {
    var mineEdge = edge(mine, true);
    return theirs && theirs.mean !== null && mineEdge !== null && theirs.mean > mineEdge;
  }

  function notSlower(theirs, mine) {
    var mineEdge = edge(mine, false);
    return theirs && theirs.mean !== null && mineEdge !== null && theirs.mean <= mineEdge;
  }

  function faster(theirs, mine) {
    var mineEdge = edge(mine, false);
    return theirs && theirs.mean !== null && mineEdge !== null && theirs.mean < mineEdge;
  }

  /* ── top bar ───────────────────────────────────────────────────────── */

  function topbar() {
    var bar = el("div", { class: "topbar" });
    bar.appendChild(el("h1", { text: "Evaluation report" }));
    bar.appendChild(
      el(
        "div",
        { class: "group" },
        el("span", { text: "Screen" }),
        screenButton("overview", "Overview"),
        screenButton("text", "Text"),
        screenButton("errors", "Error types")
      )
    );
    bar.appendChild(
      el(
        "div",
        { class: "group" },
        el("span", {
          text: "Text language",
          title:
            "The language of the student texts, not of the prompt. " + languageLabel(DECIDING) +
            " is the average of those two languages; French is reported on its own.",
        }),
        select(languageChoices(), state.language, function (value) {
          state.language = value;
          render();
        }),
        // The error table is over every band, so the filter is not offered where it does nothing.
        state.screen === "errors"
          ? null
          : el("span", { text: "Band", title: "The length of the texts: short, page or pages." }),
        state.screen === "errors"
          ? null
          : select(["all"].concat(bands()), state.band, function (value) {
              state.band = value;
              render();
            })
      )
    );
    if (INDEX.eval_versions && INDEX.eval_versions.length > 1 && state.screen !== "text") {
      bar.appendChild(
        el(
          "div",
          { class: "group" },
          el("span", {
            text: "Eval version",
            title:
              "The version of the test procedure (prompts, answer format, scoring). Runs of " +
              "different versions are never in one table. See About.",
          }),
          select(
            INDEX.eval_versions.map(String),
            String(state.evalVersion),
            function (value) {
              state.evalVersion = Number(value);
              render();
            }
          )
        )
      );
    }
    if (state.screen === "text") {
      bar.appendChild(textPicker());
      bar.appendChild(
        el(
          "div",
          { class: "group" },
          el("span", { text: "Show" }),
          modeButton("read", "Read"),
          modeButton("changes", "Changes"),
          modeButton("original", "Original"),
          el("span", { text: "Layout" }),
          select(["stack", "grid", "page"], state.layout, function (value) {
            state.layout = value;
            render();
          }),
          shownVariants().length === 2
            ? el(
                "label",
                { title: "hide what both versions did the same" },
                el("input", {
                  type: "checkbox",
                  checked: state.diffOnly ? "checked" : null,
                  onchange: function (event) {
                    state.diffOnly = event.target.checked;
                    render();
                  },
                }),
                " only differing"
              )
            : null
        )
      );
    }
    bar.appendChild(el("div", { class: "spacer" }));
    bar.appendChild(
      el(
        "div",
        { class: "group" },
        variantMenu(),
        el("button", {
          class: state.info ? "on" : "",
          text: "ⓘ About",
          title: "What the labels, modes and eval versions mean",
          onclick: function () {
            state.info = !state.info;
            render();
          },
        })
      )
    );
    return bar;
  }

  /* The variant menu: every variant with a tick, grouped by model, and one button per
   * combination of answer mode and eval version, so one kind of run is one click. */
  function combinations() {
    var found = [];
    var byKey = {};
    INDEX.variants.forEach(function (variant) {
      var key = variant.facts.mode + " · eval " + variant.eval_version;
      if (!byKey[key]) {
        byKey[key] = { key: key, mode: variant.facts.mode, version: variant.eval_version, ids: [] };
        found.push(byKey[key]);
      }
      byKey[key].ids.push(variant.id);
    });
    return found;
  }

  function variantMenu() {
    var ticked = INDEX.variants.filter(function (variant) {
      return state.variants[variant.id];
    }).length;
    var panel = el("div", { class: "panel" });
    var quick = el("div", { class: "quick" });
    quick.appendChild(
      el("button", {
        text: "all",
        onclick: function () {
          INDEX.variants.forEach(function (variant) {
            state.variants[variant.id] = true;
          });
          render();
        },
      })
    );
    quick.appendChild(
      el("button", {
        text: "none",
        onclick: function () {
          INDEX.variants.forEach(function (variant) {
            state.variants[variant.id] = false;
          });
          render();
        },
      })
    );
    combinations().forEach(function (combo) {
      quick.appendChild(
        el("button", {
          text: "only " + combo.key,
          title: combo.ids.length + " variants",
          onclick: function () {
            INDEX.variants.forEach(function (variant) {
              state.variants[variant.id] = combo.ids.indexOf(variant.id) >= 0;
            });
            if (state.screen !== "text") state.evalVersion = combo.version;
            render();
          },
        })
      );
    });
    panel.appendChild(quick);
    groupsOf(INDEX.variants).forEach(function (group) {
      panel.appendChild(el("div", { class: "modelname" }, el("span", { class: "model", text: modelOf(group[0]) })));
      group.forEach(function (variant) {
        panel.appendChild(
          el(
            "label",
            { class: "choice", title: factsTitle(variant) },
            el("input", {
              type: "checkbox",
              checked: state.variants[variant.id] ? "checked" : null,
              onchange: function (event) {
                state.variants[variant.id] = event.target.checked;
                render();
              },
            }),
            " " + shortLabel(variant, group)
          )
        );
      });
    });
    return el(
      "details",
      {
        class: "variantmenu",
        open: state.menuOpen ? "open" : null,
        ontoggle: function (event) {
          state.menuOpen = event.target.open;
        },
      },
      el("summary", {
        text: "Variants " + ticked + " / " + INDEX.variants.length,
        title: "Which variants (model + mode + settings) the screens show",
      }),
      panel
    );
  }

  var MODE_TEXT = {
    exchange:
      "the proofreading as the app does it: ① the corrected text, ② an explanation per change, " +
      "③ a check for unclear passages",
    text:
      "one request for the corrected text only, no explanations (the app asks it only when a " +
      "review answer failed)",
    review:
      "what the app does today: one request that returns the corrections with their " +
      "explanations as one JSON answer",
    "correct-only": "step ① of the exchange alone: the same corrections, no explanations, faster",
  };

  /* The About panel: what every label in the report means, for a reader who was not there. */
  function infoPanel() {
    var panel = el("section", { class: "info" });
    panel.appendChild(el("h2", { text: "How to read this report" }));
    var list = el("dl");
    function entry(term, body) {
      list.appendChild(el("dt", { text: term }));
      list.appendChild(el("dd", null, body));
    }
    entry(
      "Variant",
      "One model run one way: model · answer mode · temperature (T) · eval version · server. " +
        "The same variant run several times gives repeats (r1, r2 …); a bar shows how far they " +
        "spread. Choose which variants are shown in the Variants menu of the top bar."
    );
    var combos = combinations();
    var perCombo = el("ul");
    combos.forEach(function (combo) {
      var note = INDEX.eval_notes[String(combo.version)];
      perCombo.appendChild(
        el(
          "li",
          null,
          el("b", { text: combo.key }),
          " · " + combo.ids.length + (combo.ids.length === 1 ? " variant" : " variants") + " · " +
            (MODE_TEXT[combo.mode] || combo.mode) +
            (note && note.date ? ", measured under the procedure of " + note.date : "")
        )
      );
    });
    entry("Mode and eval in this report", perCombo);
    entry(
      "Answer mode",
      "How the model was asked: exchange, text, review or correct-only (see above for the ones " +
        "in this report)."
    );
    var versions = el("ul");
    INDEX.eval_versions.forEach(function (version) {
      var note = INDEX.eval_notes[String(version)];
      versions.appendChild(
        el("li", null, el("b", { text: "eval " + version }), note ? " (" + note.date + "): " + note.why : "")
      );
    });
    entry(
      "Eval version",
      el(
        "div",
        null,
        "The version of the test procedure: the prompts, the answer format and the way answers " +
          "are scored. A figure from one eval version says nothing about another, so the tables " +
          "hold one at a time (Eval version in the top bar). Versions in this report:",
        versions
      )
    );
    entry(
      "Text language",
      "The language of the student texts, not of the prompt. " + languageLabel(DECIDING) +
        " is the average of the two; French is reported on its own."
    );
    entry(
      "Band",
      "The length of the texts: short, page (about a page) or pages (several pages)."
    );
    entry(
      "idle server / shared server (shown in the details only)",
      "Idle: the server was empty before the run, so the seconds are comparable. Shared: other " +
        "models may have been loaded, so speeds are less reliable. It makes no difference to " +
        "which errors are found."
    );
    entry(
      "prompt A, B …",
      "The letter names the instructions a variant used; variants with the same letter used the " +
        "same ones. The text is under Prompts."
    );
    entry(
      "Bars",
      "One small vertical bar per reference error in a sentence: green and tall fixed exactly " +
        "as the reference does, amber changed in the right place but differently, short red " +
        "missed. +n counts the edits the reference does not have. The Reference row shows " +
        "one grey bar per error. In the scorecard and the error table a bar is a figure, one " +
        "per repeat or per outcome."
    );
    var promptList = el("div");
    Object.keys(PROMPT_LETTERS).forEach(function (hash) {
      var found = (INDEX.prompts || {})[hash];
      var users = INDEX.variants.filter(function (variant) {
        return (variant.facts || {}).prompt8 === hash;
      }).length;
      promptList.appendChild(
        el(
          "details",
          null,
          el("summary", {
            text:
              "prompt " + PROMPT_LETTERS[hash] + " · " + users +
              (users === 1 ? " variant" : " variants") +
              (found ? (found.exact ? "" : " · as built today, not exactly what the run sent") : " · text not available"),
          }),
          found ? el("pre", { class: "prompt", text: found.text }) : null
        )
      );
    });
    entry("Prompts (the instructions sent to the model)", promptList);
    panel.appendChild(list);
    return panel;
  }

  function screenButton(name, label) {
    return el("button", {
      class: state.screen === name ? "on" : "",
      text: label,
      onclick: function () {
        state.screen = name;
        render();
      },
    });
  }

  function modeButton(name, label) {
    return el("button", {
      class: state.mode === name ? "on" : "",
      text: label,
      onclick: function () {
        state.mode = name;
        render();
      },
    });
  }

  function select(values, current, onChange) {
    var node = el("select", {
      onchange: function (event) {
        onChange(event.target.value);
      },
    });
    values.forEach(function (entry) {
      // An entry is a value, or [value, what the reader sees].
      var value = Array.isArray(entry) ? entry[0] : entry;
      var label = Array.isArray(entry) ? entry[1] : entry;
      node.appendChild(
        el("option", {
          value: value,
          text: label,
          selected: value === current ? "selected" : null,
        })
      );
    });
    return node;
  }

  /* ── overview ──────────────────────────────────────────────────────── */

  function overview() {
    var screen = el("div");
    screen.appendChild(scorecard());
    screen.appendChild(heatmap());
    return screen;
  }

  function scorecard() {
    var screen = el("div");
    screen.appendChild(
      el(
        "h2",
        null,
        "Scorecard · " + state.language + " · " + state.band + " · " +
          tabVariants().length + " variants (" + shownVariants().length + " ticked)" +
          (state.evalVersion !== null && INDEX.eval_versions.length > 1
            ? " · eval version " + state.evalVersion
            : "")
      )
    );
    var table = el("table", { class: "scorecard" });
    var head = el("tr");
    head.appendChild(el("th", { class: "static", text: "Variant" }));
    MEASURES.forEach(function (measure) {
      head.appendChild(
        el("th", {
          class: "num",
          text: measure.label,
          onclick: function () {
            state.sort = {
              key: measure.key,
              desc: state.sort.key !== measure.key || !state.sort.desc,
            };
            render();
          },
        })
      );
    });
    head.appendChild(el("th", { class: "num static", text: "Texts" }));
    table.appendChild(el("thead", null, head));

    var body = el("tbody");
    sortedVariants().forEach(function (variant) {
      var row = el("tr");
      var classes = [];
      if (dominated(variant.id)) classes.push("dominated");
      if (!state.variants[variant.id]) classes.push("unticked");
      row.className = classes.join(" ");
      var label = el("td", { class: "label" });
      label.appendChild(
        el("input", {
          type: "checkbox",
          checked: state.variants[variant.id] ? "checked" : null,
          onchange: function (event) {
            state.variants[variant.id] = event.target.checked;
            render();
          },
        })
      );
      label.appendChild(document.createTextNode(" " + variant.label));
      if (!variant.complete) {
        var done = Object.keys(variant.texts_done).map(function (repeat) {
          return variant.texts_done[repeat];
        });
        label.appendChild(
          el("span", { class: "hint", text: " · incomplete: " + done.join(" / ") + " texts" })
        );
      }
      row.appendChild(label);
      MEASURES.forEach(function (measure) {
        row.appendChild(measureCell(variant.id, measure));
      });
      var numbers = rowOf(variant.id, state.language, state.band);
      row.appendChild(el("td", { class: "num" }, numbers ? numbers.texts : "–"));
      body.appendChild(row);
    });
    table.appendChild(body);
    screen.appendChild(table);
    screen.appendChild(
      el("p", {
        class: "hint",
        text:
          "⊘ dominated: another variant is not worse beyond the spread in recall, precision and " +
          "left alone, and costs no more time. The bar is the spread of the repeats, the dot " +
          "their mean. There is no total score. " + DECIDING + " is the mean of the two languages " +
          "that decide; French has its own row, and the failed count is of the languages shown.",
      })
    );
    return screen;
  }

  function sortedVariants() {
    var key = state.sort.key;
    var desc = state.sort.desc;
    return tabVariants()
      .slice()
      .sort(function (first, second) {
        var one = measureOf(first.id, key);
        var other = measureOf(second.id, key);
        var left = one && one.mean !== null ? one.mean : -1;
        var right = other && other.mean !== null ? other.mean : -1;
        return desc ? right - left : left - right;
      });
  }

  function heatmap() {
    var screen = el("div");
    var texts = shownTexts();
    screen.appendChild(el("h2", null, "Heat map · " + F_LABEL + " per text · mean over the repeats · ✓ = a correct text left alone"));
    var table = el("table", { class: "heatmap" });
    var head = el("tr");
    head.appendChild(el("th", { class: "static", text: "" }));
    texts.forEach(function (text) {
      head.appendChild(
        el("th", {
          class: "text",
          text: text.id,
          title: (text.band || text.kind) + " · " + text.rows + " sentences",
          onclick: function () {
            openText(text.id, 0);
          },
        })
      );
    });
    table.appendChild(el("thead", null, head));
    var body = el("tbody");
    shownVariants().forEach(function (variant) {
      var row = el("tr");
      row.appendChild(el("td", { class: "label", text: variant.label }));
      texts.forEach(function (text) {
        var cell = (INDEX.cells[variant.id] || {})[text.id];
        if (cell && text.kind === "correct" && cell.answered) {
          // A text without a reference error has no F-score: what counts is leaving it alone.
          var kept = cell.changed === 0;
          row.appendChild(
            el(
              "td",
              {
                class: "cell " + (kept ? "kept" : "changed"),
                text: kept ? "✓" : cell.changed + "/" + cell.answered,
                title:
                  text.id + " · no error in this text · " +
                  (kept ? "left alone" : "changed in " + cell.changed + " of " + cell.answered),
                onclick: function () {
                  openText(text.id, 0);
                },
              }
            )
          );
          return;
        }
        if (!cell || cell.f_beta === null || cell.f_beta === undefined) {
          row.appendChild(el("td", { class: "cell missing", title: "not answered" }, "·"));
          return;
        }
        row.appendChild(
          el(
            "td",
            {
              class: "cell" + (cell.failed ? " failed" : ""),
              text: Math.round(cell.f_beta * 100),
              title:
                text.id + " · " + F_LABEL + " " + num(cell.f_beta) +
                (cell.failed ? " · " + cell.failed + " failed" : "") +
                (cell.fallback ? " · " + cell.fallback + " fallback" : ""),
              style: "background:" + heat(cell.f_beta),
              onclick: function () {
                openText(text.id, 0);
              },
            }
          )
        );
      });
      body.appendChild(row);
    });
    table.appendChild(body);
    screen.appendChild(table);
    return screen;
  }

  function heat(value) {
    var clamped = Math.max(0, Math.min(1, value));
    return "hsl(205, 45%, " + (96 - Math.round(clamped * 46)) + "%)";
  }

  /* ── the text screen ───────────────────────────────────────────────── */

  function openText(id, row) {
    state.screen = "text";
    state.text = id;
    state.row = row || 0;
    state.drawer = null;
    render();
  }

  /* Move to the next (or previous) text of the language and band on screen. */
  function stepText(step) {
    var texts = shownTexts();
    var index = texts.findIndex(function (one) {
      return one.id === state.text;
    });
    var target = index >= 0 ? texts[index + step] : null;
    if (target) openText(target.id, 0);
  }

  /* Which text is open, and the way to the others.  It sits in the top bar, which stays in reach
   * while the text scrolls, so a long text does not trap the reader on one text. */
  function textPicker() {
    var texts = shownTexts();
    var index = texts.findIndex(function (one) {
      return one.id === state.text;
    });
    var previous = index > 0 ? texts[index - 1] : null;
    var next = index >= 0 && index + 1 < texts.length ? texts[index + 1] : null;
    return el(
      "div",
      { class: "group" },
      el("span", { text: "Text" }),
      el("button", {
        text: "◀",
        title: previous ? "previous text: " + previous.id : "this is the first text",
        disabled: previous ? null : "disabled",
        onclick: function () {
          if (previous) openText(previous.id, 0);
        },
      }),
      select(
        texts.map(function (one) {
          return one.id;
        }),
        state.text,
        function (value) {
          openText(value, 0);
        }
      ),
      el("button", {
        text: "▶",
        title: next ? "next text: " + next.id : "this is the last text",
        disabled: next ? null : "disabled",
        onclick: function () {
          if (next) openText(next.id, 0);
        },
      }),
      el("span", { class: "hint", text: index >= 0 ? index + 1 + " / " + texts.length : "" })
    );
  }

  function textScreen() {
    var screen = el("div");
    if (!state.text) {
      var first = shownTexts()[0];
      if (!first) {
        screen.appendChild(el("p", { class: "hint", text: "No text in this language and band." }));
        return screen;
      }
      state.text = first.id;
    }
    var loaded = TEXTS[state.text];
    if (!loaded) {
      if (FAILED[state.text]) {
        screen.appendChild(
          el("p", {
            class: "hint",
            text:
              "Could not load " +
              FAILED[state.text] +
              ". The report folder is incomplete, or it was built by another version of the " +
              "viewer. Rebuild it with bazel run //tools:eval_report -- build.",
          })
        );
        return screen;
      }
      screen.appendChild(el("p", { class: "hint", text: "Loading " + state.text + "…" }));
      loadText(state.text, function () {
        render();
      });
      return screen;
    }
    screen.appendChild(textbar(loaded.text));
    if (state.layout === "grid") screen.appendChild(grid(loaded));
    else if (state.layout === "page") screen.appendChild(pageView(loaded));
    else screen.appendChild(stack(loaded));
    screen.appendChild(legend());
    return screen;
  }

  function textbar(text) {
    var bar = el("div", { class: "textbar" });
    bar.appendChild(
      el("button", {
        text: "◀",
        title: "previous text",
        onclick: function () {
          stepText(-1);
        },
      })
    );
    bar.appendChild(
      el("button", {
        text: "▶",
        title: "next text",
        onclick: function () {
          stepText(1);
        },
      })
    );
    bar.appendChild(
      el(
        "span",
        { class: "title" },
        text.id + " · " + text.language + " · " + (text.band || text.kind) + " · " +
          text.rows.length + " sentences · " + text.ref_edits.length + " reference errors"
      )
    );
    bar.appendChild(
      el(
        "span",
        { class: "group" },
        el("span", {
          text: "Repeat",
          title: "the sentence stack shows every repeat under 'all'; the grid and the page show the first",
        }),
        select(["all"].concat(allRepeats().map(String)), state.repeat, function (value) {
          state.repeat = value;
          state.drawer = null;
          render();
        })
      )
    );
    if (state.layout === "grid") {
      bar.appendChild(
        el(
          "span",
          { class: "group" },
          el("span", { text: "Rows" }),
          select(
            ["all", "disagree", "missed", "extra", "unchanged", "error"],
            state.filter,
            function (value) {
              state.filter = value;
              render();
            }
          )
        )
      );
    }
    return bar;
  }

  function legend() {
    return el(
      "div",
      { class: "legend" },
      el("span", { class: "fixed", text: "fixed exactly" }),
      el("span", { class: "located", text: "right place, other fix" }),
      el("span", { class: "extra", text: "extra, not in the reference" }),
      el("span", { class: "missed", text: "missed: left as the student wrote it" }),
      el("span", {
        class: "note",
        text: "bars, one per error: green fixed · amber right place, other fix · red short: missed · +n: extra edits",
      }),
      el("span", {
        class: "note",
        text: "n/m: repeats that wrote the sentence the same way as the first one (click to list them)",
      })
    );
  }

  /* The versions of a variant the repeat menu asks for, in repeat order. */
  function itemsOf(loaded, variantId) {
    var repeats = repeatsOf(variantId);
    var wanted =
      state.repeat === "all" || state.expanded[variantId] ? repeats : [Number(state.repeat)];
    var found = [];
    wanted.forEach(function (repeat) {
      var item = loaded.items[variantId + "/r" + repeat + "/" + loaded.text.id];
      if (item) found.push(item);
    });
    return found;
  }

  function itemOf(loaded, variantId) {
    return itemsOf(loaded, variantId)[0] || null;
  }

  /* The complete sentence of a version in a row; a row it left as it was is the text's own. */
  function rowText(text, item, row) {
    return item && item.rows[row] ? item.rows[row].text : text.rows[row].source.text;
  }

  function firstItems(loaded) {
    return shownVariants().map(function (variant) {
      return itemOf(loaded, variant.id);
    });
  }

  /* One line per repeat: whether it wrote the sentence like the first one, and what it did with
   * each reference error.  It is the tooltip of the n/m figure. */
  function repeatSummary(loaded, variantId, row) {
    var text = loaded.text;
    var first = null;
    return repeatsOf(variantId)
      .map(function (repeat) {
        var item = loaded.items[variantId + "/r" + repeat + "/" + text.id];
        if (!item) return "r" + repeat + " · not answered";
        var written = rowText(text, item, row);
        if (first === null) first = written;
        var symbols = outcomeSymbols(text, item, row).join(" ");
        return (
          "r" + repeat + " · " + (written === first ? "same sentence" : "other sentence") +
          (symbols ? " · " + symbols : "")
        );
      })
      .join("\n");
  }

  function repeatCount(loaded, variantId, row) {
    var repeats = repeatsOf(variantId);
    var wanted = null;
    var same = 0;
    repeats.forEach(function (repeat) {
      var item = loaded.items[variantId + "/r" + repeat + "/" + loaded.text.id];
      if (!item) return;
      var written = rowText(loaded.text, item, row);
      if (wanted === null) wanted = written;
      if (written === wanted) same += 1;
    });
    return { same: same, of: repeats.length };
  }

  /* ── how a sentence is shown ───────────────────────────────────────── */

  function marked(sentence, marks, className, onMark, tag, titleOf) {
    var nodes = [];
    var cursor = 0;
    marks.forEach(function (mark) {
      if (mark.start > cursor) {
        nodes.push(document.createTextNode(sentence.slice(cursor, mark.start)));
      }
      var inner = sentence.slice(mark.start, mark.end);
      var name = typeof className === "function" ? className(mark) : className;
      var handler = onMark
        ? function () {
            onMark(mark);
          }
        : null;
      var title = titleOf ? titleOf(mark) : null;
      if (inner) {
        nodes.push(el(tag || "mark", { class: name, title: title, onclick: handler }, inner));
      } else {
        // A point in the student's sentence is a word the reference adds; in a corrected
        // sentence it is a word that was taken out.
        var gone = name === "ref" ? "a word is missing here" : "deleted here";
        nodes.push(
          el("mark", { class: name + " gone", title: title || gone, onclick: handler }, "⁁")
        );
      }
      cursor = Math.max(cursor, mark.end);
    });
    if (cursor < sentence.length) nodes.push(document.createTextNode(sentence.slice(cursor)));
    return nodes;
  }

  function tokenOffsets(tokens, row) {
    var offsets = [0];
    for (var index = row.start; index < row.end; index++) {
      offsets.push(offsets[offsets.length - 1] + tokens[index].length);
    }
    return offsets;
  }

  function changesView(text, row, edits) {
    var source = text.rows[row].source.text;
    var offsets = tokenOffsets(text.tokens, row);
    var nodes = [];
    var cursor = 0;
    edits
      .slice()
      .sort(function (first, second) {
        return first.start - second.start;
      })
      .forEach(function (edit) {
        var from = offsets[edit.start - row.start];
        var to = offsets[edit.end - row.start];
        if (from === undefined || to === undefined) return;
        if (from > cursor) nodes.push(document.createTextNode(source.slice(cursor, from)));
        if (to > from) nodes.push(el("del", null, source.slice(from, to)));
        if (edit.text) nodes.push(el("ins", null, edit.text));
        cursor = Math.max(cursor, to);
      });
    if (cursor < source.length) nodes.push(document.createTextNode(source.slice(cursor)));
    return nodes;
  }

  function refEditsOf(text, row) {
    return text.ref_edits.filter(function (edit) {
      return edit.row === row;
    });
  }

  function originalContent(text, row) {
    var found = text.rows[row];
    if (state.mode === "changes") return [document.createTextNode(found.source.text)];
    return marked(found.source.text, found.source.marks, "ref", null);
  }

  function referenceContent(text, row, onMark) {
    var found = text.rows[row];
    if (state.mode === "changes") return changesView(text, row, refEditsOf(text, row));
    if (state.mode === "original") {
      return marked(found.source.text, found.source.marks, "ref", null);
    }
    return marked(found.reference.text, found.reference.marks, "changed", onMark, "b", function (mark) {
      return "was: " + words(text, text.ref_edits[mark.index]);
    });
  }

  function modelContent(text, row, item, onMark) {
    if (state.mode === "changes") {
      return changesView(
        text,
        row,
        item.edits.filter(function (edit) {
          return edit.row === row;
        })
      );
    }
    if (state.mode === "original") {
      return marked(text.rows[row].source.text, text.rows[row].source.marks, "ref", null);
    }
    var other = state.diffOnly ? pairPartner(item) : null;
    return marked(
      rowText(text, item, row),
      versionMarks(text, row, item),
      function (mark) {
        if (mark.missed) return "missed";
        var edit = item.edits[mark.index];
        // The data says "exact"; the colour of a reference error fixed exactly is "fixed".
        var kind = edit.outcome === "exact" ? "fixed" : edit.outcome;
        return kind + (other && madeAlike(other, edit) ? " same" : "");
      },
      onMark,
      null,
      function (mark) {
        if (mark.missed) {
          return "missed: " + words(text, text.ref_edits[mark.index]) + " → " +
            (text.ref_edits[mark.index].text || "(deleted)") + " in the reference";
        }
        return "was: " + words(text, item.edits[mark.index]);
      }
    );
  }

  /* The marks of a version's sentence: its own edits, and the reference errors it left alone.  A
   * sentence the version did not change is the student's, so its reference errors are marked as
   * the original marks them. */
  function versionMarks(text, row, item) {
    if (item.rows[row]) return item.rows[row].marks;
    return text.rows[row].source.marks
      .filter(function (mark) {
        return item.ref_outcomes[mark.index] === "missed";
      })
      .map(function (mark) {
        return { start: mark.start, end: mark.end, index: mark.index, missed: true };
      });
  }

  /* ── two versions side by side: what both did the same ─────────────── */

  /* With exactly two variants ticked and "only differing" on, an edit both made the same way is
   * dimmed and a sentence both wrote alike is skipped when moving through the text. */
  function pairMode() {
    return state.diffOnly && shownVariants().length === 2;
  }

  function pairPartner(item) {
    var shown = shownVariants();
    if (shown.length !== 2 || !TEXTS[item.text]) return null;
    var other = shown[0].id === item.variant ? shown[1] : shown[0];
    return itemOf(TEXTS[item.text], other.id);
  }

  function madeAlike(item, edit) {
    return item.edits.some(function (other) {
      return other.start === edit.start && other.end === edit.end && other.text === edit.text;
    });
  }

  function rowDiffers(loaded, row) {
    var items = firstItems(loaded);
    var seen = items.map(function (item) {
      return item ? rowText(loaded.text, item, row) : null;
    });
    return seen.some(function (one) {
      return one !== seen[0];
    });
  }

  /* Move to the next (or previous) sentence; with two versions compared, over the ones they
   * wrote differently.  Returns whether the row changed. */
  function moveRow(loaded, step) {
    var total = loaded.text.rows.length;
    var row = state.row + step;
    while (row >= 0 && row < total) {
      if (!pairMode() || rowDiffers(loaded, row)) {
        state.row = row;
        return true;
      }
      row += step;
    }
    return false;
  }

  /* Jump to the next sentence (after this one, from the top again at the end) where `filter`
   * holds: a missed reference error, versions that disagree, or an edit not in the reference. */
  function jumpTo(loaded, filter) {
    var text = loaded.text;
    var items = firstItems(loaded);
    for (var step = 1; step <= text.rows.length; step++) {
      var row = (state.row + step) % text.rows.length;
      if (keepRow(text, row, items, filter)) {
        state.row = row;
        return;
      }
    }
  }

  /* ── the sentence stack ────────────────────────────────────────────── */

  function stack(loaded) {
    var text = loaded.text;
    var row = Math.max(0, Math.min(state.row, text.rows.length - 1));
    state.row = row;
    var screen = el("div");
    var bar = el("div", { class: "sentencebar" });
    bar.appendChild(
      el("button", {
        text: "▲",
        title: "previous sentence",
        onclick: function () {
          moveRow(loaded, -1);
          render();
        },
      })
    );
    bar.appendChild(
      el("button", {
        text: "▼",
        title: "next sentence",
        onclick: function () {
          moveRow(loaded, 1);
          render();
        },
      })
    );
    bar.appendChild(
      el("span", { class: "title", text: "sentence " + (row + 1) + " of " + text.rows.length })
    );
    bar.appendChild(
      el("span", { class: "hint", text: refEditsOf(text, row).length + " reference errors" })
    );
    [
      ["missed", "next missed error"],
      ["disagree", "next where versions disagree"],
      ["extra", "next extra edit"],
    ].forEach(function (jump) {
      bar.appendChild(
        el("button", {
          text: jump[1],
          onclick: function () {
            jumpTo(loaded, jump[0]);
            render();
          },
        })
      );
    });
    screen.appendChild(bar);
    if (pairMode() && !rowDiffers(loaded, row)) {
      screen.appendChild(
        el("p", { class: "hint", text: "Both versions wrote this sentence the same way." })
      );
    }

    var versions = el("div", { class: "versions" });
    // The original stays in view while the lines below it scroll.
    var pinned = el("div", { class: "pinned" });
    pinned.appendChild(stackHead());
    pinned.appendChild(
      versionLine(
        "Original",
        "ref",
        originalContent(text, row),
        el("div", { class: "marks" }, errorCount(text, row))
      )
    );
    versions.appendChild(pinned);
    // The reference is a line like the variants', first among them, so only the student's
    // original stays pinned.
    var referenceBlock = el("div", { class: "modelgroup" });
    referenceBlock.appendChild(
      versionLine("Reference", "ref", referenceContent(text, row, function (mark) {
        state.drawer = { kind: "reference", row: row, index: mark.index };
        render();
      }), el("div", { class: "marks" }, referenceMarks(text, row)))
    );
    versions.appendChild(referenceBlock);
    groupsOf(shownVariants()).forEach(function (group) {
      var block = el("div", { class: "modelgroup" });
      block.appendChild(
        el(
          "div",
          { class: "modelname" },
          el("span", { class: "model", text: modelOf(group[0]) }),
          el("span", { class: "facts", text: sharedFacts(group[0]) }),
          el("span", {
            class: "count",
            text: group.length + (group.length === 1 ? " variant" : " variants"),
          })
        )
      );
      group.forEach(function (variant) {
        var found = itemsOf(loaded, variant.id);
        var name = shortLabel(variant, group);
        if (!found.length) {
          block.appendChild(
            versionLine(name, "model", [document.createTextNode("(not answered)")], null, factsTitle(variant))
          );
          return;
        }
        var counts = repeatCount(loaded, variant.id, row);
        found.forEach(function (item) {
          block.appendChild(
            versionLine(
              name + (found.length > 1 ? " · r" + item.repeat : ""),
              "model",
              modelContent(text, row, item, function (mark) {
                if (mark.missed) {
                  state.drawer = { kind: "reference", row: row, index: mark.index };
                  render();
                  return;
                }
                state.drawer = {
                  kind: "edit",
                  row: row,
                  variant: variant.id,
                  repeat: item.repeat,
                  index: mark.index,
                };
                render();
              }),
              el(
                "div",
                {
                  class: "marks",
                  title:
                    "one bar per reference error: green fixed exactly, amber right place with another " +
                    "fix, short red missed · +n: edits the reference does not have · n/m: " +
                    "how many repeats wrote this sentence the same way (click it to show every " +
                    "repeat)",
                },
                outcomeMarks(text, item, row),
                el("span", {
                  class: "hint repeats",
                  text: " " + counts.same + "/" + counts.of,
                  title: repeatSummary(loaded, variant.id, row) +
                    "\n\nClick to " + (state.expanded[variant.id] ? "hide" : "show") +
                    " the other repeats as rows.",
                  onclick: function () {
                    state.expanded[variant.id] = !state.expanded[variant.id];
                    render();
                  },
                })
              ),
              factsTitle(variant)
            )
          );
        });
      });
      versions.appendChild(block);
    });
    screen.appendChild(versions);
    if (state.drawer) screen.appendChild(drawer(loaded));
    return screen;
  }

  /* The column titles of the stack, so the marks column is not a row of unexplained symbols. */
  function stackHead() {
    return el(
      "div",
      { class: "stackhead" },
      el("span", { class: "name", text: "variant" }),
      el("span", { class: "sentence", text: "sentence" }),
      el("span", {
        class: "marks",
        text: "errors fixed · repeats",
        title:
          "one bar per reference error: green fixed exactly, amber right place with another fix, " +
          "short red missed · +n: edits the reference does not have · n/m: repeats that " +
          "wrote this sentence the same way",
      })
    );
  }

  /* Every setting that makes one variant differ from another, one per line.  Shown as the tooltip
   * of a variant's name and in the drawer, so a result can always be traced to its settings. */
  var FACT_LINES = [
    ["model", "model"],
    ["quant", "quantisation"],
    ["mode", "mode"],
    ["temperature", "temperature"],
    ["prompt8", "prompt version"],
    ["step1_prompt", "step ① prompt variant"],
    ["eval_version", "eval version"],
    ["answer_format_version", "answer format"],
    ["profile", "profile"],
    ["num_ctx", "context"],
    ["isolated", "server"],
    ["seed", "seeds (one per repeat)"],
    ["machine", "machine"],
    ["date", "date"],
  ];

  function factLines(variant) {
    var facts = (variant && variant.facts) || {};
    return FACT_LINES.filter(function (pair) {
      return facts[pair[0]] !== undefined && facts[pair[0]] !== "" && facts[pair[0]] !== "-";
    }).map(function (pair) {
      var value = String(facts[pair[0]]);
      if (pair[0] === "prompt8") value = promptName(value);
      return [pair[1], shown(pair[0], value)];
    });
  }

  function factsTitle(variant) {
    return factLines(variant)
      .map(function (pair) {
        return pair[0] + ": " + pair[1];
      })
      .join("\n");
  }

  /* How many errors the reference marks in a sentence: the number a variant is measured against. */
  function errorCount(text, row) {
    var count = refEditsOf(text, row).length;
    return el("span", {
      class: "errcount" + (count ? "" : " none"),
      text: count === 1 ? "1 error" : count + " errors",
      title: "errors the reference marks in this sentence",
    });
  }

  function versionLine(name, kind, content, marks, title) {
    var line = el("div", { class: "version" });
    line.appendChild(
      el("div", { class: "name" + (kind === "ref" ? " strong" : ""), text: name, title: title })
    );
    var body = el("div", { class: "sentence" });
    append(body, [content]);
    line.appendChild(body);
    line.appendChild(marks || el("div", { class: "marks" }));
    return line;
  }

  /* One small vertical bar per reference error of a sentence, and one per extra edit: green and
   * tall for fixed exactly, amber for the right place with another fix, red and short for missed,
   * and a "+n" for the edits the reference does not have.  The height repeats the colour, so the bars
   * read without it. */
  function tick(kind, title) {
    return el("i", { class: "tick " + kind, title: title });
  }

  function outcomeMarks(text, item, row) {
    var nodes = [];
    refEditsOf(text, row).forEach(function (edit) {
      var outcome = item.ref_outcomes[edit.index];
      var kind = outcome === "fixed" ? "fixed" : outcome === "located" ? "located" : "missed";
      nodes.push(tick(kind, (edit.code || "reference error") + " · " + outcome));
    });
    var extras = item.edits.filter(function (edit) {
      return edit.row === row && edit.outcome === "extra";
    });
    if (extras.length) {
      nodes.push(
        el("b", {
          class: "extra",
          text: "+" + extras.length,
          title:
            "edits the reference does not have: " +
            extras
              .map(function (edit) {
                return words(text, edit) + " → " + (edit.text || "(deleted)");
              })
              .join(" · "),
        })
      );
    }
    return nodes;
  }

  /* The same row of bars for the reference itself: one grey bar per error, so the columns line up
   * and the count of errors is the number of bars. */
  function referenceMarks(text, row) {
    return refEditsOf(text, row).map(function (edit) {
      return tick("ref", (edit.code || "reference error") + " · " + (edit.category || ""));
    });
  }

  /* The outcomes as text, for a tooltip. */
  function outcomeSymbols(text, item, row) {
    var found = refEditsOf(text, row).map(function (edit) {
      var outcome = item.ref_outcomes[edit.index];
      return outcome === "fixed" ? "✓" : outcome === "located" ? "~" : "⁁";
    });
    var extra = item.edits.filter(function (edit) {
      return edit.row === row && edit.outcome === "extra";
    }).length;
    if (extra) found.push("+" + extra);
    return found;
  }

  /* ── the drawer ────────────────────────────────────────────────────── */

  function drawer(loaded) {
    var text = loaded.text;
    var panel = el("div", { class: "drawer" });
    panel.appendChild(
      el("button", {
        class: "close",
        text: "×",
        onclick: function () {
          state.drawer = null;
          render();
        },
      })
    );
    if (state.drawer.kind === "reference") {
      var edit = text.ref_edits[state.drawer.index];
      panel.appendChild(el("h3", { text: "Reference error in " + text.id }));
      panel.appendChild(
        el("div", { class: "pair", text: words(text, edit) + " → " + (edit.text || "(deleted)") })
      );
      panel.appendChild(el("div", { class: "row", text: edit.code + " · " + edit.category }));
      panel.appendChild(el("div", { class: "row muted", text: "main type: " + edit.main_type }));
      return panel;
    }
    // The edit is looked up in the version it was clicked in, not in whichever repeat the menu
    // names now: another repeat has other edits, and an index means nothing there.
    var item = loaded.items[state.drawer.variant + "/r" + state.drawer.repeat + "/" + text.id];
    var found = item ? item.edits[state.drawer.index] : null;
    if (!found) {
      panel.appendChild(el("div", { class: "row muted", text: "This edit is no longer shown." }));
      return panel;
    }
    var variant = variantById(state.drawer.variant);
    panel.appendChild(
      el("h3", { text: "Edit in " + text.id + ", sentence " + (state.drawer.row + 1) })
    );
    var facts = el("div", { class: "small" });
    if (found.ref !== null && found.ref !== undefined) {
      var reference = text.ref_edits[found.ref];
      facts.appendChild(
        el("div", {
          text:
            "Reference: " + (reference.code || "no label") + " (" +
            (reference.category || "–") + ")",
        })
      );
    } else {
      facts.appendChild(el("div", { text: "Not in the reference: an extra edit." }));
    }
    facts.appendChild(
      el("div", { text: "Outcome: " + found.outcome + " · category: " + (found.category || "–") })
    );
    panel.appendChild(facts);
    panel.appendChild(el("hr"));
    panel.appendChild(
      el("div", { class: "pair", text: words(text, found) + " → " + (found.text || "(deleted)") })
    );
    panel.appendChild(el("div", { class: "row muted", text: "Explanation" }));
    repeatsOf(state.drawer.variant).forEach(function (one) {
      var other = loaded.items[state.drawer.variant + "/r" + one + "/" + text.id];
      if (!other) return;
      var same = other.edits.filter(function (edit) {
        return edit.start === found.start && edit.end === found.end && edit.text === found.text;
      })[0];
      panel.appendChild(
        el(
          "div",
          { class: "row" },
          el("b", { text: "r" + one + " " }),
          el("span", { text: same ? same.outcome : "edit not made" }),
          same && same.explanation
            ? el("div", { class: "muted", text: "“" + same.explanation + "”" })
            : null
        )
      );
    });
    panel.appendChild(el("hr"));
    panel.appendChild(el("h3", { text: "Settings" }));
    factLines(variant).forEach(function (pair) {
      panel.appendChild(
        el(
          "div",
          { class: "fact" },
          el("span", { class: "muted", text: pair[0] }),
          el("span", { text: pair[1] })
        )
      );
    });
    var used = (INDEX.prompts || {})[(variant.facts || {}).prompt8];
    if (used) {
      panel.appendChild(
        el(
          "details",
          null,
          el("summary", { text: "prompt " + promptName(variant.facts.prompt8) + ": the instructions" }),
          el("pre", { class: "prompt", text: used.text })
        )
      );
    }
    return panel;
  }

  function words(text, edit) {
    return text.tokens.slice(edit.start, edit.end).join("") || "(nothing)";
  }

  /* ── the grid ──────────────────────────────────────────────────────── */

  var GRID_NUMBER = 40;
  var GRID_ORIGINAL = 260;
  var GRID_REFERENCE = 260;
  var GRID_VARIANT = 260;

  function grid(loaded) {
    var text = loaded.text;
    var variants = shownVariants();
    var table = el("table", { class: "grid" });
    // Fixed column widths: a wide label must wrap in its column, not widen it.
    table.style.width = GRID_NUMBER + GRID_ORIGINAL + GRID_REFERENCE + GRID_VARIANT * variants.length + "px";
    var head = el("tr");
    head.appendChild(el("th", { class: "static number", text: "#" }));
    head.appendChild(el("th", { class: "static original", text: "Original" }));
    head.appendChild(el("th", { class: "static reference", text: "Reference" }));
    variants.forEach(function (variant) {
      var first = itemOf(loaded, variant.id);
      head.appendChild(
        el("th", {
          class: "static",
          text: variant.label + (first && repeatsOf(variant.id).length > 1 ? " · r" + first.repeat : ""),
          title: factsTitle(variant),
        })
      );
    });
    table.appendChild(el("thead", null, head));
    var body = el("tbody");
    text.rows.forEach(function (row, index) {
      var items = variants.map(function (variant) {
        return itemOf(loaded, variant.id);
      });
      if (!keepRow(text, index, items, state.filter)) return;
      var line = el("tr");
      line.appendChild(el("td", { class: "number", text: String(index + 1) }));
      var original = el("td", { class: "original" });
      original.appendChild(el("div", { class: "errline" }, errorCount(text, index)));
      original.appendChild(el("div", null, originalContent(text, index)));
      line.appendChild(original);
      line.appendChild(el("td", { class: "reference" }, referenceContent(text, index, null)));
      variants.forEach(function (variant, position) {
        var item = items[position];
        var cell = el("td");
        if (!item) {
          cell.appendChild(el("span", { class: "hint", text: "(not answered)" }));
        } else {
          append(cell, [modelContent(text, index, item, null)]);
          cell.appendChild(el("div", null, outcomeMarks(text, item, index)));
        }
        line.appendChild(cell);
      });
      body.appendChild(line);
    });
    table.appendChild(body);
    // The table scrolls inside its own frame, so the header row and the two text columns stay
    // in view in both directions.
    return el("div", { class: "gridframe" }, table);
  }

  function keepRow(text, index, items, filter) {
    if (filter === "all") return true;
    var edits = refEditsOf(text, index);
    if (filter === "error") {
      return edits.some(function (edit) {
        return state.errorGroup === null || edit.main_type === state.errorGroup;
      });
    }
    if (filter === "unchanged") return edits.length === 0;
    if (filter === "missed") {
      return items.some(function (item) {
        return (
          item &&
          item.ref_outcomes.some(function (outcome, position) {
            return outcome === "missed" && text.ref_edits[position].row === index;
          })
        );
      });
    }
    if (filter === "extra") {
      return items.some(function (item) {
        return (
          item &&
          item.edits.some(function (edit) {
            return edit.row === index && edit.outcome === "extra";
          })
        );
      });
    }
    if (filter === "disagree") {
      var seen = items.map(function (item) {
        return item ? rowText(text, item, index) : null;
      });
      return seen.some(function (one) {
        return one !== seen[0];
      });
    }
    return true;
  }

  /* ── the page view ─────────────────────────────────────────────────── */

  /* The complete texts side by side.  Every sentence is one row of the table, so the columns stay
   * level however long each version's sentence is, and there is one scroll bar. */
  var PAGE_COLUMNS = 3;

  /* The variants the page puts next to the original: the reader's choice, else the first ones. */
  function pageChoice() {
    var shown = shownVariants();
    var ids = shown.map(function (variant) {
      return variant.id;
    });
    var chosen = [];
    for (var i = 0; i < PAGE_COLUMNS; i++) {
      var id = state.pageVariants[i];
      chosen.push(id === "" || (id && ids.indexOf(id) >= 0) ? id : ids[i] || "");
    }
    return chosen;
  }

  function pageView(loaded) {
    var text = loaded.text;
    var chosen = pageChoice();
    var picks = el("div", { class: "textbar" });
    chosen.forEach(function (id, position) {
      var menu = el("select", {
        onchange: function (event) {
          var next = pageChoice();
          next[position] = event.target.value;
          state.pageVariants = next;
          render();
        },
      });
      menu.appendChild(el("option", { value: "", text: "(none)" }));
      shownVariants().forEach(function (variant) {
        menu.appendChild(
          el("option", {
            value: variant.id,
            text: variant.label,
            selected: variant.id === id ? "selected" : null,
          })
        );
      });
      picks.appendChild(el("span", { class: "group" }, el("span", { text: "Column " + (position + 1) }), menu));
    });
    var versions = chosen
      .filter(function (id) {
        return id;
      })
      .map(function (id) {
        var variant = variantById(id);
        var item = itemOf(loaded, id);
        return {
          title: variant.label + (item && repeatsOf(id).length > 1 ? " · r" + item.repeat : ""),
          hover: factsTitle(variant),
          item: item,
        };
      });
    var table = el("table", { class: "pagegrid" });
    var head = el("tr");
    ["Original", "Reference"].concat(
      versions.map(function (version) {
        return version.title;
      })
    ).forEach(function (title) {
      head.appendChild(el("th", { class: "static", text: title }));
    });
    // The heads of the chosen variants say which settings they are: hover for the complete list.
    Array.prototype.slice.call(head.children, 2).forEach(function (node, position) {
      node.title = versions[position].hover;
    });
    table.appendChild(el("thead", null, head));
    var body = el("tbody");
    text.rows.forEach(function (row, index) {
      var line = el("tr");
      function cell(content) {
        var node = el("td", {
          title: "open this sentence",
          onclick: function () {
            state.row = index;
            state.layout = "stack";
            render();
          },
        });
        append(node, [content]);
        line.appendChild(node);
      }
      cell([el("div", { class: "errline" }, errorCount(text, index)), originalContent(text, index)]);
      cell(referenceContent(text, index, null));
      versions.forEach(function (version) {
        cell(version.item ? modelContent(text, index, version.item, null) : []);
      });
      body.appendChild(line);
    });
    table.appendChild(body);
    return el("div", null, picks, table);
  }

  /* ── the error types ───────────────────────────────────────────────── */

  function errorScreen() {
    var screen = el("div");
    var rows = INDEX.errors.filter(function (entry) {
      return inLanguage(entry.language);
    });
    var total = rows.reduce(function (sum, entry) {
      return sum + entry.total;
    }, 0);
    var version = state.evalVersion === null ? "" : String(state.evalVersion);
    screen.appendChild(
      el("h2", null, "Reference errors · " + state.language + " · " + total + " errors")
    );
    if (!rows.length) {
      screen.appendChild(el("p", { class: "hint", text: "No labelled errors in this language." }));
      return screen;
    }
    var table = el("table");
    var head = el("tr");
    head.appendChild(el("th", { class: "static", text: "Type" }));
    head.appendChild(el("th", { class: "num static", text: "n" }));
    shownVariants().forEach(function (variant) {
      head.appendChild(el("th", { class: "num static", text: variant.label }));
    });
    head.appendChild(
      el("th", {
        class: "num static",
        text: "missed by all",
        title: "errors that no version of any variant of this eval version fixed or located",
      })
    );
    table.appendChild(el("thead", null, head));
    var body = el("tbody");
    rows.forEach(function (entry) {
      var line = el("tr");
      line.appendChild(
        el("td", {
          text: entry.group + (state.language.indexOf("+") >= 0 ? " · " + entry.language : ""),
          title: "show the sentences with this error",
          onclick: function () {
            state.errorGroup = entry.group;
            state.filter = "error";
            state.layout = "grid";
            state.screen = "text";
            render();
          },
        })
      );
      line.appendChild(el("td", { class: "num", text: String(entry.total) }));
      shownVariants().forEach(function (variant) {
        var found = entry.shares[variant.id];
        if (!found || found.fixed === null) {
          line.appendChild(el("td", { class: "num" }, "–"));
          return;
        }
        var tile = el("td", { class: "num errtile" });
        var parts = [
          ["fixed", "✓", found.fixed],
          ["located", "~", found.located],
          ["missed", "⁁", found.missed],
        ];
        var bar = el("span", { class: "vbars" });
        var numbers = el("span", { class: "split" });
        parts.forEach(function (part) {
          var percent = Math.round((part[2] || 0) * 100);
          bar.appendChild(
            el("i", {
              class: "vbar " + part[0],
              style: "height:" + Math.max(percent, 3) + "%",
              title: part[1] + " " + percent + "%",
            })
          );
          numbers.appendChild(el("b", { class: part[0], text: part[1] + percent }));
        });
        tile.title =
          "fixed exactly " + share(found.fixed) + " · right place, other fix " +
          share(found.located) + " · missed " + share(found.missed) + " (mean of the repeats)";
        tile.appendChild(bar);
        tile.appendChild(numbers);
        line.appendChild(tile);
      });
      line.appendChild(
        el("td", { class: "num", text: String(entry.missed_by_all[version] || 0) })
      );
      body.appendChild(line);
    });
    table.appendChild(body);
    screen.appendChild(table);
    screen.appendChild(
      el("p", {
        class: "hint",
        text:
          "Per variant, the share of these errors ✓ fixed exactly, ~ changed in the right place " +
          "with another fix, ⁁ missed (percent, mean of the repeats; they add up to 100). Click a " +
          "type to see its sentences.",
      })
    );
    return screen;
  }

  /* ── footer ────────────────────────────────────────────────────────── */

  function footer() {
    var node = el("footer");
    node.appendChild(el("div", { text: "Built from " + INDEX.inputs.length + " cache(s):" }));
    var list = el("ul");
    INDEX.inputs.forEach(function (entry) {
      list.appendChild(
        el("li", {
          text:
            entry.directory + " · " + entry.files + " runs · " + entry.rows + " rows" +
            (entry.skipped.length ? " · " + entry.skipped.length + " skipped" : ""),
        })
      );
      entry.skipped.forEach(function (skip) {
        list.appendChild(
          el("li", { class: "hint", text: "skipped " + skip.file + ": " + skip.reason })
        );
      });
    });
    node.appendChild(list);
    node.appendChild(
      el("div", {
        text:
          INDEX.texts.length + " texts · " + INDEX.variants.length + " variants · " +
          Object.keys(INDEX.numbers).length + " scorecards",
      })
    );
    return node;
  }

  /* ── render ────────────────────────────────────────────────────────── */

  function render() {
    var app = document.getElementById("app");
    app.textContent = "";
    // The text on screen must be one the language and band still show, or the picker and the
    // arrows would point at nothing and the reader would be stuck on a text they filtered out.
    if (state.screen === "text") {
      var texts = shownTexts();
      var known = texts.some(function (one) {
        return one.id === state.text;
      });
      if (!known && texts.length) {
        state.text = texts[0].id;
        state.row = 0;
      }
    }
    app.appendChild(topbar());
    var main = el("main");
    if (state.info) main.appendChild(infoPanel());
    if (state.screen === "overview") main.appendChild(overview());
    else if (state.screen === "text") main.appendChild(textScreen());
    else main.appendChild(errorScreen());
    main.appendChild(footer());
    app.appendChild(main);
    // The sticky parts of a screen sit under the top bar, whose height depends on the window.
    var bar = app.querySelector(".topbar");
    if (bar) {
      document.documentElement.style.setProperty("--topbar-h", bar.offsetHeight + "px");
    }
  }

  document.addEventListener("keydown", function (event) {
    if (event.target.tagName === "SELECT" || event.target.tagName === "INPUT") return;
    // Ctrl/Cmd/Alt belong to the browser (tabs, back, forward).
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    // Up and Down move between sentences only where there is one sentence on screen; in the grid
    // and the page they keep scrolling.
    var stacked = state.screen === "text" && state.layout === "stack";
    if (event.key === "1") state.mode = "read";
    else if (event.key === "2") state.mode = "changes";
    else if (event.key === "3") state.mode = "original";
    else if (event.key === "ArrowDown" && stacked && TEXTS[state.text]) {
      moveRow(TEXTS[state.text], 1);
    } else if (event.key === "ArrowUp" && stacked && TEXTS[state.text]) {
      moveRow(TEXTS[state.text], -1);
    } else if (event.key === "ArrowRight" && state.screen === "text") {
      stepText(1);
    } else if (event.key === "ArrowLeft" && state.screen === "text") {
      stepText(-1);
    }
    else if (event.key === "Escape") state.drawer = null;
    else return;
    event.preventDefault();
    render();
  });

  render();
})();
