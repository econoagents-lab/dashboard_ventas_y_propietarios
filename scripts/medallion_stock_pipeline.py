"""
medallion_stock_pipeline.py

Pipeline end-to-end bronze/silver/gold para:
1) Universo de ventas / propietarios: solo unidades con venta.
2) Universo de stock completo: todas las unidades desde tabla unidades,
   con propietarios vacíos cuando no existe proceso de venta.

No depende de leads_crm.xlsx.

RAW esperado:
- data/raw/procesos.parquet
- data/raw/clientes.parquet
- data/raw/proyectos.parquet
- data/raw/unidades.parquet

Outputs:
- data/bronze/*.parquet
- data/silver/*.parquet
- data/gold/mart_propietarios_ventas.parquet
- data/gold/mart_stock_unidades_completo.parquet
- data/exports/stock_unidades_completo.xlsx

Run:
python scripts/medallion_stock_pipeline.py --build_all --export_excel
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd


# =========================================================
# Helpers
# =========================================================


def clean_colnames(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza nombres de columnas, no los valores de datos."""
    out = df.copy()
    out.columns = (
        out.columns.astype(str)
        .str.strip()
        .str.lower()
        .str.replace(" ", "_", regex=False)
        .str.replace("-", "_", regex=False)
        .str.replace(".", "_", regex=False)
    )
    return out


def ensure_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c not in out.columns:
            out[c] = pd.NA
    return out


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


def coalesce_series(df: pd.DataFrame, candidates: list[str], default=pd.NA) -> pd.Series:
    """Coalesce por fila entre varias columnas candidatas.

    Evita el error de elegir una sola columna global cuando algunos proyectos
    alimentan un precio/descuento con un alias y otros con otro alias.
    """
    out = pd.Series(default, index=df.index)
    for c in candidates:
        if c in df.columns:
            out = out.where(out.notna(), df[c])
    return out


