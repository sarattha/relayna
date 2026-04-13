#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen


SUPPORTED_ROUTES = [
    "status.latest",
    "status.history",
    "workflow.topology",
    "dlq.messages",
    "execution.graph",
    "health.workers",
]
DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_PUBLIC_HOST = "host.docker.internal"
DEFAULT_PORT = 9100
DEFAULT_STUDIO_URL = "http://localhost:8000"
MOCK_BASE_TIME = datetime.now(UTC).replace(microsecond=0)
TIMESTAMP_OFFSETS = {
    "07:50:00": timedelta(minutes=4, seconds=30),
    "07:55:00": timedelta(minutes=4),
    "07:55:10": timedelta(minutes=3, seconds=50),
    "08:00:00": timedelta(minutes=3, seconds=30),
    "08:00:02": timedelta(minutes=3, seconds=28),
    "08:00:05": timedelta(minutes=3, seconds=25),
    "08:01:00": timedelta(minutes=3),
    "08:01:10": timedelta(minutes=2, seconds=50),
    "08:01:15": timedelta(minutes=2, seconds=45),
    "08:02:10": timedelta(minutes=2),
    "08:04:20": timedelta(minutes=1, seconds=20),
    "08:05:30": timedelta(seconds=50),
    "08:06:10": timedelta(seconds=40),
    "08:06:30": timedelta(seconds=20),
    "08:08:09": timedelta(seconds=9),
    "08:08:10": timedelta(seconds=8),
    "08:08:11": timedelta(seconds=7),
    "08:08:12": timedelta(seconds=6),
    "08:08:14": timedelta(seconds=4),
}


def utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def timestamp_for(value: str) -> str:
    offset = TIMESTAMP_OFFSETS.get(value)
    if offset is None:
        raise KeyError(f"Unsupported mock timestamp '{value}'.")
    return (MOCK_BASE_TIME - offset).isoformat().replace("+00:00", "Z")


def task_graph(*, task_id: str, status: str, correlation_id: str | None, parent_task_id: str | None) -> dict[str, Any]:
    annotations = {
        key: value
        for key, value in {
            "correlation_id": correlation_id,
            "parent_task_id": parent_task_id,
        }.items()
        if value is not None
    }
    return {
        "task_id": task_id,
        "topology_kind": "shared_tasks_shared_status",
        "summary": {
            "status": status,
            "started_at": timestamp_for("08:00:00"),
            "ended_at": None if status not in {"completed", "failed"} else timestamp_for("08:06:30"),
            "duration_ms": None if status not in {"completed", "failed"} else 390000,
            "graph_completeness": "full",
        },
        "nodes": [
            {
                "id": f"task:{task_id}",
                "kind": "task",
                "task_id": task_id,
                "label": task_id,
                "timestamp": timestamp_for("08:00:00"),
                "annotations": annotations,
            },
            {
                "id": f"stage:{task_id}:ingress",
                "kind": "stage",
                "label": "ingress",
                "timestamp": timestamp_for("08:00:05"),
                "annotations": {},
            },
            {
                "id": f"stage:{task_id}:worker",
                "kind": "stage",
                "label": "worker",
                "timestamp": timestamp_for("08:01:15"),
                "annotations": {},
            },
        ],
        "edges": [
            {
                "source": f"task:{task_id}",
                "target": f"stage:{task_id}:ingress",
                "kind": "enqueued",
                "timestamp": timestamp_for("08:00:02"),
                "annotations": {},
            },
            {
                "source": f"stage:{task_id}:ingress",
                "target": f"stage:{task_id}:worker",
                "kind": "dequeued",
                "timestamp": timestamp_for("08:01:10"),
                "annotations": {},
            },
        ],
        "annotations": {},
        "related_task_ids": [],
    }


@dataclass(frozen=True)
class MockTask:
    task_id: str
    latest_event: dict[str, Any]
    history: list[dict[str, Any]]
    graph: dict[str, Any]


