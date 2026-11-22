"""Tests of the FRP categorization (boundary values of VIIRS and MODIS, day and night)."""

from __future__ import annotations

import pandas as pd
import pytest

from fire_etl.transform.processing.frp_classification import classify_frp


def _classify_one(instrument: str, frp: float, dn: str) -> tuple[object, str]:
    out = classify_frp([{"instrument": instrument, "frp": frp, "daynight": dn}])
    frp_class = out.loc[0, "frp_class"]
    return (None if pd.isna(frp_class) else int(frp_class)), out.loc[0, "frp_category"]


@pytest.mark.parametrize(
    ("instrument", "frp", "dn", "frp_class", "category"),
    [
        # VIIRS day (minimum 3.7)
        ("VIIRS", 3.69, "D", 0, "discarded"),
        ("VIIRS", 3.7, "D", 1, "low"),
        ("VIIRS", 35.0, "D", 1, "low"),
        ("VIIRS", 35.01, "D", 2, "medium"),
        ("VIIRS", 70.0, "D", 2, "medium"),
        ("VIIRS", 70.01, "D", 3, "high"),
        # VIIRS night (minimum 0.7)
        ("VIIRS", 0.69, "N", 0, "discarded"),
        ("VIIRS", 0.7, "N", 1, "low"),
        ("VIIRS", 1.0, "D", 0, "discarded"),  # valid at night, not by day
        # MODIS day (minimum 5.7)
        ("MODIS", 5.69, "D", 0, "discarded"),
        ("MODIS", 5.7, "D", 1, "low"),
        ("MODIS", 75.0, "D", 1, "low"),
        ("MODIS", 75.01, "D", 2, "medium"),
        ("MODIS", 175.0, "D", 2, "medium"),
        ("MODIS", 175.01, "D", 3, "high"),
        # MODIS night (minimum 3.7)
        ("MODIS", 3.69, "N", 0, "discarded"),
        ("MODIS", 3.7, "N", 1, "low"),
        ("MODIS", 75.0, "N", 1, "low"),
    ],
)
def test_boundary_values(instrument, frp, dn, frp_class, category):
    assert _classify_one(instrument, frp, dn) == (frp_class, category)


@pytest.mark.parametrize(
    ("text", "frp", "expected"),
    [
        ("viirs", 50.0, 2),
        ("VIIRS_SNPP", 50.0, 2),
        ("VIIRS_NOAA20", 50.0, 2),
        ("MODIS_NRT", 50.0, 1),
    ],
)
def test_instrument_variants(text, frp, expected):
    assert _classify_one(text, frp, "D")[0] == expected


def test_invalid_data_does_not_fail_and_warns():
    df = pd.DataFrame(
        {
            "instrument": ["VIIRS", "AVHRR", "MODIS", "VIIRS", "MODIS"],
            "frp": [50.0, 50.0, 50.0, -1.0, None],
            "daynight": ["D", "D", "X", "D", "N"],
            "lat": [1, 2, 3, 4, 5],
        }
    )
    with pytest.warns(UserWarning, match="4 row"):
        out = classify_frp(df)
    assert out["frp_category"].tolist() == ["medium"] + ["unknown"] * 4
    assert out["frp_class"].isna().tolist() == [False, True, True, True, True]


def test_does_not_modify_original_and_keeps_columns():
    df = pd.DataFrame({"instrument": ["MODIS"], "frp": [100.0], "daynight": ["D"], "lat": [9.9]})
    before = df.copy()
    out = classify_frp(df)
    pd.testing.assert_frame_equal(df, before)
    assert out.loc[0, "lat"] == 9.9 and out.loc[0, "frp_category"] == "medium"


def test_configurable_column_names():
    df = pd.DataFrame({"sat": ["VIIRS"], "power": [80.0], "dn": ["N"]})
    out = classify_frp(df, instrument_col="sat", col_frp="power", col_daynight="dn")
    assert out.loc[0, "frp_category"] == "high"
