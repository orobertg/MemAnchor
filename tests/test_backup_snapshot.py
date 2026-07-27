"""Coordinated backup snapshot: real subprocess run against a temp config +
a tiny real SQLite db + a dummy Chroma directory."""

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

SCRIPT_PATH = Path(__file__).parent.parent / "memanchor" / "tools" / "backup_snapshot.sh"


def test_backup_snapshot_produces_valid_sqlite_chroma_and_manifest(tmp_path):
    data_dir = tmp_path / "data"
    chroma_dir = data_dir / "chroma"
    chroma_dir.mkdir(parents=True)
    (chroma_dir / "dummy.bin").write_text("dummy chroma data")

    sqlite_path = data_dir / "memanchor.db"
    conn = sqlite3.connect(sqlite_path)
    conn.execute("CREATE TABLE facts (id TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO facts VALUES ('1', 'hello')")
    conn.commit()
    conn.close()

    config_path = tmp_path / "memanchor.yaml"
    config_path.write_text(
        "sqlite_path: ./data/memanchor.db\n"
        "chroma_path: ./data/chroma\n"
        "embedding_model: sentence-transformers/all-MiniLM-L6-v2\n"
    )

    backup_root = tmp_path / "backups"
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "memanchor.yaml", str(backup_root)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    snapshot_dirs = list(backup_root.iterdir())
    assert len(snapshot_dirs) == 1
    snapshot_dir = snapshot_dirs[0]

    backup_db_path = snapshot_dir / "memanchor.db"
    assert backup_db_path.exists()
    backup_conn = sqlite3.connect(backup_db_path)
    rows = backup_conn.execute("SELECT * FROM facts").fetchall()
    backup_conn.close()
    assert rows == [("1", "hello")]

    assert (snapshot_dir / "chroma" / "dummy.bin").read_text() == "dummy chroma data"

    manifest = json.loads((snapshot_dir / "manifest.json").read_text())
    assert manifest["embedding_model"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert manifest["sqlite_source"] == "./data/memanchor.db"
    assert manifest["chroma_source"] == "./data/chroma"
    assert manifest["snapshot_timestamp"].endswith("Z")


def test_backup_snapshot_fails_cleanly_when_config_missing(tmp_path):
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "nonexistent.yaml", str(tmp_path / "backups")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "config file not found" in result.stderr


def test_backup_snapshot_uses_config_defined_paths_not_hardcoded(tmp_path):
    """Confirms the script actually reads sqlite_path/chroma_path from the
    config rather than assuming ./data/... -- uses non-default paths."""
    custom_data_dir = tmp_path / "custom_location"
    custom_chroma_dir = custom_data_dir / "vectors"
    custom_chroma_dir.mkdir(parents=True)
    (custom_chroma_dir / "marker.bin").write_text("marker")

    sqlite_path = custom_data_dir / "anchor.db"
    conn = sqlite3.connect(sqlite_path)
    conn.execute("CREATE TABLE facts (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    config_path = tmp_path / "memanchor.yaml"
    config_path.write_text(
        "sqlite_path: ./custom_location/anchor.db\n"
        "chroma_path: ./custom_location/vectors\n"
        "embedding_model: custom-model\n"
    )

    backup_root = tmp_path / "backups"
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "memanchor.yaml", str(backup_root)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    snapshot_dir = next(backup_root.iterdir())
    assert (snapshot_dir / "memanchor.db").exists()
    assert (snapshot_dir / "chroma" / "marker.bin").read_text() == "marker"
    manifest = json.loads((snapshot_dir / "manifest.json").read_text())
    assert manifest["embedding_model"] == "custom-model"

    shutil.rmtree(custom_data_dir, ignore_errors=True)
