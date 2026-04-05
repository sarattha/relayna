import type { CSSProperties } from "react";

const sectionStyle: CSSProperties = {
  border: "1px solid #d9d4c7",
  borderRadius: 16,
  padding: 20,
  background: "rgba(255,255,255,0.82)",
  boxShadow: "0 12px 40px rgba(73, 58, 38, 0.08)",
};

export function App() {
  return (
    <main
      style={{
        minHeight: "100vh",
        padding: "48px 24px",
        background:
          "radial-gradient(circle at top left, #f7e5b2 0%, #f4f1ea 35%, #dce7ef 100%)",
        color: "#2e2a24",
        fontFamily: "Georgia, 'Iowan Old Style', serif",
      }}
    >
      <div style={{ maxWidth: 1080, margin: "0 auto" }}>
        <header style={{ marginBottom: 28 }}>
          <p style={{ letterSpacing: 2, textTransform: "uppercase", fontSize: 12 }}>
            Relayna v2
          </p>
          <h1 style={{ fontSize: "clamp(2.8rem, 7vw, 5rem)", lineHeight: 1, margin: "0 0 12px" }}>
            Studio
          </h1>
          <p style={{ maxWidth: 720, fontSize: 18, lineHeight: 1.5 }}>
            Operator-facing workflow graph, runtime state, and DLQ inspection surface for Relayna.
          </p>
        </header>
        <section
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: 18,
          }}
        >
          <div style={sectionStyle}>
            <h2>Topology</h2>
            <p>Graph and route visualizations backed by `relayna.api` workflow endpoints.</p>
          </div>
          <div style={sectionStyle}>
            <h2>Runs</h2>
            <p>Current stage, status, and workflow diagnosis for active task runs.</p>
          </div>
          <div style={sectionStyle}>
            <h2>DLQ</h2>
            <p>Indexed queues, replay actions, and message detail inspection.</p>
          </div>
        </section>
      </div>
    </main>
  );
}
