"""Structured logging only for now. OpenTelemetry tracing is deferred to Phase 3 (the
per-tenant-pod sidecar work); it only makes sense once there's a sidecar to receive spans.

get_logger(name) is what the rest of this codebase imports in place of print().
"""
import logging
import structlog

_logging_configured = False


def setup_logging() -> None:
    global _logging_configured
    if _logging_configured:
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _logging_configured = True


def get_logger(name: str):
    return structlog.get_logger(name)
