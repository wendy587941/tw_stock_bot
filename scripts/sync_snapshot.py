"""Sync cloud dbt marts → local Parquet snapshot (Week 13, Stage 1).

This is the *only* moment the on-prem QA touches the cloud: read the already
dbt-materialised Gold marts out of S3 and land them as local Parquet under
``data/snapshot/``. After this runs, the whole QA stack is fully offline.

Design notes (sticking to the project's "ETL computes, query side only reads"
philosophy — the snapshot never recomputes anything):

* dbt-athena writes each mart as Athena CTAS output under a *per-run UUID*
  prefix (``athena-results/tables/<uuid>/``). That UUID changes on every dbt
  build, so we resolve the current location from the **Glue Catalog**
  (``get_table``) rather than hardcoding paths.
* Those CTAS files carry **no** ``.parquet`` extension, so DuckDB is told to
  read them as parquet explicitly, and we consolidate the splits into one
  ``<name>.parquet`` per mart for simple downstream queries.
* ``dividend.parquet`` is exported from the production DynamoDB hot table
  (``DIVIDEND#{code}/META`` items) — read-only scan, never a write.

Usage (from repo root)::

    python scripts/sync_snapshot.py

Requires local AWS credentials (the existing admin profile) + ``duckdb``,
``boto3``, ``pyarrow`` (see ``local_llm/requirements.txt``).
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import boto3
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

# Make ``local_llm`` importable no matter the current working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_llm import config  # noqa: E402

# snapshot 檔名 → Glue mart 表名
MARTS = {
    "ohlcv": "fct_daily_ohlcv",
    "market_breadth": "mart_market_breadth",
    "top_movers": "mart_top_movers",
    "signals": "fct_signals",
    "yield": "fct_yield",
}

# DIVIDEND META 要匯出的欄位（Decimal → float，None 保留）
_DIVIDEND_FIELDS = (
    "code", "name", "cash_dividend", "dividend_year",
    "period", "ex_date", "pay_date", "frequency",
)


def _clients():
    glue = boto3.client("glue", region_name=config.AWS_REGION)
    s3 = boto3.client("s3", region_name=config.AWS_REGION)
    ddb = boto3.resource("dynamodb", region_name=config.AWS_REGION)
    return glue, s3, ddb


def _table_location(glue, table: str) -> tuple[str, str]:
    """Current S3 (bucket, prefix) of a mart, from the Glue Catalog."""
    resp = glue.get_table(DatabaseName=config.GLUE_DATABASE, Name=table)
    loc = resp["Table"]["StorageDescriptor"]["Location"]
    u = urlparse(loc)
    return u.netloc, u.path.lstrip("/").rstrip("/") + "/"


def _download_mart(s3, bucket: str, prefix: str, dest_dir: Path) -> list[Path]:
    """Download every CTAS split object under prefix; return local file paths."""
    paths: list[Path] = []
    paginator = s3.get_paginator("list_objects_v2")
    for i, page in enumerate(paginator.paginate(Bucket=bucket, Prefix=prefix)):
        for j, obj in enumerate(page.get("Contents", [])):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            local = dest_dir / f"part_{i:03d}_{j:04d}"
            s3.download_file(bucket, key, str(local))
            paths.append(local)
    return paths


def _consolidate(parts: list[Path], out: Path) -> int:
    """Merge parquet splits (no extension) into one file; return row count."""
    con = duckdb.connect()
    try:
        files = [p.as_posix() for p in parts]
        out_posix = out.as_posix()  # internal/trusted path → embedded as COPY target literal
        con.execute(
            f"COPY (SELECT * FROM read_parquet(?)) TO '{out_posix}' (FORMAT PARQUET)",
            [files],
        )
        return con.execute(f"SELECT count(*) FROM read_parquet('{out_posix}')").fetchone()[0]
    finally:
        con.close()


def _sync_marts(glue, s3, snap_dir: Path) -> None:
    raw_root = snap_dir / "_raw"
    for name, table in MARTS.items():
        bucket, prefix = _table_location(glue, table)
        dest = raw_root / name
        dest.mkdir(parents=True, exist_ok=True)
        for old in dest.iterdir():          # clear any previous splits
            old.unlink()
        parts = _download_mart(s3, bucket, prefix, dest)
        if not parts:
            print(f"  ! {name:<15} no objects under s3://{bucket}/{prefix} — skipped")
            continue
        rows = _consolidate(parts, snap_dir / f"{name}.parquet")
        print(f"  + {name:<15} {len(parts)} file(s) -> {name}.parquet  ({rows} rows)")


def _dec(v):
    if isinstance(v, Decimal):
        return float(v)
    return v


def _sync_dividend(ddb, snap_dir: Path) -> None:
    """Export DIVIDEND#{code}/META items from the hot table to dividend.parquet."""
    table = ddb.Table(config.HOT_TABLE)
    records: list[dict] = []
    kwargs = {
        "FilterExpression": "begins_with(PK, :p)",
        "ExpressionAttributeValues": {":p": "DIVIDEND#"},
    }
    while True:
        resp = table.scan(**kwargs)
        for it in resp.get("Items", []):
            code = it.get("code") or it["PK"].split("#", 1)[1]
            records.append({f: (_dec(it.get(f)) if f != "code" else code) for f in _DIVIDEND_FIELDS})
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek

    # Explicit schema so an all-null column never collapses to the wrong type.
    schema = pa.schema([
        ("code", pa.string()), ("name", pa.string()),
        ("cash_dividend", pa.float64()), ("dividend_year", pa.string()),
        ("period", pa.string()), ("ex_date", pa.string()),
        ("pay_date", pa.string()), ("frequency", pa.string()),
    ])
    tbl = pa.Table.from_pylist(records, schema=schema)
    pq.write_table(tbl, (snap_dir / "dividend.parquet").as_posix())
    print(f"  + {'dividend':<15} DynamoDB scan -> dividend.parquet  ({len(records)} rows)")


def main() -> None:
    snap_dir = config.SNAPSHOT_DIR
    snap_dir.mkdir(parents=True, exist_ok=True)
    print(f"Syncing marts snapshot → {snap_dir}")
    glue, s3, ddb = _clients()
    _sync_marts(glue, s3, snap_dir)
    _sync_dividend(ddb, snap_dir)
    print("Done. QA stack can now run fully offline.")


if __name__ == "__main__":
    main()
