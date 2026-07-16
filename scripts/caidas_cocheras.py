"""
Identifica procesos activos de estacionamientos/cocheras sin unidades asignadas.

Conversión mejorada del notebook:
    "Caidas de Cocheras que debieron caer.ipynb"

Ejemplo:
    python caidas_cocheras.py

Con rutas explícitas:
    python caidas_cocheras.py \
        --input data/raw/procesos.parquet \
        --output-dir data/processed/caidas_cocheras \
        --plots

Dependencias mínimas:
    pip install pandas

Dependencias según el formato:
    pip install pyarrow      # Parquet
    pip install openpyxl     # Excel
    pip install matplotlib   # Gráficos
"""

from __future__ import annotations

import argparse
import json
import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd


LOGGER = logging.getLogger("caidas_cocheras")

PROYECTOS_PREDETERMINADOS = (
    "TZ",
    "NP",
    "EEUU",
    "MD",
    "MA",
    "SL",
    "GY",
    "FX",
    "MT",
    "CP",
)

COLUMNAS_REQUERIDAS = (
    "estado",
    "codigo_proyecto",
    "nombre_proyecto",
    "tipo_unidad_principal",
    "codigo_unidades_asignadas",
)


@dataclass(frozen=True)
class Configuracion:
    """Parámetros principales del procesamiento."""

    archivo_procesos: Path
    carpeta_salida: Path
    proyectos: tuple[str, ...] = PROYECTOS_PREDETERMINADOS
    estado_activo: str = "Activo"
    formato_salida: str = "both"
    generar_graficos: bool = False
    mostrar_graficos: bool = False


def configurar_logging(verbose: bool = False) -> None:
    """Configura mensajes de ejecución legibles."""

    nivel = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=nivel,
        format="%(levelname)s | %(message)s",
    )


def obtener_raiz_proyecto() -> Path:
    """
    Obtiene una raíz razonable sin depender del directorio de ejecución.

    Si el archivo está dentro de ``scripts/``, usa su carpeta padre como raíz.
    En cualquier otro caso, usa la carpeta donde está este script.
    """

    carpeta_script = Path(__file__).resolve().parent
    return carpeta_script.parent if carpeta_script.name == "scripts" else carpeta_script


def quitar_acentos(texto: str) -> str:
    """Convierte, por ejemplo, 'depósito' en 'deposito'."""

    normalizado = unicodedata.normalize("NFKD", texto)
    return "".join(caracter for caracter in normalizado if not unicodedata.combining(caracter))


def normalizar_texto(serie: pd.Series) -> pd.Series:
    """Normaliza texto para comparaciones robustas: nulos, espacios, mayúsculas y tildes."""

    texto = serie.astype("string").fillna("").str.strip().str.casefold()
    return texto.map(quitar_acentos)


def mascara_vacio(serie: pd.Series) -> pd.Series:
    """Detecta nulos, cadenas vacías o cadenas compuestas solo por espacios."""

    return serie.isna() | serie.astype("string").str.strip().eq("").fillna(False)


def validar_columnas(df: pd.DataFrame, columnas: Iterable[str]) -> None:
    """Detiene la ejecución con un mensaje claro cuando faltan columnas."""

    faltantes = sorted(set(columnas) - set(df.columns))
    if faltantes:
        raise KeyError(
            "Faltan columnas requeridas en el archivo de procesos: "
            + ", ".join(faltantes)
        )


