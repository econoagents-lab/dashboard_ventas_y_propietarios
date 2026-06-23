r"""
build_ventas_por_proyecto_excel_oop.py

Versión OOP / configurable del script de ventas por proyecto.

Objetivo
--------
- Mantener la lógica actual del pipeline.
- Permitir seleccionar columnas finales desde CLI.
- Permitir agregar columnas calculadas de forma ordenada vía registry.
- Separar responsabilidades:
  1) Configuración
  2) Lectura de fuentes
  3) Transformación / joins
  4) Selección de columnas
  5) Exportación Excel aesthetic

Run base:
python .\scripts\build_ventas_por_proyecto_excel_oop.py --raw_dir data/raw --out_dir data/exports

Seleccionar columnas:
python .\scripts\build_ventas_por_proyecto_excel_oop.py --raw_dir data/raw --out_dir data/exports --columns proyecto tipo_unidad num_unidad comprador precio_lista_al_comprar precio_venta

Agregar columnas calculadas predefinidas:
python .\scripts\build_ventas_por_proyecto_excel_oop.py --raw_dir data/raw --out_dir data/exports --extra_columns flag_tiene_coprop total_coprops precio_venta_miles

Filtrar proyectos:
python .\scripts\build_ventas_por_proyecto_excel_oop.py --raw_dir data/raw --out_dir data/exports --project_filter "SIALIA" "Torre Nápoles"
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable
import numpy as np
import pandas as pd


# =========================================================
# Helpers generales
# =========================================================


def clean_colnames(df: pd.DataFrame) -> pd.DataFrame:
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


def first_non_null(series: pd.Series):
    vals = series.dropna()
    if vals.empty:
        return pd.NA
    return vals.iloc[0]


def normalize_project_list(projects: list[str] | None) -> set[str] | None:
    if not projects:
        return None
    clean: set[str] = set()
    for p in projects:
        norm = norm_text_value(p)
        if pd.notna(norm):
            clean.add(str(norm))
    return clean if clean else None


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


def pick_first_existing(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    cols = set(df.columns)
    for c in candidates:
        if c in cols:
            return c
    return None


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


def extract_num_unidad(item_nombre, codigo_item=None, tipo_unidad=None) -> str | pd.NA:
    """Devuelve el valor visible de unidad.

    Regla de negocio:
    - Si la unidad es departamento, conserva unidades.nombre completo, incluyendo
      la letra inicial: A1201, D-1201, DPTO A1201, etc.
    - Para estacionamientos, depósitos y otros adicionales, mantiene la lógica
      anterior: extrae solo el componente numérico mediante regex.
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
    if not txt:
        return pd.NA

    tipo_norm = norm_text_value(tipo_unidad)
    tipo_norm_txt = str(tipo_norm) if pd.notna(tipo_norm) else ""
    if "DEPARTAMENTO" in tipo_norm_txt or tipo_norm_txt in {"FLAT", "DUPLEX", "TRIPLEX"}:
        return txt

    matches = re.findall(r"\d+", txt)
    if matches:
        return matches[-1]
    return txt


def simplify_tipo_unidad(tipo_unidad) -> str | pd.NA:
    norm = norm_text_value(tipo_unidad)
    if pd.isna(norm):
        return pd.NA
    norm = str(norm)
    if "DEPARTAMENTO" in norm or norm in {"FLAT", "DUPLEX", "TRIPLEX"}:
        return "DEPARTAMENTO"
    if norm.startswith("ESTACIONAMIENTO") or "COCHERA" in norm:
        return "ESTACIONAMIENTO"
    if "DEPOSITO" in norm:
        return "DEPÓSITO"
    return norm


# =========================================================
# Configuración OOP
# =========================================================


@dataclass(frozen=True)
class ColumnSpec:
    """Define una columna exportable.

    name: nombre interno de la columna en el DataFrame.
    header: nombre visible en Excel.
    width: ancho visual sugerido.
    kind: texto, dinero, numero, fecha.
    """

    name: str
    header: str | None = None
    width: int = 16
    kind: str = "text"

    @property
    def display_name(self) -> str:
        return self.header or self.name


