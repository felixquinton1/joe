import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import test from "node:test";

const require = createRequire(import.meta.url);
const { authenticatedFetch, pairBrowser } = require(
  "../src/joe/web_assets/app_auth.js"
);
const { apply, initialLanguage, translate, translations } = require(
  "../src/joe/web_assets/i18n.js"
);
// markdown.js s'installe sur `window` : on lui en fournit un.
globalThis.window = globalThis.window || {};
globalThis.document = globalThis.document || {
  createElement: () => ({ set textContent(v) { this._v = v; }, get innerHTML() { return this._v; } })
};
require("../src/joe/web_assets/markdown.js");
const { window } = globalThis;
const katex = require("../src/joe/web_assets/katex.min.js");

test("pairs from the fragment and removes it from browser history", async () => {
  const calls = [];
  const history = {
    replaceState(...args) {
      calls.push(["history", ...args]);
    },
  };
  const paired = await pairBrowser({
    location: {
      hash: "#token=secret",
      pathname: "/",
      search: "?project=one",
    },
    history,
    async fetcher(path, options) {
      calls.push(["fetch", path, options]);
      return { ok: true };
    },
  });

  assert.equal(paired, true);
  assert.equal(calls[0][1], "/api/pair");
  assert.equal(calls[0][2].headers.Authorization, "Bearer secret");
  assert.deepEqual(calls[1], ["history", null, "", "/?project=one"]);
});

test("does not call the server without a pairing fragment", async () => {
  const paired = await pairBrowser({
    location: { hash: "", pathname: "/", search: "" },
    history: {},
    async fetcher() {
      throw new Error("fetch must not run");
    },
  });

  assert.equal(paired, false);
});

test("turns an API 401 into an actionable pairing message", async () => {
  await assert.rejects(
    () => authenticatedFetch(
      "/api/conversations",
      undefined,
      async () => ({ status: 401 })
    ),
    /joe url/
  );
});

test("selects a supported interface language and falls back to French", () => {
  assert.equal(initialLanguage({ getItem: () => "en" }, "fr-FR"), "en");
  assert.equal(initialLanguage({ getItem: () => null }, "en-US"), "en");
  assert.equal(initialLanguage({ getItem: () => null }, "de-DE"), "fr");
  assert.equal(translate("en", "send"), "Send");
  assert.equal(translate("fr", "send"), "Envoyer");
});

test("keeps French and English catalogs in lockstep", () => {
  assert.deepEqual(
    Object.keys(translations.en).sort(),
    Object.keys(translations.fr).sort()
  );
  for (const [language, catalog] of Object.entries(translations)) {
    for (const [key, value] of Object.entries(catalog)) {
      assert.ok(value.trim(), `${language}.${key} must not be empty`);
    }
  }
});

test("interpolates translated dynamic labels", () => {
  assert.equal(
    translate("en", "reset_in", { duration: "2 h" }),
    "Reset in 2 h"
  );
  assert.equal(
    translate("fr", "old_backend", { version: "1.2.3" }),
    "Le serveur Joe 1.2.3 utilise encore un ancien backend. Arrête-le avec Ctrl+C, relance joe, puis recharge cette page."
  );
});

test("all translation markers in the page exist in both catalogs", () => {
  const html = readFileSync(new URL("../src/joe/web_assets/index.html", import.meta.url), "utf8");
  const keys = [...html.matchAll(/data-i18n(?:-placeholder|-title|-aria-label)?="([^"]+)"/g)]
    .map(match => match[1]);
  for (const key of keys) {
    assert.ok(translations.fr[key], `missing French translation: ${key}`);
    assert.ok(translations.en[key], `missing English translation: ${key}`);
  }
});

test("live history refresh deduplicates the optimistic user prompt by run id", () => {
  const source = readFileSync(
    new URL("../src/joe/web_assets/app_conversations.js", import.meta.url),
    "utf8"
  );
  const app = readFileSync(
    new URL("../src/joe/web_assets/app.js", import.meta.url),
    "utf8"
  );
  assert.match(source, /querySelectorAll\("\.message\.user"\)/);
  assert.match(source, /dataset\.source === message\.content/);
  assert.match(app, /optimisticUserBubble\.closest\("\.message"\)\.dataset\.runId = run_id/);
});

