import os
import time
from collections.abc import Mapping
from typing import Any

from prometheus_client import (
    CollectorRegistry,
    Counter,
    GCCollector,
    Gauge,
    Histogram,
    Info,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)


REGISTRY = CollectorRegistry()
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)

SERVICE_INFO = Info(
    "caupolican_service",
    "Caupolican service and build identity.",
    registry=REGISTRY,
)
SERVICE_INFO.info(
    {
        "version": os.getenv("CAUPOLICAN_BUILD_VERSION", "development"),
    }
)

HTTP_REQUESTS = Counter(
    "caupolican_http_requests_total",
    "HTTP requests handled by method, normalized route, and status class.",
    ("method", "route", "status_class"),
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION = Histogram(
    "caupolican_http_request_duration_seconds",
    "HTTP request duration by method and normalized route.",
    ("method", "route"),
    registry=REGISTRY,
)
DEPENDENCY_OPERATIONS = Counter(
    "caupolican_dependency_operations_total",
    "Dependency operation outcomes.",
    ("dependency", "operation", "result"),
    registry=REGISTRY,
)
WORKER_TRANSITIONS = Counter(
    "caupolican_worker_transitions_total",
    "Channel worker lifecycle transition outcomes.",
    ("transition", "result"),
    registry=REGISTRY,
)
WORKER_RESTARTS = Counter(
    "caupolican_worker_restarts_total",
    "Worker restart and backoff outcomes.",
    ("result",),
    registry=REGISTRY,
)
WORKER_STALL_CHECKS = Counter(
    "caupolican_worker_stall_checks_total",
    "Worker stall check outcomes.",
    ("result",),
    registry=REGISTRY,
)
SEGMENT_CLEANUPS = Counter(
    "caupolican_segment_cleanup_total",
    "Segment and channel cleanup outcomes.",
    ("reason", "result"),
    registry=REGISTRY,
)
CLEANUP_RUNS = Counter(
    "caupolican_cleanup_runs_total",
    "Periodic and disk-pressure cleanup job outcomes.",
    ("kind", "result"),
    registry=REGISTRY,
)
CHANNEL_WORKERS = Gauge(
    "caupolican_channel_workers",
    "Current channel workers by bounded source state.",
    ("state",),
    registry=REGISTRY,
)
DISK_USAGE_RATIO = Gauge(
    "caupolican_disk_usage_ratio",
    "Current output filesystem usage ratio.",
    registry=REGISTRY,
)

ROUTES = frozenset(
    {
        "/api/health",
        "/api/channels",
        "/api/channels/{channel_id}/status",
        "/api/channels/{channel_id}/set-source",
        "/api/channels/{channel_id}/stop",
        "/api/channels/{channel_id}/segments",
        "/api/channels/{channel_id}",
        "/metrics",
    }
)
METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "POST", "PUT"})


def observe_http(method: str, route: str, status_code: int, started_at: float) -> None:
    normalized_method = method if method in METHODS else "OTHER"
    normalized_route = route if route in ROUTES else "unmatched"
    status_class = f"{status_code // 100}xx" if 100 <= status_code <= 599 else "other"
    HTTP_REQUESTS.labels(
        method=normalized_method, route=normalized_route, status_class=status_class
    ).inc()
    HTTP_REQUEST_DURATION.labels(method=normalized_method, route=normalized_route).observe(
        time.perf_counter() - started_at
    )


def refresh_runtime_state(workers: Mapping[str, Any], out_root: str) -> None:
    counts = {"live": 0, "standby": 0}
    for worker in workers.values():
        state = "live" if worker.active and worker.current_source == "live" else "standby"
        counts[state] += 1
    for state, count in counts.items():
        CHANNEL_WORKERS.labels(state=state).set(count)

    try:
        stat = os.statvfs(out_root)
        total = stat.f_blocks * stat.f_frsize
        available = stat.f_bavail * stat.f_frsize
        if total:
            DISK_USAGE_RATIO.set(1 - (available / total))
        DEPENDENCY_OPERATIONS.labels(
            dependency="filesystem", operation="stat", result="success"
        ).inc()
    except OSError:
        DISK_USAGE_RATIO.set(float("nan"))
        DEPENDENCY_OPERATIONS.labels(
            dependency="filesystem", operation="stat", result="failure"
        ).inc()


def render_metrics() -> bytes:
    return generate_latest(REGISTRY)
