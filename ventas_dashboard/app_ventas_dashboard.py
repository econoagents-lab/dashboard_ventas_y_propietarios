"""
app_ventas_dashboard.py

Dashboard Streamlit para visualizar ventas por proyecto y descargar el Excel aesthetic.

Coloca este archivo en la misma carpeta que:
- build_ventas_por_proyecto_excel_oop.py

Estructura esperada:
project_root/
├─ app_ventas_dashboard.py
├─ build_ventas_por_proyecto_excel_oop.py
└─ data/
   └─ raw/
      ├─ procesos.parquet
      ├─ clientes.parquet
      ├─ proyectos.parquet
      └─ unidades.parquet

Run local:
streamlit run app_ventas_dashboard.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from build_ventas_por_proyecto_excel_oop import (
    DEFAULT_COLUMNS,
    OPTIONAL_COLUMNS,
    AestheticExcelExporter,
    CalculatedColumnRegistry,
    ParquetRepository,
    PipelineConfig,
    SalesPipeline,
    SalesTransformer,
    build_column_specs,
)


# =========================================================
# Config visual
# =========================================================

st.set_page_config(
    page_title="Ventas por Proyecto | BI Inmobiliario",
    page_icon="🏗️",
    layout="wide",
)

st.markdown(
    """
    <style>
    .main .block-container {padding-top: 1.5rem; padding-bottom: 2rem;}
    div[data-testid="stMetric"] {
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        padding: 14px 16px;
        border-radius: 14px;
    }
    .small-note {color:#64748B; font-size:0.88rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# Helpers
# =========================================================


def money_fmt(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "S/ 0"
    return f"S/ {float(value):,.0f}"


def number_fmt(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "0"
    return f"{float(value):,.0f}"


def available_column_names() -> list[str]:
    base = [c.name for c in DEFAULT_COLUMNS]
    optional = [c for c in OPTIONAL_COLUMNS.keys() if c not in base]
    calculated = [c for c in CalculatedColumnRegistry().available() if c not in base and c not in optional]
    return base + optional + calculated


@st.cache_data(show_spinner=False)
def run_pipeline(raw_dir: str, selected_columns: tuple[str, ...], project_filter: tuple[str, ...]) -> pd.DataFrame:
    calc_registry = CalculatedColumnRegistry()
    calculated_names = set(calc_registry.available())
    extra_calculated_columns = [c for c in selected_columns if c in calculated_names]

    config = PipelineConfig(
        raw_dir=Path(raw_dir),
        out_dir=Path("../data/exports"),
        output_name="ventas_por_proyecto.xlsx",
        project_filter=list(project_filter) if project_filter else None,
        columns=build_column_specs(list(selected_columns), extra_columns=None),
        extra_calculated_columns=extra_calculated_columns,
    )

    pipeline = SalesPipeline(
        repository=ParquetRepository(config.raw_dir),
        transformer=SalesTransformer(),
        calc_registry=calc_registry,
        config=config,
    )
    return pipeline.run()


def apply_ui_filters(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    with st.sidebar:
        st.markdown("### Filtros de dashboard")

        if "proyecto" in out.columns:
            proyectos = sorted([x for x in out["proyecto"].dropna().unique()])
            selected = st.multiselect("Proyecto", proyectos, default=proyectos)
            if selected:
                out = out[out["proyecto"].isin(selected)]

        if "tipo_unidad" in out.columns:
            tipos = sorted([x for x in out["tipo_unidad"].dropna().unique()])
            selected = st.multiselect("Tipo de unidad", tipos, default=tipos)
            if selected:
                out = out[out["tipo_unidad"].isin(selected)]

        if "flag_tiene_coprop" in out.columns:
            flags = sorted([x for x in out["flag_tiene_coprop"].dropna().unique()])
            selected = st.multiselect("Tiene copropietario", flags, default=flags)
            if selected:
                out = out[out["flag_tiene_coprop"].isin(selected)]

        if "comprador" in out.columns:
            search = st.text_input("Buscar comprador / DNI / unidad", "")
            if search.strip():
                pattern = search.strip().casefold()
                joined = out.astype(str).agg(" ".join, axis=1).str.casefold()
                out = out[joined.str.contains(pattern, regex=False)]

    return out


def make_excel_bytes(df: pd.DataFrame, selected_columns: list[str]) -> bytes:
    calc_registry = CalculatedColumnRegistry()
    calc_names = set(calc_registry.available())
    config = PipelineConfig(
        raw_dir=Path("../data/raw"),
        out_dir=Path(tempfile.mkdtemp()),
        output_name="ventas_por_proyecto_dashboard.xlsx",
        columns=build_column_specs(selected_columns, extra_columns=None),
        extra_calculated_columns=[c for c in selected_columns if c in calc_names],
    )
    exporter = AestheticExcelExporter(config)
    exporter.write(df)
    return config.out_path.read_bytes()


# =========================================================
# UI principal
# =========================================================

st.title("🏗️ Ventas por proyecto")
st.caption("Dashboard operativo desde parquets locales + descarga Excel aesthetic")

with st.sidebar:
    st.markdown("## Configuración")
    raw_dir = st.text_input("Carpeta raw", value="../data/raw")

    all_columns = available_column_names()
    default_columns = [c.name for c in DEFAULT_COLUMNS]
    selected_columns = st.multiselect(
        "Columnas a cargar/exportar",
        options=all_columns,
        default=default_columns,
        help="Puedes agregar columnas opcionales o calculadas sin tocar el código principal.",
    )

    project_filter_text = st.text_input(
        "Filtro inicial de proyectos (opcional)",
        value="",
        help="Separar por coma. Ejemplo: SIALIA,Torre Nápoles",
    )
    project_filter = tuple([x.strip() for x in project_filter_text.split(",") if x.strip()])

    load_clicked = st.button("Actualizar dashboard", type="primary", use_container_width=True)

if not selected_columns:
    st.warning("Selecciona al menos una columna para cargar el dashboard.")
    st.stop()

try:
    df = run_pipeline(raw_dir, tuple(selected_columns), project_filter)
except Exception as exc:
    st.error("No pude construir el dashboard con la carpeta/raw indicada.")
    st.exception(exc)
    st.stop()

if df.empty:
    st.info("El pipeline corrió, pero no encontró ventas con los filtros actuales.")
    st.stop()

filtered = apply_ui_filters(df)

# KPIs
precio_total = pd.to_numeric(filtered.get("precio_venta", pd.Series(dtype=float)), errors="coerce").sum()
items = len(filtered)
proyectos = filtered["proyecto"].nunique(dropna=True) if "proyecto" in filtered.columns else 0
compradores = filtered["dni_comprador"].nunique(dropna=True) if "dni_comprador" in filtered.columns else 0

k1, k2, k3, k4 = st.columns(4)
k1.metric("Total venta", money_fmt(precio_total))
k2.metric("Items vendidos", number_fmt(items))
k3.metric("Proyectos", number_fmt(proyectos))
k4.metric("Compradores", number_fmt(compradores))

st.divider()

# Charts
chart_col1, chart_col2 = st.columns((1.3, 1))

with chart_col1:
    st.subheader("Venta por proyecto")
    if {"proyecto", "precio_venta"}.issubset(filtered.columns):
        by_project = (
            filtered.assign(precio_venta=pd.to_numeric(filtered["precio_venta"], errors="coerce"))
            .groupby("proyecto", dropna=False, as_index=False)
            .agg(total_venta=("precio_venta", "sum"), items=("proyecto", "count"))
            .sort_values("total_venta", ascending=True)
        )
        fig = px.bar(
            by_project,
            x="total_venta",
            y="proyecto",
            orientation="h",
            text="items",
            labels={"total_venta": "Total venta", "proyecto": "Proyecto", "items": "Items"},
        )
        fig.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Agrega las columnas proyecto y precio_venta para este gráfico.")

with chart_col2:
    st.subheader("Mix por tipo de unidad")
    if "tipo_unidad" in filtered.columns:
        by_type = filtered.groupby("tipo_unidad", dropna=False).size().reset_index(name="items")
        fig = px.pie(by_type, names="tipo_unidad", values="items", hole=0.45)
        fig.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Agrega tipo_unidad para este gráfico.")

st.subheader("Detalle filtrado")
st.dataframe(filtered, use_container_width=True, hide_index=True)

excel_bytes = make_excel_bytes(filtered, list(selected_columns))
st.download_button(
    label="⬇️ Descargar Excel aesthetic",
    data=excel_bytes,
    file_name="ventas_por_proyecto_dashboard.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)

st.markdown(
    "<p class='small-note'>Tip: lo que descargas respeta los filtros y columnas activos del dashboard.</p>",
    unsafe_allow_html=True,
)
