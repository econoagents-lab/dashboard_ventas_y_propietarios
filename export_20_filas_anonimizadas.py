from pathlib import Path
import pandas as pd
import hashlib

# =========================
# CONFIG
# =========================
RAW_DIR = Path("data/raw")
N_ROWS = 20

# Exporta los CSV en la misma carpeta data/raw
OUTPUT_SUFFIX = "_sample_20_anon.csv"

# Columnas sensibles típicas
SENSITIVE_PATTERNS = [
    "dni",
    "documento",
    "ruc",
    "telefono",
    "celular",
    "phone",
    "email",
    "correo",
    "nombre_cliente",
    "nombres",
    "apellidos",
    "cliente",
    "direccion",
    "address",
]


def hash_value(value):
    """
    Convierte valores sensibles en un identificador anónimo estable.
    """
    if pd.isna(value):
        return value

    text = str(value).strip()

    if text == "":
        return text

    return "anon_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def is_sensitive_column(column_name: str) -> bool:
    """
    Detecta si una columna debe anonimizarse según su nombre.
    """
    col = column_name.lower()
    return any(pattern in col for pattern in SENSITIVE_PATTERNS)


def anonymize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Anonimiza columnas sensibles detectadas por nombre.
    """
    df = df.copy()

    for col in df.columns:
        if is_sensitive_column(col):
            df[col] = df[col].apply(hash_value)

    return df


def export_samples():
    parquet_files = sorted(RAW_DIR.glob("*.parquet"))

    if not parquet_files:
        print(f"No se encontraron archivos .parquet en: {RAW_DIR.resolve()}")
        return

    print("==============================================")
    print(" EXPORTANDO 20 FILAS ANONIMIZADAS POR TABLA")
    print("==============================================")
    print(f"Carpeta origen/destino: {RAW_DIR.resolve()}")
    print("")

    for parquet_path in parquet_files:
        try:
            print(f"Procesando: {parquet_path.name}")

            df = pd.read_parquet(parquet_path)

            sample_df = df.head(N_ROWS)
            sample_anon = anonymize_dataframe(sample_df)

            output_path = RAW_DIR / f"{parquet_path.stem}{OUTPUT_SUFFIX}"

            sample_anon.to_csv(
                output_path,
                index=False,
                encoding="utf-8-sig"
            )

            print(f"  OK -> {output_path.name} ({len(sample_anon)} filas)")

        except Exception as e:
            print(f"  ERROR procesando {parquet_path.name}: {e}")

    print("")
    print("Proceso terminado.")


if __name__ == "__main__":
    export_samples()