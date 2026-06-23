"""
1_build_fuente_ventas_cobranzas/main_pipeline.py

Objetivo:
Reemplazar la lógica SQL de fuente de ventas/cobranzas usando Parquets locales
en data/raw.

Inputs esperados en --raw_dir:
- procesos.parquet
- clientes.parquet
- proyectos.parquet
- unidades.parquet

Output:
- fuente_ventas_cobranzas.parquet
- fuente_ventas_cobranzas.csv opcional si --fmt csv

Grano final:
1 fila por proyecto + codigo_proyecto + documento_cliente.

Run:
python .\\1_build_fuente_ventas_cobranzas\\main_pipeline.py --raw_dir data/raw --out_dir data_clean --fmt parquet

Ejemplo filtrando Torre Nápoles:
python .\\1_build_fuente_ventas_cobranzas\\main_pipeline.py --raw_dir data/raw --out_dir data_clean --fmt parquet --project_filter "Torre Nápoles"

Ejemplo sin filtro de proyecto:
python .\\1_build_fuente_ventas_cobranzas\\main_pipeline.py --raw_dir data/raw --out_dir data_clean --fmt parquet --project_filter ""
"""

from __future__ import annotations

import argparse
from pathlib import Path
import unicodedata
import numpy as np
import pandas as pd


# =========================================================
# Helpers
# =========================================================