DEFAULT_COLUMNS: list[ColumnSpec] = [
    ColumnSpec("proyecto", "Proyecto", 22),
    ColumnSpec("tipo_unidad", "Tipo Unidad", 18),
    ColumnSpec("num_unidad", "N° Unidad", 12),
    ColumnSpec("comprador", "Comprador", 34),
    ColumnSpec("dni_comprador", "DNI Comprador", 16),
    ColumnSpec("copropietario_1", "Copropietario 1", 30),
    ColumnSpec("dni_coprop_1", "DNI Coprop. 1", 16),
    ColumnSpec("copropietario_2", "Copropietario 2", 30),
    ColumnSpec("dni_coprop_2", "DNI Coprop. 2", 16),
    ColumnSpec("copropietario_3", "Copropietario 3", 30),
    ColumnSpec("dni_coprop_3", "DNI Coprop. 3", 16),
    ColumnSpec("precio_lista_al_comprar", "Precio Lista al Comprar", 22, "money"),
    ColumnSpec("precio_venta", "Precio Venta", 18, "money"),
]

OPTIONAL_COLUMNS: dict[str, ColumnSpec] = {
    "codigo_proforma": ColumnSpec("codigo_proforma", "Código Proforma", 18),
    "codigo_item": ColumnSpec("codigo_item", "Código Item", 18),
    "codigo_proyecto": ColumnSpec("codigo_proyecto", "Código Proyecto", 18),
    "fecha_separacion": ColumnSpec("fecha_separacion", "Fecha Separación", 16, "date"),
    "fecha_minuta": ColumnSpec("fecha_minuta", "Fecha Minuta", 16, "date"),
    "tipo_financiamiento": ColumnSpec("tipo_financiamiento", "Tipo Financiamiento", 20),
    "origen_item": ColumnSpec("origen_item", "Origen Item", 22),
    "precio_base_proforma": ColumnSpec("precio_base_proforma", "Precio Base Proforma", 22, "money"),
    "precio_venta_miles": ColumnSpec("precio_venta_miles", "Precio Venta Miles", 18, "number"),
    "flag_tiene_coprop": ColumnSpec("flag_tiene_coprop", "Tiene Coprop.", 14),
    "total_coprops": ColumnSpec("total_coprops", "Total Coprops", 14, "number"),
}


def build_column_specs(requested: list[str] | None, extra_columns: list[str] | None) -> list[ColumnSpec]:
    """Arma la lista final de columnas exportables.

    - Sin --columns: usa DEFAULT_COLUMNS.
    - Con --columns: respeta exactamente el orden pedido.
    - --extra_columns agrega columnas al final.
    """

    catalog = {c.name: c for c in DEFAULT_COLUMNS} | OPTIONAL_COLUMNS

    if requested:
        specs = [catalog.get(c, ColumnSpec(c, c)) for c in requested]
    else:
        specs = list(DEFAULT_COLUMNS)

    for c in extra_columns or []:
        spec = catalog.get(c, ColumnSpec(c, c))
        if spec.name not in [s.name for s in specs]:
            specs.append(spec)

    return specs


@dataclass
class PipelineConfig:
    raw_dir: Path
    out_dir: Path
    output_name: str = "ventas_por_proyecto.xlsx"
    project_filter: list[str] | None = None
    columns: list[ColumnSpec] = field(default_factory=lambda: list(DEFAULT_COLUMNS))
    extra_calculated_columns: list[str] = field(default_factory=list)

    @property
    def out_path(self) -> Path:
        return self.out_dir / self.output_name

    @property
    def final_column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def project_sheet_column_names(self) -> list[str]:
        return [c for c in self.final_column_names if c != "proyecto"]

    @property
    def display_headers(self) -> dict[str, str]:
        return {c.name: c.display_name for c in self.columns}


# =========================================================
# Lectura de datos
# =========================================================


@dataclass
class SalesSources:
    procesos: pd.DataFrame
    clientes: pd.DataFrame
    proyectos: pd.DataFrame
    unidades: pd.DataFrame


