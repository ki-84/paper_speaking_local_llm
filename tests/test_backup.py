import os
import subprocess
import sys

from paperspeak import config, papers


def test_backup_refuses_missing_referenced_assets_without_publishing(
    database, tmp_path
):
    papers.register(
        {
            "source_id": "test",
            "version": "v1",
            "title": "Backup test",
            "pdf_path": "papers/missing.pdf",
        }
    )
    target = tmp_path / "output.tar.gz"
    r = subprocess.run(
        [
            sys.executable,
            str(config.ROOT / "scripts/manage.py"),
            "backup",
            "--output",
            str(target),
        ],
        env=os.environ | {"PAPERSPEAK_DATA": str(database)},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert r.returncode != 0 and "referenced asset is missing" in r.stderr
    assert (
        not target.exists() and not target.with_name(target.name + ".partial").exists()
    )