@dataclass(frozen=True)
class MockService:
    service_id: str
    name: str
    environment: str
    tags: tuple[str, ...]
    path_prefix: str
    service_title: str
    topology: dict[str, Any]
    workers: tuple[dict[str, Any], ...]
    tasks: tuple[MockTask, ...]
    dlq_messages: tuple[dict[str, Any], ...] = ()

    def base_url(self, *, public_host: str, port: int) -> str:
        return f"http://{public_host}:{port}{self.path_prefix}"

    def registration_payload(self, *, public_host: str, port: int) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "name": self.name,
            "base_url": self.base_url(public_host=public_host, port=port),
            "environment": self.environment,
            "tags": list(self.tags),
            "auth_mode": "internal_network",
        }

    def capability_document(self) -> dict[str, Any]:
        return {
            "relayna_version": "1.4.4",
            "topology_kind": "shared_tasks_shared_status",
            "alias_config_summary": {
                "aliasing_enabled": False,
                "payload_aliases": {},
                "http_aliases": {},
            },
            "supported_routes": SUPPORTED_ROUTES,
            "feature_flags": ["mock_service"],
            "service_metadata": {
                "service_title": self.service_title,
                "capability_path": "/relayna/capabilities",
                "discovery_source": "live",
                "compatibility": "capabilities_v1",
            },
        }

    def task_map(self) -> dict[str, MockTask]:
        return {task.task_id: task for task in self.tasks}