def filtrar_df(
    df: pd.DataFrame,
    condiciones: Mapping[str, Any],
    *,
    nombre: str = "filtro",
    mostrar_resumen: bool = True,
) -> pd.DataFrame:
    """
    Filtra un DataFrame usando un diccionario de condiciones.

    Formas admitidas:
        {"estado": "Activo"}
        {"codigo_proyecto": ["TZ", "NP"]}
        {"campo": {"op": "vacio"}}
        {"campo": {"op": "no_vacio"}}
        {"campo": {"op": "igual_ci", "valor": "activo"}}
        {"campo": {"op": "en_ci", "valores": ["TZ", "NP"]}}
        {"campo": {"op": "contiene_ci", "valor": "estacionamiento"}}

    ``_ci`` significa comparación normalizada, sin distinguir mayúsculas,
    espacios periféricos ni tildes.
    """

    validar_columnas(df, condiciones.keys())
    mascara = pd.Series(True, index=df.index, dtype=bool)

    for columna, condicion in condiciones.items():
        serie = df[columna]
        vacio = mascara_vacio(serie)

        if isinstance(condicion, Mapping):
            operador = condicion.get("op")

            if operador == "vacio":
                condicion_actual = vacio

            elif operador == "no_vacio":
                condicion_actual = ~vacio

            elif operador == "igual":
                condicion_actual = serie.eq(condicion.get("valor")).fillna(False)

            elif operador == "distinto":
                condicion_actual = serie.ne(condicion.get("valor")).fillna(False)

            elif operador == "en":
                condicion_actual = serie.isin(condicion.get("valores", []))

            elif operador == "no_en":
                condicion_actual = ~serie.isin(condicion.get("valores", []))

            elif operador == "igual_ci":
                valor = quitar_acentos(str(condicion.get("valor", "")).strip().casefold())
                condicion_actual = normalizar_texto(serie).eq(valor)

            elif operador == "en_ci":
                valores = {
                    quitar_acentos(str(valor).strip().casefold())
                    for valor in condicion.get("valores", [])
                }
                condicion_actual = normalizar_texto(serie).isin(valores)

            elif operador == "contiene_ci":
                valor = quitar_acentos(str(condicion.get("valor", "")).strip().casefold())
                condicion_actual = normalizar_texto(serie).str.contains(
                    valor,
                    regex=False,
                    na=False,
                )

            else:
                raise ValueError(
                    f"Operador no reconocido para '{columna}': {operador!r}"
                )

        elif isinstance(condicion, (list, tuple, set, frozenset)):
            condicion_actual = serie.isin(condicion)

        else:
            condicion_actual = serie.eq(condicion).fillna(False)

        mascara &= condicion_actual.fillna(False).astype(bool)

    resultado = df.loc[mascara].copy()

    if mostrar_resumen:
        antes = len(df)
        despues = len(resultado)
        porcentaje = (despues / antes * 100) if antes else 0.0
        LOGGER.info(
            "%s | antes=%s | después=%s | descartadas=%s | conservado=%.2f%%",
            nombre,
            f"{antes:,}",
            f"{despues:,}",
            f"{antes - despues:,}",
            porcentaje,
        )

    return resultado


def homologar_tipo_unidad(serie: pd.Series) -> pd.Series:
    """
    Agrupa los tipos de unidad sin fallar ante nulos o variaciones de texto.

    Se conserva una categoría ``otro`` para valores no reconocidos, en lugar
    de descartarlos silenciosamente.
    """

    tipo = normalizar_texto(serie)
    resultado = pd.Series("otro", index=serie.index, dtype="string")

    reglas = {
        "departamento": "departamento",
        "estacionamiento": "estacionamiento",
        "cochera": "estacionamiento",
        "deposito": "deposito",
        "local comercial": "local comercial",
    }

    # El orden importa: la primera coincidencia válida se conserva.
    sin_clasificar = resultado.eq("otro")
    for prefijo, categoria in reglas.items():
        coincide = tipo.str.startswith(prefijo, na=False) & sin_clasificar
        resultado.loc[coincide] = categoria
        sin_clasificar = resultado.eq("otro")

    return resultado


def leer_procesos(ruta: Path) -> pd.DataFrame:
    """Lee procesos desde Parquet, CSV o Excel y valida su estructura."""

    ruta = ruta.expanduser().resolve()
    if not ruta.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {ruta}")

    extension = ruta.suffix.lower()
    LOGGER.info("Leyendo procesos desde: %s", ruta)

    try:
        if extension == ".parquet":
            df = pd.read_parquet(ruta)
        elif extension == ".csv":
            df = pd.read_csv(ruta, low_memory=False)
        elif extension in {".xlsx", ".xls"}:
            df = pd.read_excel(ruta)
        else:
            raise ValueError(
                "Formato de entrada no admitido. Usa .parquet, .csv, .xlsx o .xls"
            )
    except ImportError as exc:
        if extension == ".parquet":
            mensaje = "Para leer Parquet instala pyarrow: pip install pyarrow"
        elif extension in {".xlsx", ".xls"}:
            mensaje = "Para leer Excel instala openpyxl: pip install openpyxl"
        else:
            mensaje = str(exc)
        raise RuntimeError(mensaje) from exc

    validar_columnas(df, COLUMNAS_REQUERIDAS)
    LOGGER.info(
        "Procesos cargados: %s filas y %s columnas",
        f"{len(df):,}",
        len(df.columns),
    )
    return df


def preparar_procesos(
    df_procesos: pd.DataFrame,
    *,
    proyectos: Iterable[str],
    estado_activo: str = "Activo",
) -> pd.DataFrame:
    """Selecciona el universo activo y crea el tipo de unidad homologado."""

    proyectos_limpios = tuple(
        dict.fromkeys(
            str(proyecto).strip().upper()
            for proyecto in proyectos
            if str(proyecto).strip()
        )
    )
    if not proyectos_limpios:
        raise ValueError("La lista de proyectos no puede estar vacía.")

    procesos = filtrar_df(
        df_procesos,
        {
            "estado": {"op": "igual_ci", "valor": estado_activo},
            "codigo_proyecto": {"op": "en_ci", "valores": proyectos_limpios},
        },
        nombre="proyectos activos relevantes",
    )

    procesos["tipo_unidad_homologado"] = homologar_tipo_unidad(
        procesos["tipo_unidad_principal"]
    )

    LOGGER.info(
        "Tipos homologados:\n%s",
        procesos["tipo_unidad_homologado"]
        .value_counts(dropna=False)
        .to_string(),
    )
    return procesos


