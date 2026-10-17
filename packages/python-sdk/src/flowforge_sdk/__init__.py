"""FlowForge Python SDK.

from flowforge_sdk import FlowForgeClient

client = FlowForgeClient(base_url="http://localhost:8000", api_key="ffk_...")
job = client.submit(task_type="generate_report", payload={"report_id": 42}, priority=5, max_retries=3)
print(job.id)
"""

__version__ = "0.1.0"

from flowforge_sdk.client import AsyncFlowForgeClient, FlowForgeClient
from flowforge_sdk.errors import (
    AuthenticationError,
    ConflictError,
    FlowForgeError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    TaskWaitTimeout,
    TransportError,
    ValidationError,
)
from flowforge_sdk.models import BatchItem, Task, TaskPage

__all__ = [
    "AsyncFlowForgeClient",
    "AuthenticationError",
    "BatchItem",
    "ConflictError",
    "FlowForgeClient",
    "FlowForgeError",
    "NotFoundError",
    "PayloadTooLargeError",
    "PermissionDeniedError",
    "RateLimitError",
    "ServerError",
    "Task",
    "TaskPage",
    "TaskWaitTimeout",
    "TransportError",
    "ValidationError",
]