MOCK_SERVICES = (
    MockService(
        service_id="orders-api",
        name="Orders API",
        environment="mock",
        tags=("mock", "checkout", "core"),
        path_prefix="/mock/orders-api",
        service_title="Orders API",
        topology={
            "workflow_exchange": "orders.workflow",
            "status_queue": "orders.status",
            "stages": [
                {
                    "name": "order-intake",
                    "queue": "orders.intake",
                    "binding_keys": ["orders.created"],
                    "publish_routing_key": "orders.validated",
                    "description": "Accepts checkout payloads and allocates order ids.",
                    "role": "ingress",
                    "owner": "commerce",
                    "tags": ["mock", "orders"],
                    "allowed_next_stages": ["order-allocator"],
                    "terminal": False,
                },
                {
                    "name": "order-allocator",
                    "queue": "orders.allocator",
                    "binding_keys": ["orders.validated"],
                    "publish_routing_key": "orders.completed",
                    "description": "Reserves inventory and marks the order ready for downstream services.",
                    "role": "worker",
                    "owner": "commerce",
                    "tags": ["mock", "orders"],
                    "allowed_next_stages": [],
                    "terminal": True,
                },
            ],
            "entry_routes": [
                {"name": "checkout", "routing_key": "orders.created", "target_stage": "order-intake"},
            ],
            "edges": [
                {"source": "order-intake", "target": "order-allocator", "routing_key": "orders.validated"},
            ],
        },
        workers=(
            {"worker_name": "orders-intake-0", "running": True, "last_heartbeat_at": timestamp_for("08:08:10")},
            {"worker_name": "orders-allocator-0", "running": True, "last_heartbeat_at": timestamp_for("08:08:09")},
        ),
        tasks=(
            MockTask(
                task_id="order-1001",
                latest_event={
                    "status": "completed",
                    "stage": "order-allocator",
                    "timestamp": timestamp_for("08:05:30"),
                    "correlation_id": "checkout-1001",
                    "meta": {"parent_task_id": "cart-1001"},
                },
                history=[
                    {
                        "task_id": "order-1001",
                        "status": "queued",
                        "stage": "order-intake",
                        "timestamp": timestamp_for("08:00:00"),
                        "correlation_id": "checkout-1001",
                        "meta": {"parent_task_id": "cart-1001"},
                    },
                    {
                        "task_id": "order-1001",
                        "status": "processing",
                        "stage": "order-allocator",
                        "timestamp": timestamp_for("08:02:10"),
                        "correlation_id": "checkout-1001",
                        "meta": {"parent_task_id": "cart-1001"},
                    },
                    {
                        "task_id": "order-1001",
                        "status": "completed",
                        "stage": "order-allocator",
                        "timestamp": timestamp_for("08:05:30"),
                        "correlation_id": "checkout-1001",
                        "meta": {"parent_task_id": "cart-1001"},
                    },
                ],
                graph=task_graph(
                    task_id="order-1001",
                    status="completed",
                    correlation_id="checkout-1001",
                    parent_task_id="cart-1001",
                ),
            ),
        ),
    ),
    MockService(
        service_id="payments-api",
        name="Payments API",
        environment="mock",
        tags=("mock", "money", "core"),
        path_prefix="/mock/payments-api",
        service_title="Payments API",
        topology={
            "workflow_exchange": "payments.workflow",
            "status_queue": "payments.status",
            "stages": [
                {
                    "name": "payment-intake",
                    "queue": "payments.intake",
                    "binding_keys": ["payments.requested"],
                    "publish_routing_key": "payments.authorized",
                    "description": "Normalizes charge requests and prepares auth metadata.",
                    "role": "ingress",
                    "owner": "payments",
                    "tags": ["mock", "payments"],
                    "allowed_next_stages": ["payment-authorizer"],
                    "terminal": False,
                },
                {
                    "name": "payment-authorizer",
                    "queue": "payments.authorizer",
                    "binding_keys": ["payments.authorized"],
                    "publish_routing_key": "payments.captured",
                    "description": "Contacts the PSP and captures successful authorizations.",
                    "role": "worker",
                    "owner": "payments",
                    "tags": ["mock", "payments"],
                    "allowed_next_stages": [],
                    "terminal": True,
                },
            ],
            "entry_routes": [
                {"name": "charge-card", "routing_key": "payments.requested", "target_stage": "payment-intake"},
            ],
            "edges": [
                {"source": "payment-intake", "target": "payment-authorizer", "routing_key": "payments.authorized"},
            ],
        },
        workers=(
            {"worker_name": "payments-intake-0", "running": True, "last_heartbeat_at": timestamp_for("08:08:14")},
            {"worker_name": "payments-authorizer-0", "running": True, "last_heartbeat_at": timestamp_for("08:08:12")},
        ),
        tasks=(
            MockTask(
                task_id="order-1001",
                latest_event={
                    "status": "processing",
                    "stage": "payment-authorizer",
                    "timestamp": timestamp_for("08:04:20"),
                    "correlation_id": "checkout-1001",
                    "meta": {"parent_task_id": "order-1001"},
                },
                history=[
                    {
                        "task_id": "order-1001",
                        "status": "queued",
                        "stage": "payment-intake",
                        "timestamp": timestamp_for("08:01:00"),
                        "correlation_id": "checkout-1001",
                        "meta": {"parent_task_id": "order-1001"},
                    },
                    {
                        "task_id": "order-1001",
                        "status": "processing",
                        "stage": "payment-authorizer",
                        "timestamp": timestamp_for("08:04:20"),
                        "correlation_id": "checkout-1001",
                        "meta": {"parent_task_id": "order-1001"},
                    },
                ],
                graph=task_graph(
                    task_id="order-1001",
                    status="processing",
                    correlation_id="checkout-1001",
                    parent_task_id="order-1001",
                ),
            ),
            MockTask(
                task_id="payment-9001",
                latest_event={
                    "status": "failed",
                    "stage": "payment-authorizer",
                    "timestamp": timestamp_for("07:55:00"),
                    "correlation_id": "checkout-0999",
                    "meta": {"parent_task_id": "order-0999"},
                },
                history=[
                    {
                        "task_id": "payment-9001",
                        "status": "queued",
                        "stage": "payment-intake",
                        "timestamp": timestamp_for("07:50:00"),
                        "correlation_id": "checkout-0999",
                        "meta": {"parent_task_id": "order-0999"},
                    },
                    {
                        "task_id": "payment-9001",
                        "status": "failed",
                        "stage": "payment-authorizer",
                        "timestamp": timestamp_for("07:55:00"),
                        "correlation_id": "checkout-0999",
                        "meta": {"parent_task_id": "order-0999"},
                    },
                ],
                graph=task_graph(
                    task_id="payment-9001",
                    status="failed",
                    correlation_id="checkout-0999",
                    parent_task_id="order-0999",
                ),
            ),
        ),
        dlq_messages=(
            {
                "dlq_id": "payments-dlq-1",
                "queue_name": "payments.dlq",
                "source_queue_name": "payments.authorizer",
                "retry_queue_name": "payments.retry",
                "task_id": "payment-9001",
                "correlation_id": "checkout-0999",
                "reason": "gateway_timeout",
                "exception_type": "TimeoutError",
                "retry_attempt": 3,
                "max_retries": 3,
                "content_type": "application/json",
                "body_encoding": "utf-8",
                "dead_lettered_at": timestamp_for("07:55:10"),
                "state": "dead_lettered",
                "replay_count": 0,
                "replayed_at": None,
                "replay_target_queue_name": None,
            },
        ),
    ),
    MockService(
        service_id="shipping-api",
        name="Shipping API",
        environment="mock",
        tags=("mock", "fulfillment", "async"),
        path_prefix="/mock/shipping-api",
        service_title="Shipping API",
        topology={
            "workflow_exchange": "shipping.workflow",
            "status_queue": "shipping.status",
            "stages": [
                {
                    "name": "shipment-intake",
                    "queue": "shipping.intake",
                    "binding_keys": ["shipping.created"],
                    "publish_routing_key": "shipping.dispatched",
                    "description": "Creates shipments after payment clears.",
                    "role": "ingress",
                    "owner": "fulfillment",
                    "tags": ["mock", "shipping"],
                    "allowed_next_stages": ["carrier-dispatch"],
                    "terminal": False,
                },
                {
                    "name": "carrier-dispatch",
                    "queue": "shipping.dispatch",
                    "binding_keys": ["shipping.dispatched"],
                    "publish_routing_key": "shipping.completed",
                    "description": "Sends labels to the carrier and tracks the handoff.",
                    "role": "worker",
                    "owner": "fulfillment",
                    "tags": ["mock", "shipping"],
                    "allowed_next_stages": [],
                    "terminal": True,
                },
            ],
            "entry_routes": [
                {"name": "create-shipment", "routing_key": "shipping.created", "target_stage": "shipment-intake"},
            ],
            "edges": [
                {"source": "shipment-intake", "target": "carrier-dispatch", "routing_key": "shipping.dispatched"},
            ],
        },
        workers=(
            {"worker_name": "shipping-intake-0", "running": True, "last_heartbeat_at": timestamp_for("08:08:11")},
            {"worker_name": "carrier-dispatch-0", "running": True, "last_heartbeat_at": timestamp_for("08:08:10")},
        ),
        tasks=(
            MockTask(
                task_id="order-1001",
                latest_event={
                    "status": "queued",
                    "stage": "shipment-intake",
                    "timestamp": timestamp_for("08:06:10"),
                    "correlation_id": "checkout-1001",
                    "meta": {"parent_task_id": "order-1001"},
                },
                history=[
                    {
                        "task_id": "order-1001",
                        "status": "queued",
                        "stage": "shipment-intake",
                        "timestamp": timestamp_for("08:06:10"),
                        "correlation_id": "checkout-1001",
                        "meta": {"parent_task_id": "order-1001"},
                    }
                ],
                graph=task_graph(
                    task_id="order-1001",
                    status="queued",
                    correlation_id="checkout-1001",
                    parent_task_id="order-1001",
                ),
            ),
        ),
    ),
)