def clean_colnames(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = (
        out.columns.astype(str)
        .str.strip()
        .str.lower()
        .str.replace(" ", "_", regex=False)
        .str.replace("-", "_", regex=False)
    )
    return out


def ensure_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c not in out.columns:
            out[c] = pd.NA
    return out


def read_parquet_required(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"No encuentro archivo requerido: {path}")
    return clean_colnames(pd.read_parquet(path))


def norm_text_value(x):
    if x is None:
        return pd.NA
    try:
        if pd.isna(x):
            return pd.NA
    except Exception:
        pass

    txt = str(x).strip()
    if txt == "":
        return pd.NA

    txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")
    txt = txt.upper()
    txt = " ".join(txt.split())
    return txt if txt else pd.NA


def norm_doc_value(x):
    if x is None:
        return pd.NA
    try:
        if pd.isna(x):
            return pd.NA
    except Exception:
        pass

    txt = str(x).strip().replace(".0", "")
    digits = "".join(ch for ch in txt if ch.isdigit())
    return digits if digits else pd.NA


def to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


def to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def first_non_null(series: pd.Series):
    vals = series.dropna()
    if vals.empty:
        return pd.NA
    return vals.iloc[0]


def join_unique(values, sep: str = " | "):
    vals = []
    for v in values:
        if pd.isna(v):
            continue
        txt = str(v).strip()
        if txt and txt not in vals:
            vals.append(txt)
    return sep.join(sorted(vals)) if vals else pd.NA


""" def normalize_project_filter(x: str | None) -> str | None:
    if x is None:
        return None
    x = str(x).strip()
    if x == "":
        return None
    return str(norm_text_value(x)) """

def normalize_project_list(projects: list[str] | None) -> set[str] | None:
    if not projects:
        return None

    clean = set()

    for p in projects:
        if p is None:
            continue

        txt = str(p).strip()

        if txt == "":
            continue

        norm = norm_text_value(txt)

        if pd.notna(norm):
            clean.add(str(norm))

    return clean if clean else None

def prepare_for_export(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == "object":
            out[c] = out[c].astype("string")
    return out


def write_table(df: pd.DataFrame, out_dir: Path, name: str, fmt: str):
    out_dir.mkdir(parents=True, exist_ok=True)
    out = prepare_for_export(df)
    path = out_dir / f"{name}.{fmt}"

    if fmt == "csv":
        out.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        out.to_parquet(path, index=False)

    print(f"OK -> {path}")


# =========================================================
# Transformaciones principales
# =========================================================

def build_sep(procesos: pd.DataFrame) -> pd.DataFrame:
    p = procesos.copy()

    p = ensure_columns(
        p,
        [
            "fecha_inicio",
            "codigo_proforma",
            "documento_cliente",
            "codigo_unidad",
            "codigo_proyecto",
            "usuario_separacion",
            "usuario",
            "asesor",
            "estado",
            "tipo_financiamiento",
            "codigo_unidades_asignadas",
            "nombre",
        ],
    )

    p["nombre_norm"] = p["nombre"].apply(norm_text_value).astype("string")
    p["estado_norm"] = p["estado"].apply(norm_text_value).astype("string")

    sep = p[
        p["nombre_norm"].isin(["SEPARACION"])
        & p["estado_norm"].eq("ACTIVO")
        & p["codigo_proforma"].notna()
    ].copy()

    sep["fecha_separacion"] = to_date(sep["fecha_inicio"])

    # Si usuario_separacion no existe o viene vacío, intenta usuario/asesor.
    sep["usuario_separacion_final"] = sep["usuario_separacion"]
    sep["usuario_separacion_final"] = sep["usuario_separacion_final"].fillna(sep["usuario"])
    sep["usuario_separacion_final"] = sep["usuario_separacion_final"].fillna(sep["asesor"])

    out = pd.DataFrame(
        {
            "fecha_separacion": sep["fecha_separacion"],
            "codigo_proforma": sep["codigo_proforma"].astype("string"),
            "documento_cliente": sep["documento_cliente"].astype("string"),
            "codigo_principal": sep["codigo_unidad"].astype("string"),
            "codigo_proyecto": sep["codigo_proyecto"].astype("string"),
            "usuario_separacion": sep["usuario_separacion_final"].astype("string"),
            "estado": sep["estado"].astype("string"),
            "tipo_financiamiento": sep["tipo_financiamiento"].astype("string"),
            "adicionales_sep_raw": sep["codigo_unidades_asignadas"].astype("string"),
        }
    )

    return out


def build_ven(procesos: pd.DataFrame) -> pd.DataFrame:
    p = procesos.copy()

    p = ensure_columns(
        p,
        [
            "fecha_fin",
            "codigo_proforma",
            "codigo_unidades_asignadas",
            "nombre",
        ],
    )

    p["nombre_norm"] = p["nombre"].apply(norm_text_value).astype("string")

    ven = p[
        p["nombre_norm"].eq("VENTA")
        & p["codigo_proforma"].notna()
    ].copy()

    ven["fecha_fin"] = to_date(ven["fecha_fin"])

    if ven.empty:
        return pd.DataFrame(
            columns=[
                "codigo_proforma",
                "fecha_minuta",
                "adicionales_ven_raw",
            ]
        )

    out = (
        ven
        .groupby("codigo_proforma", dropna=False)
        .agg(
            fecha_minuta=("fecha_fin", "max"),
            adicionales_ven_raw=("codigo_unidades_asignadas", first_non_null),
        )
        .reset_index()
    )

    out["codigo_proforma"] = out["codigo_proforma"].astype("string")
    out["adicionales_ven_raw"] = out["adicionales_ven_raw"].astype("string")

    return out


def build_cli(clientes: pd.DataFrame) -> pd.DataFrame:
    c = clientes.copy()

    c = ensure_columns(
        c,
        [
            "documento",
            "nombres",
            "apellidos",
            "cliente",
            "medio_captacion",
            "celulares",
            "email",
        ],
    )

    if "cliente" not in c.columns or c["cliente"].isna().all():
        c["cliente"] = (
            c["nombres"].astype("string").fillna("")
            + " "
            + c["apellidos"].astype("string").fillna("")
        ).str.strip()
    else:
        c["cliente"] = c["cliente"].fillna(
            (
                c["nombres"].astype("string").fillna("")
                + " "
                + c["apellidos"].astype("string").fillna("")
            ).str.strip()
        )

    out = pd.DataFrame(
        {
            "documento_cliente": c["documento"].astype("string"),
            "documento_cliente_norm": c["documento"].apply(norm_doc_value).astype("string"),
            "cliente": c["cliente"].astype("string"),
            "medio_captacion": c["medio_captacion"].astype("string"),
            "celulares": c["celulares"].astype("string"),
            "email": c["email"].astype("string"),
        }
    )

    out = out.drop_duplicates("documento_cliente", keep="first")

    return out


def build_proy(proyectos: pd.DataFrame) -> pd.DataFrame:
    p = proyectos.copy()
    p = ensure_columns(p, ["codigo", "nombre"])

    out = pd.DataFrame(
        {
            "codigo_proyecto": p["codigo"].astype("string"),
            "proyecto": p["nombre"].astype("string"),
            "proyecto_norm": p["nombre"].apply(norm_text_value).astype("string"),
        }
    )

    return out.drop_duplicates("codigo_proyecto", keep="first")


def build_uni(unidades: pd.DataFrame) -> pd.DataFrame:
    u = unidades.copy()
    u = ensure_columns(u, ["codigo", "nombre", "tipo_unidad", "precio_venta"])

    out = pd.DataFrame(
        {
            "codigo_item": u["codigo"].astype("string"),
            "item_nombre": u["nombre"].astype("string"),
            "tipo_unidad": u["tipo_unidad"].astype("string"),
            "precio_venta": to_num(u["precio_venta"]),
        }
    )

    out["tipo_unidad_norm"] = out["tipo_unidad"].apply(norm_text_value).astype("string")

    return out.drop_duplicates("codigo_item", keep="first")


def split_codes(value) -> list[str]:
    if value is None:
        return []

    try:
        if pd.isna(value):
            return []
    except Exception:
        pass

    txt = str(value).strip()

    if txt == "" or txt.upper() in ["<NA>", "NAN", "NONE", "NULL"]:
        return []

    parts = []
    for part in txt.split(","):
        code = part.strip()
        if code and code.upper() not in ["<NA>", "NAN", "NONE", "NULL"]:
            parts.append(code)

    return parts


def build_codes(base: pd.DataFrame) -> pd.DataFrame:
    rows = []

    cols = [
        "codigo_proforma",
        "documento_cliente",
        "codigo_proyecto",
        "usuario_separacion",
        "fecha_separacion",
        "fecha_minuta",
        "tipo_financiamiento",
    ]

    for _, r in base.iterrows():
        common = {c: r.get(c, pd.NA) for c in cols}

        # Principal
        principal = r.get("codigo_principal", pd.NA)
        if pd.notna(principal) and str(principal).strip() != "":
            rows.append({**common, "codigo_item": str(principal).strip(), "origen_item": "PRINCIPAL"})

        # Adicionales separación
        for code in split_codes(r.get("adicionales_sep_raw", pd.NA)):
            rows.append({**common, "codigo_item": code, "origen_item": "ADICIONAL_SEPARACION"})

        # Adicionales venta
        for code in split_codes(r.get("adicionales_ven_raw", pd.NA)):
            rows.append({**common, "codigo_item": code, "origen_item": "ADICIONAL_VENTA"})

    out = pd.DataFrame(rows)

    if out.empty:
        return pd.DataFrame(
            columns=cols + ["codigo_item", "origen_item"]
        )

    out["codigo_item"] = out["codigo_item"].astype("string")

    # Dedup por proforma + item, equivalente a ROW_NUMBER PARTITION BY codigo_proforma, codigo_item.
    out = (
        out
        .sort_values(["codigo_proforma", "codigo_item", "origen_item"])
        .drop_duplicates(["codigo_proforma", "codigo_item"], keep="first")
        .reset_index(drop=True)
    )

    return out


def categorize_items(codes: pd.DataFrame, unidades: pd.DataFrame) -> pd.DataFrame:
    df = codes.merge(
        unidades,
        how="inner",
        on="codigo_item",
    )

    tipo = df["tipo_unidad_norm"].astype("string").fillna("")

    df["categoria"] = np.select(
        [
            tipo.isin(["DEPARTAMENTO FLAT", "DEPARTAMENTO DUPLEX", "DEPARTAMENTO TRIPLEX"]),
            tipo.str.startswith("ESTACIONAMIENTO"),
            tipo.isin(["DEPOSITO"]),
        ],
        [
            "DEPA",
            "ESTAC",
            "DEPO",
        ],
        default="OTRO",
    )

    return df


""" def apply_project_filter(df: pd.DataFrame, project_filter_norm: str | None) -> pd.DataFrame:
    if project_filter_norm is None:
        return df.copy()

    return df[df["proyecto_norm"].eq(project_filter_norm)].copy()
 """
def apply_project_filter(df: pd.DataFrame, project_filter_norms: set[str] | None) -> pd.DataFrame:
    if project_filter_norms is None:
        return df.copy()

    return df[df["proyecto_norm"].isin(project_filter_norms)].copy()

def build_final_table(
    items_cat: pd.DataFrame,
    clientes: pd.DataFrame,
    proyectos: pd.DataFrame,
    project_filter: str | None,
) -> pd.DataFrame:
    df = items_cat.merge(
        proyectos,
        how="left",
        on="codigo_proyecto",
    )

    """ project_filter_norm = normalize_project_filter(project_filter)
    df = apply_project_filter(df, project_filter_norm) """

    project_filter_norms = normalize_project_list(project_filter)
    df = apply_project_filter(df, project_filter_norms)

    df = df[df["categoria"].isin(["DEPA", "ESTAC", "DEPO"])].copy()

    if df.empty:
        return pd.DataFrame(
            columns=[
                "proyecto",
                "codigo_proyecto",
                "documento_cliente",
                "cliente",
                "medio_captacion",
                "celulares",
                "email",
                "asesor",
                "codigo_proformas",
                "tipos_financiamiento",
                "departamentos",
                "estacionamientos",
                "depositos",
                "total_departamentos",
                "total_estacionamientos",
                "total_depositos",
                "total_venta_items",
                "primera_separacion",
                "ultima_minuta",
            ]
        )

    df = df.merge(
        clientes,
        how="left",
        on="documento_cliente",
    )

    group_keys = [
        "proyecto",
        "codigo_proyecto",
        "documento_cliente",
    ]

    # Base cliente/proyecto
    base = (
        df
        .groupby(group_keys, dropna=False)
        .agg(
            cliente=("cliente", first_non_null),
            medio_captacion=("medio_captacion", first_non_null),
            celulares=("celulares", first_non_null),
            email=("email", first_non_null),
            asesor=("usuario_separacion", "max"),
            primera_separacion=("fecha_separacion", "min"),
            ultima_minuta=("fecha_minuta", "max"),
        )
        .reset_index()
    )

    # Listas
    lst_proformas = (
        df[["proyecto", "codigo_proyecto", "documento_cliente", "codigo_proforma"]]
        .drop_duplicates()
        .groupby(group_keys, dropna=False)
        .agg(codigo_proformas=("codigo_proforma", join_unique))
        .reset_index()
    )

    lst_fin = (
        df[["proyecto", "codigo_proyecto", "documento_cliente", "tipo_financiamiento"]]
        .dropna(subset=["tipo_financiamiento"])
        .drop_duplicates()
        .groupby(group_keys, dropna=False)
        .agg(tipos_financiamiento=("tipo_financiamiento", join_unique))
        .reset_index()
    )

    lst_depa = (
        df[df["categoria"].eq("DEPA")]
        [["proyecto", "codigo_proyecto", "documento_cliente", "item_nombre"]]
        .drop_duplicates()
        .groupby(group_keys, dropna=False)
        .agg(departamentos=("item_nombre", join_unique))
        .reset_index()
    )

    lst_estac = (
        df[df["categoria"].eq("ESTAC")]
        [["proyecto", "codigo_proyecto", "documento_cliente", "item_nombre"]]
        .drop_duplicates()
        .groupby(group_keys, dropna=False)
        .agg(estacionamientos=("item_nombre", join_unique))
        .reset_index()
    )

    lst_depo = (
        df[df["categoria"].eq("DEPO")]
        [["proyecto", "codigo_proyecto", "documento_cliente", "item_nombre"]]
        .drop_duplicates()
        .groupby(group_keys, dropna=False)
        .agg(depositos=("item_nombre", join_unique))
        .reset_index()
    )

    # Totales
    tmp = df.copy()
    tmp["precio_venta"] = to_num(tmp["precio_venta"]).fillna(0)

    tmp["monto_depa"] = np.where(tmp["categoria"].eq("DEPA"), tmp["precio_venta"], 0)
    tmp["monto_estac"] = np.where(tmp["categoria"].eq("ESTAC"), tmp["precio_venta"], 0)
    tmp["monto_depo"] = np.where(tmp["categoria"].eq("DEPO"), tmp["precio_venta"], 0)
    tmp["monto_total_item"] = np.where(tmp["categoria"].isin(["DEPA", "ESTAC", "DEPO"]), tmp["precio_venta"], 0)

    totales = (
        tmp
        .groupby(group_keys, dropna=False)
        .agg(
            total_departamentos=("monto_depa", "sum"),
            total_estacionamientos=("monto_estac", "sum"),
            total_depositos=("monto_depo", "sum"),
            total_venta_items=("monto_total_item", "sum"),
        )
        .reset_index()
    )

    out = base.copy()

    for right in [lst_proformas, lst_fin, lst_depa, lst_estac, lst_depo, totales]:
        out = out.merge(
            right,
            how="left",
            on=group_keys,
        )

    out["documento_cliente_norm"] = out["documento_cliente"].apply(norm_doc_value).astype("string")

    final_cols = [
        "proyecto",
        "codigo_proyecto",
        "documento_cliente",
        "documento_cliente_norm",
        "cliente",
        "medio_captacion",
        "celulares",
        "email",
        "asesor",
        "codigo_proformas",
        "tipos_financiamiento",
        "departamentos",
        "estacionamientos",
        "depositos",
        "total_departamentos",
        "total_estacionamientos",
        "total_depositos",
        "total_venta_items",
        "primera_separacion",
        "ultima_minuta",
    ]

    out = ensure_columns(out, final_cols)
    out = out[final_cols].copy()

    out = out.sort_values(
        ["proyecto", "total_venta_items"],
        ascending=[True, False],
    ).reset_index(drop=True)

    return out


# =========================================================
# Main
# =========================================================

def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("--raw_dir", type=str, default="data/raw")
    ap.add_argument("--out_dir", type=str, default="data/silver")
    ap.add_argument("--fmt", type=str, default="parquet", choices=["parquet", "csv"])
    ap.add_argument("--project_filter", type=str)
    ##ap.add_argument("--project_filter", type=str, default="Torre Nápoles")
    ap.add_argument("--output_name", type=str, default="fuente_ventas_cobranzas")

    args = ap.parse_args()

    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)

    print("==============================================")
    print("1 | Build Fuente Ventas Cobranzas desde Parquet")
    print("==============================================")

    print("Leyendo raw parquets...")
    procesos = read_parquet_required(raw_dir / "procesos.parquet")
    clientes = read_parquet_required(raw_dir / "clientes.parquet")
    proyectos = read_parquet_required(raw_dir / "proyectos.parquet")
    unidades = read_parquet_required(raw_dir / "unidades.parquet")

    print("Construyendo separación activa...")
    sep = build_sep(procesos)
    print("Separaciones activas:", len(sep))

    print("Construyendo ventas/minutas...")
    ven = build_ven(procesos)
    print("Ventas/minutas:", len(ven))

    print("Preparando dimensiones...")
    cli = build_cli(clientes)
    proy = build_proy(proyectos)
    uni = build_uni(unidades)

    print("Uniendo sep + ven...")
    base = sep.merge(
        ven,
        how="left",
        on="codigo_proforma",
    )

    print("Explotando unidades principales y adicionales...")
    codes = build_codes(base)
    print("Items explotados:", len(codes))

    print("Categorizando items...")
    items_cat = categorize_items(codes, uni)
    print("Items categorizados:", len(items_cat))

    print("Categorías encontradas:")
    print(items_cat["categoria"].value_counts(dropna=False).to_string())

    print("Construyendo tabla final...")
    final = build_final_table(
        items_cat=items_cat,
        clientes=cli,
        proyectos=proy,
        project_filter=args.project_filter,
    )

    print("Exportando...")
    write_table(final, out_dir, args.output_name, args.fmt)

    # CSV de auditoría opcional para revisar rápido aunque el formato principal sea parquet.
    audit_name = f"{args.output_name}_items_auditoria"
    audit_cols = [
        "codigo_proforma",
        "documento_cliente",
        "codigo_proyecto",
        "usuario_separacion",
        "fecha_separacion",
        "fecha_minuta",
        "tipo_financiamiento",
        "codigo_item",
        "item_nombre",
        "tipo_unidad",
        "precio_venta",
        "categoria",
        "origen_item",
    ]
    items_audit = ensure_columns(items_cat, audit_cols)[audit_cols].copy()
    write_table(items_audit, out_dir, audit_name, args.fmt)

    print("==============================================")
    print("OK | Fuente ventas/cobranzas generada")
    print("==============================================")
    print("Output:", out_dir.resolve())
    print("")
    print("Resumen rápido:")
    print("Proyecto filtro:", args.project_filter if args.project_filter else "SIN FILTRO")
    print("Filas finales:", len(final))
    print("Clientes únicos:", final["documento_cliente"].nunique(dropna=True) if not final.empty else 0)
    print("Total venta items:", float(pd.to_numeric(final["total_venta_items"], errors="coerce").sum()) if not final.empty else 0)


if __name__ == "__main__":
    main()