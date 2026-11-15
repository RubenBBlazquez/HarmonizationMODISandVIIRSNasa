"""Tests de la categorización de FRP (valores límite de VIIRS y MODIS, día y noche)."""

from __future__ import annotations

import pandas as pd
import pytest

from fire_etl.frp_classification import clasificar_frp


def _una(instrumento: str, frp: float, dn: str) -> tuple[object, str]:
    out = clasificar_frp([{"instrumento": instrumento, "frp": frp, "daynight": dn}])
    clase = out.loc[0, "frp_clase"]
    return (None if pd.isna(clase) else int(clase)), out.loc[0, "frp_categoria"]


@pytest.mark.parametrize(
    ("instrumento", "frp", "dn", "clase", "categoria"),
    [
        # VIIRS día (mínimo 15)
        ("VIIRS", 14.99, "D", None, "descartado"),
        ("VIIRS", 15.0, "D", 1, "baja"),
        ("VIIRS", 35.0, "D", 1, "baja"),
        ("VIIRS", 35.01, "D", 2, "media"),
        ("VIIRS", 70.0, "D", 2, "media"),
        ("VIIRS", 70.01, "D", 3, "alta"),
        # VIIRS noche (mínimo 6)
        ("VIIRS", 5.99, "N", None, "descartado"),
        ("VIIRS", 6.0, "N", 1, "baja"),
        ("VIIRS", 10.0, "D", None, "descartado"),  # válido de noche, no de día
        # MODIS día (mínimo 30)
        ("MODIS", 29.99, "D", None, "descartado"),
        ("MODIS", 30.0, "D", 1, "baja"),
        ("MODIS", 75.0, "D", 1, "baja"),
        ("MODIS", 75.01, "D", 2, "media"),
        ("MODIS", 175.0, "D", 2, "media"),
        ("MODIS", 175.01, "D", 3, "alta"),
        # MODIS noche (mínimo 12)
        ("MODIS", 11.99, "N", None, "descartado"),
        ("MODIS", 12.0, "N", 1, "baja"),
        ("MODIS", 75.0, "N", 1, "baja"),
    ],
)
def test_valores_limite(instrumento, frp, dn, clase, categoria):
    assert _una(instrumento, frp, dn) == (clase, categoria)


@pytest.mark.parametrize(
    ("texto", "frp", "esperado"),
    [("viirs", 50.0, 2), ("VIIRS_SNPP", 50.0, 2), ("VIIRS_NOAA20", 50.0, 2), ("MODIS_NRT", 50.0, 1)],
)
def test_variantes_instrumento(texto, frp, esperado):
    assert _una(texto, frp, "D")[0] == esperado


def test_datos_invalidos_no_fallan_y_avisan():
    df = pd.DataFrame(
        {
            "instrumento": ["VIIRS", "AVHRR", "MODIS", "VIIRS", "MODIS"],
            "frp": [50.0, 50.0, 50.0, -1.0, None],
            "daynight": ["D", "D", "X", "D", "N"],
            "lat": [1, 2, 3, 4, 5],
        }
    )
    with pytest.warns(UserWarning, match="4 fila"):
        out = clasificar_frp(df)
    assert out["frp_categoria"].tolist() == ["media"] + ["desconocido"] * 4
    assert out["frp_clase"].isna().tolist() == [False, True, True, True, True]


def test_no_modifica_original_y_conserva_columnas():
    df = pd.DataFrame({"instrumento": ["MODIS"], "frp": [100.0], "daynight": ["D"], "lat": [9.9]})
    antes = df.copy()
    out = clasificar_frp(df)
    pd.testing.assert_frame_equal(df, antes)
    assert out.loc[0, "lat"] == 9.9 and out.loc[0, "frp_categoria"] == "media"


def test_nombres_de_columna_configurables():
    df = pd.DataFrame({"sat": ["VIIRS"], "potencia": [80.0], "dn": ["N"]})
    out = clasificar_frp(df, col_instrumento="sat", col_frp="potencia", col_daynight="dn")
    assert out.loc[0, "frp_categoria"] == "alta"
