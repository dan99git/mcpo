"""
Thread-safe global metrics aggregator for top-level metrics and error breakdowns.

This aggregator is responsible for:
 - Tracking total calls received at the HTTP layer (including pre-execution failures)
 - Tracking top-level errors.byCode: disabled, invalid_timeout, timeout, unexpected
 - Producing a consolidated metrics payload that includes perTool metrics from RunnerService
"""

from __future__ import annotations

import threading
from typing import Dict, Any, Optional


class MetricsAggregator:
    """Aggregates cross-cutting metrics outside of RunnerService."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls_total: int = 0
        self._errors_by_code: Dict[str, int] = {
            "disabled": 0,
            "invalid_timeout": 0,
            "timeout": 0,
            "unexpected": 0,
        }

    def record_call(self) -> None:
        with self._lock:
            self._calls_total += 1

    def record_error(self, code: str) -> None:
        with self._lock:
            if code not in self._errors_by_code:
                # Normalize unknown codes under "unexpected"
                code = "unexpected"
            self._errors_by_code[code] += 1

    def reset(self) -> None:
        with self._lock:
            self._calls_total = 0
            for key in list(self._errors_by_code.keys()):
                self._errors_by_code[key] = 0

    def build_metrics(self, per_tool_metrics: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Return consolidated metrics payload.

        Note: errors.total equals the sum of byCode counters maintained here.
        Per-tool error counts are preserved separately under perTool.
        """
        with self._lock:
            by_code = dict(self._errors_by_code)
            total_errors = sum(by_code.values())
            return {
                "calls": self._calls_total,
                "errors": {
                    "total": total_errors,
                    "byCode": by_code,
                },
                "perTool": per_tool_metrics or {},
            }


_aggregator: Optional[MetricsAggregator] = None
_aggregator_lock = threading.Lock()


def get_metrics_aggregator() -> MetricsAggregator:
    global _aggregator
    with _aggregator_lock:
        if _aggregator is None:
            _aggregator = MetricsAggregator()
        return _aggregator


