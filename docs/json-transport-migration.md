# JSON transport migration after v1.4.29

Relayna intentionally changes its RabbitMQ message-body JSON codec after the
`v1.4.29` production freeze. This is a wire-format and input-domain change,
approved specifically for the JSON transport optimization. Public Python APIs,
envelope schemas, routing, Redis data, canonical hashes, deduplication inputs,
HTTP responses, and SSE payloads are not part of this change.

## Production strategy

Relayna now uses Pydantic Core for both sides of its AMQP transport:

- outbound bodies use `pydantic_core.to_json` after the same Relayna model or
  mapping preparation used in `v1.4.29`;
- inbound bodies use `pydantic_core.from_json`, followed by the same alias
  normalization, envelope selection, and Pydantic validation used in
  `v1.4.29`.

The benchmark also evaluated orjson. It was faster for large outbound bodies,
but Relayna does not add it as a production dependency because it rejects
outbound integers beyond 64 bits, loses precision for the tested inbound
`2**100` integer, rejects non-finite input tokens, and rejects non-string
mapping keys. Pydantic Core provides material complete-path gains without that
additional native dependency or JSON-domain reduction.

Pydantic Core 2.41.5 already ships in Relayna installations through Pydantic.
PyPI provides CPython 3.13 and 3.14 wheels for Linux x86_64/aarch64 and macOS
x86_64/ARM64, matching Relayna's supported interpreter and deployment targets.

## Intentional breaks

### Outbound whitespace and exact bytes

AMQP JSON is now compact:

```text
v1.4.29: {"task_id": "task-1", "payload": {"value": 7}}
new:     {"task_id":"task-1","payload":{"value":7}}
```

Object field order and parsed values remain stable for Relayna-prepared
envelopes, but byte length and byte-for-byte content change. Consumers must not
compare, sign, hash, or deduplicate raw AMQP bodies unless they own an explicit
canonicalization step.

Relayna's own canonical hashes, status-event IDs, persisted Redis JSON, workflow
contract signatures, and deduplication inputs continue using their existing
serializers and bytes.

### UTF-8

Inbound AMQP bodies must now contain valid UTF-8. In `v1.4.29`, Relayna decoded
bytes with replacement before JSON parsing, so an invalid byte inside a JSON
string could become U+FFFD and reach a handler. The new parser rejects that
body in the JSON parse stage.

Operationally, invalid UTF-8 is classified as `malformed_json`. With retry/DLQ
enabled it follows the existing malformed-message path; without retry it is
rejected or acknowledged according to the existing consumer-specific malformed
message policy.

### Nesting depth

Relayna preserves messages at the outbound encoder's supported nesting depth.
When Pydantic Core reaches its lower inbound recursion limit, Relayna retries
that body with the standard-library parser after a strict UTF-8 decode. This
fallback does not restore the `v1.4.29` replacement-character behavior:
invalid UTF-8 remains `malformed_json`.

### Huge integers

Integers beyond signed or unsigned 64-bit ranges remain accepted and exact on
both outbound and inbound paths. The compatibility suite covers `2**100`.
Relayna did not choose orjson because its behavior does not preserve this
domain.

### Non-finite floats

`NaN`, `Infinity`, and `-Infinity` remain accepted transport extensions and are
emitted as bare tokens. These values are not RFC 8259 JSON and may be rejected
by strict non-Relayna clients. Applications that require interoperable standard
JSON should reject or convert non-finite values before publishing.

### Mapping keys

Relayna envelope mappings should use string keys. For raw mapping publishers,
Pydantic Core stringifies common non-string keys:

- integers, finite floats, and booleans retain the same strings produced by the
  `v1.4.29` stdlib encoder;
- `None` now becomes `"None"` instead of `"null"`;
- some additional keys, including tuples, datetimes, and UUIDs, may now be
  stringified where the old encoder rejected them;
- unsupported keys still raise a serialization error.

Do not rely on non-string key coercion for a stable external contract. Convert
keys explicitly before publishing.

### Aliases

The built-in `documentId` alias and configured payload aliases continue to work.
Parsing still produces a mapping before Relayna runs its existing recursive
normalization for task batches and its normal per-message normalization for
task, workflow, and status messages. Alias keys continue to be dropped when the
current normalization path requests it.

### Malformed JSON versus invalid envelopes

The rejection boundary is preserved:

- invalid UTF-8 or malformed JSON syntax fails during parsing and is classified
  as `malformed_json`;
- valid JSON that does not satisfy the selected Relayna envelope fails during
  Pydantic validation and is classified as `invalid_envelope`.

Exception classes and message text differ because parsing now comes from
Pydantic Core. Integrations should use Relayna's rejection reason rather than
matching parser exception strings.

## Scope by data path

The new codec is used for:

- task, batch, workflow, status, retry, and dead-letter AMQP publishing owned by
  Relayna;
- task, aggregation, workflow, status hub, and status history AMQP parsing;
- DLQ replay override bodies.

The following remain byte-compatible with `v1.4.29`:

- original DLQ body replay when no override is supplied;
- Redis status, observability, task-lease, workflow-contract, and DLQ records;
- status-event canonical hashes and deduplication inputs;
- HTTP/API and SSE JSON output;
- diagnostic decoding of stored dead-letter bodies.

## Rollout guidance

For valid UTF-8 envelope traffic, rolling upgrades are interoperable: old
consumers parse compact new bodies, and new consumers parse spaced old bodies.
Before upgrading:

1. Search consumers for raw-body byte comparisons, signatures, or size
   assumptions.
2. Confirm publishers emit valid UTF-8 and string mapping keys.
3. Remove exception-message matching and use `malformed_json` or
   `invalid_envelope` rejection reasons.
4. Drain or inspect poison messages that depend on UTF-8 replacement.
5. Run a canary and monitor malformed-message/DLQ counts during rollout.

Rollback to `v1.4.29` remains possible for valid JSON because its stdlib parser
accepts compact bodies. Raw-body hashes or signatures generated after the
upgrade will not match pre-upgrade bytes.

## Reproduce the evidence

Run the self-contained CPU benchmark:

```bash
uv run --extra benchmark python -m benchmarks run json-engine-evaluation
```

The generated report is
`reports/json-engine-evaluation.html`. orjson remains in the optional
benchmark-only dependency group and is not required by Relayna production.
