/* Align shared design tokens with Material's supported palette preference. */
(() => {
  const root = document.documentElement;
  let theme = "light";
  try {
    const scope = new URL(document.currentScript.dataset.mdScope, location);
    const palette = JSON.parse(localStorage.getItem(`${scope.pathname}.__palette`));
    if (palette?.color?.scheme === "slate") theme = "dark";
  } catch {
    // Missing, invalid or unavailable storage keeps the first visit light.
  }
  root.dataset.rnTheme = theme;

  new MutationObserver((records) => {
    if (records.some((record) => record.target === document.body)) {
      root.dataset.rnTheme = document.body.dataset.mdColorScheme === "slate" ? "dark" : "light";
    }
  }).observe(root, { subtree: true, attributes: true, attributeFilter: ["data-md-color-scheme"] });
})();
