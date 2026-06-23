from __future__ import annotations

import os
import json
import datetime as dt
from typing import Iterable

import pandas as pd
from dotenv import load_dotenv
import redshift_connector

from common.io_utils import RAW, LOGS, log_event, write_table

LIB = "redshift_daily_extract"


def _today_str() -> str:
    return dt.date.today().isoformat()


def load_redshift_config() -> dict:
    load_dotenv()

    cfg = {
        "host": os.getenv("REDSHIFT_HOST"),
        "port": int(os.getenv("REDSHIFT_PORT", "5439")),
        "database": os.getenv("REDSHIFT_DATABASE"),
        "schema": os.getenv("REDSHIFT_SCHEMA", "public"),
        "user": os.getenv("REDSHIFT_USER"),
        "password": os.getenv("REDSHIFT_PASSWORD"),
        "tables": [
            t.strip()
            for t in os.getenv("REDSHIFT_TABLES", "proformas,procesos,datos_extras,unidades").split(",")
            if t.strip()
        ],
        "raw_output_format": os.getenv("RAW_OUTPUT_FORMAT", "parquet").lower(),
        "force_extract": os.getenv("FORCE_EXTRACT", "false").lower() == "true",
    }

    missing = [k for k in ["host", "database", "user", "password"] if not cfg.get(k)]
    if missing:
        raise ValueError(
            "Faltan variables en .env: "
            + ", ".join(missing)
            + ". Revisa .env.example."
        )

    return cfg


def build_connection(cfg: dict):
    return redshift_connector.connect(
        host=cfg["host"],
        port=cfg["port"],
        database=cfg["database"],
        user=cfg["user"],
        password=cfg["password"],
        timeout=120,
    )


def marker_path(table_name: str):
    return RAW / f"_{table_name}_extract_marker.json"


def data_path(table_name: str, fmt: str):
    return RAW / f"{table_name}.{fmt}"


def has_fresh_raw(table_name: str, fmt: str) -> bool:
    marker = marker_path(table_name)
    data = data_path(table_name, fmt)

    if not marker.exists() or not data.exists():
        return False

    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        return payload.get("extract_date") == _today_str()
    except Exception:
        return False


def write_marker(table_name: str, rows: int, fmt: str):
    payload = {
        "table": table_name,
        "extract_date": _today_str(),
        "extracted_at": dt.datetime.now().isoformat(timespec="seconds"),
        "rows": int(rows),
        "format": fmt,
    }
    marker_path(table_name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _safe_identifier(name: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
    if not name or any(ch not in allowed for ch in name):
        raise ValueError(f"Identificador inválido para Redshift: {name}")
    return name


def extract_table(conn, schema: str, table_name: str) -> pd.DataFrame:
    schema = _safe_identifier(schema)
    table_name = _safe_identifier(table_name)

    query = f'SELECT * FROM "{schema}"."{table_name}"'

    with conn.cursor() as cursor:
        cursor.execute(query)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

    return pd.DataFrame(rows, columns=columns)


def test_connection() -> dict:
    cfg = load_redshift_config()
    conn = build_connection(cfg)

    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT current_database, current_user")
            row = cursor.fetchone()

        return {
            "status": "ok",
            "database": row[0],
            "user": row[1],
            "schema": cfg["schema"],
            "tables": cfg["tables"],
            "connector": "redshift_connector",
        }
    finally:
        conn.close()


def extract_redshift_daily(tables: Iterable[str] | None = None) -> dict:
    cfg = load_redshift_config()
    fmt = cfg["raw_output_format"]
    selected_tables = list(tables or cfg["tables"])

    if fmt not in {"parquet", "csv"}:
        raise ValueError("RAW_OUTPUT_FORMAT debe ser parquet o csv.")

    RAW.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    log_event(
        LIB,
        "start",
        "ok",
        "Inicio de extracción diaria desde Redshift.",
        {
            "tables": selected_tables,
            "force_extract": cfg["force_extract"],
            "format": fmt,
            "connector": "redshift_connector",
        },
    )

    results = {}
    conn = None

    try:
        for table in selected_tables:
            if not cfg["force_extract"] and has_fresh_raw(table, fmt):
                msg = f"{table}: ya existe extracción fresca del día en data/raw. No se consulta Redshift."
                log_event(LIB, f"skip_{table}", "ok", msg)
                results[table] = {"status": "skipped_cache_today"}
                continue

            if conn is None:
                conn = build_connection(cfg)

            log_event(LIB, f"extract_{table}", "running", f"Extrayendo {table} desde Redshift.")
            df = extract_table(conn, cfg["schema"], table)

            path = data_path(table, fmt)
            write_table(df, path)
            write_marker(table, len(df), fmt)

            log_event(
                LIB,
                f"extract_{table}",
                "ok",
                f"{table}: extracción completada y guardada en {path}.",
                {
                    "rows": int(len(df)),
                    "columns": list(df.columns),
                },
            )

            results[table] = {
                "status": "extracted",
                "rows": int(len(df)),
                "path": str(path),
            }

    finally:
        if conn is not None:
            conn.close()

    log_event(LIB, "finish", "ok", "Extracción diaria Redshift completada.", results)
    return results