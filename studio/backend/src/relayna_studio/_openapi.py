"""Normalize approved OpenAPI operations into bounded Studio request forms."""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import unquote, urlsplit


def _resolve(document: dict[str, Any], value: Any, trail: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Expected an OpenAPI object")
    if "$ref" not in value:
        return copy.deepcopy(value)
    ref = value["$ref"]
    if not isinstance(ref, str) or not ref.startswith("#/") or ref in trail or len(trail) > 16:
        raise ValueError("Only non-recursive local OpenAPI references are supported")
    target: Any = document
    try:
        for segment in unquote(ref[2:]).split("/"):
            target = target[segment.replace("~1", "/").replace("~0", "~")]
    except (KeyError, TypeError) as exc:
        raise ValueError("OpenAPI reference could not be resolved") from exc
    resolved = _resolve(document, target, (*trail, ref))
    return _merge(resolved, {key: item for key, item in value.items() if key != "$ref"})


def _merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    if (
        left.get("additionalProperties") is False and set(right.get("properties", {})) - set(left.get("properties", {}))
    ) or (
        right.get("additionalProperties") is False
        and set(left.get("properties", {})) - set(right.get("properties", {}))
    ):
        raise ValueError("Closed object composition needs an explicit input_schema")
    result = copy.deepcopy(left)
    for key, value in right.items():
        if key == "properties":
            properties = result.setdefault(key, {})
            for name, child in value.items():
                if name in properties and properties[name] != child:
                    raise ValueError("Overlapping composed properties need an explicit input_schema")
                properties[name] = child
        elif key == "required":
            result[key] = sorted(set(result.get(key, [])) | set(value))
        elif key in result and result[key] != value and key not in {"title", "description", "example", "examples"}:
            raise ValueError("Conflicting composed schema constraints need an explicit input_schema")
        else:
            result[key] = value
    return result


def _schema(document: dict[str, Any], value: Any, depth: int = 0, budget: list[int] | None = None) -> dict[str, Any]:
    budget = [0] if budget is None else budget
    budget[0] += 1
    if budget[0] > 1000:
        raise ValueError("OpenAPI request schema exceeds 1000 fields")
    if depth > 8:
        raise ValueError("Recursive or deeply nested schemas need an explicit input_schema")
    schema = _resolve(document, value)
    for part in schema.pop("allOf", []):
        schema = _merge(schema, _resolve(document, part))
    # FastAPI/Pydantic emits anyOf [T, null] for optional values.
    if "anyOf" in schema:
        variants = [_resolve(document, item) for item in schema.pop("anyOf")]
        nonnull = [item for item in variants if item.get("type") != "null"]
        if len(variants) != 2 or len(nonnull) != 1:
            raise ValueError("Multiple request variants need an explicit input_schema")
        schema = _merge(nonnull[0], schema)
        schema["nullable"] = True
    nullable = schema.pop("nullable", False)
    for key in (
        "example",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
        "xml",
        "externalDocs",
        "$schema",
        "$defs",
    ):
        schema.pop(key, None)
    schema = {key: item for key, item in schema.items() if not key.startswith("x-")}
    if "type" not in schema and "properties" in schema:
        schema["type"] = "object"
    kind = schema.get("type")
    if isinstance(kind, list):
        concrete = [item for item in kind if item != "null"]
        if len(concrete) != 1 or len(kind) != 2:
            raise ValueError("Multiple field types need an explicit input_schema")
        kind = concrete[0]
        nullable = True
    if kind == "object":
        properties = _resolve(document, schema.get("properties", {}))
        writable = {name: child for name, child in properties.items() if not _resolve(document, child).get("readOnly")}
        schema["properties"] = {name: _schema(document, child, depth + 1, budget) for name, child in writable.items()}
        schema["required"] = [name for name in schema.get("required", []) if name in writable]
        if isinstance(schema.get("additionalProperties"), dict):
            raise ValueError("Dictionary fields need an explicit input_schema")
        schema["additionalProperties"] = False
    elif kind == "array":
        schema["items"] = _schema(document, schema.get("items"), depth + 1, budget)
        schema["maxItems"] = min(schema.get("maxItems", 100), 100)
    for bound in ("Minimum", "Maximum"):
        exclusive = schema.get(f"exclusive{bound}")
        if isinstance(exclusive, bool):
            schema.pop(f"exclusive{bound}")
            if exclusive:
                schema[f"exclusive{bound}"] = schema.pop(bound.lower())
    if kind is not None:
        schema["type"] = [kind, "null"] if nullable else kind
    return schema


def _is_sdk_operation(path: str, operation: dict[str, Any] | None = None) -> bool:
    # Accept common API mount prefixes, but do not classify /orders or /tasks as SDK routes.
    path = re.sub(r"^/(?:api/)?v[0-9]+(?=/)", "", path)
    path = path.removeprefix("/api") if path.startswith("/api/") else path
    prefixes = ("/relayna", "/dlq", "/broker/dlq", "/failed-tasks", "/events", "/status", "/history")
    if any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes):
        return True
    if path in {"/workflow/topology", "/workflow/stages"} or re.fullmatch(r"/executions/[^/]+/graph", path):
        return True
    tags = (operation or {}).get("tags", [])
    return any(str(tag).lower() == "relayna" or str(tag).lower().startswith(("relayna:", "relayna.")) for tag in tags)


