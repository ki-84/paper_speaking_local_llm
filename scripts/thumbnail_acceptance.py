"""Inspect actual saved thumbnails and verify the protected original pixel portraits."""

import argparse
import json

import numpy as np
import pymupdf
from paperspeak import config, thumbnails, video

p = argparse.ArgumentParser()
p.add_argument("project_id")
args = p.parse_args()
report = {"project_id": args.project_id, "sets": {}, "checks": {}}
for mode in ["overview", "deep_dive"]:
    row = thumbnails.get(args.project_id, mode)
    if not row or row["state"] != "ready":
        raise SystemExit("Both ready thumbnail sets are required")
    candidates = []
    for c in row["data"]["candidates"]:
        files = {}
        for format in ["png", "jpg"]:
            path = config.safe_path(c[format])
            pix = pymupdf.Pixmap(str(path))
            assert (pix.width, pix.height) == (1280, 720)
            assert path.stat().st_size < 2_000_000
            files[format] = {
                "path": c[format],
                "bytes": path.stat().st_size,
                "sha256": video.file_digest(path),
            }
        assert files["png"]["sha256"] == c["sha256"]
        candidates.append(
            {
                "id": c["id"],
                "lines": c["lines"],
                "files": files,
                "generation": c["generation"],
            }
        )
    assert len(candidates) == 3
    key = video.digest(
        [
            thumbnails.VERSION,
            row["data"]["manifest"]["characters"],
            row["data"]["manifest"]["image_model"],
            row["data"]["manifest"]["renderer"],
        ]
    )[:16]
    chars = config.DATA / "thumbnails/characters" / key
    portraits = {}
    for role in ["guide", "host"]:
        values = []
        for suffix in ["-reference", ""]:
            pix = pymupdf.Pixmap(str(chars / (role + suffix + ".png")))
            values.append(
                np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n
                )
            )
        reference, edited = values
        assert edited.shape[:2] == reference.shape[:2] == (512, 512)
        mask = np.ones((512, 512), bool)
        for x, y, w, h in [[23, 25, 19, 9], [27, 35, 13, 9]]:
            mask[y * 8 : (y + h) * 8, x * 8 : (x + w) * 8] = False
        if edited.shape[2] == 4:
            mask &= edited[:, :, 3] == 255
        error = int(
            np.max(
                np.abs(
                    reference[:, :, :3].astype(int)[mask]
                    - edited[:, :, :3].astype(int)[mask]
                )
            )
        )
        assert error == 0, (
            "Hair/clothes/body pixels changed outside the permitted face regions"
        )
        portraits[role] = {
            "sha256": video.file_digest(chars / (role + ".png")),
            "protected_region_max_rgb_error": error,
            "protected_pixels": int(mask.sum()),
        }
    report["sets"][mode] = {
        "id": row["id"],
        "recommended_id": row["data"]["recommended_id"],
        "selected_id": row["data"]["selected_id"],
        "review": row["data"]["review"],
        "warnings": row["data"]["warnings"],
        "candidates": candidates,
        "portraits": portraits,
    }
report["checks"] = {
    "two_sets_three_candidates_each": True,
    "png_jpeg_1280_720_under_2mb": True,
    "recorded_png_hashes_match": True,
    "hair_clothes_body_preserved": True,
}
report["status"] = "passed"
target = config.DATA / "evaluation" / ("thumbnails-" + args.project_id + ".json")
target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(
    json.dumps(
        {"status": report["status"], "checks": report["checks"], "report": str(target)},
        ensure_ascii=False,
    )
)
