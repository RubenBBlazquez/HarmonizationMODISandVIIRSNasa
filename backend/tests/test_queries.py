"""Tests de las consultas y la conversión a GeoJSON (sin base de datos)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from backend.api import queries
from backend.api.queries import (
    Filters,
    InvalidBBoxError,
    detection_feature,
    detections_query,
    event_feature,
    events_query,
    fetch_collection,
    fetch_summary,
    parse_bbox,
)

SPAIN_BBOX = (-9.5, 35.8, 4.5, 44.0)
POINT = {"type": "Point", "coordinates": [-7.2, 42.5]}
WHEN = datetime(2022, 8, 1, 14, 30, tzinfo=timezone.utc)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None  # como psycopg: None si no hay filas


class FakeConnection:
    """Devuelve filas por orden de llamada y registra las consultas ejecutadas."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return FakeResult(self.results.pop(0))


def detection_row(index: int = 0) -> dict:
    return {
        "detection_id": f"id{index}",
        "event_id": None,
        "source": "VIIRS",
        "satellite": "N20",
        "acq_datetime": WHEN,
        "daynight": "D",
        "frp": 12.3456,
        "frp_category": "low",
        "biomass_kg_s": 4.5432,
        "confidence_norm": "nominal",
        "land_cover": None,
        "area_width_km": 0.4,
        "area_height_km": 0.5,
        "geometry": POINT,
    }


def test_parse_bbox_accepts_a_valid_box():
    assert parse_bbox("-9.5,35.8,4.5,44") == SPAIN_BBOX


@pytest.mark.parametrize(
    "text", ["1,2,3", "a,b,c,d", "", "1,2,3,4,5", "5,0,1,1", "0,5,1,1", "-181,0,0,1", "0,-91,1,0"]
)
def test_parse_bbox_rejects_invalid_boxes(text):
    with pytest.raises(InvalidBBoxError):
        parse_bbox(text)


def test_detections_query_only_selects_valid_detections_by_default():
    query = detections_query(Filters())

    assert "WHERE d.is_valid ORDER BY" in query.sql
    assert query.params == ()
    assert query.sql.endswith("LIMIT %s")
    assert "count(*) AS total" in query.count_sql and "LIMIT" not in query.count_sql


def test_detections_query_combines_all_filters_in_a_stable_order():
    filters = Filters(
        bbox=SPAIN_BBOX,
        start=date(2022, 1, 1),
        end=date(2022, 12, 31),
        source="MODIS",
        frp_category="high",
        limit=10,
    )

    query = detections_query(filters)

    assert "d.geom_point && ST_MakeEnvelope(%s, %s, %s, %s, 4326)" in query.sql
    assert "d.acq_datetime >= %s" in query.sql and "d.acq_datetime < %s" in query.sql
    assert "d.source = %s" in query.sql and "d.frp_category = %s" in query.sql
    assert query.params[:4] == SPAIN_BBOX
    assert query.params[4] == datetime(2022, 1, 1, tzinfo=timezone.utc)
    assert query.params[5] == datetime(2023, 1, 1, tzinfo=timezone.utc)  # end incluido
    assert query.params[6:] == ("MODIS", "high")
    assert query.sql.count("%s") == len(query.params) + 1  # + LIMIT


def test_country_filter_keeps_what_falls_inside_the_border_in_parameter_order():
    filters = Filters(bbox=SPAIN_BBOX, country="PRT", start=date(2022, 1, 1))

    detections = detections_query(filters)
    events = events_query(filters)

    assert "c.code = %s AND c.geom && d.geom_point AND ST_Intersects(c.geom, d.geom_point)" in (
        detections.sql
    )
    assert "ST_Intersects(c.geom, e.geom_hull)" in events.sql
    # bbox, país y fechas: los parámetros siguen el orden de las condiciones.
    assert detections.params[:5] == (*SPAIN_BBOX, "PRT")
    assert detections.params[5] == datetime(2022, 1, 1, tzinfo=timezone.utc)
    assert detections.sql.count("%s") == len(detections.params) + 1
    assert "c.code = %s" in detections.count_sql


