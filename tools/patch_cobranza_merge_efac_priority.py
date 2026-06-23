# -*- coding: utf-8 -*-
from pathlib import Path
import shutil
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "3_merge_venta_recibido" / "cobranza_pipeline_efac_priority_replacement.py"
DST = ROOT / "3_merge_venta_recibido" / "cobranza_pipeline.py"
if not SRC.exists():
    raise SystemExit(f"No encuentro replacement: {SRC}")
if not DST.exists():
    raise SystemExit(f"No encuentro destino: {DST}")
bak = DST.with_suffix(DST.suffix + ".bak_efac_priority")
shutil.copy2(DST, bak)
shutil.copy2(SRC, DST)
print("OK patch aplicado")
print("Backup:", bak)
print("Destino:", DST)