def obtener_cocheras_en_riesgo(procesos: pd.DataFrame) -> pd.DataFrame:
    """
    Obtiene estacionamientos activos sin código de unidades asignadas.

    Esta función replica la regla presente en el notebook original. No infiere
    reglas adicionales de negocio para decidir si una separación debe caer.
    """

    return filtrar_df(
        procesos,
        {
            "tipo_unidad_homologado": "estacionamiento",
            "codigo_unidades_asignadas": {"op": "vacio"},
        },
        nombre="cocheras activas sin unidad asignada",
    )


def obtener_departamentos_activos(procesos: pd.DataFrame) -> pd.DataFrame:
    """Genera el universo de departamentos activos como archivo de contraste."""

    return filtrar_df(
        procesos,
        {"tipo_unidad_homologado": "departamento"},
        nombre="departamentos activos",
    )


def exportar_dataframe(
    df: pd.DataFrame,
    ruta_base: Path,
    formato: str,
) -> list[Path]:
    """Exporta un resultado en CSV, Parquet o ambos formatos."""

    rutas: list[Path] = []

    if formato in {"csv", "both"}:
        ruta_csv = ruta_base.with_suffix(".csv")
        df.to_csv(ruta_csv, index=False, encoding="utf-8-sig")
        rutas.append(ruta_csv)

    if formato in {"parquet", "both"}:
        ruta_parquet = ruta_base.with_suffix(".parquet")
        try:
            df.to_parquet(ruta_parquet, index=False)
        except ImportError as exc:
            raise RuntimeError(
                "Para exportar Parquet instala pyarrow: pip install pyarrow"
            ) from exc
        rutas.append(ruta_parquet)

    for ruta in rutas:
        LOGGER.info("Archivo generado: %s", ruta)

    return rutas


def guardar_resumen(
    *,
    carpeta_salida: Path,
    procesos_originales: pd.DataFrame,
    procesos_relevantes: pd.DataFrame,
    cocheras_en_riesgo: pd.DataFrame,
    departamentos_activos: pd.DataFrame,
    proyectos: Iterable[str],
) -> Path:
    """Guarda métricas de auditoría para revisar qué produjo cada ejecución."""

    resumen = {
        "filas_procesos_originales": int(len(procesos_originales)),
        "filas_procesos_activos_relevantes": int(len(procesos_relevantes)),
        "filas_cocheras_activas_sin_unidad_asignada": int(len(cocheras_en_riesgo)),
        "filas_departamentos_activos": int(len(departamentos_activos)),
        "proyectos_considerados": list(proyectos),
        "cocheras_por_proyecto": {
            str(clave): int(valor)
            for clave, valor in cocheras_en_riesgo["nombre_proyecto"]
            .value_counts(dropna=False)
            .items()
        },
    }

    ruta = carpeta_salida / "resumen_ejecucion.json"
    ruta.write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    LOGGER.info("Resumen generado: %s", ruta)
    return ruta


def generar_graficos(
    procesos: pd.DataFrame,
    cocheras_en_riesgo: pd.DataFrame,
    carpeta_salida: Path,
    *,
    mostrar: bool = False,
) -> list[Path]:
    """Genera gráficos livianos y los cierra para evitar consumo innecesario."""

    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError(
            "Para generar gráficos instala matplotlib: pip install matplotlib"
        ) from exc

    rutas: list[Path] = []

    conteo_proyectos = procesos["nombre_proyecto"].value_counts().sort_values()
    if not conteo_proyectos.empty:
        alto = max(4.0, min(10.0, 0.42 * len(conteo_proyectos)))
        fig, ax = plt.subplots(figsize=(12, alto))
        conteo_proyectos.plot(kind="barh", ax=ax)
        ax.set_title("Cantidad de procesos activos por proyecto")
        ax.set_xlabel("Procesos")
        ax.set_ylabel("Proyecto")
        fig.tight_layout()
        ruta = carpeta_salida / "procesos_activos_por_proyecto.png"
        fig.savefig(ruta, dpi=150, bbox_inches="tight")
        rutas.append(ruta)
        if mostrar:
            plt.show()
        plt.close(fig)

    conteo_riesgo = cocheras_en_riesgo["nombre_proyecto"].value_counts().sort_values()
    if not conteo_riesgo.empty:
        alto = max(4.0, min(10.0, 0.42 * len(conteo_riesgo)))
        fig, ax = plt.subplots(figsize=(12, alto))
        conteo_riesgo.plot(kind="barh", ax=ax)
        ax.set_title("Cocheras activas sin unidad asignada por proyecto")
        ax.set_xlabel("Cocheras")
        ax.set_ylabel("Proyecto")
        fig.tight_layout()
        ruta = carpeta_salida / "cocheras_en_riesgo_por_proyecto.png"
        fig.savefig(ruta, dpi=150, bbox_inches="tight")
        rutas.append(ruta)
        if mostrar:
            plt.show()
        plt.close(fig)

    for ruta in rutas:
        LOGGER.info("Gráfico generado: %s", ruta)

    if not rutas:
        LOGGER.warning("No había datos suficientes para generar gráficos.")

    return rutas


