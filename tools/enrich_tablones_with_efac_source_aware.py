# -*- coding: utf-8 -*-
"""
enrich_tablones_with_efac_source_aware.py

Enriquece TABLON_* por comprobante usando el maestro EFAC correcto segun source_file.

Regla importante:
- Cada VENTA*.xlsx cruza SOLO con su MAESTRO_COMPROBANTES_* correspondiente.
- tablon_MASTER se reconstruye DESPUES de enriquecer los tablones individuales.

Mapeo esperado:
- VENTA URSA 2026.xlsx          -> MAESTRO_COMPROBANTES_URSA.xlsx
- VENTA 10 DE ABRIL 2026.xlsx  -> MAESTRO_COMPROBANTES_10_DE_ABRIL.xlsx
- VENTA LYNX 2026.xlsx          -> MAESTRO_COMPROBANTES_LYNX.xlsx
- VENTA CYGNUS 2026.xlsx        -> MAESTRO_COMPROBANTES_CYGNUS.xlsx
"""

from __future__ import annotations

import argparse
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


COMPANY_RULES = [
    {
        "company": "URSA",
        "venta_file": "VENTA URSA 2026.xlsx",
        "maestro_file": "MAESTRO_COMPROBANTES_URSA.xlsx",
        "tablon_contains": ["URSA"],
    },
    {
        "company": "10_DE_ABRIL",
        "venta_file": "VENTA 10 DE ABRIL 2026.xlsx",
        "maestro_file": "MAESTRO_COMPROBANTES_10_DE_ABRIL.xlsx",
        "tablon_contains": ["10_DE_ABRIL", "10 DE ABRIL", "10_ABRIL"],
    },
    {
        "company": "LYNX",
        "venta_file": "VENTA LYNX 2026.xlsx",
        "maestro_file": "MAESTRO_COMPROBANTES_LYNX.xlsx",
        "tablon_contains": ["LYNX"],
    },
    {
        "company": "CYGNUS",
        "venta_file": "VENTA CYGNUS 2026.xlsx",
        "maestro_file": "MAESTRO_COMPROBANTES_CYGNUS.xlsx",
        "tablon_contains": ["CYGNUS"],
    },
]

EFAC_VALUE_COLS = ["tipos_unidad_efac", "u1_tipo_efac", "u1_n_efac"]
EFAC_META_COLS = [
    "source_company",
    "efac_expected_master",
    "efac_source_file",
    "efac_source_sheet",
    "efac_match_method",
    "efac_match_count",
]


def norm_text(x) -> str:
    if x is None or pd.isna(x):
        return ""
    s = str(x).strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_file_key(x) -> str:
    return norm_text(Path(str(x)).name)


def norm_col(c) -> str:
    s = norm_text(c)
    # conservar pistas de N° como N
    return s


def normalize_comprobante(x) -> str:
    """Normaliza comprobante para comparar formatos como 01-F001-00000013 vs F001-13."""
    if x is None or pd.isna(x):
        return ""
    s = str(x).strip().upper()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.replace("º", "").replace("°", "")
    s = re.sub(r"\s+", "", s)
    s = s.replace("_", "-").replace("/", "-")
    s = re.sub(r"[^A-Z0-9\-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")

    # Quitar prefijos administrativos tipo 01-, 03-, 001- si antes de B/Fxxx.
    m = re.search(r"([BF]\d{3})-?0*(\d+)$", s)
    if m:
        serie = m.group(1)
        numero = m.group(2).zfill(8)
        return f"{serie}-{numero}"

    # Si vino con prefijo: 01-F001-00000013
    m = re.search(r"(?:^|-)\d{1,3}-([BF]\d{3})-?0*(\d+)$", s)
    if m:
        serie = m.group(1)
        numero = m.group(2).zfill(8)
        return f"{serie}-{numero}"

    return s


def infer_company_from_value(value: str) -> Optional[str]:
    key = norm_file_key(value)
    for rule in COMPANY_RULES:
        if norm_file_key(rule["venta_file"]) == key:
            return rule["company"]
        if norm_file_key(rule["maestro_file"]) == key:
            return rule["company"]
        for token in rule["tablon_contains"]:
            if norm_text(token) in key:
                return rule["company"]
    return None


