#!/usr/bin/env python3
import argparse
import time

from paperspeak import db

p = argparse.ArgumentParser()
p.add_argument("kind", choices=["models", "speech"])
a = p.parse_args()
db.init()
kind = "benchmark" if a.kind == "models" else "calibration"
print(
    db.enqueue(
        kind,
        "evaluation:" + time.strftime("%Y%m%d-%H%M%S"),
        priority=5 if kind == "benchmark" else 6,
    )
)
print("Queued. Start the studio and follow the job in “In the making”.")
