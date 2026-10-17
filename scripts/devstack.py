#!/usr/bin/env python3
"""Native development stack (no Docker required): PostgreSQL + Redis.

    python scripts/devstack.py init-env   # create .env from .env.example with generated secrets
    python scripts/devstack.py up         # init (first run) and start PostgreSQL and Redis
    python scripts/devstack.py status
    python scripts/devstack.py down
    python scripts/devstack.py stop-redis | start-redis | stop-postgres | start-postgres   (fault injection)

PostgreSQL: uses `pg_ctl`/`initdb` from FLOWFORGE_PG_BIN, `.devtools/pgsql/bin`, or PATH. Data lives in
`.devtools/data/postgres`.

Redis: uses `redis-server` from FLOWFORGE_REDIS_BIN or PATH. On Windows, where unsigned Redis builds may be blocked by
Smart App Control, it falls back to a WSL distro (FLOWFORGE_WSL_DISTRO, default `flowforge-dev`) with redis installed.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEVTOOLS = ROOT / ".devtools"
PG_DATA = DEVTOOLS / "data" / "postgres"
LOGS = DEVTOOLS / "logs"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
IS_WINDOWS = os.name == "nt"
EXE = ".exe" if IS_WINDOWS else ""

PG_PORT = int(os.environ.get("FLOWFORGE_DEV_PG_PORT", "5432"))
REDIS_PORT = int(os.environ.get("FLOWFORGE_DEV_REDIS_PORT", "6379"))
WSL_DISTRO = os.environ.get("FLOWFORGE_WSL_DISTRO", "flowforge-dev")
DATABASES = ("flowforge", "flowforge_test")


# ----------------------------------------------------------------------------------------------- helpers
def log(msg: str) -> None:
    print(f"[devstack] {msg}", flush=True)


def run(
    cmd: list[str], *, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, capture_output=True, env=env)


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def init_env(force: bool = False) -> None:
    if ENV_FILE.exists() and not force:
        log(".env already exists (use --force to regenerate)")
        return
    template = ENV_EXAMPLE.read_text(encoding="utf-8")

    def secret(_: re.Match[str]) -> str:
        return secrets.token_urlsafe(36)

    content = re.sub(r"__GENERATE__", secret, template)
    # Local development serves the API over http://localhost; browsers accept Secure cookies there, but
    # non-browser clients (tests, curl over 127.0.0.1) are simpler with this off. Production keeps the default.
    content = content.replace("FLOWFORGE_COOKIE_SECURE=true", "FLOWFORGE_COOKIE_SECURE=false")
    ENV_FILE.write_text(content, encoding="utf-8")
    log(f"wrote {ENV_FILE} with freshly generated secrets")


# ----------------------------------------------------------------------------------------------- postgres
def pg_bin(name: str) -> str:
    candidates = []
    if os.environ.get("FLOWFORGE_PG_BIN"):
        candidates.append(Path(os.environ["FLOWFORGE_PG_BIN"]) / f"{name}{EXE}")
    candidates.append(DEVTOOLS / "pgsql" / "bin" / f"{name}{EXE}")
    for c in candidates:
        if c.exists():
            return str(c)
    found = shutil.which(name)
    if found:
        return found
    sys.exit(f"[devstack] cannot find PostgreSQL binary {name!r}; set FLOWFORGE_PG_BIN")


def pg_superuser_password() -> str:
    pw_file = DEVTOOLS / "pg-superuser-password"
    if not pw_file.exists():
        pw_file.parent.mkdir(parents=True, exist_ok=True)
        pw_file.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    return pw_file.read_text(encoding="utf-8").strip()


def postgres_init() -> None:
    if (PG_DATA / "PG_VERSION").exists():
        return
    PG_DATA.mkdir(parents=True, exist_ok=True)
    pwfile = DEVTOOLS / "pg-pwfile.tmp"
    pwfile.write_text(pg_superuser_password(), encoding="utf-8")
    try:
        log("initializing PostgreSQL data directory")
        run(
            [
                pg_bin("initdb"),
                "-D",
                str(PG_DATA),
                "-U",
                "postgres",
                "--pwfile",
                str(pwfile),
                "--auth=scram-sha-256",
                "-E",
                "UTF8",
                "--locale=C",
                "--no-instructions",
            ]
        )
    finally:
        pwfile.unlink(missing_ok=True)
    conf = PG_DATA / "postgresql.conf"
    with conf.open("a", encoding="utf-8") as f:
        f.write(
            "\n# --- flowforge devstack ---\n"
            f"port = {PG_PORT}\n"
            "listen_addresses = 'localhost'\n"
            "max_connections = 300\n"
            "shared_buffers = 256MB\n"
            "timezone = 'UTC'\n"
            "log_timezone = 'UTC'\n"
            # durability stays ON: benchmarks must reflect real commit costs
            "fsync = on\n"
            "synchronous_commit = on\n"
        )


def postgres_running() -> bool:
    if not (PG_DATA / "PG_VERSION").exists():
        return False
    result = run([pg_bin("pg_ctl"), "status", "-D", str(PG_DATA)], check=False)
    return result.returncode == 0


def postgres_start() -> None:
    postgres_init()
    if postgres_running():
        log(f"PostgreSQL already running on :{PG_PORT}")
    else:
        LOGS.mkdir(parents=True, exist_ok=True)
        log("starting PostgreSQL")
        # The postmaster inherits pg_ctl's stdio handles. Capturing them through pipes would block forever
        # (EOF never arrives while the server runs), so stdio goes to a file instead.
        with (LOGS / "pg_ctl.log").open("a", encoding="utf-8") as out:
            subprocess.run(
                [
                    pg_bin("pg_ctl"),
                    "start",
                    "-D",
                    str(PG_DATA),
                    "-l",
                    str(LOGS / "postgres.log"),
                    "-w",
                    "-t",
                    "60",
                ],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=out,
            )
    ensure_databases()


def postgres_stop(mode: str = "fast") -> None:
    if postgres_running():
        log(f"stopping PostgreSQL ({mode})")
        run([pg_bin("pg_ctl"), "stop", "-D", str(PG_DATA), "-m", mode, "-w", "-t", "60"])
    else:
        log("PostgreSQL not running")


def psql(sql: str, database: str = "postgres") -> str:
    env = {**os.environ, "PGPASSWORD": pg_superuser_password()}
    result = run(
        [
            pg_bin("psql"),
            "-h",
            "localhost",
            "-p",
            str(PG_PORT),
            "-U",
            "postgres",
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-tAc",
            sql,
        ],
        env=env,
    )
    return result.stdout.strip()


def ensure_databases() -> None:
    env = read_env()
    user = env.get("POSTGRES_USER", "flowforge")
    password = env.get("POSTGRES_PASSWORD")
    if not password or password == "__GENERATE__":  # noqa: S105 - placeholder check, not a secret
        sys.exit(
            "[devstack] POSTGRES_PASSWORD missing in .env; run `python scripts/devstack.py init-env`"
        )
    # Role DDL cannot use bind parameters through psql -c; both values are validated against a strict
    # [A-Za-z0-9_-] allowlist above, which rules out quote injection.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", user) or not re.fullmatch(r"[A-Za-z0-9_-]+", password):
        sys.exit("[devstack] POSTGRES_USER/POSTGRES_PASSWORD must be URL-safe ([A-Za-z0-9_-])")
    exists = psql(f"SELECT 1 FROM pg_roles WHERE rolname = '{user}'")  # noqa: S608
    if exists != "1":
        psql(f"CREATE ROLE \"{user}\" LOGIN PASSWORD '{password}'")
        log(f"created role {user}")
    else:
        psql(f"ALTER ROLE \"{user}\" PASSWORD '{password}'")
    for db in DATABASES:
        if psql(f"SELECT 1 FROM pg_database WHERE datname = '{db}'") != "1":  # noqa: S608
            psql(f'CREATE DATABASE "{db}" OWNER "{user}"')
            log(f"created database {db}")


# ----------------------------------------------------------------------------------------------- redis
def native_redis() -> str | None:
    if os.environ.get("FLOWFORGE_REDIS_BIN"):
        return os.environ["FLOWFORGE_REDIS_BIN"]
    if IS_WINDOWS:
        return None  # unsigned Windows builds are frequently blocked; use WSL
    return shutil.which("redis-server")


def redis_args(*, inside_wsl: bool) -> list[str]:
    # Native Redis binds to loopback only. Inside the WSL VM, 0.0.0.0 is required for Windows localhost
    # forwarding; the VM sits behind NAT and is not reachable from the LAN.
    return [
        "--port",
        str(REDIS_PORT),
        "--bind",
        "0.0.0.0" if inside_wsl else "127.0.0.1",  # noqa: S104 - see comment above
        "--protected-mode",
        "no",
        "--appendonly",
        "yes",
        "--appendfsync",
        "everysec",
        "--save",
        "",
    ]


def redis_running() -> bool:
    return port_open(REDIS_PORT)


def redis_start() -> None:
    if redis_running():
        log(f"Redis already running on :{REDIS_PORT}")
        return
    native = native_redis()
    if native:
        data = DEVTOOLS / "data" / "redis"
        data.mkdir(parents=True, exist_ok=True)
        LOGS.mkdir(parents=True, exist_ok=True)
        log("starting native redis-server")
        subprocess.Popen(
            [
                native,
                *redis_args(inside_wsl=False),
                "--dir",
                str(data),
                "--logfile",
                str(LOGS / "redis.log"),
                "--daemonize",
                "yes",
            ],
        )
    else:
        log(f"starting Redis inside WSL distro {WSL_DISTRO!r}")
        script = (
            "mkdir -p /var/lib/ffredis && "
            f"redis-server {' '.join(a if a else repr(a) for a in redis_args(inside_wsl=True))} "
            "--dir /var/lib/ffredis --logfile /var/lib/ffredis/redis.log --daemonize yes"
        )
        run(["wsl", "-d", WSL_DISTRO, "-u", "root", "--", "sh", "-c", script])
    for _ in range(50):
        if redis_running():
            log(f"Redis is up on :{REDIS_PORT}")
            return
        time.sleep(0.2)
    sys.exit("[devstack] Redis did not start")


def redis_stop() -> None:
    if not redis_running():
        log("Redis not running")
        return
    log("stopping Redis")
    if native_redis():
        cli = shutil.which("redis-cli")
        if cli:
            run([cli, "-p", str(REDIS_PORT), "shutdown"], check=False)
    else:
        run(
            [
                "wsl",
                "-d",
                WSL_DISTRO,
                "-u",
                "root",
                "--",
                "sh",
                "-c",
                f"redis-cli -p {REDIS_PORT} shutdown || pkill redis-server",
            ],
            check=False,
        )
    for _ in range(50):
        if not redis_running():
            return
        time.sleep(0.2)


# ----------------------------------------------------------------------------------------------- commands
def status() -> None:
    pg = "running" if postgres_running() else "stopped"
    rd = "running" if redis_running() else "stopped"
    log(f"PostgreSQL :{PG_PORT} {pg} | Redis :{REDIS_PORT} {rd}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command",
        choices=[
            "init-env",
            "up",
            "down",
            "status",
            "start-postgres",
            "stop-postgres",
            "kill-postgres",
            "start-redis",
            "stop-redis",
            "psql",
        ],
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sql", default="SELECT version()")
    parser.add_argument("--database", default="flowforge")
    args = parser.parse_args()

    if args.command == "init-env":
        init_env(force=args.force)
    elif args.command == "up":
        init_env()
        postgres_start()
        redis_start()
        status()
    elif args.command == "down":
        postgres_stop()
        redis_stop()
    elif args.command == "status":
        status()
    elif args.command == "start-postgres":
        postgres_start()
    elif args.command == "stop-postgres":
        postgres_stop()
    elif args.command == "kill-postgres":
        postgres_stop(mode="immediate")
    elif args.command == "start-redis":
        redis_start()
    elif args.command == "stop-redis":
        redis_stop()
    elif args.command == "psql":
        print(psql(args.sql, args.database))


if __name__ == "__main__":
    main()