class ParquetRepository:
    def __init__(self, raw_dir: Path):
        self.raw_dir = raw_dir

    def read_required(self, file_name: str) -> pd.DataFrame:
        path = self.raw_dir / file_name
        if not path.exists():
            raise FileNotFoundError(f"No encuentro archivo requerido: {path}")
        return clean_colnames(pd.read_parquet(path))

    def load(self) -> SalesSources:
        return SalesSources(
            procesos=self.read_required("procesos.parquet"),
            clientes=self.read_required("clientes.parquet"),
            proyectos=self.read_required("proyectos.parquet"),
            unidades=self.read_required("unidades.parquet"),
        )


# =========================================================
# Transformaciones de dominio
# =========================================================


class SalesTransformer:
    """Transforma las 4 tablas base hacia el grano venta/unidad."""

    def build_sep(self, procesos: pd.DataFrame) -> pd.DataFrame:
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

        sep = p[p["nombre_norm"].isin(["SEPARACION"]) & p["codigo_proforma"].notna()].copy()
        sep_activa = sep[sep["estado_norm"].eq("ACTIVO")].copy()
        if not sep_activa.empty:
            sep = sep_activa

        sep["fecha_separacion"] = to_date(sep["fecha_inicio"])
        sep["usuario_separacion_final"] = sep["usuario_separacion"].fillna(sep["usuario"]).fillna(sep["asesor"])

        out = pd.DataFrame(
            {
                "fecha_separacion": sep["fecha_separacion"],
                "codigo_proforma": sep["codigo_proforma"].astype("string"),
                "documento_cliente": sep["documento_cliente"].astype("string"),
                "codigo_principal": sep["codigo_unidad"].astype("string"),
                "codigo_proyecto": sep["codigo_proyecto"].astype("string"),
                "usuario_separacion": sep["usuario_separacion_final"].astype("string"),
                "tipo_financiamiento": sep["tipo_financiamiento"].astype("string"),
                "adicionales_sep_raw": sep["codigo_unidades_asignadas"].astype("string"),
            }
        )
        return out.drop_duplicates("codigo_proforma", keep="last")

    def build_ven(self, procesos: pd.DataFrame) -> pd.DataFrame:
        p = procesos.copy()
        p = ensure_columns(p, ["fecha_fin", "codigo_proforma", "codigo_unidades_asignadas", "nombre"])
        p["nombre_norm"] = p["nombre"].apply(norm_text_value).astype("string")

        ven = p[p["nombre_norm"].eq("VENTA") & p["codigo_proforma"].notna()].copy()
        #sep = p[p["nombre_norm"].eq("SEPARACION") & p["codigo_proforma"].notna()].copy()
        ven["fecha_minuta"] = to_date(ven["fecha_fin"])
        #sep["fecha_separacion"] = to_date(sep["fecha_separacion"])

        if ven.empty:
            return pd.DataFrame(columns=["codigo_proforma", "fecha_minuta", "adicionales_ven_raw"])

        out = (
            ven.groupby("codigo_proforma", dropna=False)
            .agg(fecha_minuta=("fecha_minuta", "max"), adicionales_ven_raw=("codigo_unidades_asignadas", first_non_null))
            .reset_index()
        )
        """  if sep.empty:
            return pd.DataFrame(columns=["codigo_proforma", "fecha_separacion", "adicionales_ven_raw"])

        out = (
            sep.groupby("codigo_proforma", dropna=False)
            .agg(fecha_separacion=("fecha_separacion", "max"), adicionales_ven_raw=("codigo_unidades_asignadas", first_non_null))
            .reset_index()
        ) """
        out["codigo_proforma"] = out["codigo_proforma"].astype("string")
        out["adicionales_ven_raw"] = out["adicionales_ven_raw"].astype("string")
        return out
    """ 
    def build_ven(self, procesos: pd.DataFrame) -> pd.DataFrame:
        p = procesos.copy()
        p = ensure_columns(p, ["fecha_fin", "codigo_proforma", "codigo_unidades_asignadas", "nombre"])
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
        return out """

    def build_cli(self, clientes: pd.DataFrame) -> pd.DataFrame:
        c = clientes.copy()
        c = ensure_columns(c, ["documento", "nombres", "apellidos", "cliente"])

        full_name = (c["nombres"].astype("string").fillna("") + " " + c["apellidos"].astype("string").fillna("")).str.strip()
        c["cliente_final"] = c["cliente"].astype("string")
        c["cliente_final"] = c["cliente_final"].where(c["cliente_final"].notna() & (c["cliente_final"].str.strip() != ""), full_name)

        out = pd.DataFrame(
            {
                "documento_cliente": c["documento"].astype("string"),
                "dni_comprador": c["documento"].apply(norm_doc_value).astype("string"),
                "comprador": c["cliente_final"].astype("string"),
            }
        )
        return out.drop_duplicates("documento_cliente", keep="first")

    def build_proy(self, proyectos: pd.DataFrame) -> pd.DataFrame:
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

    def build_uni(self, unidades: pd.DataFrame) -> pd.DataFrame:
        u = unidades.copy()
        u = ensure_columns(u, ["codigo", "nombre", "tipo_unidad", "precio_venta", "precio_base_proforma"])
        out = pd.DataFrame(
            {
                "codigo_item": u["codigo"].astype("string"),
                "item_nombre": u["nombre"].astype("string"),
                "tipo_unidad_raw": u["tipo_unidad"].astype("string"),
                "precio_base_proforma": to_num(u["precio_base_proforma"]),
                "precio_lista_al_comprar": to_num(u["precio_base_proforma"]),
                "precio_venta": to_num(u["precio_venta"]),
            }
        )
        out["tipo_unidad"] = out["tipo_unidad_raw"].apply(simplify_tipo_unidad).astype("string")
        return out.drop_duplicates("codigo_item", keep="first")

    def build_coprops(self, procesos: pd.DataFrame, clientes: pd.DataFrame) -> pd.DataFrame:
        """Extrae copropietarios desde procesos y los resuelve contra clientes.

        Soporta dos escenarios:
        1) Procesos ya trae nombres/dnis tipo copropietario_1, dni_coprop_1.
        2) Procesos trae documento_copropietarios y documento_conyuge, y se busca el nombre en clientes.documento.
        """

        p = procesos.copy()
        p = ensure_columns(p, ["codigo_proforma", "documento_copropietarios", "documento_conyuge"])

        c = clientes.copy()
        c = ensure_columns(c, ["documento", "nombres", "apellidos", "cliente"])
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

            docs: list[str] = []
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

        # Fallback: si no hubo documentos tipo documento_copropietarios/documento_conyuge,
        # intenta columnas directas similares a tu script original.
        if not rows:
            return self._build_coprops_from_direct_columns(p)

        out = pd.DataFrame(rows)
        agg = {f"copropietario_{i}": first_non_null for i in [1, 2, 3]}
        agg.update({f"dni_coprop_{i}": first_non_null for i in [1, 2, 3]})
        return out.groupby("codigo_proforma", dropna=False).agg(agg).reset_index()

    def _build_coprops_from_direct_columns(self, procesos: pd.DataFrame) -> pd.DataFrame:
        p = procesos.copy()
        p = ensure_columns(p, ["codigo_proforma"])

        name_candidates = {
            1: ["copropietario_1", "copropietario1", "coprop_1", "copropietario_1_nombre", "nombre_copropietario_1", "cotitular_1", "cotitular1"],
            2: ["copropietario_2", "copropietario2", "coprop_2", "copropietario_2_nombre", "nombre_copropietario_2", "cotitular_2", "cotitular2"],
            3: ["copropietario_3", "copropietario3", "coprop_3", "copropietario_3_nombre", "nombre_copropietario_3", "cotitular_3", "cotitular3"],
        }
        dni_candidates = {
            1: ["dni_coprop_1", "dni_copropietario_1", "documento_copropietario_1", "doc_coprop_1", "dni_cotitular_1", "documento_cotitular_1"],
            2: ["dni_coprop_2", "dni_copropietario_2", "documento_copropietario_2", "doc_coprop_2", "dni_cotitular_2", "documento_cotitular_2"],
            3: ["dni_coprop_3", "dni_copropietario_3", "documento_copropietario_3", "doc_coprop_3", "dni_cotitular_3", "documento_cotitular_3"],
        }

        out = pd.DataFrame({"codigo_proforma": p["codigo_proforma"].astype("string")})
        for i in [1, 2, 3]:
            name_col = pick_first_existing(p, name_candidates[i])
            dni_col = pick_first_existing(p, dni_candidates[i])
            out[f"copropietario_{i}"] = p[name_col].astype("string") if name_col else pd.NA
            out[f"dni_coprop_{i}"] = p[dni_col].apply(norm_doc_value).astype("string") if dni_col else pd.NA

        agg = {f"copropietario_{i}": first_non_null for i in [1, 2, 3]}
        agg.update({f"dni_coprop_{i}": first_non_null for i in [1, 2, 3]})
        return out.groupby("codigo_proforma", dropna=False).agg(agg).reset_index()

    def build_codes(self, base: pd.DataFrame) -> pd.DataFrame:
        rows = []
        common_cols = [
            "codigo_proforma",
            "documento_cliente",
            "codigo_proyecto",
            "fecha_separacion",
            "fecha_minuta",
            "tipo_financiamiento",
            "estado_operacion",
        ]

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
        out = (
            out.sort_values(["codigo_proforma", "codigo_item", "origen_item"])
            .drop_duplicates(["codigo_proforma", "codigo_item"], keep="first")
            .reset_index(drop=True)
        )
        return out

    def build_base(self, sources: SalesSources, project_filter: list[str] | None = None) -> pd.DataFrame:
        sep = self.build_sep(sources.procesos)
        ven = self.build_ven(sources.procesos)
        cli = self.build_cli(sources.clientes)
        proy = self.build_proy(sources.proyectos)
        uni = self.build_uni(sources.unidades)
        cop = self.build_coprops(sources.procesos, sources.clientes)

        """ base = sep.merge(ven, how="inner", on="codigo_proforma")
        codes = self.build_codes(base) """

        # Separaciones + ventas:
        # - Si tiene proceso VENTA, trae fecha_minuta.
        # - Si aún no tiene minuta, se mantiene como separación pendiente.
        base = sep.merge(ven, how="left", on="codigo_proforma")

        base["estado_operacion"] = np.where(
            base["fecha_minuta"].notna(),
            "Venta con minuta",
            "Separación sin minuta"
        )

        codes = self.build_codes(base)

        df = codes.merge(uni, how="left", on="codigo_item")
        df = df.merge(proy, how="left", on="codigo_proyecto")
        df = df.merge(cli, how="left", on="documento_cliente")
        df = df.merge(cop, how="left", on="codigo_proforma")

        project_filter_norms = normalize_project_list(project_filter)
        if project_filter_norms is not None:
            df = df[df["proyecto_norm"].isin(project_filter_norms)].copy()

        df["num_unidad"] = df.apply(
            lambda r: extract_num_unidad(r.get("item_nombre"), r.get("codigo_item"), r.get("tipo_unidad_raw")),
            axis=1,
        )
        df["dni_comprador"] = df["dni_comprador"].fillna(df["documento_cliente"].apply(norm_doc_value))
        df["precio_base_proforma"] = to_num(df["precio_base_proforma"])
        df["precio_lista_al_comprar"] = to_num(df["precio_lista_al_comprar"])
        df["precio_venta"] = to_num(df["precio_venta"])

        return df.sort_values(["proyecto", "tipo_unidad", "num_unidad"], na_position="last").reset_index(drop=True)


