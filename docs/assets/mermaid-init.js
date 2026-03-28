document.addEventListener("DOMContentLoaded", async () => {
  if (typeof window.mermaid === "undefined") {
    return;
  }

  const codeBlocks = document.querySelectorAll("pre > code.language-mermaid");
  if (codeBlocks.length === 0) {
    return;
  }

  for (const code of codeBlocks) {
    const pre = code.parentElement;
    if (!pre || !pre.parentElement) {
      continue;
    }
    const container = document.createElement("div");
    container.className = "mermaid";
    container.textContent = code.textContent || "";
    pre.replaceWith(container);
  }

  window.mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });
  await window.mermaid.run({ querySelector: ".mermaid" });
});
