# -*- coding: utf-8 -*-
"""
Patch para 8_revenue_unit_match_ops/src/revenue_unit_match_engine_ops.py

Corrige KeyError: 'documento_cliente_norm' en build_candidates(), causado por
cambios de nombres/sufijos después del merge entre pagos_eventos e items de ventas.

Uso desde la raíz del proyecto:
python .\tools\patch_revenue_unit_match_document_norm.py
"""
from pathlib import Path
import re

TARGET = Path("8_revenue_unit_match_ops/src/revenue_unit_match_engine_ops.py")

HELPER = r'''

# =========================================================
# Patch OPS: blindaje columnas documento después de merges
# =========================================================
def _ops_digits_only_value(x):
    try:
        import pandas as pd
        if x is None or pd.isna(x):
            return pd.NA
    except Exception:
        if x is None:
            return None
    txt = str(x).strip().replace(".0", "")
    digits = "".join(ch for ch in txt if ch.isdigit())
    return digits if digits else None


def _ops_first_existing_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None


def ensure_document_columns_after_merge(cand):
    """
    Garantiza que existan cand['dni_norm'] y cand['documento_cliente_norm']
    después del merge pago-unidad, aunque pandas haya aplicado sufijos (_x/_y,
    _pago/_item/_venta) o aunque solo exista el documento sin normalizar.
    """
    import pandas as pd

    cand = cand.copy()

    if "dni_norm" not in cand.columns:
        src = _ops_first_existing_col(
            cand,
            [
                "dni_norm_pago", "dni_norm_x", "dni_norm_y",
                "dni", "dni_pago", "documento_pago", "documento_cliente_pago",
            ],
        )
        if src:
            cand["dni_norm"] = cand[src].apply(_ops_digits_only_value).astype("string")
        else:
            cand["dni_norm"] = pd.NA

    if "documento_cliente_norm" not in cand.columns:
        src = _ops_first_existing_col(
            cand,
            [
                "documento_cliente_norm_item", "documento_cliente_norm_venta",
                "documento_cliente_norm_y", "documento_cliente_norm_x",
                "documento_cliente", "documento_cliente_item", "documento_cliente_venta",
                "documento_cliente_y", "documento_cliente_x",
            ],
        )
        if src:
            cand["documento_cliente_norm"] = cand[src].apply(_ops_digits_only_value).astype("string")
        else:
            cand["documento_cliente_norm"] = pd.NA

    cand["dni_norm"] = cand["dni_norm"].apply(_ops_digits_only_value).astype("string")
    cand["documento_cliente_norm"] = cand["documento_cliente_norm"].apply(_ops_digits_only_value).astype("string")

    # Diagnóstico visible en consola para OPS.
    doc_cols = [c for c in cand.columns if "documento" in str(c).lower() or str(c).lower() in ["dni", "dni_norm"]]
    print("[OPS debug] columnas documento disponibles en candidatos:", doc_cols[:20])
    print("[OPS debug] candidatos con dni_norm:", int(cand["dni_norm"].notna().sum()))
    print("[OPS debug] candidatos con documento_cliente_norm:", int(cand["documento_cliente_norm"].notna().sum()))
    print("[OPS debug] candidatos con DNI match:", int(cand["dni_norm"].eq(cand["documento_cliente_norm"]).sum()))

    return cand
'''

REPLACEMENTS = [
    (
        'cand["flag_dni_match"] = cand["dni_norm"].eq(cand["documento_cliente_norm"])',
        'cand = ensure_document_columns_after_merge(cand)\n    cand["flag_dni_match"] = cand["dni_norm"].eq(cand["documento_cliente_norm"])',
    ),
    (
        "cand['flag_dni_match'] = cand['dni_norm'].eq(cand['documento_cliente_norm'])",
        "cand = ensure_document_columns_after_merge(cand)\n    cand['flag_dni_match'] = cand['dni_norm'].eq(cand['documento_cliente_norm'])",
    ),
]


def main():
    if not TARGET.exists():
        raise SystemExit(f"No encuentro el archivo objetivo: {TARGET}")

    txt = TARGET.read_text(encoding="utf-8")
    backup = TARGET.with_suffix(TARGET.suffix + ".bak_document_norm")
    backup.write_text(txt, encoding="utf-8")

    changed = False

    if "def ensure_document_columns_after_merge" not in txt:
        # Insertar helper antes del primer bloque de funciones o antes de main si no encuentra.
        marker = "# -----------------------------"
        pos = txt.find(marker)
        if pos == -1:
            pos = txt.find("def main")
        if pos == -1:
            pos = len(txt)
        txt = txt[:pos] + HELPER + "\n" + txt[pos:]
        changed = True

    for old, new in REPLACEMENTS:
        if old in txt and "ensure_document_columns_after_merge(cand)" not in txt[txt.find(old)-120:txt.find(old)+120]:
            txt = txt.replace(old, new, 1)
            changed = True

    # Fallback regex si la línea tiene espacios raros.
    if "ensure_document_columns_after_merge(cand)" not in txt:
        pattern = r'(\s*)cand\[([\"\'])flag_dni_match\2\]\s*=\s*cand\[([\"\'])dni_norm\3\]\.eq\(cand\[([\"\'])documento_cliente_norm\4\]\)'
        repl = r'\1cand = ensure_document_columns_after_merge(cand)\n\1cand[\2flag_dni_match\2] = cand[\3dni_norm\3].eq(cand[\4documento_cliente_norm\4])'
        txt2, n = re.subn(pattern, repl, txt, count=1)
        if n:
            txt = txt2
            changed = True

    if not changed:
        print("No se aplicaron cambios. Puede que el patch ya esté aplicado o la línea objetivo haya cambiado.")
        print(f"Backup creado igualmente en: {backup}")
        return

    TARGET.write_text(txt, encoding="utf-8")
    print("OK | Patch aplicado")
    print(f"Archivo modificado: {TARGET}")
    print(f"Backup: {backup}")
    print("Ahora vuelve a correr:")
    print(r".\run_all_revenue_match_with_ops_resilient.ps1 -SkipRedshift")


if __name__ == "__main__":
    main()
