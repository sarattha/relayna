import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { initialInput, RequestField, type InputSchema } from "./load-testing";

describe("structured service inputs", () => {
  it("handles nested arrays, numeric and boolean fields without a JSON editor", () => {
    const schema: InputSchema = { type: "object", required: ["enabled", "weights"], properties: {
      enabled: { type: "boolean" },
      weights: { type: "array", minItems: 1, maxItems: 2, items: { type: "number", minimum: 0, maximum: 1 } },
    } };
    function Form() {
      const [value, setValue] = useState(initialInput(schema));
      return <><RequestField schema={schema} value={value} onChange={setValue} label="Inputs" /><output>{JSON.stringify(value)}</output></>;
    }
    render(<Form />);
    fireEvent.change(screen.getByLabelText("enabled *"), { target: { value: "true" } });
    fireEvent.change(screen.getByLabelText("weights 1 *"), { target: { value: "0.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Add weights item" }));
    expect(screen.getByRole("button", { name: "Add weights item" })).toBeDisabled();
    expect(screen.getByText('{"enabled":true,"weights":[0.5,0]}')).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove 2" }));
    expect(screen.getByRole("button", { name: "Remove 1" })).toBeDisabled();
  });
});
