"""End-to-end throughput and latency benchmark of a real FlowForge deployment.

Starts the API, one scheduler and N worker processes exactly as in production (``python -m …``, default timings),
submits tasks of one workload through the HTTP batch endpoint and measures, from PostgreSQL's own timestamps:

* throughput  — tasks per second from the first submission to the last completion;
* end-to-end  — submitted → finished, per task (p50 / p95 / p99);
* queue wait  — queued → attempt started (p50 / p95 / p99);
* resources   — worker CPU per task, peak resident memory of the workers and the API, PostgreSQL commits per task;
* correctness — every task SUCCEEDED, exactly one successful attempt each, no failures;
* recovery    — with ``--kill-worker-after``, one worker is SIGKILLed mid-run: lost attempts, retries, recovery time.

Workloads: ``noop`` (platform overhead), ``io`` (waits 100 ms without using CPU) and ``cpu`` (about 25 ms of SHA-256
in the worker's process pool).

    python -m tests.performance.benchmark                       # full matrix: 100/1k/10k/50k jobs x 1/2/4/8 workers
    python -m tests.performance.benchmark --workload io --jobs 100,1000,10000 --out docs/benchmarks/io-bound
    python -m tests.performance.benchmark --jobs 100,1000 --workers 1,2 --out docs/benchmarks/quick
    python -m tests.performance.benchmark --jobs 100,100,100 --workers 8 --warmup 0 --out …  # cold-start cost
    python -m tests.performance.benchmark --workload io --jobs 10000,10000,10000 --workers 4 --kill-worker-after 0.3 --out …

Runs against the *test* database (it is wiped) and Redis DB 2 with its own key prefix, so the dev stack and the test
suite are untouched. Results are written as JSON and Markdown, with the hardware they were measured on. Nothing is
estimated or extrapolated: a run that does not finish within its time limit is reported as such and ends the matrix,
and so does any sign that the machine paused the benchmark (sleep or standby), whose run is discarded.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import json
import os
import platform
import secrets
import socket
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import psutil
from dotenv import dotenv_values
from redis.asyncio import Redis

from tests.chaos.processes import ServiceProcess

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "benchmark-password-not-secret"
TABLES_PER_RUN = ["task_logs", "task_events", "task_attempts", "outbox_events", "tasks"]
WARMUP_TASKS_PER_WORKER = 50
PAUSE_LIMIT_SECONDS = (
    5.0  # a longer gap in the harness's own sampling means the machine stopped running it
)
IO_WAIT_SECONDS = 0.1
CPU_HASH_ITERATIONS = 50_000  # about 25 ms of CPU on the benchmark laptop


@dataclass(frozen=True)
class Workload:
    task_type: str
    payload: dict[str, Any]
    description: str


WORKLOADS = {
    "noop": Workload(
        "demo.noop", {"tag": "bench"}, "returns immediately, so it measures platform overhead"
    ),
    "io": Workload(
        "demo.sleep",
        {"seconds": IO_WAIT_SECONDS, "report_progress": False},
        f"waits {IO_WAIT_SECONDS * 1000:.0f} ms without using CPU (I/O-bound)",
    ),
    "cpu": Workload(
        "demo.cpu_hash",
        {"iterations": CPU_HASH_ITERATIONS},
        f"runs {CPU_HASH_ITERATIONS:,} rounds of SHA-256 in the worker's process pool (CPU-bound)",
    ),
}


@dataclass
class RunResult:
    jobs: int
    workers: int
    concurrency: int
    completed: int
    failed: int
    submit_seconds: float
    wall_seconds: float | None
    throughput_per_second: float | None
    e2e_ms: dict[str, float | None] = field(default_factory=dict)
    queue_wait_ms: dict[str, float | None] = field(default_factory=dict)
    exec_ms_p50: float | None = None
    attempts_per_task: float | None = None
    duplicate_successes: int = 0
    commits_per_task: float | None = None
    worker_cpu_ms_per_task: float | None = None
    worker_rss_mb_peak: float | None = None
    api_rss_mb_peak: float | None = None
    timed_out: bool = False
    # Set when a worker was killed during the run (--kill-worker-after).
    killed_after_completed: int | None = None
    lost_attempts: int | None = None
    retried_tasks: int | None = None
    recovery_seconds: float | None = None
    last_finish_after_kill_seconds: float | None = None


# ------------------------------------------------------------------------------------------------ environment
def resolve_urls() -> tuple[str, str]:
    db = os.environ.get("FLOWFORGE_BENCH_DATABASE_URL")
    redis = os.environ.get("FLOWFORGE_BENCH_REDIS_URL")
    values = dotenv_values(ROOT / ".env")
    if not db and values.get("FLOWFORGE_DATABASE_URL"):
        db = str(values["FLOWFORGE_DATABASE_URL"]).rpartition("/")[0] + "/flowforge_test"
        if "${POSTGRES_PASSWORD}" in db:
            db = db.replace("${POSTGRES_PASSWORD}", str(values.get("POSTGRES_PASSWORD", "")))
    if not redis and values.get("FLOWFORGE_REDIS_URL"):
        redis = str(values["FLOWFORGE_REDIS_URL"]).rpartition("/")[0] + "/2"
    if not db or not redis:
        raise SystemExit(
            "set FLOWFORGE_BENCH_DATABASE_URL and FLOWFORGE_BENCH_REDIS_URL, or create .env"
        )
    name = db.rsplit("/", 1)[-1].split("?")[0]
    if "test" not in name and "bench" not in name:
        raise SystemExit(f"refusing to wipe database {name!r}: use a *_test or *_bench database")
    return db, redis


def keep_awake() -> None:
    """Ask Windows not to sleep while the benchmark runs: a sleeping laptop, or one in Modern Standby, suspends every
    process and spoils the run. The request ends with this process. Elsewhere, keep the machine awake yourself."""
    if sys.platform == "win32":
        es_continuous, es_system_required, es_display_required = 0x80000000, 0x1, 0x2
        ctypes.windll.kernel32.SetThreadExecutionState(
            es_continuous | es_system_required | es_display_required
        )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def service_env(db: str, redis: str, api_port: int) -> dict[str, str]:
    return {
        "FLOWFORGE_ENVIRONMENT": "test",
        "FLOWFORGE_DATABASE_URL": db,
        "FLOWFORGE_REDIS_URL": redis,
        "FLOWFORGE_REDIS_PREFIX": "ffbench",
        "FLOWFORGE_JWT_SECRET": secrets.token_urlsafe(48),
        "FLOWFORGE_API_PORT": str(api_port),
        "FLOWFORGE_COOKIE_SECURE": "false",
        "FLOWFORGE_LOG_LEVEL": "WARNING",
        "FLOWFORGE_RATE_LIMIT_PER_MINUTE": "1000000",
        "FLOWFORGE_AUTH_RATE_LIMIT_PER_MINUTE": "1000",
        # Production default: two pool processes per worker for CPU-bound handlers (spawned on first use only).
        "FLOWFORGE_WORKER_CPU_PROCESSES": "2",
        "FLOWFORGE_WORKER_METRICS_PORT": "0",
        "FLOWFORGE_SCHEDULER_METRICS_PORT": "0",
        # Database pools keep the production defaults (10 + 10 overflow). A pool smaller than a worker's slots plus
        # its background loops opens and closes overflow connections all the time, and PostgreSQL on Windows opens
        # connections one at a time at about 0.1 s each (see the performance report).
        "FLOWFORGE_DATABASE_POOL_SIZE": "10",
        "FLOWFORGE_DATABASE_MAX_OVERFLOW": "10",
        "FLOWFORGE_WORKER_QUEUES": "default",
    }


def hardware(pg_version: str, redis_version: str) -> dict[str, Any]:
    cpu = platform.processor()
    if sys.platform == "win32":
        with contextlib.suppress(OSError):
            cpu = (
                subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        "(Get-CimInstance Win32_Processor).Name",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                ).stdout.strip()
                or cpu
            )
    return {
        "cpu": cpu,
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "memory_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "python": platform.python_version(),
        "postgresql": pg_version,
        "redis": redis_version,
    }


# ------------------------------------------------------------------------------------------------ baseline
async def commit_rate(dsn: str, connections: int = 8, seconds: float = 3.0) -> float:
    """Single-row INSERT transactions per second that PostgreSQL alone sustains here: the floor under everything."""
    setup = await asyncpg.connect(dsn)
    await setup.execute(
        "CREATE TABLE IF NOT EXISTS bench_commit_probe (id bigserial PRIMARY KEY, v int)"
    )
    done = 0
    stop = time.monotonic() + seconds

    async def hammer() -> None:
        nonlocal done
        conn = await asyncpg.connect(dsn)
        try:
            while time.monotonic() < stop:
                await conn.execute(
                    "INSERT INTO bench_commit_probe (v) VALUES (1)"
                )  # autocommit: one fsync each
                done += 1
        finally:
            await conn.close()

    start = time.monotonic()
    await asyncio.gather(*(hammer() for _ in range(connections)))
    elapsed = time.monotonic() - start
    await setup.execute("DROP TABLE bench_commit_probe")
    await setup.close()
    return round(done / elapsed, 1)


def cpu_seconds(procs: list[ServiceProcess]) -> float:
    """User + system CPU time used so far by the given processes (and their children)."""
    total = 0.0
    for proc in procs:
        for p in proc.tree():
            try:
                times = p.cpu_times()
            except psutil.NoSuchProcess:
                continue
            total += times.user + times.system
    return total


def tree_rss_mb(proc: ServiceProcess) -> float:
    """Resident memory of a service's process tree, including its process pool and, on Windows, the few MB of the
    virtual-env launcher process."""
    total = 0
    for p in proc.tree():
        try:
            total += p.memory_info().rss
        except psutil.NoSuchProcess:
            continue
    return total / 2**20


class PeakMemory:
    """Samples resident memory every half second while a run is in progress.

    It also notices when the whole machine stopped running the benchmark: a laptop that sleeps or enters Modern
    Standby suspends every process, which would otherwise show up as a mysteriously slow result.
    """

    def __init__(self, workers: list[ServiceProcess], api: ServiceProcess) -> None:
        self.workers = workers
        self.api = api
        self.worker_peak = 0.0  # the largest single worker
        self.api_peak = 0.0
        self.longest_pause = 0.0  # seconds beyond the sampling interval

    def sample(self) -> None:
        sizes = [tree_rss_mb(w) for w in self.workers if w.running]
        if sizes:
            self.worker_peak = max(self.worker_peak, *sizes)
        self.api_peak = max(self.api_peak, tree_rss_mb(self.api))

    async def watch(self, stop: asyncio.Event) -> None:
        last = time.time()  # wall clock: it keeps counting while the machine sleeps
        while not stop.is_set():
            await asyncio.to_thread(self.sample)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=0.5)
            now = time.time()
            self.longest_pause = max(self.longest_pause, now - last - 0.5)
            last = now


async def commits(conn: asyncpg.Connection) -> int:
    return int(
        await conn.fetchval(
            "SELECT xact_commit FROM pg_stat_database WHERE datname = current_database()"
        )
    )


# ------------------------------------------------------------------------------------------------ measurement
def percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None}
    if len(values) == 1:
        return {"p50": values[0], "p95": values[0], "p99": values[0]}
    q = statistics.quantiles(values, n=100, method="inclusive")
    return {"p50": round(q[49], 1), "p95": round(q[94], 1), "p99": round(q[98], 1)}


async def measure_recovery(conn: asyncpg.Connection, killed_at: datetime) -> dict[str, Any]:
    """What a killed worker cost: attempts it lost, tasks retried because of it, and how long recovery took."""
    lost = await conn.fetchrow(
        "SELECT count(*) AS attempts, count(DISTINCT task_id) AS tasks FROM task_attempts WHERE status = 'LOST'"
    )
    recovery = await conn.fetchval(
        "SELECT extract(epoch FROM max(t.finished_at) - $1) FROM tasks t "
        "WHERE t.id IN (SELECT task_id FROM task_attempts WHERE status = 'LOST')",
        killed_at,
    )
    last = await conn.fetchval(
        "SELECT extract(epoch FROM max(finished_at) - $1) FROM tasks", killed_at
    )
    return {
        "lost_attempts": int(lost["attempts"]),
        "retried_tasks": int(lost["tasks"]),
        "recovery_seconds": round(float(recovery), 1) if recovery is not None else None,
        "last_finish_after_kill_seconds": round(float(last), 1) if last is not None else None,
    }


async def measure(
    conn: asyncpg.Connection,
    jobs: int,
    workers: int,
    concurrency: int,
    submit_s: float,
    timed_out: bool,
    commit_delta: int,
    worker_cpu_s: float | None,
    memory: PeakMemory,
) -> RunResult:
    counts = dict(await conn.fetch("SELECT state, count(*) FROM tasks GROUP BY state"))
    completed = int(counts.get("SUCCEEDED", 0))
    failed = sum(int(n) for s, n in counts.items() if s in {"FAILED", "DEAD_LETTERED", "CANCELLED"})
    span = await conn.fetchrow(
        "SELECT min(created_at) AS first, max(finished_at) AS last FROM tasks WHERE state = 'SUCCEEDED'"
    )
    wall = (
        (span["last"] - span["first"]).total_seconds() if span["first"] and span["last"] else None
    )
    e2e = [
        float(r[0])
        for r in await conn.fetch(
            "SELECT extract(epoch FROM finished_at - created_at) * 1000 FROM tasks WHERE state = 'SUCCEEDED'"
        )
    ]
    waits = [
        float(r[0])
        for r in await conn.fetch(
            "SELECT queue_wait_ms FROM task_attempts WHERE queue_wait_ms IS NOT NULL"
        )
    ]
    execs = [
        float(r[0])
        for r in await conn.fetch(
            "SELECT duration_ms FROM task_attempts WHERE status = 'SUCCEEDED'"
        )
    ]
    attempts = await conn.fetchval("SELECT count(*) FROM task_attempts")
    duplicates = await conn.fetchval(
        "SELECT count(*) FROM (SELECT task_id FROM task_attempts WHERE status = 'SUCCEEDED' "
        "GROUP BY task_id HAVING count(*) > 1) d"
    )
    return RunResult(
        jobs=jobs,
        workers=workers,
        concurrency=concurrency,
        completed=completed,
        failed=failed,
        submit_seconds=round(submit_s, 2),
        wall_seconds=round(wall, 2) if wall else None,
        throughput_per_second=round(completed / wall, 1) if wall else None,
        e2e_ms=percentiles(e2e),
        queue_wait_ms=percentiles(waits),
        exec_ms_p50=percentiles(execs)["p50"],
        attempts_per_task=round(attempts / jobs, 3) if jobs else None,
        duplicate_successes=int(duplicates),
        # Includes the other processes' background transactions (heartbeats, polling) during the run.
        commits_per_task=round(commit_delta / jobs, 2) if jobs else None,
        worker_cpu_ms_per_task=(
            round(worker_cpu_s * 1000 / completed, 2)
            if completed and worker_cpu_s is not None
            else None
        ),
        worker_rss_mb_peak=round(memory.worker_peak, 1) if memory.worker_peak else None,
        api_rss_mb_peak=round(memory.api_peak, 1) if memory.api_peak else None,
        timed_out=timed_out,
    )


# ------------------------------------------------------------------------------------------------ orchestration
class Bench:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.db_url, self.redis_url = resolve_urls()
        self.pg_dsn = self.db_url.replace("postgresql+asyncpg://", "postgresql://")
        self.api_port = free_port()
        self.env = service_env(self.db_url, self.redis_url, self.api_port)
        self.log_dir = Path(args.out) / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.base = f"http://127.0.0.1:{self.api_port}/api/v1"
        self.processes: list[ServiceProcess] = []
        self.workload = WORKLOADS[args.workload]

    def spawn(self, module: str, *argv: str, name: str) -> ServiceProcess:
        proc = ServiceProcess(module, *argv, log_dir=self.log_dir, name=name, env=self.env).start()
        self.processes.append(proc)
        return proc

    def stop_all(self) -> None:
        """Every process this run started, even after a failure: a leaked worker would skew the next run."""
        for proc in reversed(self.processes):
            proc.stop()
        self.processes.clear()

    async def reset(self, conn: asyncpg.Connection, *, everything: bool) -> None:
        if everything:
            tables = [
                r[0]
                for r in await conn.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            ]
        else:
            tables = TABLES_PER_RUN
        await conn.execute(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE")

    async def wait_api(self) -> None:
        async with httpx.AsyncClient() as http:
            for _ in range(240):
                try:
                    if (
                        await http.get(f"http://127.0.0.1:{self.api_port}/health/ready")
                    ).status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.25)
        raise SystemExit("API did not become ready; see logs in " + str(self.log_dir))

    async def login(self, http: httpx.AsyncClient) -> dict[str, str]:
        resp = await http.post(
            f"{self.base}/auth/register",
            json={"email": "bench@example.com", "password": PASSWORD, "full_name": "Benchmark"},
        )
        if resp.status_code == 409:
            resp = await http.post(
                f"{self.base}/auth/login", json={"email": "bench@example.com", "password": PASSWORD}
            )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        me = (
            await http.get(f"{self.base}/auth/me", headers={"Authorization": f"Bearer {token}"})
        ).json()
        return {
            "Authorization": f"Bearer {token}",
            "X-Project-ID": me["memberships"][0]["projects"][0]["id"],
        }

    async def submit(self, http: httpx.AsyncClient, headers: dict[str, str], jobs: int) -> float:
        task = {"task_type": self.workload.task_type, "payload": self.workload.payload}
        batch = [task] * min(jobs, 1000)
        sizes = [min(1000, jobs - i) for i in range(0, jobs, 1000)]
        gate = asyncio.Semaphore(4)

        async def send(size: int) -> None:
            async with gate:
                resp = await http.post(
                    f"{self.base}/tasks/batch",
                    headers=headers,
                    json={"tasks": batch[:size]},
                    timeout=120,
                )
                resp.raise_for_status()

        start = time.perf_counter()
        await asyncio.gather(*(send(s) for s in sizes))
        return time.perf_counter() - start

    async def wait_workers(self, conn: asyncpg.Connection, names: list[str]) -> None:
        """Start measuring only once every worker has registered and heartbeated. Several Python processes starting
        at once on Windows can take well over the few seconds a fixed sleep would allow."""
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            # Workers append their pid to the given name ("bench-4w-0-1234").
            ready = await conn.fetchval(
                "SELECT count(*) FROM workers WHERE name LIKE ANY($1::text[]) AND status = 'ACTIVE' "
                "AND last_heartbeat_at > now() - interval '10 seconds'",
                [f"{name}-%" for name in names],
            )
            if ready >= len(names):
                await asyncio.sleep(2)  # first reads issued, consumer groups created
                return
            await asyncio.sleep(0.25)
        raise SystemExit(f"workers {names} did not all register within 120 s; see {self.log_dir}")

    async def wait_done(self, conn: asyncpg.Connection, jobs: int) -> bool:
        deadline = time.monotonic() + max(120.0, jobs / 20)  # generous: at least 20 tasks/s
        while time.monotonic() < deadline:
            done = await conn.fetchval(
                "SELECT count(*) FROM tasks WHERE state IN ('SUCCEEDED', 'FAILED', 'DEAD_LETTERED', 'CANCELLED')"
            )
            if done >= jobs:
                return False
            await asyncio.sleep(0.25)
        return True

    async def start_worker_set(
        self,
        conn: asyncpg.Connection,
        http: httpx.AsyncClient,
        workers: int,
        generation: int = 0,
    ) -> list[ServiceProcess]:
        prefix = f"bench-{workers}w" + (f"-g{generation}" if generation else "")
        names = [f"{prefix}-{i}" for i in range(workers)]
        procs = [
            self.spawn(
                "flowforge_worker",
                "--concurrency",
                str(self.args.concurrency),
                "--name",
                name,
                name=name,
            )
            for name in names
        ]
        await self.wait_workers(conn, names)
        # Unmeasured warm-up: caches fill on a fresh process's first tasks (cold numbers: see the performance report).
        warmup = self.args.warmup * workers
        if warmup:
            await self.reset(conn, everything=False)
            await self.submit(http, await self.login(http), warmup)
            await self.wait_done(conn, warmup)
        return procs

    async def kill_when_finished(self, worker: ServiceProcess, threshold: int) -> datetime:
        """SIGKILL ``worker`` (no shutdown, no lease release) once ``threshold`` tasks have succeeded. Returns
        PostgreSQL's clock at the moment of the kill."""
        conn = await asyncpg.connect(self.pg_dsn)
        try:
            while True:
                done = await conn.fetchval("SELECT count(*) FROM tasks WHERE state = 'SUCCEEDED'")
                if done >= threshold:
                    break
                await asyncio.sleep(0.05)
            await asyncio.to_thread(worker.kill)
            killed_at: datetime = await conn.fetchval("SELECT clock_timestamp()")
            return killed_at
        finally:
            await conn.close()

    async def run(self) -> dict[str, Any]:
        jobs_list = [int(j) for j in self.args.jobs.split(",")]
        worker_list = [int(w) for w in self.args.workers.split(",")]
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "flowforge_core.cli", "migrate"],
            env={**os.environ, **self.env},
            check=True,
        )
        conn = await asyncpg.connect(self.pg_dsn)
        redis = Redis.from_url(self.redis_url)
        await redis.flushdb()
        await self.reset(conn, everything=True)
        baseline = await commit_rate(self.pg_dsn)
        print(f"PostgreSQL baseline: {baseline} single-row commits/s", flush=True)  # noqa: T201
        info = hardware(
            await conn.fetchval("SHOW server_version"),
            (await redis.info("server"))["redis_version"],
        )
        await redis.aclose()

        api = self.spawn("flowforge_api", name="api")
        self.spawn("flowforge_scheduler", name="scheduler")
        results: list[RunResult] = []

        def report(complete: bool) -> dict[str, Any]:
            return {
                "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "complete": complete,
                "hardware": info,
                "postgres_commit_rate_per_second": baseline,
                "workload": self.args.workload,
                "settings": {
                    "task_type": self.workload.task_type,
                    "payload": json.dumps(self.workload.payload),
                    "worker_concurrency": self.args.concurrency,
                    "worker_cpu_processes": 2,
                    "submission": "HTTP POST /tasks/batch, 1000 tasks per request, 4 requests in flight",
                    "database_pool_per_process": "10 + 10 overflow (production defaults)",
                    "timings": "production defaults (heartbeat 5 s, lease 30 s)",
                    "warm_up": (
                        f"each worker set first completes {self.args.warmup} unmeasured tasks per worker"
                        if self.args.warmup
                        else "none (cold workers)"
                    ),
                },
                "results": [asdict(r) for r in results],
            }

        try:
            await self.wait_api()
            async with httpx.AsyncClient() as http:
                for workers in worker_list:
                    procs = await self.start_worker_set(conn, http, workers)
                    for run_index, jobs in enumerate(jobs_list):
                        if self.args.kill_worker_after is not None and run_index:
                            # A killed worker does not come back: each run with a kill gets a fresh worker set.
                            for proc in procs:
                                proc.stop()
                            procs = await self.start_worker_set(conn, http, workers, run_index)
                        await self.reset(conn, everything=False)
                        # A fresh session per run: a long run outlives a 15-minute access token.
                        headers = await self.login(http)
                        before = await commits(conn)
                        cpu_before = cpu_seconds(procs)
                        memory = PeakMemory(procs, api)
                        stop_sampling = asyncio.Event()
                        sampler = asyncio.create_task(memory.watch(stop_sampling))
                        killer = (
                            asyncio.create_task(
                                self.kill_when_finished(
                                    procs[0], int(jobs * self.args.kill_worker_after)
                                )
                            )
                            if self.args.kill_worker_after is not None
                            else None
                        )
                        try:
                            submit_s = await self.submit(http, headers, jobs)
                            timed_out = await self.wait_done(conn, jobs)
                        finally:
                            stop_sampling.set()
                            await sampler
                        if memory.longest_pause > PAUSE_LIMIT_SECONDS:
                            raise SystemExit(
                                f"the machine paused the benchmark for {memory.longest_pause:.0f} s during the "
                                f"{jobs}-job run on {workers} workers (sleep or standby?). That run is discarded; "
                                "keep the machine awake and run again."
                            )
                        delta = await commits(conn) - before
                        # A killed worker's CPU time disappears with it, so CPU per task is not reported then.
                        cpu = None if killer else cpu_seconds(procs) - cpu_before
                        result = await measure(
                            conn,
                            jobs,
                            workers,
                            self.args.concurrency,
                            submit_s,
                            timed_out,
                            delta,
                            cpu,
                            memory,
                        )
                        if killer is not None:
                            if not killer.done():
                                killer.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await killer
                                raise SystemExit(
                                    "the run ended before the kill point; lower --kill-worker-after"
                                )
                            killed_at = killer.result()
                            result.killed_after_completed = int(jobs * self.args.kill_worker_after)
                            for key, value in (await measure_recovery(conn, killed_at)).items():
                                setattr(result, key, value)
                        results.append(result)
                        # Written after every run, so a failure later in the matrix keeps what was measured.
                        write_report(Path(self.args.out), report(complete=False))
                        print(  # noqa: T201 - progress for the operator
                            f"{jobs:>6} jobs x {workers} workers: {result.throughput_per_second} tasks/s, "
                            f"e2e p95 {result.e2e_ms['p95']} ms, completed {result.completed}/{jobs}"
                            + (" (TIMED OUT)" if timed_out else ""),
                            flush=True,
                        )
                        if timed_out:
                            # Its unfinished tasks would run during, and distort, every later run.
                            raise SystemExit("a run timed out; the report above is partial")
                    for proc in procs:
                        proc.stop()
        finally:
            self.stop_all()
            await conn.close()
        return report(complete=True)