test("live history refresh reuses the pending assistant bubble by run id", () => {
  const source = readFileSync(
    new URL("../src/joe/web_assets/app_conversations.js", import.meta.url),
    "utf8"
  );
  assert.match(source, /activeRun\?\.runId === message\.run_id/);
  assert.match(source, /querySelectorAll\("\.message\.assistant"\)/);
  assert.match(source, /existing\.dataset\.runId = message\.run_id/);
  const app = readFileSync(
    new URL("../src/joe/web_assets/app.js", import.meta.url),
    "utf8"
  );
  assert.match(app, /finalBubble\.closest\("\.message"\)\.dataset\.runId = run_id/);
});

test("conversation navigation remembers and restores the last opened item", () => {
  const source = readFileSync(
    new URL("../src/joe/web_assets/app_conversations.js", import.meta.url),
    "utf8"
  );
  assert.match(source, /joe-last-conversation-id/);
  assert.match(source, /localStorage\.getItem\(lastConversationKey\)/);
  assert.match(source, /state\.conversations\.find\(item => item\.id === remembered\)/);
  assert.match(source, /localStorage\.setItem\(lastConversationKey, conversationId\)/);
});

test("applies translated text, placeholders, titles, and aria labels", () => {
  const nodes = {
    "[data-i18n]": [{ dataset: { i18n: "send" }, textContent: "" }],
    "[data-i18n-placeholder]": [{ dataset: { i18nPlaceholder: "optional" }, placeholder: "" }],
    "[data-i18n-title]": [{ dataset: { i18nTitle: "new_project" }, title: "" }],
    "[data-i18n-aria-label]": [{ dataset: { i18nAriaLabel: "automation_type" }, setAttribute(name, value) { this[name] = value; } }]
  };
  const fakeDocument = {
    documentElement: { lang: "fr" },
    querySelectorAll: selector => nodes[selector] || []
  };
  apply(fakeDocument, "en");
  assert.equal(nodes["[data-i18n]"][0].textContent, "Send");
  assert.equal(nodes["[data-i18n-placeholder]"][0].placeholder, "Optional");
  assert.equal(nodes["[data-i18n-title]"][0].title, "New project");
  assert.equal(nodes["[data-i18n-aria-label]"][0]["aria-label"], "Automation type");
});

test("turns an API 403 into an actionable profile message", async () => {
  // Sans cela, le corps d'erreur JSON est consommé comme une donnée valide
  // et casse le rendu bien plus loin (« projects is not iterable »).
  await assert.rejects(
    () => authenticatedFetch("/api/projects", undefined, async () => ({
      status: 403,
      ok: false,
    })),
    /profile maintainer/
  );
});

test("lets a successful response through untouched", async () => {
  const expected = { status: 200, ok: true };
  assert.equal(
    await authenticatedFetch("/api/projects", undefined, async () => expected),
    expected
  );
});

test("turns a joe:question block into a question and a clean body", () => {
  const parse = window.JoeMarkdown.extractQuestion;
  const parsed = parse(
    'Voici mon analyse.\n\n```joe:question\n'
    + '{"question":"Quelle option ?","options":["Régénérer","Restreindre"]}\n```'
  );
  assert.equal(parsed.question, "Quelle option ?");
  assert.deepEqual(parsed.options, ["Régénérer", "Restreindre"]);
  // Le bloc est retiré du corps affiché.
  assert.equal(parsed.body, "Voici mon analyse.");
});

test("turns an unfenced question payload into buttons", () => {
  // Le balisage est demandé au modèle, jamais garanti : sans ce repli, le JSON
  // s'affichait brut à la place des boutons.
  const parse = window.JoeMarkdown.extractQuestion;
  const parsed = parse(
    'Voici mon analyse.\n\n'
    + '{"question": "Quel claim pilote le MVP ?", '
    + '"options": ["Induction multi-tâches", "Recherche Bermudan seule"]}'
  );
  assert.equal(parsed.question, "Quel claim pilote le MVP ?");
  assert.deepEqual(parsed.options, ["Induction multi-tâches", "Recherche Bermudan seule"]);
  assert.equal(parsed.body, "Voici mon analyse.");
});

