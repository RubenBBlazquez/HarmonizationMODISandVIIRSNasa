"""Tests de la categorización de FRP (valores límite de VIIRS y MODIS, día y noche)."""

from __future__ import annotations

import pandas as pd
import pytest

from fire_etl.frp_classification import classify_frp


def _una(instrumento: str, frp: float, dn: str) -> tuple[object, str]:
    out = classify_frp([{"instrument": instrumento, "frp": frp, "daynight": dn}])
    clase = out.loc[0, "frp_class"]
    return (None if pd.isna(clase) else int(clase)), out.loc[0, "frp_category"]


@pytest.mark.parametrize(
    ("instrumento", "frp", "dn", "clase", "categoria"),
    [
        # VIIRS día (mínimo 3,7)
        ("VIIRS", 3.69, "D", 0, "discarded"),
        ("VIIRS", 3.7, "D", 1, "low"),
        ("VIIRS", 35.0, "D", 1, "low"),
        ("VIIRS", 35.01, "D", 2, "medium"),
        ("VIIRS", 70.0, "D", 2, "medium"),
        ("VIIRS", 70.01, "D", 3, "high"),
        # VIIRS noche (mínimo 0,7)
        ("VIIRS", 0.69, "N", 0, "discarded"),
        ("VIIRS", 0.7, "N", 1, "low"),
        ("VIIRS", 1.0, "D", 0, "discarded"),  # válido de noche, no de día
        # MODIS día (mínimo 5,7)
        ("MODIS", 5.69, "D", 0, "discarded"),
        ("MODIS", 5.7, "D", 1, "low"),
        ("MODIS", 75.0, "D", 1, "low"),
        ("MODIS", 75.01, "D", 2, "medium"),
        ("MODIS", 175.0, "D", 2, "medium"),
        ("MODIS", 175.01, "D", 3, "high"),
        # MODIS noche (mínimo 3,7)
        ("MODIS", 3.69, "N", 0, "discarded"),
        ("MODIS", 3.7, "N", 1, "low"),
        ("MODIS", 75.0, "N", 1, "low"),
    ],
)
def test_valores_limite(instrumento, frp, dn, clase, categoria):
    assert _una(instrumento, frp, dn) == (clase, categoria)


@pytest.mark.parametrize(
    ("texto", "frp", "esperado"),
    [
        ("viirs", 50.0, 2),
        ("VIIRS_SNPP", 50.0, 2),
        ("VIIRS_NOAA20", 50.0, 2),
        ("MODIS_NRT", 50.0, 1),
    ],
)
def test_variantes_instrumento(texto, frp, esperado):
    assert _una(texto, frp, "D")[0] == esperado


def test_datos_invalidos_no_fallan_y_avisan():
    df = pd.DataFrame(
        {
            "instrument": ["VIIRS", "AVHRR", "MODIS", "VIIRS", "MODIS"],
            "frp": [50.0, 50.0, 50.0, -1.0, None],
            "daynight": ["D", "D", "X", "D", "N"],
            "lat": [1, 2, 3, 4, 5],
        }
    )
    with pytest.warns(UserWarning, match="4 fila"):
        out = classify_frp(df)
    assert out["frp_category"].tolist() == ["medium"] + ["unknown"] * 4
    assert out["frp_class"].isna().tolist() == [False, True, True, True, True]


def test_no_modifica_original_y_conserva_columnas():
    df = pd.DataFrame({"instrument": ["MODIS"], "frp": [100.0], "daynight": ["D"], "lat": [9.9]})
    antes = df.copy()
    out = classify_frp(df)
    pd.testing.assert_frame_equal(df, antes)
    assert out.loc[0, "lat"] == 9.9 and out.loc[0, "frp_category"] == "medium"


def test_nombres_de_columna_configurables():
    df = pd.DataFrame({"sat": ["VIIRS"], "potencia": [80.0], "dn": ["N"]})
    out = classify_frp(df, instrument_col="sat", col_frp="potencia", col_daynight="dn")
    assert out.loc[0, "frp_category"] == "high"
