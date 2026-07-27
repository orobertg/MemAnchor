#!/usr/bin/env bash
# backup_snapshot.sh: coordinated MemAnchor backup snapshot.
#   1. Checkpoints the SQLite WAL
#   2. Exports SQLite via `.backup`
#   3. Copies the ChromaDB persistent directory wholesale -- it's already
#      just files on disk (a sqlite file plus binary index data), so a
#      filesystem copy achieves the same outcome as re-exporting through
#      the Python client's get() method without needing a running Python
#      process here, and is what Chroma's own docs recommend for backing
#      up an embedded PersistentClient.
#   4. Writes a manifest with the snapshot timestamp and embedding model version
#
# Usage: backup_snapshot.sh [config_path] [backup_root]
#   config_path defaults to ./memanchor.yaml
#   backup_root defaults to ./backups

set -euo pipefail

CONFIG_PATH="${1:-memanchor.yaml}"
BACKUP_ROOT="${2:-backups}"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "error: config file not found: $CONFIG_PATH" >&2
  exit 1
fi

read_yaml_value() {
  # Simple top-level "key: value" extraction -- memanchor.yaml has no
  # nesting for the keys this script needs, so a grep/sed pair is enough
  # and avoids requiring a Python/PyYAML dependency in a script whose job
  # is just copying files.
  local key="$1"
  grep -E "^${key}:" "$CONFIG_PATH" | head -n1 | sed -E "s/^${key}:[[:space:]]*//" | sed -E 's/[[:space:]]*(#.*)?$//' | tr -d '"'"'"''
}

# Same defaults as memanchor/config.py's Config dataclass.
sqlite_path="$(read_yaml_value sqlite_path)"
chroma_path="$(read_yaml_value chroma_path)"
embedding_model="$(read_yaml_value embedding_model)"
sqlite_path="${sqlite_path:-./data/memanchor.db}"
chroma_path="${chroma_path:-./data/chroma}"
embedding_model="${embedding_model:-sentence-transformers/all-MiniLM-L6-v2}"

if [[ ! -f "$sqlite_path" ]]; then
  echo "error: sqlite database not found: $sqlite_path" >&2
  exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
snapshot_dir="${BACKUP_ROOT}/${timestamp}"
mkdir -p "$snapshot_dir"

echo "==> Checkpointing SQLite WAL"
sqlite3 "$sqlite_path" "PRAGMA wal_checkpoint(TRUNCATE);" > /dev/null

echo "==> Exporting SQLite via .backup"
sqlite3 "$sqlite_path" ".backup '${snapshot_dir}/memanchor.db'"

if [[ -d "$chroma_path" ]]; then
  echo "==> Copying ChromaDB persistent directory"
  cp -a "$chroma_path" "${snapshot_dir}/chroma"
else
  echo "warning: chroma path not found, skipping: $chroma_path" >&2
fi

manifest_path="${snapshot_dir}/manifest.json"
snapshot_iso_timestamp="$(date -u +%Y-%m-%dT%H:%M:%S.000000Z)"
cat > "$manifest_path" <<EOF
{
  "snapshot_timestamp": "${snapshot_iso_timestamp}",
  "embedding_model": "${embedding_model}",
  "sqlite_source": "${sqlite_path}",
  "chroma_source": "${chroma_path}"
}
EOF

echo "==> Snapshot complete: ${snapshot_dir}"