def build_registration_payloads(*, public_host: str, port: int) -> list[dict[str, Any]]:
    return [service.registration_payload(public_host=public_host, port=port) for service in MOCK_SERVICES]


def build_ingest_payload() -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for service in MOCK_SERVICES:
        for task in service.tasks:
            latest_timestamp = str(task.latest_event.get("timestamp") or utcnow_iso())
            latest_status = str(task.latest_event.get("status") or "unknown").lower()
            event_key = f"{service.service_id}:{task.task_id}:{latest_status}:{latest_timestamp}"
            events.append(
                {
                    "service_id": service.service_id,
                    "ingest_method": "pull",
                    "event": {
                        "cursor": event_key,
                        "task_id": task.task_id,
                        "event_type": f"status.{latest_status}",
                        "source_kind": "status",
                        "component": "mock-service",
                        "timestamp": latest_timestamp,
                        "event_id": event_key,
                        "correlation_id": task.latest_event.get("correlation_id"),
                        "parent_task_id": (
                            task.latest_event.get("meta", {}).get("parent_task_id")
                            if isinstance(task.latest_event.get("meta"), dict)
                            else None
                        ),
                        "payload": task.latest_event,
                    },
                }
            )
    return {"events": events}


def write_json_response(handler: BaseHTTPRequestHandler, *, status_code: int, payload: Any) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def write_not_found(handler: BaseHTTPRequestHandler, detail: str) -> None:
    write_json_response(handler, status_code=404, payload={"detail": detail})


