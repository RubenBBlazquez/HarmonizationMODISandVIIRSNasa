"""
Common paths of the scripts. When imported, it adds ``backend/etls`` to ``sys.path``.

This way ``from fire_etl... import ...`` works when running ``python scripts/<script>.py``
without installing the package (having the dependencies is enough). It must be imported before
``fire_etl``.
"""

import sys
from pathlib import Path

ETLS_DIR = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ETLS_DIR / "data" / "sample"

if str(ETLS_DIR) not in sys.path:
    sys.path.insert(0, str(ETLS_DIR))