def infer_company_for_tablon(path: Path, df: pd.DataFrame) -> Optional[str]:
    # 1) por source_file si existe y es unico o mayoritario
    if "source_file" in df.columns:
        values = df["source_file"].dropna().astype(str)
        if len(values):
            counts = values.map(infer_company_from_value).dropna().value_counts()
            if len(counts):
                return str(counts.index[0])
    # 2) por nombre del parquet/csv
    return infer_company_from_value(path.name)


def find_header_row_for_excel(path: Path, sheet: str, max_rows: int = 30) -> int:
    preview = pd.read_excel(path, sheet_name=sheet, header=None, nrows=max_rows, engine="openpyxl")
    for i in range(len(preview)):
        row = [norm_col(v) for v in preview.iloc[i].tolist() if pd.notna(v)]
        joined = " | ".join(row)
        if "COMPROBANTE" in joined and ("U1" in joined or "TIPOS UNIDAD" in joined or "TIPO" in joined):
            return i
        if "COMPROBANTE" in joined:
            return i
    return 0


def standardize_efac_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    for c in df.columns:
        nc = norm_col(c)
        if "COMPROBANTE" in nc:
            rename[c] = "nro_comprobante_efac"
        elif nc in ["TIPOS UNIDAD", "TIPO UNIDAD", "TIPOS DE UNIDAD", "TIPO DE UNIDAD"]:
            rename[c] = "tipos_unidad_efac"
        elif nc in ["U1 TIPO", "U 1 TIPO", "U1 TIPO UNIDAD", "U1 TIPO DE UNIDAD"]:
            rename[c] = "u1_tipo_efac"
        elif nc in ["U1 N", "U1 NUM", "U1 NUMERO", "U 1 N", "U 1 NUMERO", "U1"]:
            rename[c] = "u1_n_efac"
    out = df.rename(columns=rename).copy()
    return out


def first_non_empty(series: pd.Series):
    for v in series:
        if pd.notna(v) and str(v).strip() != "":
            return v
    return pd.NA


def concat_unique(series: pd.Series) -> str:
    vals = []
    for v in series.dropna().astype(str):
        t = v.strip()
        if t and t not in vals:
            vals.append(t)
    return " | ".join(vals) if vals else pd.NA


def load_one_efac_master(path: Path, company: str, debug: bool = False) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"No existe maestro EFAC requerido para {company}: {path}")

    xls = pd.ExcelFile(path, engine="openpyxl")
    frames = []
    for sheet in xls.sheet_names:
        header_row = find_header_row_for_excel(path, sheet)
        df = pd.read_excel(path, sheet_name=sheet, header=header_row, engine="openpyxl")
        df = df.dropna(how="all")
        df = standardize_efac_columns(df)

        if "nro_comprobante_efac" not in df.columns:
            if debug:
                print(f"[WARN] {path.name} / {sheet}: no encontre columna comprobante. Columnas={list(df.columns)}")
            continue

        for c in EFAC_VALUE_COLS:
            if c not in df.columns:
                df[c] = pd.NA

        df["efac_source_file"] = path.name
        df["efac_source_sheet"] = sheet
        df["source_company"] = company
        df["efac_expected_master"] = path.name
        df["efac_comprobante_key"] = df["nro_comprobante_efac"].map(normalize_comprobante).astype("string")
        df = df[df["efac_comprobante_key"].astype("string").fillna("").ne("")].copy()

        keep = [
            "source_company",
            "efac_expected_master",
            "efac_source_file",
            "efac_source_sheet",
            "efac_comprobante_key",
        ] + EFAC_VALUE_COLS
        frames.append(df[keep])

    if not frames:
        raise ValueError(f"No pude leer comprobantes validos desde {path}")

    raw = pd.concat(frames, ignore_index=True)

    # Compactar duplicados por comprobante dentro de la misma empresa.
    raw["efac_match_count"] = raw.groupby("efac_comprobante_key")["efac_comprobante_key"].transform("size")
    agg = (
        raw.groupby("efac_comprobante_key", dropna=False)
        .agg(
            source_company=("source_company", first_non_empty),
            efac_expected_master=("efac_expected_master", first_non_empty),
            efac_source_file=("efac_source_file", first_non_empty),
            efac_source_sheet=("efac_source_sheet", concat_unique),
            efac_match_count=("efac_match_count", "max"),
            tipos_unidad_efac=("tipos_unidad_efac", first_non_empty),
            u1_tipo_efac=("u1_tipo_efac", first_non_empty),
            u1_n_efac=("u1_n_efac", first_non_empty),
        )
        .reset_index()
    )

    if debug:
        print(f"[EFAC] {company}: {path.name} -> filas raw={len(raw):,}, comprobantes unicos={len(agg):,}")
        print(agg.head(5).to_string(index=False))

    return agg


