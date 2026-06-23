# -*- coding: utf-8 -*-
"""
enrich_tablones_with_efac.py

Hace LEFT JOIN de los TABLON_*.parquet/csv ya generados contra los maestros EFAC
ubicados en data/raw/pagos_de_efac, usando N° Comprobante / nro_comprobante / comprobante.

Trae, en este orden, las columnas principales:
- Tipos Unidad
- U1 Tipo
- U1 N

También agrega columnas de auditoría:
- efac_source_file
- efac_match_method
- efac_match_count

Uso recomendado:
  python tools/enrich_tablones_with_efac.py ^
    --tablon_dir data/bronze ^
    --efac_dir data/raw/pagos_de_efac ^
    --overwrite

Uso seguro sin sobrescribir:
  python tools/enrich_tablones_with_efac.py ^
    --tablon_dir data/bronze ^
    --efac_dir data/raw/pagos_de_efac ^
    --out_dir data/bronze_efac_enriched
"""

from __future__ import annotations

import argparse
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Iterable

import pandas as pd


TARGET_ORIGINAL = ["Tipos Unidad", "U1 Tipo", "U1 N"]
TARGET_SNAKE = ["tipos_unidad_efac", "u1_tipo_efac", "u1_n_efac"]

COMPROBANTE_CANDIDATES = [
    "N° Comprobante",
    "Nº Comprobante",
    "N Comprobante",
    "nro_comprobante",
    "numero_comprobante",
    "comprobante",
    "COMPROBANTE",
    "ID Comprobante",
    "id_comprobante",
]


GROUP_ALIASES = {
    "CYGNUS": ["CYGNUS"],
    "LYNX": ["LYNX"],
    "URSA": ["URSA"],
    "10_DE_ABRIL": ["10_DE_ABRIL", "10 DE ABRIL", "DIEZ_DE_ABRIL", "DIEZ DE ABRIL", "10ABRIL"],
}


def norm_ascii(x: object) -> str:
    if x is None or pd.isna(x):
        return ""
    s = str(x).strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.upper()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def norm_col(x: object) -> str:
    s = norm_ascii(x)
    s = s.replace("°", "").replace("º", "")
    s = re.sub(r"[^A-Z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s.lower()


def normalize_comprobante(x: object) -> str:
    """
    Normalización conservadora:
    - mantiene letras, números y guiones
    - limpia espacios y .0
    - convierte a mayúsculas sin tildes
    """
    if x is None or pd.isna(x):
        return ""
    s = norm_ascii(x)
    s = re.sub(r"\.0$", "", s)
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^A-Z0-9\-]", "", s)
    return s


def comprobante_compact_key(x: object) -> str:
    """
    Llave flexible:
    01-F001-00000013 => F001|13
    F001-00000013    => F001|13
    B001-297         => B001|297
    """
    s = normalize_comprobante(x)
    if not s:
        return ""

    # busca serie tipo F001/B001/E001 y correlativo
    m = re.search(r"([A-Z]\d{3})[-_ ]*0*([0-9]+)", s)
    if m:
        return f"{m.group(1)}|{int(m.group(2))}"

    # fallback: elimina todo lo no alfanumérico
    return re.sub(r"[^A-Z0-9]", "", s)


def find_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    normalized = {norm_col(c): c for c in df.columns}

    for cand in candidates:
        key = norm_col(cand)
        if key in normalized:
            return normalized[key]

    # fallback por contains
    for c in df.columns:
        nc = norm_col(c)
        if "comprobante" in nc:
            return c

    return None


def infer_group_from_name(name: str) -> str | None:
    n = norm_ascii(name).replace("-", "_")
    for group, aliases in GROUP_ALIASES.items():
        for alias in aliases:
            alias_norm = norm_ascii(alias).replace(" ", "_")
            if alias_norm in n:
                return group
    return None


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Formato de tablón no soportado: {path}")


