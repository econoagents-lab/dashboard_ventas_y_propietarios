from pathlib import Path
import pandas as pd
import re


# =========================
# CONFIGURACIÓN
# =========================

#INPUT_FILE = Path("../data/raw/leads_crm.xlsx")
INPUT_FILE = Path("../data/raw/clientes_proyectos.parquet")

OUTPUT_FILE = Path("outputs/auditoria_leads_alicanto_mayo.xlsx")

PROJECT_NAME = "ALICANTO"
START_DATE = "2026-05-01"
END_DATE = "2026-05-31"

# Ajusta estos nombres según tus columnas reales
COL_PROYECTO = "proyecto"
COL_FECHA = "fecha_registro"
COL_CORREO = "correo"
COL_TELEFONO = "telefono"
COL_NOMBRE = "nombre"
COL_DOCUMENTO = "documento"


# =========================
# FUNCIONES
# =========================

def clean_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_phone(value):
    """Limpia teléfonos dejando solo dígitos."""
    if pd.isna(value):
        return ""
    return re.sub(r"\D", "", str(value))


def is_valid_email(value):
    """Validación simple de correo."""
    value = clean_text(value).lower()
    if value == "":
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


def is_valid_phone_pe(value):
    """
    Validación simple para celular peruano:
    - 9 dígitos
    - empieza con 9
    También acepta teléfonos con código país 51.
    """
    phone = normalize_phone(value)

    if phone.startswith("51") and len(phone) == 11:
        phone = phone[2:]

    return len(phone) == 9 and phone.startswith("9")


def load_file(path):
    suffix = path.suffix.lower()

    if suffix in [".xlsx", ".xls"]:
        return pd.read_excel(path)

    if suffix == ".csv":
        return pd.read_csv(path, encoding="utf-8-sig")

    raise ValueError("Formato no soportado. Usa Excel o CSV.")


def require_columns(df, columns):
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(
            "Faltan columnas requeridas en el archivo: "
            + ", ".join(missing)
            + "\nColumnas disponibles: "
            + ", ".join(df.columns.astype(str))
        )


# =========================
# PROCESO
# =========================

def main():
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"No encontré el archivo {INPUT_FILE}. "
            "Coloca tu archivo en data/ y renómbralo como leads_crm.xlsx"
        )

    df = load_file(INPUT_FILE)

    require_columns(
        df,
        [COL_PROYECTO, COL_FECHA, COL_CORREO, COL_TELEFONO]
    )

    # Normalizar fechas
    df[COL_FECHA] = pd.to_datetime(df[COL_FECHA], errors="coerce")

    # Filtrar proyecto y periodo
    df_filtrado = df[
        (df[COL_PROYECTO].astype(str).str.upper().str.strip() == PROJECT_NAME.upper())
        & (df[COL_FECHA] >= pd.to_datetime(START_DATE))
        & (df[COL_FECHA] <= pd.to_datetime(END_DATE))
    ].copy()

    # Validaciones
    df_filtrado["correo_limpio"] = df_filtrado[COL_CORREO].apply(clean_text)
    df_filtrado["telefono_limpio"] = df_filtrado[COL_TELEFONO].apply(normalize_phone)

    df_filtrado["tiene_correo"] = df_filtrado[COL_CORREO].apply(is_valid_email)
    df_filtrado["tiene_telefono"] = df_filtrado[COL_TELEFONO].apply(is_valid_phone_pe)

    df_filtrado["estado_contactabilidad"] = df_filtrado.apply(
        lambda row:
            "OK: correo y teléfono"
            if row["tiene_correo"] and row["tiene_telefono"]
            else "Falta correo"
            if not row["tiene_correo"] and row["tiene_telefono"]
            else "Falta teléfono"
            if row["tiene_correo"] and not row["tiene_telefono"]
            else "Falta correo y teléfono",
        axis=1
    )

    total = len(df_filtrado)

    resumen = pd.DataFrame({
        "indicador": [
            "Proyecto auditado",
            "Periodo inicio",
            "Periodo fin",
            "Total leads revisados",
            "Leads con correo válido",
            "Leads con teléfono válido",
            "Leads con correo y teléfono",
            "Leads con faltante de correo o teléfono",
        ],
        "valor": [
            PROJECT_NAME,
            START_DATE,
            END_DATE,
            total,
            int(df_filtrado["tiene_correo"].sum()),
            int(df_filtrado["tiene_telefono"].sum()),
            int((df_filtrado["tiene_correo"] & df_filtrado["tiene_telefono"]).sum()),
            int((~(df_filtrado["tiene_correo"] & df_filtrado["tiene_telefono"])).sum()),
        ]
    })

    resumen_numerico = resumen[pd.to_numeric(resumen["valor"], errors="coerce").notna()].copy()
    if total > 0:
        resumen_numerico["porcentaje_sobre_total"] = pd.to_numeric(resumen_numerico["valor"], errors="coerce") / total
    else:
        resumen_numerico["porcentaje_sobre_total"] = 0

    faltantes = df_filtrado[
        ~(df_filtrado["tiene_correo"] & df_filtrado["tiene_telefono"])
    ].copy()

    resumen_estado = (
        df_filtrado
        .groupby("estado_contactabilidad", dropna=False)
        .size()
        .reset_index(name="cantidad")
        .sort_values("cantidad", ascending=False)
    )

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        resumen.to_excel(writer, index=False, sheet_name="resumen_general")
        resumen_estado.to_excel(writer, index=False, sheet_name="resumen_estado")
        df_filtrado.to_excel(writer, index=False, sheet_name="detalle_leads")
        faltantes.to_excel(writer, index=False, sheet_name="faltantes")

    print(f"OK - Archivo generado: {OUTPUT_FILE}")
    print(f"Total leads revisados: {total}")


if __name__ == "__main__":
    main()
