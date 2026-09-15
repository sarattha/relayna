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

it("lets users supply null or a typed value for OpenAPI nullable fields", () => {
  const schema: InputSchema = { type: ["integer", "null"], minimum: 1, default: null };
  function Form() { const [value, setValue] = useState(initialInput(schema)); return <><RequestField schema={schema} value={value} onChange={setValue} label="Priority" /><output>{JSON.stringify(value)}</output></>; }
  render(<Form />);
  expect(screen.getByLabelText("Send null for Priority")).toBeChecked();
  fireEvent.click(screen.getByLabelText("Send null for Priority"));
  fireEvent.change(screen.getByLabelText("Priority *"), { target: { value: "5" } });
  expect(screen.getByText("5", { selector: "output" })).toBeInTheDocument();
});

it("can leave null and select a concrete nullable enum value", () => {
  const schema: InputSchema = { type: ["string", "null"], enum: [null, "fast", "safe"] };
  function Form() {
    const [value, setValue] = useState(initialInput(schema));
    return <RequestField schema={schema} value={value} onChange={setValue} label="Mode" />;
  }
  render(<Form />);
  expect(screen.getByLabelText("Send null for Mode")).toBeChecked();
  fireEvent.click(screen.getByLabelText("Send null for Mode"));
  expect(screen.getByLabelText("Mode *")).toHaveValue('"fast"');
  fireEvent.change(screen.getByLabelText("Mode *"), { target: { value: '"safe"' } });
  expect(screen.getByLabelText("Mode *")).toHaveValue('"safe"');
  fireEvent.click(screen.getByLabelText("Send null for Mode"));
  expect(screen.queryByLabelText("Mode *")).not.toBeInTheDocument();
});

it("shows and enforces exclusive bounds and starts inside them", () => {
  const schema: InputSchema = { type: "number", exclusiveMinimum: 0, exclusiveMaximum: 0.5 };
  function Form() {
    const [value, setValue] = useState(initialInput(schema));
    return <RequestField schema={schema} value={value} onChange={setValue} label="Ratio" />;
  }
  render(<Form />);
  const field = screen.getByLabelText("Ratio *");
  expect(field).toHaveValue(0.25);
  expect(screen.getByText("Must be greater than 0.")).toBeInTheDocument();
  expect(screen.getByText("Must be less than 0.5.")).toBeInTheDocument();
  fireEvent.change(field, { target: { value: "0" } });
  expect(field).toBeInvalid();
  fireEvent.change(field, { target: { value: "0.5" } });
  expect(field).toBeInvalid();
  fireEvent.change(field, { target: { value: "0.3" } });
  expect(field).toBeValid();
  expect(initialInput({ type: "integer", exclusiveMinimum: 0 })).toBe(1);
  expect(initialInput({ type: "integer", exclusiveMaximum: 0 })).toBe(-1);
  expect(initialInput({ type: "number", exclusiveMinimum: 0 })).toBe(1);
  expect(initialInput({ type: "number", exclusiveMaximum: 0 })).toBe(-1);
  expect(initialInput({ type: "number", exclusiveMinimum: 0, multipleOf: 0.1 })).toBe(0.1);
});

it("does not add items to a zero-capacity array", () => {
  render(<RequestField schema={{ type: "array", maxItems: 0, items: { type: "string" } }} value={[]} onChange={() => { throw new Error("must not add"); }} label="Tags" />);
  expect(screen.getByRole("button", { name: "Add tags item" })).toBeDisabled();
});

it("validates numeric multiples from zero rather than the HTML minimum", () => {
  const schema: InputSchema = { type: "number", minimum: 0.1, multipleOf: 0.2 };
  function Form() {
    const [value, setValue] = useState(initialInput(schema));
    return <RequestField schema={schema} value={value} onChange={setValue} label="Weight" />;
  }
  render(<Form />);
  const field = screen.getByLabelText("Weight *");
  expect(field).toHaveValue(0.2);
  expect(field).toBeValid();
  fireEvent.change(field, { target: { value: "0.4" } });
  expect(field).toBeValid();
  fireEvent.change(field, { target: { value: "0.3" } });
  expect(field).toBeInvalid();
});

it("rejects nonmatching patterns and clears validity when the schema changes", () => {
  const schema: InputSchema = { type: "string", pattern: "^[A-Z]{3}$" };
  function Form() {
    const [value, setValue] = useState<unknown>("bad");
    return <RequestField schema={schema} value={value} onChange={setValue} label="Code" />;
  }
  const { rerender } = render(<Form />);
  const field = screen.getByLabelText("Code *");
  expect(field).toBeInvalid();
  fireEvent.change(field, { target: { value: "ABC" } });
  expect(field).toBeValid();
  fireEvent.change(field, { target: { value: "a" } });
  expect(field).toBeInvalid();
  rerender(<RequestField schema={{ type: "string" }} value="a" onChange={() => {}} label="Code" />);
  expect(screen.getByLabelText("Code *")).toBeValid();
});

it("enforces integer values even with a fractional lower bound", () => {
  function Form() {
    const [value, setValue] = useState<unknown>(1);
    return <RequestField schema={{ type: "integer", minimum: 0.1 }} value={value} onChange={setValue} label="Count" />;
  }
  render(<Form />);
  expect(screen.getByLabelText("Count *")).toBeValid();
  fireEvent.change(screen.getByLabelText("Count *"), { target: { value: "1.1" } });
  expect(screen.getByLabelText("Count *")).toBeInvalid();
});

it("allows empty required strings and raw bodies when minLength permits them", () => {
  const { rerender } = render(<RequestField schema={{ type: "string" }} value="" onChange={() => {}} label="body" required />);
  expect(screen.getByLabelText("body *")).toBeValid();
  rerender(<RequestField schema={{ type: "string", minLength: 1 }} value="" onChange={() => {}} label="body" required />);
  expect(screen.getByLabelText("body *")).toBeInvalid();
  rerender(<RequestField schema={{ type: "string", maxLength: 1 }} value="😀" onChange={() => {}} label="body" required />);
  expect(screen.getByLabelText("body *")).toBeValid();
});

it("selects complete structured enum alternatives", () => {
  const schema: InputSchema = { type: "object", enum: [{ mode: "fast" }, { mode: "safe" }], properties: { mode: { type: "string" } } };
  function Form() {
    const [value, setValue] = useState(initialInput(schema));
    return <RequestField schema={schema} value={value} onChange={setValue} label="Config" />;
  }
  render(<Form />);
  const field = screen.getByLabelText("Config *");
  expect(field).toHaveValue('{"mode":"fast"}');
  fireEvent.change(field, { target: { value: '{"mode":"safe"}' } });
  expect(field).toHaveValue('{"mode":"safe"}');
  expect(screen.queryByLabelText("mode *")).not.toBeInTheDocument();
});

it("blocks rounded integer input outside the exact supported range", () => {
  function Form() {
    const [value, setValue] = useState<unknown>(1);
    return <RequestField schema={{ type: "integer" }} value={value} onChange={setValue} label="Identifier" />;
  }
  render(<Form />);
  const field = screen.getByLabelText("Identifier *");
  fireEvent.change(field, { target: { value: "9007199254740993" } });
  expect(field).toBeInvalid();
  fireEvent.change(field, { target: { value: "9007199254740991" } });
  expect(field).toBeValid();
});
