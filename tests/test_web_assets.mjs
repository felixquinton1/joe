import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { authenticatedFetch, pairBrowser } = require(
  "../src/joe/web_assets/app_auth.js"
);
const { initialLanguage, translate } = require(
  "../src/joe/web_assets/i18n.js"
);
// markdown.js s'installe sur `window` : on lui en fournit un.
globalThis.window = globalThis.window || {};
globalThis.document = globalThis.document || {
  createElement: () => ({ set textContent(v) { this._v = v; }, get innerHTML() { return this._v; } })
};
require("../src/joe/web_assets/markdown.js");
const { window } = globalThis;

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
