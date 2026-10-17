#!/usr/bin/env python3
"""Seed a clearly-labelled demo workspace with REAL work: a demo account, tasks of every demo type, an ETL
workflow run and two schedules. Nothing is fabricated — the dashboard's numbers come from these tasks actually being
executed by the running workers, so start the API, a worker and the scheduler first.

    python scripts/seed_demo.py [--api http://127.0.0.1:8000] [--email demo@example.com] [--tasks 60]
"""

from __future__ import annotations

import argparse
import random
import sys

import httpx

DEMO_PASSWORD = "flowforge-demo-password"  # noqa: S105 - documented local demo account, never for production


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--password", default=DEMO_PASSWORD)
    parser.add_argument("--tasks", type=int, default=60)
    args = parser.parse_args()

    http = httpx.Client(base_url=f"{args.api.rstrip('/')}/api/v1", timeout=30)
    resp = http.post(
        "/auth/register",
        json={
            "email": args.email,
            "password": args.password,
            "full_name": "Demo User",
            "organization_name": "Demo Organization",
        },
    )
    if resp.status_code == 409:
        resp = http.post("/auth/login", json={"email": args.email, "password": args.password})
    if resp.status_code not in (200, 201):
        sys.exit(f"could not sign in: {resp.status_code} {resp.text}")
    token = resp.json()["access_token"]
    me = http.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    project_id = me["memberships"][0]["projects"][0]["id"]
    headers = {"Authorization": f"Bearer {token}", "X-Project-ID": project_id}

    types = {t["name"] for t in http.get("/task-types", headers=headers).json()}
    if not types:
        sys.exit("no task types registered: start a worker first (python -m flowforge_worker)")

    rng = random.Random(7)
    mix = [
        ("demo.noop", lambda: {"tag": "demo"}),
        ("demo.sleep", lambda: {"seconds": round(rng.uniform(0.2, 3.0), 1)}),
        (
            "generate_report",
            lambda: {"report_id": rng.randint(1, 500), "rows": rng.choice([2000, 20000, 80000])},
        ),
        (
            "send_email",
            lambda: {"to": "someone@example.com", "subject": "Your report is ready", "body": "Hi!"},
        ),
        ("demo.flaky", lambda: {"failure_rate": 0.5, "work_seconds": 0.3}),
        ("demo.cpu_hash", lambda: {"iterations": rng.choice([50_000, 200_000])}),
    ]
    batch = []
    for i in range(args.tasks):
        task_type, payload = mix[i % len(mix)]
        if task_type in types:
            batch.append(
                {"task_type": task_type, "payload": payload(), "priority": rng.choice([2, 5, 5, 8])}
            )
    batch += [
        {
            "task_type": "demo.fail",
            "payload": {"message": "upstream rejected the request", "retryable": True},
            "max_retries": 1,
            "retry_backoff_seconds": 1,
        },
        {
            "task_type": "demo.fail",
            "payload": {"message": "invalid account number"},
            "max_retries": 3,
        },
        {"task_type": "demo.sleep", "payload": {"seconds": 45}, "delay_seconds": 120},
    ]
    resp = http.post("/tasks/batch", headers=headers, json={"tasks": batch})
    resp.raise_for_status()
    print(f"submitted {resp.json()['created']} tasks")

    node = lambda key, task_type, payload, x, y: {  # noqa: E731
        "key": key,
        "name": key.replace("_", " ").title(),
        "task_type": task_type,
        "payload": payload,
        "position": {"x": x, "y": y},
    }
    graph = {
        "nodes": [
            node("extract_orders", "etl.extract", {"source": "orders", "records": 1200}, 0, -60),
            node(
                "extract_customers", "etl.extract", {"source": "customers", "records": 300}, 0, 60
            ),
            node("transform", "etl.transform", {"operation": "aggregate"}, 240, 0),
            node("validate", "etl.validate", {"min_records": 100}, 480, 0),
            node("load", "etl.load", {"destination": "warehouse.sales_daily"}, 720, 0),
        ],
        "edges": [
            {"source": "extract_orders", "target": "transform"},
            {"source": "extract_customers", "target": "transform"},
            {"source": "transform", "target": "validate"},
            {"source": "validate", "target": "load"},
        ],
    }
    resp = http.post(
        "/workflows",
        headers=headers,
        json={
            "name": "Daily sales ETL",
            "description": "Extract, aggregate, validate and load (demo)",
            "graph": graph,
        },
    )
    if resp.status_code == 201:
        workflow_id = resp.json()["id"]
        run = http.post(
            f"/workflows/{workflow_id}/execute", headers=headers, json={"input": {"date": "today"}}
        )
        print(f"workflow run {run.json()['id']} started")
    else:
        print(f"workflow not created ({resp.status_code}): probably already exists")

    for body in (
        {
            "name": "Nightly report",
            "cron_expression": "0 2 * * *",
            "timezone": "Asia/Kolkata",
            "task_type": "generate_report",
            "payload": {"report_id": 1, "rows": 5000},
        },
        {
            "name": "Heartbeat ping",
            "cron_expression": "*/5 * * * *",
            "timezone": "UTC",
            "task_type": "demo.noop",
            "payload": {"tag": "heartbeat"},
            "overlap_policy": "SKIP",
        },
    ):
        resp = http.post("/schedules", headers=headers, json=body)
        print(f"schedule {body['name']!r}: {resp.status_code}")

    print(f"\nDemo workspace ready. Sign in as {args.email} / {args.password}")


if __name__ == "__main__":
    main()