test("accepts another fence label and a single-line block", () => {
  const parse = window.JoeMarkdown.extractQuestion;
  const labelled = parse(
    'Analyse.\n\n```json\n{"question":"Quelle option ?","options":["A","B"]}\n```'
  );
  assert.deepEqual(labelled.options, ["A", "B"]);
  assert.equal(labelled.body, "Analyse.");
  // Bloc replié sur une ligne : aucun ``` ne doit rester dans le corps.
  const inline = parse(
    'Analyse.\n\n```joe:question {"question":"Quelle option ?","options":["A","B"]}```'
  );
  assert.deepEqual(inline.options, ["A", "B"]);
  assert.equal(inline.body, "Analyse.");
});

test("keeps the last question when the answer quotes an earlier one", () => {
  const parse = window.JoeMarkdown.extractQuestion;
  const parsed = parse(
    'Tu avais demandé {"question":"Ancienne ?","options":["X","Y"]}.\n\n'
    + '```joe:question\n{"question":"Nouvelle ?","options":["A","B"]}\n```'
  );
  assert.equal(parsed.question, "Nouvelle ?");
});

test("ignores a malformed or single-option question block", () => {
  const parse = window.JoeMarkdown.extractQuestion;
  assert.equal(parse("texte sans bloc"), null);
  assert.equal(parse('```joe:question\npas du json\n```'), null);
  // Une seule option n'est pas un choix : on n'affiche pas de carte.
  assert.equal(
    parse('```joe:question\n{"question":"Ok ?","options":["Oui"]}\n```'),
    null
  );
});

test("renders LaTeX delimiters as math blocks and inline math", () => {
  const target = {
    innerHTML: "",
    dataset: {},
    classList: { add() {} },
    querySelectorAll() { return []; }
  };
  window.JoeMarkdown.renderMarkdown(
    target,
    "Objectif :\n\n\\[\n\\mathcal L = \\lambda_V \\mathcal L_{prix}\n\\]"
      + "\n\nLa dérivée \\(\\Delta V\\) et $\\Gamma V$ restent contrôlées."
  );
  assert.match(target.innerHTML, /class="math-block"/);
  assert.match(target.innerHTML, /\\mathcal L_\{prix\}/);
  assert.match(target.innerHTML, /class="math-inline"/);
  assert.match(target.innerHTML, /\\Gamma V/);
  assert.doesNotMatch(target.innerHTML, /<em>\{prix\}/);
});

test("vendored KaTeX converts Joe formulas to native MathML", () => {
  const rendered = katex.renderToString(
    String.raw`\mathcal L=\lambda_V\mathcal L_{\text{prix}}+\lambda_\Delta\mathcal L_\Delta`,
    { output: "mathml", throwOnError: false }
  );
  assert.match(rendered, /<math/);
  assert.match(rendered, /<msub>/);
  assert.match(rendered, /prix/);
});

test("turns a validated plan into autonomous steps", () => {
  const steps = window.JoeMarkdown.planSteps(
    "# Plan\n\nContexte à ignorer.\n\n"
    + "1. **Corriger** le routeur\n"
    + "2) Lancer `pytest`\n"
    + "- Documenter la reprise\n"
    + "\nUne phrase de conclusion."
  );
  // Seules les lignes de liste deviennent des étapes ; le gras et les backticks
  // ne doivent pas se retrouver dans le prompt envoyé au fournisseur.
  assert.deepEqual(steps, [
    "Corriger le routeur",
    "Lancer pytest",
    "Documenter la reprise"
  ]);
});

test("reports no step when the plan has no list", () => {
  assert.deepEqual(window.JoeMarkdown.planSteps("Juste un paragraphe."), []);
  assert.deepEqual(window.JoeMarkdown.planSteps(""), []);
  assert.deepEqual(window.JoeMarkdown.planSteps(null), []);
});