# =========================================================
# Columnas calculadas flexibles
# =========================================================


CalculatedColumnFn = Callable[[pd.DataFrame], pd.Series]


class CalculatedColumnRegistry:
    """Catálogo de columnas calculadas disponibles para activar con --extra_columns."""

    def __init__(self):
        self._registry: dict[str, CalculatedColumnFn] = {}
        self.register_defaults()

    def register(self, name: str, fn: CalculatedColumnFn) -> None:
        self._registry[name] = fn

    def register_defaults(self) -> None:
        self.register(
            "flag_tiene_coprop",
            lambda df: df[["dni_coprop_1", "dni_coprop_2", "dni_coprop_3"]]
            .notna()
            .any(axis=1)
            .map({True: "Sí", False: "No"}),
        )
        self.register(
            "total_coprops",
            lambda df: df[["dni_coprop_1", "dni_coprop_2", "dni_coprop_3"]].notna().sum(axis=1),
        )
        self.register(
            "precio_venta_miles",
            lambda df: pd.to_numeric(df["precio_venta"], errors="coerce") / 1000,
        )

    def apply(self, df: pd.DataFrame, names: list[str]) -> pd.DataFrame:
        out = df.copy()
        for name in names:
            fn = self._registry.get(name)
            if fn is None:
                # No rompe el pipeline: crea columna vacía si aún no está registrada.
                if name not in out.columns:
                    out[name] = pd.NA
                continue
            out[name] = fn(out)
        return out

    def available(self) -> list[str]:
        return sorted(self._registry.keys())


