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
  assert.match(translate("en", "slogan"), /cool kids/);
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
