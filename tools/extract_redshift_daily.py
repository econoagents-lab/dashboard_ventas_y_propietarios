import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from common.redshift_extract import extract_redshift_daily

if __name__ == "__main__":
    extract_redshift_daily(
        extract_redshift_daily()
    )