"""
Rutas comunes de los scripts. Al importarlo, añade ``backend/etls`` a ``sys.path``.

Así ``from fire_etl... import ...`` funciona al ejecutar ``python scripts/<script>.py`` sin
instalar el paquete (basta con tener las dependencias). Debe importarse antes que ``fire_etl``.
"""

import sys
from pathlib import Path

ETLS_DIR = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ETLS_DIR / "data" / "sample"

if str(ETLS_DIR) not in sys.path:
    sys.path.insert(0, str(ETLS_DIR))
