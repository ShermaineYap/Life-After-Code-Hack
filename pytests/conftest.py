"""Shared fixtures: one real dbt build per test session, in a temp directory."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def built(tmp_path_factory) -> dict:
    """Build the project once into a temp target and warehouse."""
    out = tmp_path_factory.mktemp("build")
    env = dict(os.environ, DUCKDB_PATH=str(out / "warehouse.duckdb"), DBT_TARGET="dev")
    subprocess.run(
        ["dbt", "build", "--profiles-dir", ".", "--target-path", str(out / "target"), "-q"],
        cwd=ROOT, env=env, check=True,
    )
    return {
        "manifest": out / "target" / "manifest.json",
        "run_results": out / "target" / "run_results.json",
        "warehouse": out / "warehouse.duckdb",
        "dir": out,
    }


@pytest.fixture()
def warehouse_copy(built, tmp_path) -> Path:
    dst = tmp_path / "warehouse.duckdb"
    shutil.copy(built["warehouse"], dst)
    return dst
