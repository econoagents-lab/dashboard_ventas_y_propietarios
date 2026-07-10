"""
app_ventas_dashboard_stock.py

Dashboard Streamlit con dos pestañas:
1) Propietarios / Ventas: universo solo vendido.
2) Stock Completo: universo completo desde unidades + left merge de propietarios.

Run:
streamlit run app_ventas_dashboard_stock.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

APP_VERSION = "v7_excel_currency_format_fix"

from medallion_stock_pipeline import (  # noqa: E402
    AestheticStockExcelExporter,
    MedallionConfig,
    MedallionStockPipeline,
    STOCK_FINAL_COLS,
    STOCK_DISPLAY_HEADERS,
)


st.set_page_config(
    page_title="Stock Completo | BI Inmobiliario",
    page_icon="🏗️",
    layout="wide",
)

st.markdown(
    """
    <style>
    .main .block-container {padding-top: 1.4rem; padding-bottom: 2rem;}
    div[data-testid="stMetric"] {
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        padding: 14px 16px;
        border-radius: 14px;
    }
    .layer-card {
        border:1px solid #E2E8F0;
        border-radius:14px;
        padding:12px 14px;
        background:#F8FAFC;
    }
    .small-note {color:#64748B; font-size:0.88rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def money_fmt(value) -> str:
    if value is None or pd.isna(value):
        return "S/ 0"
    return f"S/ {float(value):,.0f}"


def number_fmt(value) -> str:
    if value is None or pd.isna(value):
        return "0"
    return f"{float(value):,.0f}"


@st.cache_data(show_spinner=False)
def build_or_load(
    raw_dir: str,
    bronze_dir: str,
    silver_dir: str,
    gold_dir: str,
    mode: str,
    app_version: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    config = MedallionConfig(
        raw_dir=Path(raw_dir),
        bronze_dir=Path(bronze_dir),
        silver_dir=Path(silver_dir),
        gold_dir=Path(gold_dir),
        exports_dir=Path("data/exports"),
    )
    pipeline = MedallionStockPipeline(config)
    if mode == "Reconstruir bronze + silver + gold":
        return pipeline.build_all()
    if mode == "Reconstruir gold desde silver existente":
        return pipeline.build_gold_from_existing_silver()
    return pipeline.load_gold()


def filter_stock(df: pd.DataFrame, key_prefix: str) -> pd.DataFrame:
    out = df.copy()
    if "proyecto" in out.columns:
        proyectos = sorted([x for x in out["proyecto"].dropna().unique()])
        selected = st.multiselect("Proyecto", proyectos, default=proyectos, key=f"{key_prefix}_proyecto")
        if selected:
            out = out[out["proyecto"].isin(selected)]
    if "estado_comercial" in out.columns:
        estados = sorted([x for x in out["estado_comercial"].dropna().unique()])
        selected = st.multiselect("Estado comercial", estados, default=estados, key=f"{key_prefix}_estado")
        if selected:
            out = out[out["estado_comercial"].isin(selected)]
    if "tipo_de_unidad" in out.columns:
        tipos = sorted([x for x in out["tipo_de_unidad"].dropna().unique()])
        selected = st.multiselect("Tipo de unidad", tipos, default=tipos, key=f"{key_prefix}_tipo")
        if selected:
            out = out[out["tipo_de_unidad"].isin(selected)]
    search = st.text_input("Buscar unidad / código / comprador / DNI", "", key=f"{key_prefix}_search")
    if search.strip():
        pattern = search.strip().casefold()
        joined = out.astype(str).agg(" ".join, axis=1).str.casefold()
        out = out[joined.str.contains(pattern, regex=False)]
    return out


def make_excel_bytes(stock: pd.DataFrame, owners: pd.DataFrame) -> bytes:
    tmp_dir = Path(tempfile.mkdtemp())
    config = MedallionConfig(
        exports_dir=tmp_dir,
        output_name="stock_unidades_completo_dashboard.xlsx",
    )
    AestheticStockExcelExporter(config).write(stock, owners)
    return config.excel_path.read_bytes()


def display_stock_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df[[c for c in STOCK_FINAL_COLS if c in df.columns]].rename(columns=STOCK_DISPLAY_HEADERS).copy()
    for c in out.columns:
        if not pd.api.types.is_numeric_dtype(out[c]) and not pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].astype("string").replace({"<NA>": pd.NA, "None": pd.NA, "nan": pd.NA}).fillna("")
    return out


st.title("🏗️ Dashboard inmobiliario — stock completo + propietarios")
st.caption(f"End-to-end desde bronze/silver/gold. Universo maestro: unidades.parquet. App {APP_VERSION}")

with st.sidebar:
    st.markdown("## Capas medallion")
    raw_dir = st.text_input("RAW dir", value="data/raw")
    bronze_dir = st.text_input("BRONZE dir", value="data/bronze")
    silver_dir = st.text_input("SILVER dir", value="data/silver")
    gold_dir = st.text_input("GOLD dir", value="data/gold")
    mode = st.radio(
        "Modo de ejecución",
        [
            "Reconstruir bronze + silver + gold",
            "Reconstruir gold desde silver existente",
            "Leer gold existente",
        ],
        index=0,
    )
    run_clicked = st.button("Actualizar capas/dashboard", type="primary", use_container_width=True)
    if run_clicked:
        st.cache_data.clear()

try:
    owners, stock = build_or_load(raw_dir, bronze_dir, silver_dir, gold_dir, mode, APP_VERSION)
except Exception as exc:
    st.error("No pude construir o leer las capas medallion.")
    st.exception(exc)
    st.stop()

if stock.empty:
    st.info("El mart de stock está vacío.")
    st.stop()

c1, c2, c3 = st.columns(3)
c1.markdown("<div class='layer-card'><b>Bronze</b><br>Snapshot crudo desde parquets base.</div>", unsafe_allow_html=True)
c2.markdown("<div class='layer-card'><b>Silver</b><br>Unidades, proyectos, propietarios y ventas normalizadas.</div>", unsafe_allow_html=True)
c3.markdown("<div class='layer-card'><b>Gold</b><br>Dos marts: propietarios y stock completo.</div>", unsafe_allow_html=True)

st.divider()

# Diagnóstico rápido para confirmar que estás viendo el universo completo y no solo propietarios.
stock_rows = len(stock)
owners_rows = len(owners)
status_counts = stock["estado_comercial"].value_counts(dropna=False).to_dict() if "estado_comercial" in stock.columns else {}
with st.expander("🔎 Diagnóstico de universo cargado", expanded=False):
    d1, d2, d3 = st.columns(3)
    d1.metric("Filas en stock gold", number_fmt(stock_rows))
    d2.metric("Filas con propietarios", number_fmt(owners_rows))
    d3.metric("Diferencia stock - propietarios", number_fmt(stock_rows - owners_rows))
    st.write("Distribución Estado comercial:", status_counts)
    st.caption("Si stock = propietarios y no aparece Disponible, entonces data/raw/unidades.parquet probablemente no contiene el universo completo de unidades libres, o estás corriendo una app antigua.")

tab_stock, tab_owners = st.tabs(["📦 Stock completo", "👥 Propietarios / ventas"])

with tab_stock:
    st.subheader("📦 Stock completo de unidades")
    with st.expander("Filtros de stock", expanded=True):
        filtered_stock = filter_stock(stock, "stock")

    total_unidades = len(filtered_stock)
    disponibles = int((filtered_stock["estado_comercial"] == "Disponible").sum()) if "estado_comercial" in filtered_stock else 0
    vendidas = int((filtered_stock["estado_comercial"] == "Vendido").sum()) if "estado_comercial" in filtered_stock else 0
    valor_lista = pd.to_numeric(filtered_stock.get("precio_de_lista_actual", pd.Series(dtype=float)), errors="coerce").sum()
    valor_disponible = pd.to_numeric(filtered_stock.loc[filtered_stock.get("estado_comercial") == "Disponible", "precio_de_lista_actual"], errors="coerce").sum() if "estado_comercial" in filtered_stock and "precio_de_lista_actual" in filtered_stock else 0

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total unidades", number_fmt(total_unidades))
    k2.metric("Disponibles", number_fmt(disponibles))
    k3.metric("Vendidas", number_fmt(vendidas))
    k4.metric("Valor lista actual", money_fmt(valor_lista))
    k5.metric("Valor disponible", money_fmt(valor_disponible))

    chart_col1, chart_col2 = st.columns((1.3, 1))
    with chart_col1:
        if {"proyecto", "codigo"}.issubset(filtered_stock.columns):
            by_project = (
                filtered_stock.groupby(["proyecto", "estado_comercial"], dropna=False)
                .agg(unidades=("codigo", "count"))
                .reset_index()
            )
            fig = px.bar(
                by_project,
                x="unidades",
                y="proyecto",
                color="estado_comercial",
                orientation="h",
                labels={"unidades": "Unidades", "proyecto": "Proyecto", "estado_comercial": "Estado"},
            )
            fig.update_layout(height=460, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)
    with chart_col2:
        if "estado_comercial" in filtered_stock.columns:
            by_status = filtered_stock.groupby("estado_comercial", dropna=False).size().reset_index(name="unidades")
            fig = px.pie(by_status, names="estado_comercial", values="unidades", hole=0.45)
            fig.update_layout(height=460, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)

    if "estado_comercial" in filtered_stock.columns and (filtered_stock["estado_comercial"] == "Disponible").sum() == 0:
        st.warning("No hay filas con Estado comercial = Disponible en este resultado. Verifica que estés en la pestaña 📦 Stock completo y que data/raw/unidades.parquet sea el universo completo, no solo unidades vendidas.")

    st.markdown("### Detalle filtrado — stock completo")
    st.dataframe(display_stock_table(filtered_stock), use_container_width=True, hide_index=True)

with tab_owners:
    st.subheader("👥 Propietarios / ventas")
    owners_for_tab = owners.copy()
    if not owners_for_tab.empty:
        # Agregamos estado para reusar filtros visuales mínimos si hace falta.
        owners_for_tab["estado_comercial"] = "Vendido"
        with st.expander("Filtros de propietarios", expanded=True):
            filtered_owners = filter_stock(owners_for_tab, "owners")
        k1, k2, k3 = st.columns(3)
        k1.metric("Unidades con propietario", number_fmt(len(filtered_owners)))
        k2.metric("Compradores", number_fmt(filtered_owners["dni_comprador"].nunique(dropna=True) if "dni_comprador" in filtered_owners else 0))
        k3.metric("Total venta", money_fmt(pd.to_numeric(filtered_owners.get("precio_venta", pd.Series(dtype=float)), errors="coerce").sum()))
        st.markdown("### Detalle filtrado — solo propietarios / ventas")
        owners_display = filtered_owners.copy()
        for c in owners_display.columns:
            if not pd.api.types.is_numeric_dtype(owners_display[c]) and not pd.api.types.is_datetime64_any_dtype(owners_display[c]):
                owners_display[c] = owners_display[c].astype("string").replace({"<NA>": pd.NA, "None": pd.NA, "nan": pd.NA}).fillna("")
        st.dataframe(owners_display, use_container_width=True, hide_index=True)
    else:
        filtered_owners = owners_for_tab
        st.info("No hay propietarios/ventas en el gold mart.")

st.divider()

# Descarga Excel: usa los mismos dataframes filtrados que ve el usuario en pantalla.
# La exportación corrige el formato monetario usando "S/" como texto literal,
# para que Excel no convierta los montos a fechas.
excel_bytes = make_excel_bytes(filtered_stock, filtered_owners)
st.download_button(
    label="⬇️ Descargar Excel aesthetic filtrado",
    data=excel_bytes,
    file_name="stock_unidades_completo_dashboard.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)

st.markdown("<p class='small-note'>El Excel respeta los filtros activos y conserva los montos como números, no como fechas.</p>", unsafe_allow_html=True)
