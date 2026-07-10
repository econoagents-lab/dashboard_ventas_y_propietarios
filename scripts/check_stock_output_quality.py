"""
check_stock_output_quality.py

Diagnóstico rápido del mart/excel de stock completo.
Run:
python scripts/check_stock_output_quality.py --gold_path data/gold/mart_stock_unidades_completo.parquet
"""
from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd

EXPECTED = [
    "proyecto", "torre", "nombre", "tipo_de_unidad", "codigo", "estado_comercial_raw", "estado_comercial",
    "comprador", "dni_comprador", "copropietario_1", "dni_coprop_1", "copropietario_2", "dni_coprop_2",
    "copropietario_3", "dni_coprop_3", "precio_lista_al_comprar", "precio_venta", "precio_de_lista_actual",
    "descuento_actual", "monto_actual_dscto",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold_path", default="data/gold/mart_stock_unidades_completo.parquet")
    args = ap.parse_args()
    path = Path(args.gold_path)
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_parquet(path)
    print("==============================================")
    print("Quality check stock_unidades_completo")
    print("==============================================")
    print("Path:", path.resolve())
    print("Rows:", len(df))
    print("Columns:", len(df.columns))
    missing = [c for c in EXPECTED if c not in df.columns]
    extra = [c for c in df.columns if c not in EXPECTED]
    print("Missing expected columns:", missing)
    print("Extra columns:", extra)
    if "estado_comercial" in df.columns:
        print("\nEstado comercial counts:")
        print(df["estado_comercial"].value_counts(dropna=False).to_string())
    print("\nNull counts selected:")
    for c in EXPECTED:
        if c in df.columns:
            print(f"{c:28s} {int(df[c].isna().sum()):8d}")
    money_cols = ["precio_lista_al_comprar", "precio_venta", "precio_de_lista_actual", "descuento_actual", "monto_actual_dscto"]
    print("\nMoney/discount diagnostics:")
    for c in money_cols:
        if c in df.columns:
            numeric = pd.to_numeric(df[c], errors="coerce")
            print(f"{c:28s} non_null={int(numeric.notna().sum()):8d} sum={float(numeric.sum(skipna=True)):,.2f}")

    if {"precio_de_lista_actual", "descuento_actual", "monto_actual_dscto"}.issubset(df.columns):
        expected_monto = pd.to_numeric(df["precio_de_lista_actual"], errors="coerce") * pd.to_numeric(df["descuento_actual"], errors="coerce")
        actual_monto = pd.to_numeric(df["monto_actual_dscto"], errors="coerce")
        diff = (actual_monto - expected_monto).abs()
        bad = diff.gt(0.05) & actual_monto.notna() & expected_monto.notna()
        print(f"monto_actual_dscto != precio_de_lista_actual * descuento_actual: {int(bad.sum())} rows")

    print("\nSample available rows:")
    if "estado_comercial" in df.columns:
        sample = df[df["estado_comercial"].eq("Disponible")].head(5)
    else:
        sample = df.head(5)
    print(sample[[c for c in EXPECTED if c in sample.columns]].to_string(index=False))
    print("==============================================")


if __name__ == "__main__":
    main()