def test_fetch_countries_returns_a_feature_per_country_with_its_data_flag():
    square = {"type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 0]]]]}
    spain = {"code": "ESP", "name": "España", "continent": "Europe", "has_data": True}
    portugal = {"code": "PRT", "name": "Portugal", "continent": "Europe", "has_data": False}
    connection = FakeConnection([{**spain, "geometry": square}, {**portugal, "geometry": square}])

    collection = queries.fetch_countries(connection)

    assert collection["count"] == 2
    assert [f["properties"]["code"] for f in collection["features"]] == ["ESP", "PRT"]
    assert collection["features"][0]["properties"] == spain
    assert collection["features"][1]["geometry"] == square


def test_pixel_geometry_builds_the_width_by_height_rectangle_without_buffer():
    point = detections_query(Filters())
    pixel = detections_query(Filters(bbox=SPAIN_BBOX, source="MODIS"), geometry="pixel")

    assert "LATERAL" not in point.sql and "ST_AsGeoJSON(d.geom_point, 5)" in point.sql
    assert "CROSS JOIN LATERAL (SELECT ST_Transform(d.geom_point, 25830) AS geom) p" in pixel.sql
    assert "ST_X(p.geom) - d.area_width_km * 500.0" in pixel.sql
    assert "ST_Y(p.geom) + d.area_height_km * 500.0" in pixel.sql
    assert "25830), 4326)" in pixel.sql  # envolvente en UTM y devuelta en EPSG:4326
    assert "LATERAL" not in pixel.count_sql  # el recuento no necesita la geometría
    assert pixel.params == (*SPAIN_BBOX, "MODIS")
    assert pixel.sql.count("%s") == len(pixel.params) + 1


def test_detections_query_rejects_unknown_geometry():
    with pytest.raises(ValueError):
        detections_query(Filters(), geometry="hull")


def test_events_query_selects_geometry_and_overlapping_dates():
    point = events_query(Filters(start=date(2022, 1, 1), end=date(2022, 1, 31), source="VIIRS"))
    area = events_query(Filters(), geometry="area")

    assert "ST_AsGeoJSON(e.geom_centroid, 5)" in point.sql
    assert "ST_AsGeoJSON(e.geom_hull, 5)" in area.sql
    assert "e.last_detection >= %s" in point.sql and "e.first_detection < %s" in point.sql
    assert point.params[-1] == "%VIIRS%"  # 'MODIS,VIIRS' también cuenta como VIIRS
    assert point.sql.count("%s") == len(point.params) + 1


def test_events_query_match_inside_requires_detections_in_the_box_and_dates():
    june = (date(2022, 6, 1), date(2022, 6, 30))
    filters = Filters(bbox=SPAIN_BBOX, start=june[0], end=june[1], source="MODIS")

    viewport = events_query(filters)
    inside = events_query(filters, match="inside")

    assert "e.geom_hull && ST_MakeEnvelope" in viewport.sql and "LATERAL" not in viewport.sql
    assert "CROSS JOIN LATERAL" in inside.sql and "z.zone_detections > 0" in inside.sql
    assert "d.geom_point && ST_MakeEnvelope" in inside.sql and "d.is_valid" in inside.sql
    assert "d.acq_datetime >= %s" in inside.sql and "d.acq_datetime < %s" in inside.sql
    assert "ST_AsGeoJSON(z.center, 5)" in inside.sql  # el punto cae dentro del recuadro
    # Parámetros en el orden del SQL: bbox + fechas del LATERAL, bbox del prefiltro, fuente.
    assert inside.params[:4] == SPAIN_BBOX and inside.params[-5:-1] == SPAIN_BBOX
    assert inside.params[-1] == "%MODIS%"
    assert inside.sql.count("%s") == len(inside.params) + 1
    assert "LATERAL" not in inside.count_sql.replace("CROSS JOIN LATERAL", "")  # solo una vez
    assert "ST_AsGeoJSON(e.geom_hull, 5)" in events_query(filters, "area", "inside").sql


def test_events_query_inside_needs_a_bbox_and_a_known_match():
    with pytest.raises(ValueError):
        events_query(Filters(), match="inside")

    with pytest.raises(ValueError):
        events_query(Filters(bbox=SPAIN_BBOX), match="touches")


def test_history_queries_match_events_by_their_detections_inside_the_zone():
    periods = queries.history_periods(date(2025, 7, 1), date(2025, 7, 31), years=1)

    summary_sql, _ = queries.history_summary_query(SPAIN_BBOX, periods)
    events_sql, _ = queries.history_events_query(SPAIN_BBOX, periods, 10)

    for sql in (summary_sql, events_sql):
        assert "d.geom_point && ST_MakeEnvelope" in sql and "geom_hull" not in sql.split("FROM")[1]
        assert "d.acq_datetime >= p.p_start AND d.acq_datetime < p.p_end" in sql
    assert "LEFT JOIN matched m" in summary_sql  # años sin incendios también salen
    assert "ST_AsGeoJSON(m.center, 5)" in events_sql  # el punto cae dentro del recuadro


def test_detection_feature_formats_values_for_the_map():
    feature = detection_feature(detection_row())

    assert feature["type"] == "Feature" and feature["geometry"] == POINT
    assert feature["properties"]["acq_datetime"] == "2022-08-01T14:30:00Z"
    assert feature["properties"]["frp_mw"] == 12.35
    assert feature["properties"]["biomass_kg_s"] == 4.543
    assert feature["properties"]["land_cover"] is None
    assert feature["properties"]["area_width_km"] == 0.4
    assert feature["properties"]["area_height_km"] == 0.5
    assert "scan_km" not in feature["properties"] and "track_km" not in feature["properties"]


def test_event_feature_includes_passes_and_estimated_biomass():
    row = {
        "event_id": "evt_1",
        "status": "closed",
        "first_detection": WHEN,
        "last_detection": WHEN,
        "n_detections": 50,
        "sensors": "VIIRS",
        "frp_max": 30.0,
        "biomass_max_kg_s": 11.0,
        "n_passes": 4,
        "max_pass_gap_hours": 11.4567,
        "biomass_burned_kg": 99555679.7,
        "confidence_fused": "medium",
        "footprint_area_km2": 2.5,
        "geometry": POINT,
    }

    properties = event_feature(row)["properties"]
    legacy = event_feature({k: v for k, v in row.items() if k not in queries_stats_keys()})

    assert (properties["n_passes"], properties["max_pass_gap_hours"]) == (4, 11.5)
    assert properties["biomass_burned_kg"] == 99555680.0
    assert legacy["properties"]["biomass_burned_kg"] is None  # filas sin calcular: null, no error


def queries_stats_keys() -> set[str]:
    return {"n_passes", "max_pass_gap_hours", "biomass_burned_kg"}


def test_every_event_query_selects_the_pass_and_biomass_columns():
    filters = Filters(bbox=SPAIN_BBOX)
    periods = queries.history_periods(date(2025, 7, 1), date(2025, 7, 31), years=1)
    sqls = [
        events_query(filters).sql,
        events_query(filters, match="inside").sql,
        queries.history_events_query(SPAIN_BBOX, periods, 5)[0],
        queries.EVENT_DETAIL_SQL,
    ]

    for sql in sqls:
        assert "e.n_passes, e.max_pass_gap_hours, e.biomass_burned_kg" in sql


def test_event_feature_adds_zone_fields_only_when_searched_inside_a_zone():
    base = {
        "event_id": "evt_1",
        "status": "closed",
        "first_detection": WHEN,
        "last_detection": WHEN,
        "n_detections": 50,
        "sensors": "VIIRS",
        "frp_max": 30.0,
        "biomass_max_kg_s": 11.0,
        "confidence_fused": "medium",
        "footprint_area_km2": 2.5,
        "geometry": POINT,
    }

    plain = event_feature(base)["properties"]
    zoned = event_feature({**base, "zone_detections": 12, "zone_frp_max": 20.456})["properties"]

    assert "zone_detections" not in plain
    assert (zoned["zone_detections"], zoned["zone_frp_max_mw"]) == (12, 20.46)
    assert zoned["n_detections"] == 50  # el total del incendio se conserva


def test_event_feature_formats_values_for_the_map():
    row = {
        "event_id": "evt_1",
        "status": "active",
        "first_detection": WHEN,
        "last_detection": WHEN,
        "n_detections": 3,
        "sensors": "MODIS,VIIRS",
        "frp_max": 55.556,
        "biomass_max_kg_s": 20.4444,
        "confidence_fused": "high",
        "footprint_area_km2": 12.34567,
        "geometry": POINT,
    }

    properties = event_feature(row)["properties"]

    assert properties["first_detection"] == "2022-08-01T14:30:00Z"
    assert (properties["frp_max_mw"], properties["footprint_area_km2"]) == (55.56, 12.346)
    assert properties["confidence"] == "high"


def test_fetch_collection_not_truncated_does_not_count():
    connection = FakeConnection([detection_row(0), detection_row(1)])
    query = detections_query(Filters(limit=5))

    collection = fetch_collection(connection, query, detection_feature)

    assert (collection["count"], collection["total"], collection["truncated"]) == (2, 2, False)
    assert len(connection.calls) == 1  # sin consulta de recuento
    assert connection.calls[0][1][-1] == 6  # se pide limit + 1


def test_fetch_collection_truncated_reports_the_real_total():
    rows = [detection_row(index) for index in range(4)]  # limit 3 -> pide 4 y hay 4
    connection = FakeConnection(rows, [{"total": 1234}])
    query = detections_query(Filters(limit=3))

    collection = fetch_collection(connection, query, detection_feature)

    assert (collection["count"], collection["total"], collection["truncated"]) == (3, 1234, True)
    ids = [feature["properties"]["detection_id"] for feature in collection["features"]]
    assert ids == ["id0", "id1", "id2"]
    assert collection["type"] == "FeatureCollection"


def test_fetch_summary_formats_dates():
    connection = FakeConnection(
        [
            {
                "valid_detections": 10,
                "skipped_detections": 5,
                "first_detection": WHEN,
                "last_detection": WHEN,
            }
        ],
        [{"events": 2, "active_events": 1}],
    )

    summary = fetch_summary(connection)

    assert summary["valid_detections"] == 10 and summary["active_events"] == 1
    assert summary["first_detection"] == "2022-08-01T14:30:00Z"


def test_limits_are_bounded():
    assert queries.DEFAULT_LIMIT <= queries.MAX_LIMIT


# --- Histórico de una zona -------------------------------------------------------------------


def test_history_periods_shift_the_same_dates_back_each_year():
    periods = queries.history_periods(date(2025, 7, 1), date(2025, 7, 31), years=3)

    assert [(p.label, p.start, p.end, p.is_reference) for p in periods] == [
        ("2025", date(2025, 7, 1), date(2025, 7, 31), True),
        ("2024", date(2024, 7, 1), date(2024, 7, 31), False),
        ("2023", date(2023, 7, 1), date(2023, 7, 31), False),
        ("2022", date(2022, 7, 1), date(2022, 7, 31), False),
    ]


def test_history_periods_handle_year_crossing_and_leap_days():
    crossing = queries.history_periods(date(2024, 12, 20), date(2025, 1, 10), years=1)
    leap = queries.history_periods(date(2024, 2, 29), date(2024, 3, 1), years=1)

    assert [p.label for p in crossing] == ["2024-2025", "2023-2024"]
    assert leap[1].start == date(2023, 2, 28)


@pytest.mark.parametrize(
    ("start", "end"),
    [(date(2025, 2, 1), date(2025, 1, 1)), (date(2024, 1, 1), date(2025, 6, 1))],
)
def test_history_periods_reject_reversed_or_longer_than_a_year(start, end):
    with pytest.raises(queries.InvalidPeriodError):
        queries.history_periods(start, end, years=5)


def test_history_queries_put_period_params_first_then_bbox_then_limit():
    periods = queries.history_periods(date(2025, 7, 1), date(2025, 7, 31), years=2)

    summary_sql, summary_params = queries.history_summary_query(SPAIN_BBOX, periods)
    events_sql, events_params = queries.history_events_query(SPAIN_BBOX, periods, 50)

    assert summary_sql.count("%s") == len(summary_params) == 3 * 4 + 4
    assert events_sql.count("%s") == len(events_params) == 3 * 4 + 4 + 1
    assert summary_params[:4] == (
        "2025",
        True,
        datetime(2025, 7, 1, tzinfo=timezone.utc),
        datetime(2025, 8, 1, tzinfo=timezone.utc),  # fin incluido
    )
    assert summary_params[-4:] == SPAIN_BBOX and events_params[-1] == 50
    assert "LEFT JOIN matched m" in summary_sql  # años sin incendios también salen
    assert "WHERE NOT m.is_reference" in events_sql


def test_fetch_history_fills_empty_periods_and_tags_events_with_their_year():
    periods = queries.history_periods(date(2025, 7, 1), date(2025, 7, 31), years=2)
    totals = [
        {"label": "2025", "events": 2, "detections": 30, "frp_max": 80.123},
        {"label": "2024", "events": 1, "detections": 5, "frp_max": 12.0},
    ]
    event = {
        "period": "2024",
        "event_id": "evt_1",
        "status": "closed",
        "first_detection": WHEN,
        "last_detection": WHEN,
        "n_detections": 5,
        "sensors": "VIIRS",
        "frp_max": 12.0,
        "biomass_max_kg_s": 4.4,
        "confidence_fused": "medium",
        "footprint_area_km2": 1.2,
        "geometry": POINT,
    }
    connection = FakeConnection(totals, [event])

    result = queries.fetch_history(connection, SPAIN_BBOX, periods, 10)

    assert [p["label"] for p in result["periods"]] == ["2025", "2024", "2023"]
    assert result["periods"][0]["is_reference"] and result["periods"][0]["frp_max_mw"] == 80.12
    assert result["periods"][2] == {
        "label": "2023",
        "start": "2023-07-01",
        "end": "2023-07-31",
        "is_reference": False,
        "events": 0,
        "detections": 0,
        "frp_max_mw": None,
    }
    assert result["events"]["count"] == 1
    assert result["events"]["features"][0]["properties"]["period"] == "2024"


# --- Detalle de un incendio ------------------------------------------------------------------


def event_row() -> dict:
    return {
        "event_id": "evt_1",
        "status": "closed",
        "first_detection": WHEN,
        "last_detection": WHEN,
        "n_detections": 3,
        "sensors": "VIIRS",
        "frp_max": 30.0,
        "biomass_max_kg_s": 11.0,
        "confidence_fused": "medium",
        "footprint_area_km2": 2.5,
        "geometry": {"type": "Polygon", "coordinates": []},
    }


def test_event_detections_sql_draws_pixels_of_one_event():
    sql = queries.event_detections_sql()

    assert "WHERE ed.event_id = %s" in sql and sql.endswith("LIMIT %s")
    assert "ST_X(p.geom) - d.area_width_km * 500.0" in sql
    assert sql.count("%s") == 2


def test_fetch_event_detail_returns_none_for_unknown_event():
    connection = FakeConnection([])

    assert queries.fetch_event_detail(connection, "evt_missing") is None
    assert len(connection.calls) == 1  # no busca detecciones de un evento inexistente


def test_fetch_event_detail_returns_hull_and_pixels():
    connection = FakeConnection([event_row()], [detection_row(0), detection_row(1)])

    detail = queries.fetch_event_detail(connection, "evt_1", limit=5)

    assert detail["event"]["properties"]["event_id"] == "evt_1"
    assert detail["event"]["geometry"]["type"] == "Polygon"
    assert detail["detections"]["count"] == 2 and not detail["detections"]["truncated"]
    assert connection.calls[1][1] == ("evt_1", 6)


def test_fetch_event_detail_truncates_and_reports_total_detections():
    connection = FakeConnection([event_row()], [detection_row(i) for i in range(3)])

    detail = queries.fetch_event_detail(connection, "evt_1", limit=2)

    assert (detail["detections"]["count"], detail["detections"]["total"]) == (2, 3)
    assert detail["detections"]["truncated"]


def test_fetch_activity_formats_recent_events_and_limits_the_query():
    row = {
        "event_id": "ev1",
        "status": "active",
        "sensors": "MODIS,VIIRS",
        "n_detections": 7,
        "frp_max": 12.345,
        "first_detection": WHEN,
        "last_detection": WHEN,
        "lat": 42.123456,
        "lon": -7.654321,
    }
    connection = FakeConnection([row])

    data = queries.fetch_activity(connection, 3)

    assert data["count"] == 1
    assert data["items"][0]["event_id"] == "ev1"
    assert data["items"][0]["frp_max_mw"] == 12.35
    assert data["items"][0]["last_detection"] == "2022-08-01T14:30:00Z"
    assert data["items"][0]["lat"] == 42.12346
    assert "ORDER BY last_detection DESC" in connection.calls[0][0]
    assert connection.calls[0][1] == (3,)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2025, 8, 12), date(2025, 8, 18), (812, 818)),
        (date(2025, 12, 25), date(2026, 1, 5), (1225, 105)),  # cruza el fin de año
        (date(2024, 3, 1), date(2025, 8, 1), (101, 1231)),  # un año o más: todo el año
    ],
)
def test_day_window_covers_the_same_dates_of_every_year(start, end, expected):
    assert queries.day_window(start, end) == expected