class MockServiceHandler(BaseHTTPRequestHandler):
    server_version = "RelaynaStudioMock/1.0"
    mock_services = {service.path_prefix: service for service in MOCK_SERVICES}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        for prefix, service in self.mock_services.items():
            if not parsed.path.startswith(prefix):
                continue
            relative_path = parsed.path[len(prefix) :] or "/"
            self._handle_service_request(service, relative_path, parse_qs(parsed.query, keep_blank_values=False))
            return
        write_not_found(self, "Mock service route not found.")

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _handle_service_request(self, service: MockService, path: str, query: dict[str, list[str]]) -> None:
        tasks = service.task_map()
        if path == "/relayna/capabilities":
            write_json_response(self, status_code=200, payload=service.capability_document())
            return
        if path == "/relayna/health/workers":
            write_json_response(
                self,
                status_code=200,
                payload={"reported_at": utcnow_iso(), "workers": list(service.workers)},
            )
            return
        if path.startswith("/status/"):
            task_id = path.removeprefix("/status/")
            task = tasks.get(task_id)
            if task is None:
                write_not_found(self, f"No status found for task_id '{task_id}'.")
                return
            write_json_response(self, status_code=200, payload={"task_id": task_id, "event": task.latest_event})
            return
        if path == "/history":
            task_id = first_query_value(query, "task_id")
            if task_id is None:
                events = [event for task in tasks.values() for event in task.history]
                payload: dict[str, Any] = {"count": len(events), "events": events}
            else:
                task = tasks.get(task_id)
                if task is None:
                    payload = {"count": 0, "events": [], "task_id": task_id}
                else:
                    payload = {"count": len(task.history), "events": task.history, "task_id": task_id}
            write_json_response(self, status_code=200, payload=payload)
            return
        if path == "/workflow/topology":
            write_json_response(self, status_code=200, payload=service.topology)
            return
        if path == "/dlq/messages":
            task_id = first_query_value(query, "task_id")
            state = first_query_value(query, "state")
            reason = first_query_value(query, "reason")
            cursor = first_query_value(query, "cursor")
            limit = parse_int(first_query_value(query, "limit"), default=50)
            items = list(service.dlq_messages)
            if task_id is not None:
                items = [item for item in items if item.get("task_id") == task_id]
            if state is not None:
                items = [item for item in items if item.get("state") == state]
            if reason is not None:
                items = [item for item in items if item.get("reason") == reason]
            start = parse_int(cursor, default=0)
            page = items[start : start + max(limit, 0)]
            next_cursor = None if start + limit >= len(items) else str(start + limit)
            write_json_response(self, status_code=200, payload={"items": page, "next_cursor": next_cursor})
            return
        if path.startswith("/executions/") and path.endswith("/graph"):
            task_id = path.removeprefix("/executions/").removesuffix("/graph")
            task = tasks.get(task_id)
            if task is None:
                write_not_found(self, f"No execution graph found for task_id '{task_id}'.")
                return
            write_json_response(self, status_code=200, payload=task.graph)
            return
        write_not_found(self, f"Mock route '{path}' is not implemented.")


def parse_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def first_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def make_request(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url=url, method=method, data=data, headers=headers)
    with urlopen(request, timeout=5) as response:
        body = response.read().decode("utf-8")
    if not body:
        return None
    return json.loads(body)


