(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.JoeAuth = api;
}(typeof window === "undefined" ? null : window, function () {
  "use strict";

  async function pairBrowser({
    location = window.location,
    history = window.history,
    fetcher = window.fetch.bind(window)
  } = {}) {
    const fragment = new URLSearchParams(location.hash.slice(1));
    const token = fragment.get("token");
    if (!token) return false;
    const response = await fetcher("/api/pair", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` }
    });
    history.replaceState(null, "", `${location.pathname}${location.search}`);
    if (!response.ok) throw new Error("Appairage local Joe refusé");
    return true;
  }

  async function authenticatedFetch(input, init, fetcher = window.fetch.bind(window)) {
    const response = await fetcher(input, init);
    if (response.status === 401) {
      throw new Error(
        "Session Joe non appairée. Lance `joe url` pour rouvrir l’interface."
      );
    }
    return response;
  }

  return { authenticatedFetch, pairBrowser };
}));