def test_day_window_rejects_reversed_ranges():
    with pytest.raises(queries.InvalidPeriodError):
        queries.day_window(date(2025, 9, 1), date(2025, 8, 1))


def test_day_window_filter_replaces_the_date_range_in_detections_and_events():
    filters = Filters(
        bbox=SPAIN_BBOX, start=date(2025, 8, 12), end=date(2025, 8, 18), day_window=(812, 818)
    )

    detections = detections_query(filters)
    events = events_query(filters)
    inside = events_query(filters, match="inside")

    assert "BETWEEN %s AND %s" in detections.sql
    assert "d.acq_datetime >= %s" not in detections.sql
    assert detections.params[-2:] == (812, 818)
    assert events.sql.count("BETWEEN %s AND %s") == 2  # día del inicio o del final del evento
    assert "e.last_detection >= %s" not in events.sql
    assert "BETWEEN %s AND %s" in inside.sql
    for query in (detections, events, inside):
        assert query.sql.count("%s") == len(query.params) + 1  # + LIMIT


def test_day_window_crossing_new_year_uses_or():
    query = detections_query(Filters(day_window=(1225, 105)))

    assert ">= %s OR" in query.sql and "<= %s)" in query.sql
    assert query.params[-2:] == (1225, 105)


def test_yearly_query_groups_valid_detections_by_year_for_the_same_dates():
    filters = Filters(bbox=SPAIN_BBOX, source="VIIRS", day_window=(812, 818))

    query = queries.yearly_query(filters)

    assert "GROUP BY year ORDER BY year DESC" in query.sql and "d.is_valid" in query.sql
    assert query.params == (*SPAIN_BBOX, 812, 818, "VIIRS")
    assert query.sql.count("%s") == len(query.params)


def test_yearly_query_needs_a_day_window():
    with pytest.raises(ValueError):
        queries.yearly_query(Filters(bbox=SPAIN_BBOX))


def test_fetch_yearly_fills_years_without_data_between_the_first_and_the_last():
    rows = [
        {"year": 2024, "events": 3, "detections": 40, "frp_max": 55.123},
        {"year": 2022, "events": 1, "detections": 5, "frp_max": None},
    ]
    connection = FakeConnection(rows)

    result = queries.fetch_yearly(connection, Filters(day_window=(812, 818)))

    assert result["window"] == [812, 818]
    assert [year["year"] for year in result["years"]] == [2024, 2023, 2022]
    assert result["years"][0] == {"year": 2024, "events": 3, "detections": 40, "frp_max_mw": 55.12}
    assert result["years"][1] == {"year": 2023, "events": 0, "detections": 0, "frp_max_mw": None}


def test_fetch_yearly_without_data_returns_no_years():
    connection = FakeConnection([])

    assert queries.fetch_yearly(connection, Filters(day_window=(812, 818)))["years"] == []