def write_table(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".parquet":
        df.to_parquet(path, index=False)
    elif path.suffix.lower() == ".csv":
        df.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        raise ValueError(f"Formato de salida no soportado: {path}")


def read_efac_workbook(path: Path, output_style: str = "snake") -> pd.DataFrame:
    xls = pd.ExcelFile(path, engine="openpyxl")
    frames = []

    for sh in xls.sheet_names:
        df = pd.read_excel(path, sheet_name=sh, engine="openpyxl", dtype=object)
        if df.empty:
            continue

        # Algunos maestros traen una primera fila con nombres técnicos: id_comprobante, tipo_doc, etc.
        first_col = df.columns[0]
        if len(df) and norm_ascii(df.iloc[0].get(first_col, "")) in {"ID_COMPROBANTE", "ID COMPROBANTE"}:
            df = df.iloc[1:].reset_index(drop=True)

        comp_col = find_column(df, ["ID Comprobante", "N° Comprobante", "Comprobante"])
        if not comp_col:
            continue

        col_map_norm = {norm_col(c): c for c in df.columns}

        def get_col(original_name: str) -> str | None:
            return col_map_norm.get(norm_col(original_name))

        selected = pd.DataFrame(index=df.index)
        selected["efac_comprobante_raw"] = df[comp_col]
        selected["efac_comp_key_exact"] = selected["efac_comprobante_raw"].map(normalize_comprobante)
        selected["efac_comp_key_flex"] = selected["efac_comprobante_raw"].map(comprobante_compact_key)

        for original, snake in zip(TARGET_ORIGINAL, TARGET_SNAKE):
            src = get_col(original)
            out_name = original if output_style == "original" else snake
            selected[out_name] = df[src] if src else pd.NA

        # Extras útiles, no obligatorios
        for extra in ["Proyecto", "Estado Proyecto", "Descripcion XML", "Concepto"]:
            src = get_col(extra)
            if src:
                selected[f"efac_{norm_col(extra)}"] = df[src]

        selected["efac_source_file"] = path.name
        selected["efac_source_sheet"] = sh
        frames.append(selected)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out = out[out["efac_comp_key_exact"].astype("string").fillna("").ne("")].copy()
    return out


def build_efac_lookup(efac_dir: Path, pattern: str, output_style: str = "snake") -> dict[str, pd.DataFrame]:
    files = sorted([
        p for p in efac_dir.glob(pattern)
        if p.is_file() and not p.name.startswith("~$") and p.suffix.lower() in [".xlsx", ".xlsm"]
    ])

    if not files:
        raise FileNotFoundError(f"No encontré maestros EFAC en {efac_dir} con pattern={pattern}")

    by_group: dict[str, list[pd.DataFrame]] = {}

    for f in files:
        group = infer_group_from_name(f.name) or "UNKNOWN"
        df = read_efac_workbook(f, output_style=output_style)
        if df.empty:
            print(f"[WARN] Maestro EFAC sin filas útiles: {f.name}")
            continue
        df["efac_group"] = group
        by_group.setdefault(group, []).append(df)

    lookups = {}
    all_frames = []

    for group, frames in by_group.items():
        gdf = pd.concat(frames, ignore_index=True)
        lookups[group] = prepare_lookup(gdf)
        all_frames.append(gdf)

    if all_frames:
        lookups["ALL"] = prepare_lookup(pd.concat(all_frames, ignore_index=True))

    return lookups


def first_notna(s: pd.Series):
    valid = s.dropna()
    valid = valid[valid.astype("string").str.strip().ne("")]
    return valid.iloc[0] if len(valid) else pd.NA


def prepare_lookup(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    value_cols = [
        c for c in df.columns
        if c not in ["efac_comp_key_exact", "efac_comp_key_flex"]
    ]

    # Agregación por llave exacta
    agg = {c: first_notna for c in value_cols}
    exact = (
        df.groupby("efac_comp_key_exact", dropna=False)
          .agg(**{c: (c, agg[c]) for c in value_cols})
          .reset_index()
    )

    counts = df.groupby("efac_comp_key_exact").size().reset_index(name="efac_match_count_exact")
    exact = exact.merge(counts, on="efac_comp_key_exact", how="left")

    # Agregación por llave flexible
    flex = (
        df.groupby("efac_comp_key_flex", dropna=False)
          .agg(**{c: (c, agg[c]) for c in value_cols})
          .reset_index()
    )
    counts_flex = df.groupby("efac_comp_key_flex").size().reset_index(name="efac_match_count_flex")
    flex = flex.merge(counts_flex, on="efac_comp_key_flex", how="left")

    # devuelve ambas como atributos simples
    exact.attrs["flex_lookup"] = flex
    return exact


def reorder_new_columns(df: pd.DataFrame, comp_col: str, target_cols: list[str]) -> pd.DataFrame:
    cols = list(df.columns)
    insert_after = comp_col if comp_col in cols else None

    moved = [c for c in target_cols if c in cols]
    audit = [c for c in ["efac_match_method", "efac_match_count", "efac_source_file", "efac_source_sheet"] if c in cols]
    moved = moved + audit

    remaining = [c for c in cols if c not in moved]

    if insert_after and insert_after in remaining:
        idx = remaining.index(insert_after) + 1
        new_cols = remaining[:idx] + moved + remaining[idx:]
        return df[new_cols]

    return df[remaining + moved]


def enrich_tablon(df: pd.DataFrame, lookup_exact: pd.DataFrame, output_style: str = "snake") -> tuple[pd.DataFrame, dict]:
    out = df.copy()
    comp_col = find_column(out, COMPROBANTE_CANDIDATES)

    if not comp_col:
        raise KeyError(
            "No encontré columna de comprobante en tablón. "
            f"Busqué: {COMPROBANTE_CANDIDATES}. Columnas disponibles: {list(out.columns)}"
        )

    target_cols = TARGET_ORIGINAL if output_style == "original" else TARGET_SNAKE

    out["_comp_key_exact_tmp"] = out[comp_col].map(normalize_comprobante)
    out["_comp_key_flex_tmp"] = out[comp_col].map(comprobante_compact_key)

    # Limpia columnas EFAC previas para evitar duplicados en re-ejecuciones
    drop_prior = target_cols + [
        "efac_source_file", "efac_source_sheet", "efac_match_method", "efac_match_count",
        "efac_comprobante_raw", "efac_proyecto", "efac_estado_proyecto",
        "efac_descripcion_xml", "efac_concepto", "efac_group",
    ]
    out = out.drop(columns=[c for c in drop_prior if c in out.columns], errors="ignore")

    # Match exacto
    merged = out.merge(
        lookup_exact,
        left_on="_comp_key_exact_tmp",
        right_on="efac_comp_key_exact",
        how="left",
        suffixes=("", "_efacdup"),
    )
    merged["efac_match_method"] = pd.NA
    any_target = merged[target_cols].notna().any(axis=1) if all(c in merged.columns for c in target_cols) else pd.Series(False, index=merged.index)
    merged.loc[any_target, "efac_match_method"] = "EXACT_COMPROBANTE"
    merged["efac_match_count"] = merged.get("efac_match_count_exact", pd.Series(pd.NA, index=merged.index))

    # Match flexible para los que no matchearon exacto
    flex_lookup = lookup_exact.attrs.get("flex_lookup")
    if flex_lookup is not None and not flex_lookup.empty:
        need_flex = merged["efac_match_method"].isna()
        if need_flex.any():
            flex_cols = ["efac_comp_key_flex"] + [
                c for c in flex_lookup.columns
                if c != "efac_comp_key_flex"
            ]
            flex = flex_lookup[flex_cols].copy()
            flex = flex.add_suffix("_flex")
            merged = merged.merge(
                flex,
                left_on="_comp_key_flex_tmp",
                right_on="efac_comp_key_flex_flex",
                how="left",
            )

            for c in target_cols + [
                "efac_source_file", "efac_source_sheet", "efac_comprobante_raw",
                "efac_proyecto", "efac_estado_proyecto", "efac_descripcion_xml",
                "efac_concepto", "efac_group",
            ]:
                cf = f"{c}_flex"
                if c in merged.columns and cf in merged.columns:
                    merged[c] = merged[c].where(merged[c].notna(), merged[cf])
                elif cf in merged.columns:
                    merged[c] = merged[cf]

            flex_hit = need_flex.copy()
            flex_target = merged[target_cols].notna().any(axis=1) if all(c in merged.columns for c in target_cols) else pd.Series(False, index=merged.index)
            flex_hit = need_flex & flex_target
            merged.loc[flex_hit, "efac_match_method"] = "FLEX_SERIE_NUMERO"
            if "efac_match_count_flex_flex" in merged.columns:
                merged["efac_match_count"] = merged["efac_match_count"].where(
                    merged["efac_match_count"].notna(),
                    merged["efac_match_count_flex_flex"],
                )

            flex_drop = [c for c in merged.columns if c.endswith("_flex") or c == "efac_comp_key_flex_flex"]
            merged = merged.drop(columns=flex_drop, errors="ignore")

    matched = merged["efac_match_method"].notna().sum()
    summary = {
        "rows": int(len(merged)),
        "matched": int(matched),
        "unmatched": int(len(merged) - matched),
        "match_rate": float(matched / len(merged)) if len(merged) else 0.0,
        "comp_col": comp_col,
    }

    drop_tmp = [
        "_comp_key_exact_tmp", "_comp_key_flex_tmp", "efac_comp_key_exact",
        "efac_comp_key_flex", "efac_match_count_exact", "efac_match_count_flex"
    ]
    merged = merged.drop(columns=[c for c in drop_tmp if c in merged.columns], errors="ignore")
    merged = reorder_new_columns(merged, comp_col, target_cols)

    # Blindaje Parquet
    for c in merged.columns:
        if merged[c].dtype == "object":
            merged[c] = merged[c].astype("string")

    return merged, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tablon_dir", type=str, default="data/bronze")
    ap.add_argument("--pattern", type=str, default="tablon_*.*")
    ap.add_argument("--efac_dir", type=str, default="data/raw/pagos_de_efac")
    ap.add_argument("--efac_pattern", type=str, default="MAESTRO_COMPROBANTES*.xlsx")
    ap.add_argument("--out_dir", type=str, default="")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--backup", action="store_true", default=True)
    ap.add_argument("--output_style", choices=["snake", "original"], default="snake")
    ap.add_argument("--force_all_efac", action="store_true", help="Usa todos los maestros EFAC para todos los tablones.")
    args = ap.parse_args()

    tablon_dir = Path(args.tablon_dir)
    efac_dir = Path(args.efac_dir)

    if not tablon_dir.exists():
        raise FileNotFoundError(f"No existe tablon_dir: {tablon_dir}")
    if not efac_dir.exists():
        raise FileNotFoundError(f"No existe efac_dir: {efac_dir}")

    out_dir = Path(args.out_dir) if args.out_dir else tablon_dir
    if args.out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    print("==============================================")
    print("EFAC Merge | TABLON_* + MAESTRO_COMPROBANTES")
    print("==============================================")
    print("Tablones:", tablon_dir.resolve())
    print("EFAC:", efac_dir.resolve())

    lookups = build_efac_lookup(efac_dir, args.efac_pattern, output_style=args.output_style)

    tablon_files = sorted([
        p for p in tablon_dir.glob(args.pattern)
        if p.is_file()
        and p.suffix.lower() in [".parquet", ".csv"]
        and p.name.lower().startswith("tablon_")
    ])

    if not tablon_files:
        raise FileNotFoundError(f"No encontré tablones en {tablon_dir} con pattern={args.pattern}")

    rows = []

    for path in tablon_files:
        group = infer_group_from_name(path.name)
        lookup_key = "ALL" if args.force_all_efac or path.stem.upper() in ["TABLON_MASTER", "TABLON_MASTER_EFAC_ENRICHED"] else (group or "ALL")
        lookup = lookups.get(lookup_key)
        if lookup is None:
            lookup = lookups.get("ALL")

        if lookup is None or lookup.empty:
            print(f"[WARN] Sin lookup EFAC para {path.name}. Saltando.")
            continue

        df = read_table(path)
        enriched, summary = enrich_tablon(df, lookup, output_style=args.output_style)

        out_path = path if args.overwrite else (out_dir / path.name)

        if args.overwrite and args.backup:
            bak = path.with_suffix(path.suffix + ".bak")
            if not bak.exists():
                shutil.copy2(path, bak)

        write_table(enriched, out_path)

        summary.update({
            "tablon_file": path.name,
            "efac_lookup": lookup_key,
            "out_path": str(out_path),
        })
        rows.append(summary)

        print(
            f"OK {path.name} | lookup={lookup_key} | rows={summary['rows']} | "
            f"matched={summary['matched']} | rate={summary['match_rate']:.2%}"
        )

    rep = pd.DataFrame(rows)
    rep_path = out_dir / "efac_merge_report.csv"
    rep.to_csv(rep_path, index=False, encoding="utf-8-sig")

    print("==============================================")
    print("OK | EFAC merge terminado")
    print("Reporte:", rep_path.resolve())
    print("==============================================")


if __name__ == "__main__":
    main()
