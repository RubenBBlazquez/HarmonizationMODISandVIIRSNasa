"""Tests del enriquecimiento de cobertura del suelo con SIGPAC (capas sintéticas, sin red)."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from fire_etl.sigpac_land_cover import (
    LandCoverError,
    enrich,
    normalize_sigpac_use,
    rename_colliding_columns,
)

POINT_TA = (40.0000, -3.7000)  # recinto TA (GPKG, EPSG:25830)
POINT_TWO_RECINTOS = (40.0005, -3.7000)  # dentro de TA y de un recinto PS solapado
POINT_UNKNOWN = (40.0100, -3.7000)  # recinto con código "XX" (shapefile, EPSG:25829)
POINT_NO_RECINTO = (40.0500, -3.7000)  # fuera de todos los recintos
POINT_INVALID = (95.0, -3.7)  # latitud fuera de rango


@pytest.mark.parametrize(
    ("code", "expected"),
    [("TA", ("crops", True)), (" fo ", ("trees", True)), ("PS", ("grass", True))]
    + [("AG", ("water", True)), ("XX", ("other", False))],
)
def test_normalize_sigpac_use(code, expected):
    assert normalize_sigpac_use(code) == expected


def test_rename_colliding_columns_refuses_to_overwrite_prev():
    df = pd.DataFrame({"land_cover": ["a"], "land_cover_prev": ["b"]})

    with pytest.raises(LandCoverError):
        rename_colliding_columns(df, ["land_cover"])


def _square(lat: float, lon: float, half_deg: float):
    return box(lon - half_deg, lat - half_deg, lon + half_deg, lat + half_deg)


@pytest.fixture
def sigpac_dir(tmp_path):
    """Dos capas con CRS distintos: GPKG en EPSG:25830 y shapefile en EPSG:25829."""
    folder = tmp_path / "sigpac"
    folder.mkdir()

    gpkg = gpd.GeoDataFrame(
        {"uso_sigpac": ["TA", "PS"]},
        geometry=[_square(*POINT_TA, 0.002), _square(*POINT_TWO_RECINTOS, 0.0002)],
        crs=4326,
    ).to_crs(25830)
    gpkg.to_file(folder / "recintos.gpkg", layer="recintos", driver="GPKG")

    shp = gpd.GeoDataFrame(
        {"USO_SIGPAC": ["XX"]}, geometry=[_square(*POINT_UNKNOWN, 0.002)], crs=4326
    ).to_crs(25829)
    shp.to_file(folder / "recintos_29.shp")

    return folder


def test_enrich_known_coordinates_with_sigpac(sigpac_dir):
    points = [POINT_TA, POINT_TWO_RECINTOS, POINT_UNKNOWN, POINT_NO_RECINTO, POINT_INVALID]
    df = pd.DataFrame(
        {
            "id": range(len(points)),
            "lat": [lat for lat, _ in points],
            "lon": [lon for _, lon in points],
            "land_cover": ["old"] * len(points),
        }
    )

    enriched, summary = enrich(df, sigpac_dir)
    rows = enriched.set_index("id")

    assert "land_cover_prev" in enriched.columns
    assert rows.loc[0, ["land_cover", "land_cover_source", "sigpac_uso"]].tolist() == [
        "crops",
        "sigpac",
        "TA",
    ]
    assert rows.loc[0, "land_cover_confidence"] == 0.95
    assert bool(rows.loc[0, "is_agricultural"]) and not bool(rows.loc[0, "is_forest"])
    assert rows.loc[1, "land_cover_source"] == "sigpac"
    assert rows.loc[2, ["land_cover", "sigpac_uso"]].tolist() == ["other", "XX"]
    for empty in (3, 4):  # sin recinto / coordenadas inválidas: campos vacíos, sin fallar
        assert rows.loc[empty, ["land_cover", "land_cover_source", "sigpac_uso"]].isna().all()
        assert pd.isna(rows.loc[empty, "land_cover_confidence"])
        assert rows.loc[empty, ["is_agricultural", "is_forest"]].isna().all()

    assert (summary.total, summary.invalid, summary.sigpac, summary.no_data) == (5, 1, 3, 1)
    assert summary.multiple_recintos == 1
    assert summary.unknown_sigpac_codes == ["XX"]


def test_enrich_forest_recinto_sets_is_forest(tmp_path):
    folder = tmp_path / "sigpac"
    folder.mkdir()
    forest = gpd.GeoDataFrame(
        {"uso_sigpac": ["FO"]}, geometry=[_square(*POINT_TA, 0.002)], crs=4326
    ).to_crs(25830)
    forest.to_file(folder / "forestal.gpkg", layer="forestal", driver="GPKG")
    df = pd.DataFrame({"id": [1], "lat": [POINT_TA[0]], "lon": [POINT_TA[1]]})

    enriched, _ = enrich(df, folder)

    assert enriched.loc[0, "land_cover"] == "trees"
    assert bool(enriched.loc[0, "is_forest"]) and not bool(enriched.loc[0, "is_agricultural"])


def test_enrich_min_confidence_threshold_clears_flags(sigpac_dir):
    df = pd.DataFrame({"id": [1], "lat": [POINT_TA[0]], "lon": [POINT_TA[1]]})

    enriched, _ = enrich(df, sigpac_dir, min_confidence=0.99)

    assert enriched.loc[0, "land_cover"] == "crops"
    assert not bool(enriched.loc[0, "is_agricultural"])


def test_enrich_empty_sigpac_folder_leaves_fields_empty(tmp_path, capsys):
    df = pd.DataFrame({"id": [1], "lat": [40.0], "lon": [-3.7]})

    enriched, summary = enrich(df, tmp_path)

    assert pd.isna(enriched.loc[0, "land_cover"])
    assert summary.no_data == 1 and summary.sigpac == 0
    assert "AVISO" in capsys.readouterr().out
