"""API load test: concurrent clients submitting and reading tasks.

    uv run locust -f tests/performance/locustfile.py --headless -u 50 -r 10 -t 60s \
        --host http://127.0.0.1:8000 --csv tests/performance/results/locust

Each simulated user registers its own account (so start the API with a generous
FLOWFORGE_AUTH_RATE_LIMIT_PER_MINUTE), or set FLOWFORGE_API_KEY to share one key. The mix approximates a service
that mostly submits work and polls for results. Workers must be running for submitted tasks to progress, but the
measured latencies are those of the API alone.
"""

from __future__ import annotations

import os
import random
import uuid
from typing import Any

from locust import HttpUser, between, task

API = "/api/v1"


class FlowForgeClient(HttpUser):
    wait_time = between(0.1, 0.5)

    def on_start(self) -> None:
        self.task_ids: list[str] = []
        key = os.environ.get("FLOWFORGE_API_KEY")
        if key:
            self.headers = {"Authorization": f"Bearer {key}"}
            return
        email = f"locust-{uuid.uuid4().hex[:12]}@example.com"
        resp = self.client.post(
            f"{API}/auth/register",
            json={"email": email, "password": "locust-password-123", "full_name": "Load Test"},
            name="POST /auth/register",
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        me = self.client.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}, name="GET /auth/me"
        ).json()
        self.headers = {
            "Authorization": f"Bearer {token}",
            "X-Project-ID": me["memberships"][0]["projects"][0]["id"],
        }

    @task(5)
    def submit(self) -> None:
        body: dict[str, Any] = {
            "task_type": "demo.noop",
            "payload": {"tag": "locust"},
            "priority": random.choice([2, 5, 8]),
        }
        with self.client.post(
            f"{API}/tasks", json=body, headers=self.headers, name="POST /tasks", catch_response=True
        ) as resp:
            if resp.status_code == 201:
                self.task_ids.append(resp.json()["id"])
                self.task_ids = self.task_ids[-50:]
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(2)
    def get_task(self) -> None:
        if self.task_ids:
            self.client.get(
                f"{API}/tasks/{random.choice(self.task_ids)}",
                headers=self.headers,
                name="GET /tasks/{id}",
            )

    @task(2)
    def list_tasks(self) -> None:
        self.client.get(f"{API}/tasks?limit=25", headers=self.headers, name="GET /tasks")

    @task(1)
    def overview(self) -> None:
        self.client.get(
            f"{API}/analytics/overview", headers=self.headers, name="GET /analytics/overview"
        )
