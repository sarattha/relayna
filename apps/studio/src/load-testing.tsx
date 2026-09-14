import { useId } from "react";
import { inputStyle, secondaryButtonStyle } from "./ui";

export type InputSchema = {
  type: "object" | "array" | "string" | "number" | "integer" | "boolean" | Array<"object" | "array" | "string" | "number" | "integer" | "boolean" | "null">;
  title?: string; description?: string; default?: unknown; enum?: unknown[];
  properties?: Record<string, InputSchema>; required?: string[]; items?: InputSchema;
  minimum?: number; maximum?: number; minLength?: number; maxLength?: number;
  minItems?: number; maxItems?: number; format?: string; pattern?: string; multipleOf?: number; exclusiveMinimum?: number; exclusiveMaximum?: number;
};
export type LoadProfile = {
  id: string; name: string; method: string; path: string; adapter: string; namespace: string;
  files?: { field: string; filename: string; content_type: string }[];
  schema_source?: "openapi" | "configured"; schema_revision?: string;
  input_schema: InputSchema; max_vus: number; max_iterations: number; max_duration_seconds: number;
};
export type LoadRun = {
  id: string; profile_name: string; environment: string; namespace: string;
  method: string; path: string; adapter: string; state: string; created_at: string;
  started_at?: string; finished_at?: string; output?: string; error?: string;
  cancel_requested?: boolean; cleanup_required?: boolean; evidence_error?: string;
  files?: { field: string; filename: string; content_type: string }[];
  request: { profile_id: string; inputs: Record<string, unknown>; vus: number; iterations: number; duration_seconds: number };
  result?: { status?: string; readiness_score?: number; evidence_coverage_percent?: number; limitations?: string[] };
  tasks?: { task_id: string; terminal_status: string; success: boolean; total_duration_ms: number }[];
};
export const terminalLoadStates = new Set(["completed", "failed", "cancelled"]);
export function initialInput(schema: InputSchema): unknown {
  if (schema.default !== undefined) return structuredClone(schema.default);
  if (schema.enum?.length) return schema.enum[0];
  if (Array.isArray(schema.type)) return initialInput({ ...schema, type: schema.type.find((item) => item !== "null")! });
  if (schema.type === "object") return Object.fromEntries(Object.entries(schema.properties || {})
    .filter(([key, child]) => schema.required?.includes(key) || child.default !== undefined)
    .map(([key, child]) => [key, initialInput(child)]));
  if (schema.type === "array") return Array.from({ length: schema.minItems || 0 }, () => initialInput(schema.items!));
  if (schema.type === "boolean") return false;
  if (schema.type === "number" || schema.type === "integer") return schema.minimum ?? 0;
  return "";
}

export function RequestField({ schema, value, onChange, label, required = true }: {
  schema: InputSchema; value: unknown; onChange: (value: unknown) => void; label: string; required?: boolean;
}) {
  const id = useId();
  const name = schema.title || label;
  if (Array.isArray(schema.type)) {
    const concrete = schema.type.find((item) => item !== "null")!;
    const concreteSchema = { ...schema, type: concrete, enum: schema.enum?.filter((item) => item !== null) };
    return <div className="load-field">{(!schema.enum || schema.enum.includes(null)) && <label><input type="checkbox" checked={value === null} onChange={(event) => onChange(event.target.checked ? null : initialInput({ ...concreteSchema, default: undefined }))} /> Send null for {name}</label>}{value !== null && <RequestField schema={concreteSchema} value={value} onChange={onChange} label={label} required={required} />}</div>;
  }
  if (schema.type === "object") {
    const fields = (value || {}) as Record<string, unknown>;
    return <fieldset className="load-input-group"><legend>{name}</legend>{schema.description && <p>{schema.description}</p>}
      {Object.entries(schema.properties || {}).map(([key, child]) => {
        const needed = schema.required?.includes(key) || false;
        const present = Object.prototype.hasOwnProperty.call(fields, key);
        return <div key={key} className="load-field">
          {!needed && <label className="load-optional"><input type="checkbox" checked={present} onChange={(event) => {
            const next = { ...fields };
            if (event.target.checked) next[key] = initialInput(child); else delete next[key];
            onChange(next);
          }} /> Include {child.title || key}</label>}
          {(needed || present) && <RequestField schema={child} value={fields[key]} required={needed || present} label={key} onChange={(next) => onChange({ ...fields, [key]: next })} />}
        </div>;
      })}
    </fieldset>;
  }
  if (schema.type === "array") {
    const items = (value || []) as unknown[];
    return <fieldset className="load-input-group"><legend>{name}</legend>{schema.description && <p>{schema.description}</p>}
      {items.map((item, index) => <div className="load-array-item" key={index}>
        <RequestField schema={schema.items!} value={item} label={`${name} ${index + 1}`} onChange={(next) => onChange(items.map((old, i) => i === index ? next : old))} />
        <button type="button" style={secondaryButtonStyle} disabled={items.length <= (schema.minItems || 0)} onClick={() => onChange(items.filter((_, i) => i !== index))}>Remove {index + 1}</button>
      </div>)}
      <button type="button" style={secondaryButtonStyle} disabled={items.length >= (schema.maxItems || 100)} onClick={() => onChange([...items, initialInput(schema.items!)])}>Add {name.toLowerCase()} item</button>
    </fieldset>;
  }
  return <div className="load-field"><label htmlFor={id}>{name}{required ? " *" : ""}</label>
    {schema.enum ? <select id={id} style={inputStyle} required={required} value={JSON.stringify(value)} onChange={(event) => onChange(JSON.parse(event.target.value))}>
      {schema.enum.map((option) => <option key={JSON.stringify(option)} value={JSON.stringify(option)}>{String(option)}</option>)}
    </select> : schema.type === "boolean" ? <select id={id} style={inputStyle} value={String(value ?? false)} onChange={(event) => onChange(event.target.value === "true")}><option value="false">No</option><option value="true">Yes</option></select>
      : schema.type === "string" ? <textarea id={id} style={inputStyle} required={required} rows={2} value={String(value ?? "")} minLength={schema.minLength} maxLength={schema.maxLength} onChange={(event) => onChange(event.target.value)} />
        : <input id={id} style={inputStyle} type="number" required={required} value={value === undefined ? "" : Number(value)} min={schema.minimum} max={schema.maximum} step={schema.multipleOf ?? (schema.type === "integer" ? 1 : "any")} onChange={(event) => onChange(event.target.value === "" ? undefined : Number(event.target.value))} />}
    {schema.description && <small>{schema.description}</small>}
    {schema.format && <small>Format: {schema.format}</small>}{schema.pattern && <small>Must match: {schema.pattern}</small>}
  </div>;
}