class ColumnSelector:
    def __init__(self, columns: list[ColumnSpec]):
        self.columns = columns

    def select(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        for spec in self.columns:
            if spec.name not in out.columns:
                out[spec.name] = pd.NA
        return out[[spec.name for spec in self.columns]].reset_index(drop=True)

    def rename_for_display(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.rename(columns={spec.name: spec.display_name for spec in self.columns})


class SalesPipeline:
    def __init__(
        self,
        repository: ParquetRepository,
        transformer: SalesTransformer,
        calc_registry: CalculatedColumnRegistry,
        config: PipelineConfig,
    ):
        self.repository = repository
        self.transformer = transformer
        self.calc_registry = calc_registry
        self.config = config

    def run(self) -> pd.DataFrame:
        sources = self.repository.load()
        base = self.transformer.build_base(sources, project_filter=self.config.project_filter)
        enriched = self.calc_registry.apply(base, self.config.extra_calculated_columns)
        selected = ColumnSelector(self.config.columns).select(enriched)
        return selected


# =========================================================
# Excel aesthetic
# =========================================================


class AestheticExcelExporter:
    def __init__(self, config: PipelineConfig):
        self.config = config

    def write(self, df: pd.DataFrame) -> None:
        out_path = self.config.out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with pd.ExcelWriter(out_path, engine="xlsxwriter") as writer:
            workbook = writer.book
            formats = self._build_formats(workbook)

            self._write_summary(writer, workbook, formats, df)
            self._write_base(writer, workbook, formats, df)
            self._write_project_sheets(writer, workbook, formats, df)

    def _build_formats(self, workbook):
        return {
            "title": workbook.add_format({"bold": True, "font_size": 18, "font_color": "#FFFFFF", "bg_color": "#0B1F33", "align": "left", "valign": "vcenter"}),
            "subtitle": workbook.add_format({"font_size": 10, "font_color": "#475569", "align": "left", "valign": "vcenter"}),
            "kpi_label": workbook.add_format({"bold": True, "font_color": "#0B1F33", "bg_color": "#E2E8F0", "border": 1, "border_color": "#CBD5E1"}),
            "kpi_value": workbook.add_format({"bold": True, "font_color": "#111827", "bg_color": "#F8FAFC", "border": 1, "border_color": "#CBD5E1", "num_format": "#,##0"}),
            "money_kpi": workbook.add_format({"bold": True, "font_color": "#111827", "bg_color": "#F8FAFC", "border": 1, "border_color": "#CBD5E1", "num_format": "S/ #,##0.00"}),
            "header": workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#153B5C", "align": "center", "valign": "vcenter", "border": 1, "border_color": "#0B1F33"}),
            "money": workbook.add_format({"num_format": "S/ #,##0.00"}),
            "number": workbook.add_format({"num_format": "#,##0.00"}),
            "date": workbook.add_format({"num_format": "dd/mm/yyyy"}),
            "text": workbook.add_format({"font_color": "#111827"}),
            "alt": workbook.add_format({"bg_color": "#F8FAFC"}),
        }

    def _display_df(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.rename(columns=self.config.display_headers)

    def _write_summary(self, writer, workbook, formats, df: pd.DataFrame) -> None:
        if df.empty or "proyecto" not in df.columns:
            resumen = pd.DataFrame(columns=["proyecto", "items_vendidos", "compradores", "precio_venta_total"])
        else:
            agg_dict = {"items_vendidos": ("proyecto", "count")}
            if "dni_comprador" in df.columns:
                agg_dict["compradores"] = ("dni_comprador", pd.Series.nunique)
            if "precio_venta" in df.columns:
                agg_dict["precio_venta_total"] = ("precio_venta", "sum")
            resumen = df.groupby("proyecto", dropna=False).agg(**agg_dict).reset_index()
            if "compradores" not in resumen.columns:
                resumen["compradores"] = pd.NA
            if "precio_venta_total" not in resumen.columns:
                resumen["precio_venta_total"] = 0
            resumen = resumen.sort_values("precio_venta_total", ascending=False)

        resumen.to_excel(writer, sheet_name="00_RESUMEN", index=False, startrow=5)
        ws = writer.sheets["00_RESUMEN"]
        ws.merge_range("A1:D1", "Ventas por proyecto", formats["title"])
        ws.write("A2", "Resumen ejecutivo generado desde parquets locales", formats["subtitle"])
        ws.write("A4", "Total proyectos", formats["kpi_label"])
        ws.write("B4", resumen["proyecto"].nunique(dropna=True) if not resumen.empty else 0, formats["kpi_value"])
        ws.write("C4", "Total venta", formats["kpi_label"])
        ws.write("D4", float(pd.to_numeric(resumen["precio_venta_total"], errors="coerce").sum()) if not resumen.empty else 0, formats["money_kpi"])

        for col_num, value in enumerate(resumen.columns):
            ws.write(5, col_num, value, formats["header"])
        ws.set_column("A:A", 28, formats["text"])
        ws.set_column("B:C", 16, formats["text"])
        ws.set_column("D:D", 18, formats["money"])
        ws.freeze_panes(6, 0)
        if len(resumen) > 0:
            ws.autofilter(5, 0, 5 + len(resumen), len(resumen.columns) - 1)
            ws.conditional_format(6, 3, 5 + len(resumen), 3, {"type": "data_bar", "bar_color": "#D4AF37"})

    def _write_base(self, writer, workbook, formats, df: pd.DataFrame) -> None:
        base_export = self._display_df(df)
        base_export.to_excel(writer, sheet_name="99_BASE_COMPLETA", index=False, startrow=4)
        ws = writer.sheets["99_BASE_COMPLETA"]
        ws.merge_range(0, 0, 0, max(len(base_export.columns) - 1, 0), "Base completa de ventas", formats["title"])
        ws.write(1, 0, "Una fila por unidad vendida. Incluye las columnas configuradas.", formats["subtitle"])
        for col_num, value in enumerate(base_export.columns):
            ws.write(4, col_num, value, formats["header"])
        self._style_sales_sheet(ws, base_export, startrow=4, workbook=workbook, formats=formats)

    def _write_project_sheets(self, writer, workbook, formats, df: pd.DataFrame) -> None:
        if "proyecto" not in df.columns:
            return

        used_names = {"00_RESUMEN", "99_BASE_COMPLETA"}
        for proyecto, part in df.groupby("proyecto", dropna=False, sort=True):
            sheet_name = safe_sheet_name(str(proyecto) if pd.notna(proyecto) else "SIN_PROYECTO", used_names)
            project_cols = [c for c in df.columns if c != "proyecto"]
            part_export = self._display_df(part[project_cols])
            part_export.to_excel(writer, sheet_name=sheet_name, index=False, startrow=5)
            ws = writer.sheets[sheet_name]

            title = f"Ventas | {proyecto if pd.notna(proyecto) else 'SIN PROYECTO'}"
            ws.merge_range(0, 0, 0, max(len(part_export.columns) - 1, 0), title, formats["title"])
            ws.write(1, 0, "Detalle por unidad vendida", formats["subtitle"])
            ws.write(3, 0, "Items", formats["kpi_label"])
            ws.write(3, 1, len(part_export), formats["kpi_value"])
            ws.write(3, 2, "Total venta", formats["kpi_label"])
            total = float(pd.to_numeric(part["precio_venta"], errors="coerce").sum()) if "precio_venta" in part.columns else 0
            ws.write(3, 3, total, formats["money_kpi"])

            for col_num, value in enumerate(part_export.columns):
                ws.write(5, col_num, value, formats["header"])
            self._style_sales_sheet(ws, part_export, startrow=5, workbook=workbook, formats=formats)

    def _style_sales_sheet(self, ws, data: pd.DataFrame, startrow: int, workbook, formats) -> None:
        nrows = len(data)
        ncols = len(data.columns)
        if ncols == 0:
            return

        header_to_spec = {spec.display_name: spec for spec in self.config.columns}
        for idx, col in enumerate(data.columns):
            spec = header_to_spec.get(col)
            width = spec.width if spec else 16
            kind = spec.kind if spec else "text"
            fmt = formats.get(kind, formats["text"])
            ws.set_column(idx, idx, width, fmt)

        ws.freeze_panes(startrow + 1, 0)
        if nrows > 0:
            ws.autofilter(startrow, 0, startrow + nrows, ncols - 1)
            ws.conditional_format(startrow + 1, 0, startrow + nrows, ncols - 1, {"type": "formula", "criteria": "=MOD(ROW(),2)=0", "format": formats["alt"]})
            for money_col_name in ["Precio Lista al Comprar", "Precio Venta"]:
                if money_col_name in list(data.columns):
                    price_col = list(data.columns).index(money_col_name)
                    ws.conditional_format(startrow + 1, price_col, startrow + nrows, price_col, {"type": "data_bar", "bar_color": "#D4AF37"})


# =========================================================
# Main / CLI
# =========================================================


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw_dir", type=str, default="../data/raw")
    ap.add_argument("--out_dir", type=str, default="../data/exports")
    ap.add_argument("--output_name", type=str, default="ventas_por_proyecto.xlsx")
    ap.add_argument("--project_filter", nargs="*", default=None)
    ap.add_argument("--columns", nargs="*", default=None, help="Columnas exactas a exportar, en orden.")
    ap.add_argument("--extra_columns", nargs="*", default=None, help="Columnas calculadas o columnas opcionales a agregar al final.")
    ap.add_argument("--list_columns", action="store_true", help="Muestra columnas configurables y termina.")
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    calc_registry = CalculatedColumnRegistry()

    if args.list_columns:
        print("Columnas default:")
        for c in DEFAULT_COLUMNS:
            print(f"- {c.name}")
        print("\nColumnas opcionales:")
        for c in sorted(OPTIONAL_COLUMNS):
            print(f"- {c}")
        print("\nColumnas calculadas registradas:")
        for c in calc_registry.available():
            print(f"- {c}")
        return

    selected_specs = build_column_specs(args.columns, args.extra_columns)

    config = PipelineConfig(
        raw_dir=Path(args.raw_dir),
        out_dir=Path(args.out_dir),
        output_name=args.output_name,
        project_filter=args.project_filter,
        columns=selected_specs,
        extra_calculated_columns=args.extra_columns or [],
    )

    print("==============================================")
    print("Build Ventas por Proyecto Excel | OOP")
    print("==============================================")
    print(f"Raw dir: {config.raw_dir}")
    print(f"Columnas exportadas: {', '.join(config.final_column_names)}")

    pipeline = SalesPipeline(
        repository=ParquetRepository(config.raw_dir),
        transformer=SalesTransformer(),
        calc_registry=calc_registry,
        config=config,
    )

    final = pipeline.run()

    total_venta = float(pd.to_numeric(final["precio_venta"], errors="coerce").sum()) if "precio_venta" in final.columns and not final.empty else 0
    total_proyectos = final["proyecto"].nunique(dropna=True) if "proyecto" in final.columns and not final.empty else 0

    print(f"Filas venta/unidad: {len(final):,}")
    print(f"Proyectos: {total_proyectos:,}")
    print(f"Total venta: {total_venta:,.2f}")

    AestheticExcelExporter(config).write(final)

    print("OK ->", config.out_path.resolve())
    print("==============================================")


if __name__ == "__main__":
    main()
