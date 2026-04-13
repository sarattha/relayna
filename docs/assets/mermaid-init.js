let mermaidInitialized = false;

async function renderMermaidDiagrams() {
  if (typeof window.mermaid === "undefined") {
    return;
  }

  const codeBlocks = document.querySelectorAll("pre > code.language-mermaid");
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

  if (!mermaidInitialized) {
    window.mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });
    mermaidInitialized = true;
  }
  await window.mermaid.run({ querySelector: ".mermaid" });
}

if (typeof document$ !== "undefined") {
  document$.subscribe(() => {
    void renderMermaidDiagrams();
  });
} else {
  document.addEventListener("DOMContentLoaded", () => {
    void renderMermaidDiagrams();
  });
}
