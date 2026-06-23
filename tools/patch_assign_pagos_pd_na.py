from pathlib import Path
import shutil

root = Path.cwd()
src = root / "revenue_truth_pipeline_e2e" / "src" / "revenue_truth" / "assign_pagos.py"
patch_file = Path(__file__).resolve().parents[1] / "revenue_truth_pipeline_e2e" / "src" / "revenue_truth" / "assign_pagos.py"

if not src.exists():
    raise SystemExit(f"No encuentro archivo objetivo: {src}")
if not patch_file.exists():
    raise SystemExit(f"No encuentro archivo patch: {patch_file}")

bak = src.with_suffix(src.suffix + ".bak_pd_na")
if not bak.exists():
    shutil.copy2(src, bak)
shutil.copy2(patch_file, src)
print(f"OK: reemplazado {src}")
print(f"Backup: {bak}")
