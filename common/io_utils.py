from pathlib import Path
import pandas as pd
import json
import datetime as dt

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SILVER = ROOT / "data" / "silver"
GOLD = ROOT / "data" / "gold"
PBI = ROOT / "data" / "powerbi_outputs"
LOGS = ROOT / "logs"

for p in [RAW, SILVER, GOLD, PBI, LOGS]:
    p.mkdir(parents=True, exist_ok=True)

def normalize_colname(c: str) -> str:
    return (
        str(c).strip().lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace(".", "_")
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ñ", "n")
    )

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [normalize_colname(c) for c in df.columns]
    return df

def read_optional_table(name: str) -> pd.DataFrame:
    candidates = [
        RAW / f"{name}.parquet",
        RAW / f"{name}.csv",
        RAW / f"{name}.xlsx",
        RAW / f"{name}.xls",
    ]
    for path in candidates:
        if path.exists():
            if path.suffix == ".parquet":
                return normalize_columns(pd.read_parquet(path))
            if path.suffix == ".csv":
                return normalize_columns(pd.read_csv(path, encoding="utf-8-sig"))
            if path.suffix in [".xlsx", ".xls"]:
                return normalize_columns(pd.read_excel(path))
    return pd.DataFrame()

def write_table(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".parquet":
        df.to_parquet(path, index=False)
    elif path.suffix == ".csv":
        df.to_csv(path, index=False, encoding="utf-8-sig")
    elif path.suffix == ".xlsx":
        df.to_excel(path, index=False)
    else:
        raise ValueError(f"Extensión no soportada: {path.suffix}")

def safe_read_parquet(path: Path) -> pd.DataFrame:
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame()

def log_event(library: str, step: str, status: str, message: str, metrics: dict | None = None):
    LOGS.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "library": library,
        "step": step,
        "status": status,
        "message": message,
        "metrics": metrics or {},
    }
    log_path = LOGS / f"{dt.date.today().isoformat()}_{library}.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    print(f"[{library}] {step} | {status} | {message}")

def snapshot_diff(current: pd.DataFrame, key_cols: list[str], snapshot_name: str) -> dict:
    snapshot_dir = ROOT / "data" / "_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    prev_path = snapshot_dir / f"{snapshot_name}_previous.parquet"

    result = {
        "snapshot_name": snapshot_name,
        "rows_current": int(len(current)),
        "rows_previous": 0,
        "rows_new": int(len(current)),
        "rows_removed": 0,
        "key_cols": key_cols,
    }

    if current.empty or not all(k in current.columns for k in key_cols):
        return result

    cur_keys = current[key_cols].astype(str).drop_duplicates()

    if prev_path.exists():
        prev = pd.read_parquet(prev_path)
        if all(k in prev.columns for k in key_cols):
            prev_keys = prev[key_cols].astype(str).drop_duplicates()
            cur_set = set(map(tuple, cur_keys.to_numpy()))
            prev_set = set(map(tuple, prev_keys.to_numpy()))
            result["rows_previous"] = int(len(prev))
            result["rows_new"] = int(len(cur_set - prev_set))
            result["rows_removed"] = int(len(prev_set - cur_set))

    current.to_parquet(prev_path, index=False)
    return result
