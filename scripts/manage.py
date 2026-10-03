#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import socket
import sqlite3
import subprocess
import tarfile
import time
from pathlib import Path

from paperspeak import config, db, youtube
from paperspeak.api import credentials, password_required
from paperspeak.runtime import owned_command


def host():
    configured = os.environ.get("PAPERSPEAK_LAN_HOST")
    if configured:
        return configured
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
        except OSError:
            return "localhost"


def process_alive(path, marker):
    try:
        pid = int(path.read_text())
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
        return pid if marker.encode() in cmd else None
    except (OSError, ValueError):
        return None


def serve():
    db.init()
    credentials()
    lock = open(config.DATA / "server.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("PaperSpeak is already running.")
    children = []
    logs = []
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    addr = host()
    hosts = f"https://{addr}:8443"
    if addr != "localhost":
        hosts += ", https://localhost:8443"
    caddyfile = config.DATA / "Caddyfile"
    caddyfile.write_text(
        "{\n admin off\n auto_https disable_redirects\n skip_install_trust\n storage file_system {\n root "
        + str(config.DATA / "tls")
        + "\n }\n}\n"
        + hosts
        + " {\n tls internal\n encode zstd gzip\n reverse_proxy 127.0.0.1:"
        + str(config.API_PORT)
        + " {\n flush_interval -1\n }\n}\n"
    )
    specs = [
        (
            "api",
            [
                str(config.ROOT / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "paperspeak.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(config.API_PORT),
                "--proxy-headers",
                "--forwarded-allow-ips",
                "127.0.0.1",
            ],
        ),
        ("worker", [str(config.ROOT / ".venv/bin/python"), "-m", "paperspeak.worker"]),
        (
            "caddy",
            [
                str(config.ROOT / ".tools/caddy"),
                "run",
                "--config",
                str(caddyfile),
                "--adapter",
                "caddyfile",
            ],
        ),
    ]
    # A manually launched worker may already be preparing a lesson. Adopt it via its lock.
    probe = open(config.DATA / "worker.lock", "a")
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(probe, fcntl.LOCK_UN)
    except BlockingIOError:
        specs = [(n, a) for n, a in specs if n != "worker"]
    finally:
        probe.close()
    (config.DATA / "server.pid").write_text(str(os.getpid()))
    try:
        for name, args in specs:
            log = open(config.DATA / f"logs/{name}.log", "a")
            logs.append(log)
            proc = subprocess.Popen(
                owned_command(args),
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=config.ROOT,
            )
            children.append((name, proc))
        print(f"PaperSpeak is opening at https://{addr}:8443", flush=True)
        while not stopped:
            for name, proc in children:
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"{name} stopped with code {proc.returncode}; see data/logs/{name}.log"
                    )
            time.sleep(1)
    finally:
        for _, proc in children:
            if proc.poll() is None:
                proc.terminate()
        for _, proc in children:
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
        for log in logs:
            log.close()
        (config.DATA / "server.pid").unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description="Your private PaperSpeak studio")
    p.add_argument(
        "command",
        choices=[
            "start",
            "serve",
            "stop",
            "status",
            "access",
            "auth-on",
            "auth-off",
            "doctor",
            "backup",
            "install-service",
        ],
    )
    p.add_argument("--output", default=None)
    args = p.parse_args()
    db.init()
    if args.command == "serve":
        serve()
    elif args.command == "start":
        if process_alive(config.DATA / "server.pid", "manage.py"):
            print("PaperSpeak is already running.")
            return
        unit = Path.home() / ".config/systemd/user/paperspeak.service"
        overrides = any(k.startswith("PAPERSPEAK_") for k in os.environ)
        if (
            unit.exists()
            and str(config.ROOT / "studio") in unit.read_text()
            and not overrides
        ):
            subprocess.run(["systemctl", "--user", "start", "paperspeak"], check=True)
            print(
                f"Opening https://{host()}:8443 — use './studio access' for sign-in details."
            )
            return
        with open(config.DATA / "logs/supervisor.log", "a") as out:
            proc = subprocess.Popen(
                [str(config.ROOT / "studio"), "serve"],
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        time.sleep(2)
        if proc.poll() is not None:
            raise SystemExit("Startup failed. See data/logs/supervisor.log")
        print(
            f"Opening https://{host()}:8443 — use './studio access' for sign-in details."
        )
    elif args.command == "stop":
        stopped_pid = process_alive(config.DATA / "server.pid", "manage.py")
        managed = subprocess.run(
            ["systemctl", "--user", "show", "paperspeak", "-p", "MainPID", "--value"],
            capture_output=True,
            text=True,
        )
        if (
            stopped_pid
            and managed.returncode == 0
            and managed.stdout.strip() == str(stopped_pid)
        ):
            subprocess.run(["systemctl", "--user", "stop", "paperspeak"], check=True)
        for name, marker in [("server", "manage.py"), ("worker", "paperspeak.worker")]:
            pid = process_alive(config.DATA / f"{name}.pid", marker)
            if pid:
                os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 45
        while (
            process_alive(config.DATA / "server.pid", "manage.py")
            and time.monotonic() < deadline
        ):
            time.sleep(0.25)
        print(
            "Stopped. Recordings and saved steps are kept."
            if not process_alive(config.DATA / "server.pid", "manage.py")
            else "Still stopping. Check studio status."
        )
    elif args.command == "access":
        print(f"Studio: https://{host()}:8443")
        if password_required():
            print(f"Password: {credentials()['password']}")
        else:
            print("Password: disabled for LAN access")
        cert = config.DATA / "tls/pki/authorities/local/root.crt"
        print(f"Trust this local CA certificate on your other PC: {cert}")
        if cert.exists():
            subprocess.run(
                [
                    "openssl",
                    "x509",
                    "-in",
                    str(cert),
                    "-noout",
                    "-fingerprint",
                    "-sha256",
                ],
                check=True,
            )
    elif args.command in {"auth-on", "auth-off"}:
        path = config.DATA / "credentials.json"
        data = credentials()
        data["password_required"] = args.command == "auth-on"
        pending = path.with_suffix(".json.tmp")
        pending.write_text(json.dumps(data, indent=2) + "\n")
        pending.chmod(0o600)
        pending.replace(path)
        print(
            "A password is required to open PaperSpeak."
            if data["password_required"]
            else "Password-free LAN access is enabled. Anyone who can reach this site can use it."
        )
    elif args.command in {"doctor", "status"}:
        from paperspeak.runtime import gpu_info

        print(
            json.dumps(
                {
                    "url": f"https://{host()}:8443",
                    "supervisor_pid": process_alive(
                        config.DATA / "server.pid", "manage.py"
                    ),
                    "gpu": gpu_info(),
                    "models": list(config.manifest().get("models", {})),
                    "youtube_connected": youtube.connected(),
                    "youtube_auto_upload": youtube.automatic(),
                    "video_exports": db.all(
                        "SELECT id,lesson_id,kind,state,created,updated FROM video_exports ORDER BY created DESC LIMIT 10"
                    ),
                    "jobs": db.all(
                        "SELECT id,kind,state,stage,error FROM jobs WHERE state!='completed'"
                    ),
                    "worker": db.one("SELECT * FROM cursors WHERE key='worker'"),
                    "free_disk_gib": round(
                        os.statvfs(config.DATA).f_bavail
                        * os.statvfs(config.DATA).f_frsize
                        / 2**30,
                        1,
                    ),
                },
                indent=2,
            )
        )
    elif args.command == "backup":
        destination = Path(
            args.output or f"paperspeak-backup-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
        ).resolve()
        if destination.exists():
            raise SystemExit("That backup already exists. Choose a new output file.")
        partial = destination.with_name(destination.name + ".partial")
        # The worker holds this lock for one checkpointed step. Wait for that step,
        # then snapshot the DB and immutable files while no new step can start.
        with open(config.DATA / "work-step.lock", "a") as step:
            print("Waiting for the current processing step to finish…", flush=True)
            fcntl.flock(step, fcntl.LOCK_EX)
            if destination.exists() or partial.exists():
                raise SystemExit(
                    "Choose a new backup filename; an output or partial file already exists."
                )
            partial.touch(mode=0o600, exist_ok=False)
            snapshot = config.DATA / "backup.sqlite3"
            try:
                with db.connection() as source, sqlite3.connect(snapshot) as target:
                    source.backup(target)
                    target.execute(
                        "UPDATE jobs SET state='queued',owner=NULL WHERE state='running'"
                    )
                    target.commit()
                    referenced = set()

                    def paths(value):
                        if isinstance(value, str) and value.startswith(
                            (
                                "papers/",
                                "audio/",
                                "recordings/",
                                "visuals/",
                                "videos/",
                                "thumbnails/",
                            )
                        ):
                            referenced.add(value)
                        elif isinstance(value, dict):
                            for v in value.values():
                                paths(v)
                        elif isinstance(value, list):
                            for v in value:
                                paths(v)

                    for table in (
                        "papers",
                        "sources",
                        "chapters",
                        "attempts",
                        "visual_assets",
                        "video_exports",
                        "video_projects",
                        "thumbnail_sets",
                        "nightly_video_runs",
                    ):
                        for row in target.execute(f"SELECT data FROM {table}"):
                            paths(json.loads(row[0]))
                assets = [
                    p
                    for directory in (
                        "papers",
                        "audio",
                        "recordings",
                        "visuals",
                        "videos",
                        "thumbnails",
                    )
                    for p in (config.DATA / directory).rglob("*")
                    if p.is_file()
                ]
                # Preserve actual generation parameters and evaluation records,
                # without bundling downloaded benchmark audio or prior backups.
                assets.extend(
                    p
                    for p in (config.DATA / "evaluation").glob("*.json")
                    if p.is_file() and not p.is_symlink()
                )
                present = {str(p.relative_to(config.DATA)) for p in assets}
                if referenced - present:
                    raise RuntimeError(
                        "A referenced asset is missing; backup was not published: "
                        + str(sorted(referenced - present)[:3])
                    )
                with tarfile.open(partial, "w:gz") as archive:
                    archive.add(snapshot, arcname="data/paperspeak.sqlite3")
                    for asset in assets:
                        archive.add(
                            asset, arcname="data/" + str(asset.relative_to(config.DATA))
                        )
                    archive.add(
                        config.ROOT / "models.lock.json", arcname="models.lock.json"
                    )
                partial.chmod(0o600)
                partial.replace(destination)
                print(destination)
            finally:
                snapshot.unlink(missing_ok=True)
                partial.unlink(missing_ok=True)
    elif args.command == "install-service":
        folder = Path.home() / ".config/systemd/user"
        folder.mkdir(parents=True, exist_ok=True)
        unit = folder / "paperspeak.service"
        unit.write_text(
            f"[Unit]\nDescription=PaperSpeak private learning studio\nAfter=network-online.target\n\n[Service]\nType=simple\nWorkingDirectory={config.ROOT}\nExecStart={config.ROOT}/studio serve\nRestart=on-failure\nRestartSec=10\nTimeoutStopSec=45\nKillMode=control-group\n\n[Install]\nWantedBy=default.target\n"
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(
            ["systemctl", "--user", "enable", "paperspeak.service"], check=True
        )
        print("Enabled at user login. Start with: systemctl --user start paperspeak")


if __name__ == "__main__":
    main()