def ejecutar(config: Configuracion) -> dict[str, pd.DataFrame]:
    """Ejecuta el flujo completo y devuelve los DataFrames principales."""

    config.carpeta_salida.mkdir(parents=True, exist_ok=True)

    procesos_originales = leer_procesos(config.archivo_procesos)
    procesos_relevantes = preparar_procesos(
        procesos_originales,
        proyectos=config.proyectos,
        estado_activo=config.estado_activo,
    )
    cocheras_en_riesgo = obtener_cocheras_en_riesgo(procesos_relevantes)
    departamentos_activos = obtener_departamentos_activos(procesos_relevantes)

    exportar_dataframe(
        cocheras_en_riesgo,
        config.carpeta_salida / "cocheras_activas_sin_unidad_asignada",
        config.formato_salida,
    )
    exportar_dataframe(
        departamentos_activos,
        config.carpeta_salida / "departamentos_activos",
        config.formato_salida,
    )

    guardar_resumen(
        carpeta_salida=config.carpeta_salida,
        procesos_originales=procesos_originales,
        procesos_relevantes=procesos_relevantes,
        cocheras_en_riesgo=cocheras_en_riesgo,
        departamentos_activos=departamentos_activos,
        proyectos=config.proyectos,
    )

    if config.generar_graficos:
        generar_graficos(
            procesos_relevantes,
            cocheras_en_riesgo,
            config.carpeta_salida,
            mostrar=config.mostrar_graficos,
        )

    LOGGER.info(
        "Ejecución finalizada | cocheras en riesgo=%s | departamentos activos=%s",
        f"{len(cocheras_en_riesgo):,}",
        f"{len(departamentos_activos):,}",
    )

    return {
        "procesos_relevantes": procesos_relevantes,
        "cocheras_en_riesgo": cocheras_en_riesgo,
        "departamentos_activos": departamentos_activos,
    }


def construir_parser() -> argparse.ArgumentParser:
    """Crea la interfaz de línea de comandos."""

    raiz = obtener_raiz_proyecto()

    parser = argparse.ArgumentParser(
        description=(
            "Identifica cocheras/estacionamientos activos sin unidades asignadas "
            "y genera archivos auditables."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=raiz / "data" / "raw" / "procesos.parquet",
        help="Ruta del archivo de procesos (.parquet, .csv o .xlsx).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=raiz / "data" / "processed" / "caidas_cocheras",
        help="Carpeta donde se guardarán los resultados.",
    )
    parser.add_argument(
        "--projects",
        nargs="+",
        default=list(PROYECTOS_PREDETERMINADOS),
        help="Códigos de proyecto separados por espacios.",
    )
    parser.add_argument(
        "--active-status",
        default="Activo",
        help="Valor que representa un proceso activo.",
    )
    parser.add_argument(
        "--format",
        choices=("csv", "parquet", "both"),
        default="both",
        help="Formato de exportación.",
    )
    parser.add_argument(
        "--plots",
        action="store_true",
        help="Genera gráficos PNG en la carpeta de salida.",
    )
    parser.add_argument(
        "--show-plots",
        action="store_true",
        help="Muestra además los gráficos en pantalla; implica --plots.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Muestra mensajes de depuración.",
    )
    return parser


def main() -> None:
    """Punto de entrada del script."""

    parser = construir_parser()
    args = parser.parse_args()
    configurar_logging(args.verbose)

    config = Configuracion(
        archivo_procesos=args.input,
        carpeta_salida=args.output_dir,
        proyectos=tuple(args.projects),
        estado_activo=args.active_status,
        formato_salida=args.format,
        generar_graficos=args.plots or args.show_plots,
        mostrar_graficos=args.show_plots,
    )

    try:
        ejecutar(config)
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as exc:
        LOGGER.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