def load_efac_masters(efac_dir: Path, debug: bool = False) -> dict[str, pd.DataFrame]:
    masters = {}
    for rule in COMPANY_RULES:
        p = efac_dir / rule["maestro_file"]
        masters[rule["company"]] = load_one_efac_master(p, rule["company"], debug=debug)
    return masters


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Formato no soportado: {path}")


def write_table(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".parquet":
        df.to_parquet(path, index=False)
    elif path.suffix.lower() == ".csv":
        df.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        raise ValueError(f"Formato no soportado: {path}")


def backup_file(path: Path):
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = path.with_suffix(path.suffix + f".bak_{stamp}")
        shutil.copy2(path, backup)
        return backup
    return None


def reorder_efac_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols = list(df.columns)
    # Remover columnas EFAC si existen y reinsertarlas en orden inmediatamente despues de nro_comprobante.
    special = EFAC_VALUE_COLS + EFAC_META_COLS + ["efac_comprobante_key"]
    base = [c for c in cols if c not in special]
    insert_at = base.index("nro_comprobante") + 1 if "nro_comprobante" in base else len(base)
    ordered = base[:insert_at] + [c for c in EFAC_VALUE_COLS if c in df.columns] + [c for c in EFAC_META_COLS if c in df.columns] + [c for c in ["efac_comprobante_key"] if c in df.columns] + base[insert_at:]
    # Deduplicar manteniendo orden
    seen = set()
    ordered = [c for c in ordered if not (c in seen or seen.add(c))]
    return df[ordered]


def enrich_df_source_aware(df: pd.DataFrame, path: Path, masters: dict[str, pd.DataFrame], debug: bool = False) -> pd.DataFrame:
    out = df.copy()
    out["__row_order"] = np.arange(len(out))

    # Limpiar columnas previas del enrich para que el rerun sea idempotente.
    for c in EFAC_VALUE_COLS + EFAC_META_COLS + ["efac_comprobante_key", "__company_to_match"]:
        if c in out.columns:
            out = out.drop(columns=[c])

    if "nro_comprobante" not in out.columns:
        raise ValueError(f"{path.name}: no existe columna nro_comprobante")

    out["efac_comprobante_key"] = out["nro_comprobante"].map(normalize_comprobante).astype("string")

    if "source_file" in out.columns:
        out["__company_to_match"] = out["source_file"].map(infer_company_from_value).astype("string")
    else:
        company = infer_company_for_tablon(path, out)
        out["__company_to_match"] = company

    pieces = []
    audit_rows = []
    for company, part in out.groupby("__company_to_match", dropna=False, sort=False):
        company_key = None if pd.isna(company) else str(company)
        p = part.copy()
        if company_key in masters:
            m = masters[company_key]
            before = len(p)
            p = p.merge(
                m,
                on="efac_comprobante_key",
                how="left",
                suffixes=("", "_efacdup"),
            )
            p["efac_match_method"] = np.where(p["tipos_unidad_efac"].notna() | p["u1_tipo_efac"].notna() | p["u1_n_efac"].notna(), "SOURCE_FILE_PLUS_COMPROBANTE", pd.NA)
            p["source_company"] = p["source_company"].fillna(company_key)
            expected_master = next(r["maestro_file"] for r in COMPANY_RULES if r["company"] == company_key)
            p["efac_expected_master"] = p["efac_expected_master"].fillna(expected_master)
            matched = int(p["efac_match_method"].notna().sum())
            audit_rows.append({
                "tablon_file": path.name,
                "source_company": company_key,
                "rows": before,
                "matched_rows": matched,
                "unmatched_rows": before - matched,
                "match_rate": matched / before if before else 0,
                "expected_master": expected_master,
            })
        else:
            for c in EFAC_VALUE_COLS + EFAC_META_COLS:
                p[c] = pd.NA
            audit_rows.append({
                "tablon_file": path.name,
                "source_company": company_key or "SIN_COMPANY",
                "rows": len(p),
                "matched_rows": 0,
                "unmatched_rows": len(p),
                "match_rate": 0,
                "expected_master": pd.NA,
            })
        pieces.append(p)

    result = pd.concat(pieces, ignore_index=True) if pieces else out
    result = result.sort_values("__row_order").drop(columns=["__row_order", "__company_to_match"], errors="ignore")

    # Blindaje de tipos parquet
    for c in result.columns:
        if result[c].dtype == "object":
            result[c] = result[c].astype("string")

    result = reorder_efac_columns(result)
    result.attrs["audit_rows"] = audit_rows

    if debug:
        print(f"[TABLON] {path.name}: rows={len(result):,}")
        print(pd.DataFrame(audit_rows).to_string(index=False))
        preview_cols = [c for c in ["source_file", "nro_comprobante", "source_company", "tipos_unidad_efac", "u1_tipo_efac", "u1_n_efac", "efac_match_method"] if c in result.columns]
        print(result[preview_cols].head(8).to_string(index=False))

    return result


def list_individual_tablones(bronze_dir: Path, fmt: str) -> list[Path]:
    suffix = f".{fmt}"
    all_files = sorted(bronze_dir.glob(f"tablon_*{suffix}"))
    indiv = [p for p in all_files if p.name.lower() != f"tablon_master{suffix}".lower()]

    def order_key(p: Path):
        company = infer_company_from_value(p.name) or "ZZZ"
        order = {r["company"]: i for i, r in enumerate(COMPANY_RULES)}
        return (order.get(company, 999), p.name)

    return sorted(indiv, key=order_key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bronze_dir", default="data/bronze", help="Carpeta donde estan tablon_*.parquet/csv")
    ap.add_argument("--efac_dir", default="data/raw/pagos_de_efac", help="Carpeta con MAESTRO_COMPROBANTES_*.xlsx")
    ap.add_argument("--fmt", default="parquet", choices=["parquet", "csv"])
    ap.add_argument("--overwrite", action="store_true", help="Sobrescribe tablones enriquecidos, creando backup")
    ap.add_argument("--rebuild_master", action="store_true", help="Reconstruye tablon_MASTER desde tablones individuales ya enriquecidos")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    bronze_dir = Path(args.bronze_dir)
    efac_dir = Path(args.efac_dir)
    if not bronze_dir.exists():
        raise FileNotFoundError(f"No existe bronze_dir: {bronze_dir}")
    if not efac_dir.exists():
        raise FileNotFoundError(f"No existe efac_dir: {efac_dir}")

    print("==============================================")
    print("EFAC source-aware merge | TABLON_*")
    print("==============================================")
    print("Bronze dir:", bronze_dir.resolve())
    print("EFAC dir:", efac_dir.resolve())

    masters = load_efac_masters(efac_dir, debug=args.debug)
    tablones = list_individual_tablones(bronze_dir, args.fmt)
    if not tablones:
        raise SystemExit(f"No encontre tablones individuales en {bronze_dir} con fmt={args.fmt}")

    print("\nOrden de procesamiento:")
    for p in tablones:
        print(" -", p.name)

    enriched_frames = []
    audit_all = []
    for path in tablones:
        print(f"\nEnriqueciendo {path.name}...")
        df = read_table(path)
        enriched = enrich_df_source_aware(df, path, masters, debug=args.debug)
        audit_all.extend(enriched.attrs.get("audit_rows", []))

        out_path = path
        if args.overwrite:
            b = backup_file(out_path)
            if b:
                print("Backup:", b.name)
            write_table(enriched, out_path)
        else:
            out_path = path.with_name(path.stem + "_efac" + path.suffix)
            write_table(enriched, out_path)

        enriched_frames.append(enriched)
        print("OK ->", out_path)

    audit = pd.DataFrame(audit_all)
    audit_path = bronze_dir / "efac_merge_audit.csv"
    audit.to_csv(audit_path, index=False, encoding="utf-8-sig")
    print("\nAudit ->", audit_path)
    print(audit.to_string(index=False))

    if args.rebuild_master:
        master = pd.concat(enriched_frames, ignore_index=True) if enriched_frames else pd.DataFrame()
        master_path = bronze_dir / f"tablon_MASTER.{args.fmt}"
        if args.overwrite and master_path.exists():
            b = backup_file(master_path)
            if b:
                print("Master backup:", b.name)
        write_table(master, master_path)
        print("\nMASTER reconstruido despues del EFAC merge ->", master_path)
        print("Rows master:", len(master))

    print("==============================================")
    print("OK | EFAC merge source-aware finalizado")
    print("==============================================")


if __name__ == "__main__":
    main()