def write_report(out: Path, report: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "results.md").write_text(markdown(report), encoding="utf-8")


WORKLOAD_TITLES = {"noop": "no-op tasks", "io": "I/O-bound tasks", "cpu": "CPU-bound tasks"}


def markdown(report: dict[str, Any]) -> str:
    hw = report["hardware"]
    baseline = report["postgres_commit_rate_per_second"]
    name = report.get("workload", "noop")
    workload = WORKLOADS[name]
    results = report["results"]
    lines = [
        f"# FlowForge benchmark results: {WORKLOAD_TITLES[name]}",
        "",
        f"Measured {report['measured_at']} with `python -m tests.performance.benchmark --workload {name}`. Every",
        "number below was measured on the hardware listed; nothing is estimated or extrapolated.",
        "",
        f"Workload: `{workload.task_type}` with payload `{json.dumps(workload.payload)}`, which {workload.description}.",
        "",
        *(
            []
            if report.get("complete", True)
            else ["> **Partial results:** the matrix stopped before every configuration ran.", ""]
        ),
        "## Environment",
        "",
        "| | |",
        "|---|---|",
        f"| CPU | {hw['cpu']} ({hw['physical_cores']} cores / {hw['logical_cores']} threads) |",
        f"| Memory | {hw['memory_gb']} GB |",
        f"| OS | {hw['os']} |",
        f"| Python | {hw['python']} |",
        f"| PostgreSQL | {hw['postgresql']} |",
        f"| Redis | {hw['redis']} |",
        f"| PostgreSQL baseline | {baseline} single-row commits/s (8 connections, no FlowForge involved) |",
        "",
        "Settings: "
        + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in report["settings"].items())
        + ".",
        "",
        "## Results",
        "",
        "| Jobs | Workers | Completed | Throughput (tasks/s) | E2E p50 / p95 / p99 (ms) | Queue wait p50 / p95 / p99 (ms) "
        "| Exec p50 (ms) | Attempts/task | Duplicate successes | Commits/task | Worker CPU/task (ms) "
        "| Peak RSS, worker / API (MB) |",
        "|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in results:
        e, w = r["e2e_ms"], r["queue_wait_ms"]
        lines.append(
            f"| {r['jobs']:,} | {r['workers']} | {r['completed']:,}{' (timed out)' if r['timed_out'] else ''} "
            f"| {r['throughput_per_second']} | {e['p50']} / {e['p95']} / {e['p99']} "
            f"| {w['p50']} / {w['p95']} / {w['p99']} | {r['exec_ms_p50']} | {r['attempts_per_task']} "
            f"| {r['duplicate_successes']} | {r['commits_per_task']} | {r['worker_cpu_ms_per_task']} "
            f"| {r.get('worker_rss_mb_peak')} / {r.get('api_rss_mb_peak')} |"
        )
    recovery = [r for r in results if r.get("killed_after_completed") is not None]
    if recovery:
        lines += [
            "",
            "## Recovery",
            "",
            "One worker was killed with SIGKILL (no shutdown, no lease release) once the given number of tasks had",
            "succeeded. The tasks it was running are retried once their 30 s leases expire (the reaper checks every",
            "5 s). Entries it had read but not yet claimed are reclaimed by other workers after 60 s.",
            "",
            "| Jobs | Workers at start | Killed after | Lost attempts | Tasks retried | Lost tasks done (s after kill) "
            "| Last task done (s after kill) | Completed | Duplicate successes |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in recovery:
            lines.append(
                f"| {r['jobs']:,} | {r['workers']} | {r['killed_after_completed']:,} | {r['lost_attempts']} "
                f"| {r['retried_tasks']} | {r['recovery_seconds']} | {r['last_finish_after_kill_seconds']} "
                f"| {r['completed']:,} | {r['duplicate_successes']} |"
            )
    lines += reading_the_numbers(report)
    return "\n".join(lines) + "\n"


def reading_the_numbers(report: dict[str, Any]) -> list[str]:
    baseline = report["postgres_commit_rate_per_second"]
    name = report.get("workload", "noop")
    concurrency = report["settings"]["worker_concurrency"]
    threads = report["hardware"]["logical_cores"]
    steady = [
        r
        for r in report["results"]
        if r["commits_per_task"] and r["jobs"] >= 1000 and r.get("killed_after_completed") is None
    ]
    if not steady or not baseline:
        return []
    per_task = statistics.median(r["commits_per_task"] for r in steady)
    best = max(r["throughput_per_second"] or 0 for r in steady)
    by_workers: dict[int, list[float]] = {}
    for r in steady:
        if r["worker_cpu_ms_per_task"]:
            by_workers.setdefault(r["workers"], []).append(r["worker_cpu_ms_per_task"])
    lines = [
        "",
        "## Reading the numbers",
        "",
        f"* Each task costs about {per_task:g} PostgreSQL commits (submission, outbox relay, claim, completion,",
        "  events), measured from `pg_stat_database`. This includes the services' background transactions.",
        f"* PostgreSQL alone sustained {baseline:g} commits/s on this machine, which would allow roughly",
        f"  {baseline / per_task:.0f} tasks/s. The best measured throughput was {best:g} tasks/s, so PostgreSQL is not",
        "  the bottleneck here.",
    ]
    if name == "noop" and by_workers:
        fewest, most = min(by_workers), max(by_workers)
        single = statistics.median(by_workers[fewest])
        lines += [
            f"* With {fewest} worker process(es), each task costs about {single:g} ms of worker CPU, so one process",
            f"  tops out near {1000 / single:.0f} tasks/s whatever its number of slots. The worker is CPU-bound.",
        ]
        if most != fewest:
            crowded = statistics.median(by_workers[most])
            lines += [
                f"* With {most} worker processes the cost rose to about {crowded:g} ms per task: the processes share the",
                "  machine's cores (and hyperthreads) with each other, the API, the scheduler, PostgreSQL and Redis.",
                "  That is why throughput grows with workers, but less than proportionally.",
            ]
    if name == "io":
        ceiling = concurrency / IO_WAIT_SECONDS
        largest = max(r["jobs"] for r in steady)
        reached = ", ".join(
            f"{r['workers']} worker(s) {r['throughput_per_second']:g} of {r['workers'] * ceiling:.0f}"
            for r in steady
            if r["jobs"] == largest
        )
        lines += [
            f"* Each task waits {IO_WAIT_SECONDS * 1000:.0f} ms without using CPU, so a worker with {concurrency} slots can",
            f"  finish at most {ceiling:.0f} tasks/s, and N workers N times that. Measured at {largest:,} jobs: {reached}",
            "  tasks/s. Raise a worker's concurrency (and its database pool) for I/O-bound handlers.",
        ]
    if name == "cpu":
        lines += [
            f"* Each worker runs CPU-bound handlers in {report['settings'].get('worker_cpu_processes', 2)} pool processes,"
            " so CPU-bound throughput grows with",
            "  pool processes until the machine runs out of cores. Worker CPU per task includes the pool processes.",
        ]
    if by_workers:
        most = max(by_workers)
        busiest = max(
            (r for r in steady if r["workers"] == most and r["worker_cpu_ms_per_task"]),
            key=lambda r: r["jobs"],
        )
        cores = busiest["throughput_per_second"] * busiest["worker_cpu_ms_per_task"] / 1000
        lines += [
            f"* In the {busiest['jobs']:,}-job run on {most} workers, worker processes kept about {cores:.1f} of the",
            f"  machine's {threads} hardware threads busy ({busiest['throughput_per_second']:g} tasks/s at"
            f" {busiest['worker_cpu_ms_per_task']:g} ms of CPU each).",
        ]
    lines += [
        "* End-to-end and queue-wait percentiles include the backlog: all jobs of a run are submitted at once, so",
        "  the last ones wait for the ones before them.",
        "* Runs of 100 jobs finish in about a second, so fixed latencies (submission, relay hop, the first",
        "  poll) dominate their throughput figure. Read the larger runs for sustained rates.",
    ]
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--workload", choices=sorted(WORKLOADS), default="noop")
    parser.add_argument("--jobs", default="100,1000,10000,50000")
    parser.add_argument("--workers", default="1,2,4,8")
    parser.add_argument(
        "--concurrency", type=int, default=8, help="execution slots per worker process"
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=WARMUP_TASKS_PER_WORKER,
        help="unmeasured tasks per worker before each worker set (0 measures cold workers)",
    )
    parser.add_argument(
        "--kill-worker-after",
        type=float,
        default=None,
        metavar="FRACTION",
        help="SIGKILL one worker once this fraction of the jobs has succeeded; each run gets fresh workers (2+)",
    )
    parser.add_argument("--out", default=str(ROOT / "docs" / "benchmarks"))
    args = parser.parse_args()
    if args.kill_worker_after is not None:
        if not 0 < args.kill_worker_after < 1:
            parser.error("--kill-worker-after must be between 0 and 1")
        if any(int(w) < 2 for w in args.workers.split(",")):
            parser.error("--kill-worker-after needs at least 2 workers")
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    keep_awake()
    report = asyncio.run(Bench(args).run())
    out = Path(args.out)
    write_report(out, report)
    print(f"wrote {out / 'results.md'}")  # noqa: T201


if __name__ == "__main__":
    main()
