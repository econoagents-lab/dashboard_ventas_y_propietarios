from pathlib import Path
import shutil

ROOT = Path.cwd()
TARGET = ROOT / "3_merge_venta_recibido" / "cobranza_pipeline.py"
PATCHED = ROOT / "3_merge_venta_recibido" / "cobranza_pipeline.py"
SOURCE = Path(__file__).resolve().parents[1] / "3_merge_venta_recibido" / "cobranza_pipeline.py"

if not TARGET.exists():
    raise SystemExit(f"No encuentro archivo objetivo: {TARGET}")
backup = TARGET.with_suffix(TARGET.suffix + ".bak_efac_item_priority")
if not backup.exists():
    shutil.copy2(TARGET, backup)
shutil.copy2(SOURCE, TARGET)
print("OK: cobranza_pipeline.py reemplazado con prioridad EFAC item-level")
print("Backup:", backup)