def _request_schema(document: dict[str, Any], journey: dict[str, Any]) -> dict[str, Any]:
    if not str(document.get("openapi", "")).startswith(("3.0.", "3.1.")):
        raise ValueError("OpenAPI 3.0 or 3.1 is required")
    path = journey["path"]
    if urlsplit(path).query or "{" in path:
        raise ValueError("Parameterized routes need an explicit input_schema and fixed request target")
    paths = _resolve(document, document.get("paths", {}))
    path_item = _resolve(document, paths.get(path, {}))
    method = journey["method"].lower()
    if method not in path_item:
        raise ValueError("The approved method and path are absent from OpenAPI")
    operation = _resolve(document, path_item[method])
    if _is_sdk_operation(path, operation):
        raise ValueError("Relayna SDK control endpoints are excluded from service load testing")
    parameters = {}
    for item in [*path_item.get("parameters", []), *operation.get("parameters", [])]:
        parameter = _resolve(document, item)
        parameters[(parameter.get("in"), parameter.get("name"))] = parameter
    if any(item.get("required") for item in parameters.values()):
        raise ValueError("Required path/query/header parameters need an explicit input_schema and request mapping")
    encoding = journey.get("requestEncoding", "json")
    body = _resolve(document, operation.get("requestBody", {}))
    if encoding == "none":
        if body.get("required"):
            raise ValueError("OpenAPI requires a request body for this operation")
        return {"type": "object", "properties": {}, "additionalProperties": False}
    content_type = {
        "json": "application/json",
        "form": "application/x-www-form-urlencoded",
        "multipart": "multipart/form-data",
        "raw": journey.get("contentType"),
    }[encoding]
    if not isinstance(content_type, str):
        raise ValueError("A request content type must be configured")
    media = _resolve(document, body.get("content", {})).get(content_type)
    if not isinstance(media, dict) or "schema" not in media:
        raise ValueError("The approved request encoding has no OpenAPI request schema")
    if media.get("encoding"):
        raise ValueError("Custom form encodings need an explicit input_schema")
    raw_schema = _resolve(document, media["schema"])
    if encoding == "multipart":
        properties = dict(raw_schema.get("properties", {}))
        fixtures = {item["field"] for item in journey.get("multipart", {}).get("files", [])}
        file_fields = {
            name for name, child in properties.items() if _resolve(document, child).get("format") == "binary"
        }
        if not fixtures.issubset(file_fields) or any(
            name not in fixtures for name in file_fields & set(raw_schema.get("required", []))
        ):
            raise ValueError("Multipart file fixtures do not match OpenAPI file fields")
        raw_schema["properties"] = {name: child for name, child in properties.items() if name not in file_fields}
        raw_schema["required"] = [name for name in raw_schema.get("required", []) if name not in file_fields]
    schema = _schema(document, raw_schema)
    if encoding == "raw":
        schema = {"type": "object", "additionalProperties": False, "required": ["body"], "properties": {"body": schema}}
    if schema.get("type") != "object":
        raise ValueError("This operation needs a concrete object request schema")
    return schema