def register_services(*, studio_url: str, public_host: str, port: int, refresh: bool, seed_events: bool) -> int:
    normalized_studio_url = studio_url.rstrip("/")
    created = 0
    updated = 0
    refreshed = 0
    service_ids: list[str] = []
    for payload in build_registration_payloads(public_host=public_host, port=port):
        service_id = payload["service_id"]
        service_ids.append(service_id)
        create_url = f"{normalized_studio_url}/studio/services"
        patch_url = f"{normalized_studio_url}/studio/services/{quote(service_id, safe='')}"
        try:
            make_request("POST", create_url, payload)
            created += 1
            action = "created"
        except HTTPError as exc:
            if exc.code != 409:
                raise
            update_payload = {
                "name": payload["name"],
                "base_url": payload["base_url"],
                "environment": payload["environment"],
                "tags": payload["tags"],
                "auth_mode": payload["auth_mode"],
            }
            make_request("PATCH", patch_url, update_payload)
            updated += 1
            action = "updated"
        print(f"{action}: {service_id} -> {payload['base_url']}")
        if refresh:
            make_request("POST", f"{patch_url}/refresh")
            make_request("POST", f"{patch_url}/health/refresh")
            refreshed += 1
            print(f"refreshed: {service_id}")
    if seed_events:
        ingest_response = make_request(
            "POST",
            f"{normalized_studio_url}/studio/ingest/events",
            build_ingest_payload(),
        )
        inserted = ingest_response.get("inserted", 0) if isinstance(ingest_response, dict) else 0
        duplicate = ingest_response.get("duplicate", 0) if isinstance(ingest_response, dict) else 0
        invalid = ingest_response.get("invalid", 0) if isinstance(ingest_response, dict) else 0
        print(f"seeded_events: inserted={inserted} duplicate={duplicate} invalid={invalid}")
        if refresh:
            for service_id in service_ids:
                patch_url = f"{normalized_studio_url}/studio/services/{quote(service_id, safe='')}"
                make_request("POST", f"{patch_url}/health/refresh")
            print(f"post_seed_health_refresh: services={len(service_ids)}")
    print(f"summary: created={created} updated={updated} refreshed={refreshed}")
    return 0


def print_payloads(*, public_host: str, port: int) -> int:
    print(json.dumps(build_registration_payloads(public_host=public_host, port=port), indent=2))
    return 0


def serve(*, bind_host: str, port: int) -> int:
    server = ThreadingHTTPServer((bind_host, port), MockServiceHandler)
    print(f"Serving {len(MOCK_SERVICES)} mock services on http://{bind_host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve and register Relayna Studio mock services.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Serve the mock Relayna services locally.")
    serve_parser.add_argument("--host", default=DEFAULT_BIND_HOST, help=f"Bind host. Default: {DEFAULT_BIND_HOST}")
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port. Default: {DEFAULT_PORT}")

    payloads_parser = subparsers.add_parser("payloads", help="Print Studio registry payloads for the mock services.")
    payloads_parser.add_argument(
        "--public-host",
        default=DEFAULT_PUBLIC_HOST,
        help=f"Hostname Studio should use to reach the mock server. Default: {DEFAULT_PUBLIC_HOST}",
    )
    payloads_parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Mock server port. Default: {DEFAULT_PORT}")

    register_parser = subparsers.add_parser("register", help="Create or update the mock services in Studio.")
    register_parser.add_argument(
        "--studio-url",
        default=DEFAULT_STUDIO_URL,
        help=f"Studio backend origin. Default: {DEFAULT_STUDIO_URL}",
    )
    register_parser.add_argument(
        "--public-host",
        default=DEFAULT_PUBLIC_HOST,
        help=f"Hostname Studio should use to reach the mock server. Default: {DEFAULT_PUBLIC_HOST}",
    )
    register_parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Mock server port. Default: {DEFAULT_PORT}")
    register_parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Only create/update service records; skip capability and health refresh calls.",
    )
    register_parser.add_argument(
        "--no-seed",
        action="store_true",
        help="Skip pushing mock status events into Studio's retained search index.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return serve(bind_host=args.host, port=args.port)
        if args.command == "payloads":
            return print_payloads(public_host=args.public_host, port=args.port)
        if args.command == "register":
            return register_services(
                studio_url=args.studio_url,
                public_host=args.public_host,
                port=args.port,
                refresh=not args.no_refresh,
                seed_events=not args.no_seed,
            )
    except (HTTPError, URLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
