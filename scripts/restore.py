#!/usr/bin/env python3
"""Restore a backup into a new directory; never overwrite a running studio."""

import argparse
import sqlite3
import tarfile
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("archive", type=Path)
p.add_argument("destination", type=Path)
args = p.parse_args()
dest = args.destination.resolve()
if dest.exists():
    raise SystemExit("Choose a new, empty destination directory.")
with tarfile.open(args.archive, "r:gz") as archive:
    for member in archive.getmembers():
        target = (dest / member.name).resolve()
        if not target.is_relative_to(dest) or member.issym() or member.islnk():
            raise SystemExit("Unsafe archive member.")
    dest.mkdir(parents=True, mode=0o700)
    archive.extractall(dest, filter="data")
with sqlite3.connect(dest / "data/paperspeak.sqlite3") as c:
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.execute("DELETE FROM sessions")
    c.execute(
        "UPDATE jobs SET state='queued',owner=NULL,heartbeat=0 WHERE state='running'"
    )
print("Verified restored database:", dest / "data")
print(
    "Stop the current studio, then set PAPERSPEAK_DATA to this data directory before starting it."
)
print(
    "A new password and local certificate will be created. Models remain in the application models folder."
)
