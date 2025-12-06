"""
API response models for standardized responses across all endpoints.
Consolidates error envelopes, success responses, and data models.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Error detail information."""
    message: str
    code: Optional[str] = None
    data: Optional[Any] = None
    timestamp: Optional[int] = None

    @classmethod
    def create(cls, message: str, code: str = None, data: Any = None) -> ErrorDetail:
        """Create an error detail with timestamp."""
        return cls(
            message=message,
            code=code,
            data=data,
            timestamp=int(time.time() * 1000)
        )


class ApiResponse(BaseModel):
    """Base API response wrapper."""
    ok: bool
    message: Optional[str] = None


class ErrorResponse(ApiResponse):
    """Error response envelope."""
    ok: bool = False
    error: ErrorDetail

    @classmethod
    def create(cls, message: str, code: str = None, data: Any = None) -> ErrorResponse:
        """Create a standardized error response."""
        return cls(error=ErrorDetail.create(message, code, data))


class SuccessResponse(ApiResponse):
    """Success response envelope."""
    ok: bool = True
    result: Optional[Any] = None
    data: Optional[Any] = None

    @classmethod
    def create(cls, result: Any = None, message: str = None, data: Any = None) -> SuccessResponse:
        """Create a standardized success response."""
        return cls(result=result, message=message, data=data)


class CollectionResponse(SuccessResponse):
    """Collection response with pagination info."""
    items: List[Any] = Field(default_factory=list)
    total: Optional[int] = None
    page: Optional[int] = None
    size: Optional[int] = None


# Server and tool status models
class ServerStatus(BaseModel):
    """Server status information."""
    name: str
    enabled: bool = True
    connected: bool = False
    type: Optional[str] = None
    tools: Dict[str, bool] = Field(default_factory=dict)


class ToolStatus(BaseModel):
    """Tool status information."""
    name: str
    enabled: bool = True
    server: str
    description: Optional[str] = None


class HealthStatus(BaseModel):
    """Health check status."""
    status: str = "healthy"
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    version: Optional[str] = None
    uptime: Optional[float] = None


class MetricsData(BaseModel):
    """Tool execution metrics."""
    calls: int = 0
    errors: int = 0
    total_latency: float = 0.0
    avg_latency_ms: float = 0.0


class ServerMetrics(BaseModel):
    """Server-level metrics."""
    per_tool: Dict[str, MetricsData] = Field(default_factory=dict)
    total_calls: int = 0
    total_errors: int = 0


# Compatibility functions for existing code
def create_error_envelope(message: str, code: str = None, data: Any = None) -> Dict[str, Any]:
    """Create a standardized error response envelope (backward compatibility)."""
    return ErrorResponse.create(message, code, data).model_dump()


def create_success_envelope(result: Any = None, message: str = None, data: Any = None) -> Dict[str, Any]:
    """Create a standardized success response envelope (backward compatibility)."""
    return SuccessResponse.create(result, message, data).model_dump()


def error_envelope(message: str, code: str = None, data: Any = None) -> Dict[str, Any]:
    """Helper function for API error responses (backward compatibility)."""
    return create_error_envelope(message, code, data)
