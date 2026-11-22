"""
Downloads the NASA FIRMS data (MODIS and VIIRS): annual archive of Spain since 2000 and NRT.

1. **Annual archive** (no token): one CSV per source and complete year, already clipped to Spain
   (``download`` in ``config/default.yaml``). Those already downloaded are not requested again.
2. **NRT of the current year** (needs a token): daily files of Europe for the last ~60 days
   (``nrt``). The token (it never goes in the YAML or in git) is read from the
   ``EARTHDATA_TOKEN`` environment variable or, if it does not exist, from the local file
   ``.secrets/earthdata_token`` (ignored by git):

       python scripts/download_firms.py

   Without a token the NRT is skipped and a warning is shown. The files of the last few days
   are always downloaded again because they grow during the day.

Warning: between the end of the annual archive (Dec 31st of the last complete year) and the
start of the NRT (~60 days back) there is a gap that neither source covers; it is reported at
the end.

It leaves the CSV files in ``data/raw/`` and ``data/raw/nrt/`` (ignored by git).
"""

import logging
from datetime import timedelta

from _paths import ETLS_DIR

from fire_etl.config import load_config
from fire_etl.extract.firms.archive import download_all
from fire_etl.extract.firms.nrt import download_nrt, first_uncovered_day, nrt_files

CONFIG_FILE = ETLS_DIR / "config" / "default.yaml"
RAW_DIR = ETLS_DIR / "data" / "raw"
NRT_DIR = RAW_DIR / "nrt"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

config = load_config(CONFIG_FILE)
result = download_all(config.download, RAW_DIR)

print(f"\nDownloaded: {len(result.downloaded)} | already existed: {len(result.skipped)}")
print(f"Not in the archive: {len(result.missing)} | failed: {len(result.failed)}")
for url, reason in result.failed.items():
    print(f"  FAILED {url}: {reason}")

print(f"Files in {RAW_DIR}: {len(result.available)}")

nrt = download_nrt(config.nrt, NRT_DIR, base_dir=ETLS_DIR)
print(
    f"\nNRT: downloaded {len(nrt.downloaded)} | unchanged {len(nrt.skipped)} | "
    f"failed {len(nrt.failed)} | files in {NRT_DIR}: {len(nrt_files(NRT_DIR))}"
)
if nrt.token_missing:
    print(f"  No NRT: put the token in the variable {config.nrt.token_env}")
    print(f"  or in the local file {config.nrt.token_file} (ignored by git).")
if nrt.auth_failed:
    print("  The server rejected the token (expired or invalid): generate a new one in Earthdata.")

if nrt.first_day:
    gap_start = first_uncovered_day()
    gap_end = nrt.first_day - timedelta(days=1)
    if gap_end >= gap_start:
        print(
            f"\nWARNING, uncovered gap: from {gap_start} to {gap_end} "
            f"({(gap_end - gap_start).days + 1} days). The annual archive goes up to "
            f"{gap_start - timedelta(days=1)} and the NRT starts on {nrt.first_day}."
        )
