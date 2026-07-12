import { beforeAll, expect, it, vi } from "vitest";

const renderMock = vi.fn();
const createRootMock = vi.fn(() => ({ render: renderMock }));

vi.mock("react-dom/client", () => ({ default: { createRoot: createRootMock } }));

beforeAll(() => {
  const root = document.createElement("div");
  root.id = "root";
  document.body.append(root);
});

it("mounts the Studio application at the root element", async () => {
  await import("./main");

  expect(createRootMock).toHaveBeenCalledWith(document.getElementById("root"));
  expect(renderMock).toHaveBeenCalledOnce();
});