def coalesce_numeric(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    out = pd.Series(pd.NA, index=df.index, dtype="object")
    for c in candidates:
        if c in df.columns:
            vals = to_num(df[c])
            out = out.where(pd.notna(out), vals)
    return pd.to_numeric(out, errors="coerce")


def coalesce_pct(df: pd.DataFrame, candidates: list[str]) -> pd.Series:
    out = pd.Series(pd.NA, index=df.index, dtype="object")
    for c in candidates:
        if c in df.columns:
            vals = normalize_pct(df[c])
            out = out.where(pd.notna(out), vals)
    return pd.to_numeric(out, errors="coerce")


def clean_output_missing(df: pd.DataFrame) -> pd.DataFrame:
    """Evita que Excel/Streamlit muestren None/<NA>/nan en columnas de texto."""
    out = df.copy()
    text_like = [
        c for c in out.columns
        if not pd.api.types.is_numeric_dtype(out[c]) and not pd.api.types.is_datetime64_any_dtype(out[c])
    ]
    for c in text_like:
        out[c] = out[c].astype("string").replace({"<NA>": pd.NA, "None": pd.NA, "nan": pd.NA, "NaN": pd.NA})
    return out


def normalize_pct(series: pd.Series) -> pd.Series:
    """Normaliza descuentos: acepta 0.08, 8 o 8%. Devuelve proporción decimal."""
    raw = series.astype("string").str.strip().str.replace("%", "", regex=False).str.replace(",", ".", regex=False)
    out = pd.to_numeric(raw, errors="coerce")
    return out.where(out.abs() <= 1, out / 100)


def first_non_null(series: pd.Series):
    vals = series.dropna()
    if vals.empty:
        return pd.NA
    return vals.iloc[0]


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
    parts = re.split(r"[,;|]", txt)
    return [p.strip() for p in parts if p.strip() and p.strip().upper() not in ["<NA>", "NAN", "NONE", "NULL"]]


def preserve_unit_name(item_nombre, codigo_item=None) -> str | pd.NA:
    """Preserva letras iniciales de unidades.nombre.

    Ejemplos:
    E1 -> E1
    D-1201 -> D-1201
    AX05 -> AX05
    """
    raw = item_nombre if pd.notna(item_nombre) else codigo_item
    if raw is None:
        return pd.NA
    try:
        if pd.isna(raw):
            return pd.NA
    except Exception:
        pass
    txt = str(raw).strip()
    return txt if txt else pd.NA


def simplify_tipo_unidad(tipo_unidad) -> str | pd.NA:
    norm = norm_text_value(tipo_unidad)
    if pd.isna(norm):
        return pd.NA
    norm = str(norm)
    if "DEPARTAMENTO" in norm or norm in {"FLAT", "DUPLEX", "TRIPLEX"}:
        return "departamento flat" if norm == "FLAT" else "departamento"
    if norm.startswith("ESTACIONAMIENTO") or "COCHERA" in norm:
        return "estacionamiento"
    if "DEPOSITO" in norm:
        return "depósito"
    return str(tipo_unidad).strip().lower() if pd.notna(tipo_unidad) else pd.NA


def first_existing(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Devuelve la primera columna candidata que existe y tiene al menos un valor no nulo.

    Es importante porque ensure_columns puede crear columnas vacías como fallback.
    """
    cols = set(df.columns)
    fallback = None
    for c in candidates:
        if c in cols:
            if fallback is None:
                fallback = c
            try:
                if df[c].notna().any():
                    return c
            except Exception:
                return c
    return fallback


def safe_sheet_name(name: str, used: set[str]) -> str:
    base = str(name or "SIN_PROYECTO").strip()
    base = re.sub(r"[\\/*?:\[\]]", " ", base)
    base = " ".join(base.split())[:31] or "SIN_PROYECTO"
    candidate = base
    i = 2
    while candidate in used:
        suffix = f"_{i}"
        candidate = base[: 31 - len(suffix)] + suffix
        i += 1
    used.add(candidate)
    return candidate


# =========================================================
# Columnas finales
# =========================================================


STOCK_FINAL_COLS = [
    "proyecto",
    "torre",
    "nombre",
    "tipo_de_unidad",
    "codigo",
    "estado_comercial_raw",
    "estado_comercial",
    "comprador",
    "dni_comprador",
    "copropietario_1",
    "dni_coprop_1",
    "copropietario_2",
    "dni_coprop_2",
    "copropietario_3",
    "dni_coprop_3",
    "precio_lista_al_comprar",
    "precio_venta",
    "precio_de_lista_actual",
    "descuento_actual",
    "monto_actual_dscto",
]

STOCK_DISPLAY_HEADERS = {
    "proyecto": "Proyecto",
    "torre": "Torre",
    "nombre": "nombre",
    "tipo_de_unidad": "tipo de unidad",
    "codigo": "codigo",
    "estado_comercial_raw": "estado comercial",
    "estado_comercial": "Estado comercial",
    "comprador": "comprador",
    "dni_comprador": "dni comprador",
    "copropietario_1": "copropietario 1",
    "dni_coprop_1": "dni coprop 1",
    "copropietario_2": "copropietario 2",
    "dni_coprop_2": "dni coprop 2",
    "copropietario_3": "copropietario 3",
    "dni_coprop_3": "dni coprop 3",
    "precio_lista_al_comprar": "precio lista al comprar",
    "precio_venta": "precio venta",
    "precio_de_lista_actual": "Precio de lista Actual",
    "descuento_actual": "Descuento actual",
    "monto_actual_dscto": "Monto actual dscto",
}

PROPIETARIOS_COLS = [
    "proyecto",
    "tipo_de_unidad",
    "nombre",
    "codigo",
    "comprador",
    "dni_comprador",
    "copropietario_1",
    "dni_coprop_1",
    "copropietario_2",
    "dni_coprop_2",
    "copropietario_3",
    "dni_coprop_3",
    "precio_lista_al_comprar",
    "precio_venta",
    "codigo_proforma",
    "fecha_separacion",
    "fecha_minuta",
    "origen_item",
]


@dataclass
class MedallionConfig:
    raw_dir: Path = Path("data/raw")
    bronze_dir: Path = Path("data/bronze")
    silver_dir: Path = Path("data/silver")
    gold_dir: Path = Path("data/gold")
    exports_dir: Path = Path("data/exports")
    output_name: str = "stock_unidades_completo.xlsx"
    project_filter: list[str] | None = None

    @property
    def stock_mart_path(self) -> Path:
        return self.gold_dir / "mart_stock_unidades_completo.parquet"

    @property
    def owners_mart_path(self) -> Path:
        return self.gold_dir / "mart_propietarios_ventas.parquet"

    @property
    def excel_path(self) -> Path:
        return self.exports_dir / self.output_name


@dataclass
class RawSources:
    procesos: pd.DataFrame
    clientes: pd.DataFrame
    proyectos: pd.DataFrame
    unidades: pd.DataFrame


# =========================================================
# Bronze
# =========================================================


class BronzeLayer:
    REQUIRED = ["procesos", "clientes", "proyectos", "unidades"]

    def __init__(self, config: MedallionConfig):
        self.config = config

    def build(self) -> RawSources:
        self.config.bronze_dir.mkdir(parents=True, exist_ok=True)
        loaded = {}
        for table in self.REQUIRED:
            path = self.config.raw_dir / f"{table}.parquet"
            if not path.exists():
                raise FileNotFoundError(f"No encuentro {path}. Este pipeline no usa leads_crm.xlsx.")
            df = clean_colnames(pd.read_parquet(path))
            df["_bronze_loaded_at"] = datetime.now().isoformat(timespec="seconds")
            df["_bronze_source_file"] = str(path)
            df.to_parquet(self.config.bronze_dir / f"{table}.parquet", index=False)
            loaded[table] = df
        return RawSources(**loaded)

    def load_existing(self) -> RawSources:
        loaded = {}
        for table in self.REQUIRED:
            path = self.config.bronze_dir / f"{table}.parquet"
            if not path.exists():
                raise FileNotFoundError(f"No existe {path}. Ejecuta --build_all.")
            loaded[table] = pd.read_parquet(path)
        return RawSources(**loaded)


# =========================================================
# Silver
# =========================================================


class SilverLayer:
    def __init__(self, config: MedallionConfig):
        self.config = config

    def build(self, sources: RawSources) -> dict[str, pd.DataFrame]:
        self.config.silver_dir.mkdir(parents=True, exist_ok=True)
        tables = {
            "silver_unidades": self.build_unidades(sources.unidades),
            "silver_proyectos": self.build_proyectos(sources.proyectos),
            "silver_clientes": self.build_clientes(sources.clientes),
            "silver_separaciones": self.build_separaciones(sources.procesos),
            "silver_ventas": self.build_ventas(sources.procesos),
            "silver_copropietarios": self.build_copropietarios(sources.procesos, sources.clientes),
        }
        for name, df in tables.items():
            df.to_parquet(self.config.silver_dir / f"{name}.parquet", index=False)
        return tables

    def load_existing(self) -> dict[str, pd.DataFrame]:
        names = [
            "silver_unidades",
            "silver_proyectos",
            "silver_clientes",
            "silver_separaciones",
            "silver_ventas",
            "silver_copropietarios",
        ]
        out = {}
        for name in names:
            path = self.config.silver_dir / f"{name}.parquet"
            if not path.exists():
                raise FileNotFoundError(f"No existe {path}. Ejecuta --build_all.")
            out[name] = pd.read_parquet(path)
        return out

    def build_unidades(self, unidades: pd.DataFrame) -> pd.DataFrame:
        """Construye el universo maestro de unidades.

        Esta tabla es la base del stock completo. Por eso NO depende de ventas
        ni propietarios. También intenta mapear alias reales que suelen venir
        con nombres distintos en los parquets/exports.
        """
        u = ensure_columns(
            unidades,
            [
                "codigo",
                "nombre",
                "tipo_unidad",
                "tipo_de_unidad",
                "codigo_proyecto",
                "proyecto",
                "torre",
                "estado_comercial",
                "estado",
                "precio_venta",
                "precio_base_proforma",
                "precio_lista_al_comprar",
                "precio_lista",
                "precio_de_lista_actual",
                "precio_lista_actual",
                "descuento_actual",
                "monto_actual_dscto",
                "monto_descuento_actual",
                "monto_descuento",
            ],
        )

        tipo_col = first_existing(u, ["tipo_de_unidad", "tipo_unidad"])
        estado_col = first_existing(u, ["estado_comercial", "estado"])

        # V6: coalesce por fila entre aliases reales. No elegimos una única columna global.
        # Esto corrige proyectos/exports donde precio lista, precio actual y descuento
        # vienen con nombres distintos o parcialmente poblados.
        precio_lista_compra = coalesce_numeric(
            u,
            [
                "precio_base_proforma",
                "precio_lista_al_comprar",
                "precio_lista_compra",
                "precio_lista_al_momento_compra",
                "precio_proforma",
                "precio_base",
            ],
        )
        precio_venta_unidad = coalesce_numeric(
            u,
            [
                "precio_venta",
                "precio_de_venta",
                "precio_final",
                "precio_cierre",
                "precio_venta_final",
                "precio_con_descuento",
                "monto_venta",
            ],
        )
        precio_lista_actual = coalesce_numeric(
            u,
            [
                "precio_de_lista_actual",
                "precio_lista_actual",
                "precio_actual",
                "precio_publicado",
                "precio_base_actual",
                "precio_lista_publicado",
                "precio_lista",
            ],
        )
        # Si no existe precio actual explícito, como último recurso usa precio lista al comprar.
        precio_lista_actual = precio_lista_actual.fillna(precio_lista_compra)

        descuento_actual = coalesce_pct(
            u,
            [
                "descuento_actual",
                "dscto_actual",
                "descuento",
                "dscto",
                "porcentaje_descuento",
                "pct_descuento",
                "descuento_pct",
                "porc_descuento_actual",
            ],
        )
        monto_actual_dscto = coalesce_numeric(
            u,
            [
                "monto_actual_dscto",
                "monto_actual_descuento",
                "monto_dscto_actual",
                "monto_descuento_actual",
                "monto_descuento",
                "descuento_monto",
                "dscto_monto",
            ],
        )

        out = pd.DataFrame(
            {
                "codigo": u["codigo"].astype("string"),
                "codigo_item": u["codigo"].astype("string"),
                "codigo_proyecto": u["codigo_proyecto"].astype("string"),
                "proyecto_from_unidades": u["proyecto"].astype("string"),
                "torre": u["torre"].astype("string"),
                "nombre": u["nombre"].apply(preserve_unit_name).astype("string"),
                "tipo_de_unidad": u[tipo_col].astype("string") if tipo_col else pd.Series(pd.NA, index=u.index, dtype="string"),
                "tipo_de_unidad_norm": u[tipo_col].apply(simplify_tipo_unidad).astype("string") if tipo_col else pd.Series(pd.NA, index=u.index, dtype="string"),
                "estado_comercial_raw": u[estado_col].astype("string") if estado_col else pd.Series(pd.NA, index=u.index, dtype="string"),
                "precio_venta_unidad": precio_venta_unidad,
                "precio_lista_al_comprar_unidad": precio_lista_compra,
                "precio_de_lista_actual": precio_lista_actual,
                "descuento_actual_raw": descuento_actual,
                "monto_actual_dscto_raw": monto_actual_dscto,
            }
        )
        return clean_output_missing(out).drop_duplicates("codigo", keep="first")

    def build_proyectos(self, proyectos: pd.DataFrame) -> pd.DataFrame:
        p = ensure_columns(proyectos, ["codigo", "nombre"])
        out = pd.DataFrame(
            {
                "codigo_proyecto": p["codigo"].astype("string"),
                "proyecto": p["nombre"].astype("string"),
                "proyecto_norm": p["nombre"].apply(norm_text_value).astype("string"),
            }
        )
        return out.drop_duplicates("codigo_proyecto", keep="first")

    def build_clientes(self, clientes: pd.DataFrame) -> pd.DataFrame:
        c = ensure_columns(clientes, ["documento", "nombres", "apellidos", "cliente"])
        full_name = (c["nombres"].astype("string").fillna("") + " " + c["apellidos"].astype("string").fillna("")).str.strip()
        cliente_final = c["cliente"].astype("string")
        cliente_final = cliente_final.where(cliente_final.notna() & (cliente_final.str.strip() != ""), full_name)
        out = pd.DataFrame(
            {
                "documento_cliente": c["documento"].astype("string"),
                "dni_comprador": c["documento"].apply(norm_doc_value).astype("string"),
                "comprador": cliente_final.astype("string"),
            }
        )
        return out.drop_duplicates("documento_cliente", keep="first")

    def build_separaciones(self, procesos: pd.DataFrame) -> pd.DataFrame:
        p = ensure_columns(
            procesos,
            [
                "fecha_inicio",
                "codigo_proforma",
                "documento_cliente",
                "codigo_unidad",
                "codigo_proyecto",
                "estado",
                "tipo_financiamiento",
                "codigo_unidades_asignadas",
                "nombre",
            ],
        )
        p["nombre_norm"] = p["nombre"].apply(norm_text_value).astype("string")
        p["estado_norm"] = p["estado"].apply(norm_text_value).astype("string")
        sep = p[p["nombre_norm"].eq("SEPARACION") & p["codigo_proforma"].notna()].copy()
        sep_activa = sep[sep["estado_norm"].eq("ACTIVO")].copy()
        if not sep_activa.empty:
            sep = sep_activa
        sep["fecha_separacion"] = to_date(sep["fecha_inicio"])
        out = pd.DataFrame(
            {
                "fecha_separacion": sep["fecha_separacion"],
                "codigo_proforma": sep["codigo_proforma"].astype("string"),
                "documento_cliente": sep["documento_cliente"].astype("string"),
                "codigo_principal": sep["codigo_unidad"].astype("string"),
                "codigo_proyecto": sep["codigo_proyecto"].astype("string"),
                "tipo_financiamiento": sep["tipo_financiamiento"].astype("string"),
                "adicionales_sep_raw": sep["codigo_unidades_asignadas"].astype("string"),
            }
        )
        return out.drop_duplicates("codigo_proforma", keep="last")

    def build_ventas(self, procesos: pd.DataFrame) -> pd.DataFrame:
        p = ensure_columns(procesos, ["fecha_fin", "codigo_proforma", "codigo_unidades_asignadas", "nombre"])
        p["nombre_norm"] = p["nombre"].apply(norm_text_value).astype("string")
        ven = p[p["nombre_norm"].eq("VENTA") & p["codigo_proforma"].notna()].copy()
        ven["fecha_minuta"] = to_date(ven["fecha_fin"])
        if ven.empty:
            return pd.DataFrame(columns=["codigo_proforma", "fecha_minuta", "adicionales_ven_raw"])
        out = (
            ven.groupby("codigo_proforma", dropna=False)
            .agg(fecha_minuta=("fecha_minuta", "max"), adicionales_ven_raw=("codigo_unidades_asignadas", first_non_null))
            .reset_index()
        )
        out["codigo_proforma"] = out["codigo_proforma"].astype("string")
        out["adicionales_ven_raw"] = out["adicionales_ven_raw"].astype("string")
        return out

    def build_copropietarios(self, procesos: pd.DataFrame, clientes: pd.DataFrame) -> pd.DataFrame:
        p = ensure_columns(procesos, ["codigo_proforma", "documento_copropietarios", "documento_conyuge"])
        c = ensure_columns(clientes, ["documento", "nombres", "apellidos", "cliente"])
        full_name = (c["nombres"].astype("string").fillna("") + " " + c["apellidos"].astype("string").fillna("")).str.strip()
        c["cliente_final"] = c["cliente"].astype("string")
        c["cliente_final"] = c["cliente_final"].where(c["cliente_final"].notna() & (c["cliente_final"].str.strip() != ""), full_name)
        c["doc_key"] = c["documento"].apply(norm_doc_value).astype("string")
        doc_to_name = c.dropna(subset=["doc_key"]).drop_duplicates("doc_key").set_index("doc_key")["cliente_final"].to_dict()
        rows = []
        for _, r in p.iterrows():
            codigo = r.get("codigo_proforma", pd.NA)
            if pd.isna(codigo):
                continue
            docs = []
            for raw_col in ["documento_copropietarios", "documento_conyuge"]:
                for raw_doc in split_codes(r.get(raw_col, pd.NA)):
                    doc = norm_doc_value(raw_doc)
                    if pd.notna(doc) and str(doc) not in docs:
                        docs.append(str(doc))
            row = {"codigo_proforma": str(codigo)}
            for i in [1, 2, 3]:
                doc = docs[i - 1] if len(docs) >= i else pd.NA
                row[f"dni_coprop_{i}"] = doc
                row[f"copropietario_{i}"] = doc_to_name.get(str(doc), pd.NA) if pd.notna(doc) else pd.NA
            rows.append(row)
        if not rows:
            return pd.DataFrame(columns=["codigo_proforma", "copropietario_1", "dni_coprop_1", "copropietario_2", "dni_coprop_2", "copropietario_3", "dni_coprop_3"])
        out = pd.DataFrame(rows)
        agg = {f"copropietario_{i}": first_non_null for i in [1, 2, 3]}
        agg.update({f"dni_coprop_{i}": first_non_null for i in [1, 2, 3]})
        return out.groupby("codigo_proforma", dropna=False).agg(agg).reset_index()


# =========================================================
# Gold
# =========================================================


class GoldLayer:
    def __init__(self, config: MedallionConfig):
        self.config = config

    def build(self, silver: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
        self.config.gold_dir.mkdir(parents=True, exist_ok=True)
        owners = self.build_propietarios(silver)
        stock = self.build_stock_completo(silver, owners)
        owners.to_parquet(self.config.owners_mart_path, index=False)
        stock.to_parquet(self.config.stock_mart_path, index=False)
        return owners, stock

    def load_existing(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        if not self.config.owners_mart_path.exists() or not self.config.stock_mart_path.exists():
            raise FileNotFoundError("No existen marts gold. Ejecuta --build_all.")
        return pd.read_parquet(self.config.owners_mart_path), pd.read_parquet(self.config.stock_mart_path)

    def _explode_unit_codes(self, base: pd.DataFrame) -> pd.DataFrame:
        rows = []
        common_cols = ["codigo_proforma", "documento_cliente", "codigo_proyecto", "fecha_separacion", "fecha_minuta", "tipo_financiamiento"]
        for _, r in base.iterrows():
            common = {c: r.get(c, pd.NA) for c in common_cols}
            principal = r.get("codigo_principal", pd.NA)
            if pd.notna(principal) and str(principal).strip() != "":
                rows.append({**common, "codigo_item": str(principal).strip(), "origen_item": "PRINCIPAL"})
            for code in split_codes(r.get("adicionales_sep_raw", pd.NA)):
                rows.append({**common, "codigo_item": code, "origen_item": "ADICIONAL_SEPARACION"})
            for code in split_codes(r.get("adicionales_ven_raw", pd.NA)):
                rows.append({**common, "codigo_item": code, "origen_item": "ADICIONAL_VENTA"})
        out = pd.DataFrame(rows)
        if out.empty:
            return pd.DataFrame(columns=common_cols + ["codigo_item", "origen_item"])
        out["codigo_item"] = out["codigo_item"].astype("string")
        return out.sort_values(["codigo_proforma", "codigo_item", "origen_item"]).drop_duplicates(["codigo_proforma", "codigo_item"], keep="first")

    def build_propietarios(self, silver: dict[str, pd.DataFrame]) -> pd.DataFrame:
        sep = silver["silver_separaciones"]
        ven = silver["silver_ventas"]
        cli = silver["silver_clientes"]
        uni = silver["silver_unidades"]
        proy = silver["silver_proyectos"]
        cop = silver["silver_copropietarios"]

        base = sep.merge(ven, how="inner", on="codigo_proforma")
        codes = self._explode_unit_codes(base)
        df = codes.merge(uni, how="left", left_on="codigo_item", right_on="codigo_item", suffixes=("", "_unidad"))

        # Robustez: si unidades también trae codigo_proyecto, pandas puede crear
        # codigo_proyecto_unidad. Conservamos la llave de procesos y usamos la de
        # unidades como respaldo. Esto evita KeyError: 'codigo_proyecto'.
        if "codigo_proyecto" not in df.columns:
            df["codigo_proyecto"] = pd.NA
        if "codigo_proyecto_unidad" in df.columns:
            df["codigo_proyecto"] = df["codigo_proyecto"].fillna(df["codigo_proyecto_unidad"])
        if "codigo_proyecto_x" in df.columns:
            df["codigo_proyecto"] = df["codigo_proyecto_x"]
        if "codigo_proyecto_y" in df.columns:
            df["codigo_proyecto"] = df["codigo_proyecto"].fillna(df["codigo_proyecto_y"])

        df = df.merge(proy, how="left", on="codigo_proyecto")
        df["proyecto"] = df["proyecto"].fillna(df.get("proyecto_from_unidades"))
        df = df.merge(cli, how="left", on="documento_cliente")
        df = df.merge(cop, how="left", on="codigo_proforma")

        df["nombre"] = df.apply(lambda r: preserve_unit_name(r.get("nombre"), r.get("codigo_item")), axis=1)
        df["tipo_de_unidad"] = df["tipo_de_unidad"].fillna(df.get("tipo_de_unidad_norm"))
        df["precio_lista_al_comprar"] = df["precio_lista_al_comprar_unidad"]
        df["precio_venta"] = df["precio_venta_unidad"]

        for c in PROPIETARIOS_COLS:
            if c not in df.columns:
                df[c] = pd.NA
        return clean_output_missing(df[PROPIETARIOS_COLS].drop_duplicates(["codigo", "codigo_proforma"], keep="last"))

    def build_stock_completo(self, silver: dict[str, pd.DataFrame], owners: pd.DataFrame) -> pd.DataFrame:
        uni = silver["silver_unidades"].copy()
        proy = silver["silver_proyectos"].copy()

        stock = uni.merge(proy, how="left", on="codigo_proyecto")
        stock["proyecto"] = stock["proyecto"].fillna(stock["proyecto_from_unidades"])
        stock["tipo_de_unidad"] = stock["tipo_de_unidad"].fillna(stock["tipo_de_unidad_norm"])
        stock["precio_lista_al_comprar"] = stock["precio_lista_al_comprar_unidad"]

        owner_cols = [
            "codigo",
            "comprador",
            "dni_comprador",
            "copropietario_1",
            "dni_coprop_1",
            "copropietario_2",
            "dni_coprop_2",
            "copropietario_3",
            "dni_coprop_3",
            "precio_venta",
        ]
        owners_one = owners.copy()
        for c in owner_cols:
            if c not in owners_one.columns:
                owners_one[c] = pd.NA
        owners_one = owners_one[owner_cols].drop_duplicates("codigo", keep="last")

        df = stock.merge(owners_one, how="left", on="codigo")

        # Estado comercial estándar:
        # - Si hay propietario/venta => Vendido.
        # - Si no hay propietario, se respeta estado explícito de la fuente cuando indica venta/separación.
        # - En cualquier otro caso queda Disponible.
        has_owner = df["comprador"].notna() | df["dni_comprador"].notna() | df["precio_venta"].notna()
        raw_norm = df["estado_comercial_raw"].apply(norm_text_value).astype("string")
        df["estado_comercial"] = pd.NA
        df.loc[has_owner, "estado_comercial"] = "Vendido"
        df.loc[~has_owner & raw_norm.str.contains("VEND|SEPAR|MINUTA|PROCESO DE VENTA", na=False), "estado_comercial"] = "Vendido"
        df.loc[~has_owner & raw_norm.str.contains("DISP|LIBRE|STOCK", na=False), "estado_comercial"] = "Disponible"
        df["estado_comercial"] = df["estado_comercial"].fillna("Disponible")

        df["precio_de_lista_actual"] = pd.to_numeric(df["precio_de_lista_actual"], errors="coerce")
        df["precio_venta"] = pd.to_numeric(df["precio_venta"], errors="coerce")
        df["precio_lista_al_comprar"] = pd.to_numeric(df["precio_lista_al_comprar"], errors="coerce")

        # V6: Descuento actual y monto actual dscto NO deben calcularse como
        # precio_de_lista_actual - precio_venta. Esa resta mide descuento de compra,
        # no el descuento comercial vigente.
        # Prioridad correcta:
        # 1) columnas reales de unidades,
        # 2) monto/lista si existe monto pero falta %,
        # 3) regla de negocio fallback: departamentos 8%, otros 0%,
        # 4) monto = descuento_actual * precio_de_lista_actual.
        df["descuento_actual"] = df["descuento_actual_raw"] if "descuento_actual_raw" in df.columns else pd.NA
        df["monto_actual_dscto"] = df["monto_actual_dscto_raw"] if "monto_actual_dscto_raw" in df.columns else pd.NA

        pct_from_monto = df["monto_actual_dscto"] / df["precio_de_lista_actual"]
        df["descuento_actual"] = pd.to_numeric(df["descuento_actual"], errors="coerce").fillna(pct_from_monto)

        tipo_norm = df["tipo_de_unidad"].astype("string").str.lower()
        default_desc = pd.Series(0.0, index=df.index, dtype="float")
        default_desc = default_desc.mask(tipo_norm.str.contains("departamento", na=False), 0.08)
        df["descuento_actual"] = df["descuento_actual"].fillna(default_desc)
        df.loc[df["precio_de_lista_actual"].isna(), "descuento_actual"] = pd.NA

        monto_from_pct = df["precio_de_lista_actual"] * df["descuento_actual"]
        df["monto_actual_dscto"] = pd.to_numeric(df["monto_actual_dscto"], errors="coerce").fillna(monto_from_pct)

        for c in STOCK_FINAL_COLS:
            if c not in df.columns:
                df[c] = pd.NA

        sort_cols = [c for c in ["proyecto", "tipo_de_unidad", "nombre"] if c in df.columns]
        if sort_cols:
            df = df.sort_values(sort_cols, na_position="last")
        return clean_output_missing(df[STOCK_FINAL_COLS]).reset_index(drop=True)


# =========================================================
# Excel aesthetic
# =========================================================


class AestheticStockExcelExporter:
    def __init__(self, config: MedallionConfig):
        self.config = config

    def write(self, stock: pd.DataFrame, owners: pd.DataFrame | None = None) -> Path:
        self.config.exports_dir.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(self.config.excel_path, engine="xlsxwriter") as writer:
            workbook = writer.book
            formats = self._formats(workbook)
            self._write_resumen(writer, formats, stock)
            self._write_stock(writer, formats, stock)
            if owners is not None:
                self._write_owners(writer, formats, owners)
            self._write_project_sheets(writer, formats, stock)
        return self.config.excel_path

    def _formats(self, workbook):
        return {
            "title": workbook.add_format({"bold": True, "font_size": 18, "font_color": "#FFFFFF", "bg_color": "#0B1F33", "align": "left", "valign": "vcenter"}),
            "subtitle": workbook.add_format({"font_size": 10, "font_color": "#475569"}),
            "header": workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#153B5C", "align": "center", "valign": "vcenter", "border": 1, "border_color": "#0B1F33"}),
            "kpi_label": workbook.add_format({"bold": True, "font_color": "#0B1F33", "bg_color": "#E2E8F0", "border": 1, "border_color": "#CBD5E1"}),
            "kpi_value": workbook.add_format({"bold": True, "font_color": "#111827", "bg_color": "#F8FAFC", "border": 1, "border_color": "#CBD5E1", "num_format": "#,##0"}),
            "money_kpi": workbook.add_format({"bold": True, "font_color": "#111827", "bg_color": "#F8FAFC", "border": 1, "border_color": "#CBD5E1", "num_format": "\"S/\" #,##0.00"}),
            "text": workbook.add_format({"font_color": "#111827"}),
            "money": workbook.add_format({"num_format": "\"S/\" #,##0.00"}),
            "percent": workbook.add_format({"num_format": "0.00%"}),
            "alt": workbook.add_format({"bg_color": "#F8FAFC"}),
        }

    def _display(self, df: pd.DataFrame) -> pd.DataFrame:
        out = clean_output_missing(df).rename(columns=STOCK_DISPLAY_HEADERS)

        # Columnas numéricas: forzarlas a número antes de escribir Excel.
        # Esto evita que pandas/xlsxwriter las escriban como texto u objeto mixto.
        numeric_cols = [
            "precio lista al comprar",
            "precio venta",
            "Precio de lista Actual",
            "Descuento actual",
            "Monto actual dscto",
        ]
        for c in numeric_cols:
            if c in out.columns:
                out[c] = pd.to_numeric(out[c], errors="coerce")

        # Solo para Excel: columnas de texto vacías se ven vacías, no como None/<NA>.
        for c in out.columns:
            if c not in numeric_cols and not pd.api.types.is_datetime64_any_dtype(out[c]):
                out[c] = out[c].fillna("")
        return out

    def _write_resumen(self, writer, formats, stock: pd.DataFrame) -> None:
        resumen = (
            stock.groupby(["proyecto", "estado_comercial"], dropna=False)
            .agg(unidades=("codigo", "count"), valor_lista_actual=("precio_de_lista_actual", "sum"))
            .reset_index()
            .sort_values(["proyecto", "estado_comercial"])
        )
        resumen.to_excel(writer, sheet_name="00_RESUMEN_STOCK", index=False, startrow=6)
        ws = writer.sheets["00_RESUMEN_STOCK"]
        ws.merge_range("A1:E1", "Stock completo por proyecto", formats["title"])
        ws.write("A2", "Universo completo desde unidades; propietarios se agregan con left merge desde procesos de venta.", formats["subtitle"])
        ws.write("A4", "Total unidades", formats["kpi_label"])
        ws.write("B4", len(stock), formats["kpi_value"])
        ws.write("C4", "Disponibles", formats["kpi_label"])
        ws.write("D4", int((stock["estado_comercial"] == "Disponible").sum()), formats["kpi_value"])
        ws.write("E4", "Valor lista actual", formats["kpi_label"])
        ws.write("F4", float(pd.to_numeric(stock["precio_de_lista_actual"], errors="coerce").sum()), formats["money_kpi"])
        for col_num, value in enumerate(resumen.columns):
            ws.write(6, col_num, value, formats["header"])
        ws.set_column("A:A", 24, formats["text"])
        ws.set_column("B:B", 18, formats["text"])
        ws.set_column("C:C", 14, formats["text"])
        ws.set_column("D:D", 20, formats["money"])
        ws.freeze_panes(7, 0)
        if len(resumen) > 0:
            ws.autofilter(6, 0, 6 + len(resumen), len(resumen.columns) - 1)

    def _write_stock(self, writer, formats, stock: pd.DataFrame) -> None:
        out = self._display(stock)
        out.to_excel(writer, sheet_name="01_STOCK_COMPLETO", index=False, startrow=4)
        ws = writer.sheets["01_STOCK_COMPLETO"]
        ws.merge_range(0, 0, 0, len(out.columns) - 1, "Stock completo de unidades", formats["title"])
        ws.write(1, 0, "Incluye vendidas y disponibles. Si no hay venta, propietarios quedan vacíos.", formats["subtitle"])
        for col_num, value in enumerate(out.columns):
            ws.write(4, col_num, value, formats["header"])
        self._style_table(ws, out, 4, formats)

    def _write_owners(self, writer, formats, owners: pd.DataFrame) -> None:
        out = owners.copy()
        out.to_excel(writer, sheet_name="02_PROPIETARIOS", index=False, startrow=4)
        ws = writer.sheets["02_PROPIETARIOS"]
        ws.merge_range(0, 0, 0, max(len(out.columns) - 1, 0), "Universo propietarios / ventas", formats["title"])
        ws.write(1, 0, "Solo unidades con proceso de venta asociado.", formats["subtitle"])
        for col_num, value in enumerate(out.columns):
            ws.write(4, col_num, value, formats["header"])
        self._style_table(ws, out, 4, formats)

    def _write_project_sheets(self, writer, formats, stock: pd.DataFrame) -> None:
        used = {"00_RESUMEN_STOCK", "01_STOCK_COMPLETO", "02_PROPIETARIOS"}
        for proyecto, part in stock.groupby("proyecto", dropna=False, sort=True):
            sheet_name = safe_sheet_name(str(proyecto) if pd.notna(proyecto) else "SIN_PROYECTO", used)
            out = self._display(part)
            out.to_excel(writer, sheet_name=sheet_name, index=False, startrow=5)
            ws = writer.sheets[sheet_name]
            ws.merge_range(0, 0, 0, len(out.columns) - 1, f"Stock | {proyecto}", formats["title"])
            ws.write(1, 0, "Detalle completo por unidad.", formats["subtitle"])
            ws.write(3, 0, "Unidades", formats["kpi_label"])
            ws.write(3, 1, len(part), formats["kpi_value"])
            ws.write(3, 2, "Disponibles", formats["kpi_label"])
            ws.write(3, 3, int((part["estado_comercial"] == "Disponible").sum()), formats["kpi_value"])
            for col_num, value in enumerate(out.columns):
                ws.write(5, col_num, value, formats["header"])
            self._style_table(ws, out, 5, formats)

    def _style_table(self, ws, data: pd.DataFrame, startrow: int, formats) -> None:
        widths = {
            "Proyecto": 22,
            "Torre": 12,
            "nombre": 14,
            "tipo de unidad": 24,
            "codigo": 16,
            "estado comercial": 22,
            "Estado comercial": 16,
            "comprador": 34,
            "dni comprador": 16,
            "copropietario 1": 28,
            "dni coprop 1": 16,
            "copropietario 2": 28,
            "dni coprop 2": 16,
            "copropietario 3": 28,
            "dni coprop 3": 16,
            "precio lista al comprar": 20,
            "precio venta": 18,
            "Precio de lista Actual": 20,
            "Descuento actual": 16,
            "Monto actual dscto": 18,
        }
        for idx, col in enumerate(data.columns):
            if "precio" in col.lower() or "monto" in col.lower():
                fmt = formats["money"]
            elif "descuento actual" == col:
                fmt = formats["percent"]
            else:
                fmt = formats["text"]
            ws.set_column(idx, idx, widths.get(col, 16), fmt)
        nrows, ncols = len(data), len(data.columns)
        ws.freeze_panes(startrow + 1, 0)
        if nrows > 0 and ncols > 0:
            ws.autofilter(startrow, 0, startrow + nrows, ncols - 1)
            ws.conditional_format(startrow + 1, 0, startrow + nrows, ncols - 1, {"type": "formula", "criteria": "=MOD(ROW(),2)=0", "format": formats["alt"]})


# =========================================================
# Orquestador
# =========================================================


class MedallionStockPipeline:
    def __init__(self, config: MedallionConfig):
        self.config = config
        self.bronze = BronzeLayer(config)
        self.silver = SilverLayer(config)
        self.gold = GoldLayer(config)

    def build_all(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        sources = self.bronze.build()
        silver = self.silver.build(sources)
        return self.gold.build(silver)

    def build_gold_from_existing_silver(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        silver = self.silver.load_existing()
        return self.gold.build(silver)

    def load_gold(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        return self.gold.load_existing()

    def export_excel(self, stock: pd.DataFrame, owners: pd.DataFrame | None = None) -> Path:
        return AestheticStockExcelExporter(self.config).write(stock, owners)


# =========================================================
# CLI
# =========================================================


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw_dir", default="data/raw")
    ap.add_argument("--bronze_dir", default="data/bronze")
    ap.add_argument("--silver_dir", default="data/silver")
    ap.add_argument("--gold_dir", default="data/gold")
    ap.add_argument("--exports_dir", default="data/exports")
    ap.add_argument("--output_name", default="stock_unidades_completo.xlsx")
    ap.add_argument("--build_all", action="store_true")
    ap.add_argument("--build_gold", action="store_true")
    ap.add_argument("--export_excel", action="store_true")
    args = ap.parse_args()

    config = MedallionConfig(
        raw_dir=Path(args.raw_dir),
        bronze_dir=Path(args.bronze_dir),
        silver_dir=Path(args.silver_dir),
        gold_dir=Path(args.gold_dir),
        exports_dir=Path(args.exports_dir),
        output_name=args.output_name,
    )
    pipeline = MedallionStockPipeline(config)

    print("==============================================")
    print("Pipeline Stock Completo Medallion")
    print("==============================================")
    if args.build_all:
        owners, stock = pipeline.build_all()
    elif args.build_gold:
        owners, stock = pipeline.build_gold_from_existing_silver()
    else:
        owners, stock = pipeline.load_gold()

    print(f"Propietarios/Ventas: {len(owners):,}")
    print(f"Stock total unidades: {len(stock):,}")
    print(f"Disponibles: {int((stock['estado_comercial'] == 'Disponible').sum()):,}")
    print(f"Vendidas: {int((stock['estado_comercial'] == 'Vendido').sum()):,}")
    print(f"Gold stock: {config.stock_mart_path.resolve()}")

    if args.export_excel:
        path = pipeline.export_excel(stock, owners)
        print(f"Excel: {path.resolve()}")
    print("OK")
    print("==============================================")


if __name__ == "__main__":
    main()
