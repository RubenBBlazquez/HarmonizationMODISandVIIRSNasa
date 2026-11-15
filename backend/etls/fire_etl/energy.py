"""
Energía radiada y biomasa quemada estimadas de cada evento, a partir de sus pasadas de satélite.

El FRP (MW = MJ/s) es la potencia del fuego en el instante de la pasada. Para pasar a energía
(FRE, MJ) hay que integrarlo en el tiempo, pero los satélites solo ven el fuego unas pocas veces al
día. Se usa una aproximación sencilla y explícita:

1. Las detecciones de un evento separadas menos de ``pass_gap_minutes`` son una **pasada**.
2. El FRP de la pasada es la suma de sus detecciones; si MODIS y VIIRS vieron el mismo fuego en la
   misma pasada se toma el **mayor de los dos sensores** (no se suman, para no contar dos veces).
3. Cada pasada representa un intervalo de hasta ``pass_window_hours`` horas centrado en ella; entre
   dos pasadas cercanas el intervalo se reparte por la mitad. Una pasada aislada representa la
   ventana completa.
4. FRE = suma de FRP(pasada) x horas(pasada) x 3600; biomasa quemada = FRE x coeficiente (kg/MJ).

Es una **estimación**: supone el FRP constante dentro de cada ventana, ignora el ciclo diario del
fuego y no ve lo que los satélites no detectan (fuegos pequeños, nubes, humo), así que sirve para
comparar incendios, no como medida exacta.
"""

from __future__ import annotations

import pandas as pd

from fire_etl.config import BiomassConfig

SECONDS_PER_HOUR = 3600
ENERGY_COLUMNS = ("n_passes", "max_pass_gap_hours", "fre_mj", "biomass_burned_kg")


def _passes(detections: pd.DataFrame, gap: pd.Timedelta) -> pd.DataFrame:
    """Una fila por pasada: evento, instante (el primero) y FRP de la pasada (MW)."""
    ordered = detections.loc[:, ["event_id", "acq_datetime", "source", "frp"]].sort_values(
        ["event_id", "acq_datetime"], kind="stable", ignore_index=True
    )
    same_event = ordered["event_id"].eq(ordered["event_id"].shift())
    new_pass = ~same_event | (ordered["acq_datetime"].diff() > gap)
    ordered["pass_id"] = new_pass.cumsum()

    per_sensor = ordered.groupby(["pass_id", "source"])["frp"].sum()
    passes = ordered.groupby("pass_id").agg(
        event_id=("event_id", "first"), time=("acq_datetime", "min")
    )
    passes["frp_pass"] = per_sensor.groupby("pass_id").max()

    return passes.sort_values(["event_id", "time"])


def event_energy(detections: pd.DataFrame, config: BiomassConfig) -> pd.DataFrame:
    """
    Pasadas, energía radiada y biomasa quemada estimada de cada evento.

    Parameters
    ----------
    detections : pd.DataFrame
        Detecciones de los eventos con ``event_id``, ``acq_datetime``, ``source`` y ``frp``.
    config : BiomassConfig
        Coeficiente (kg/MJ) y parámetros de pasada y ventana.

    Returns
    -------
    pd.DataFrame
        Indexado por ``event_id``: ``n_passes``, ``max_pass_gap_hours`` (mayor hueco entre
        pasadas; 0 si solo hay una), ``fre_mj`` y ``biomass_burned_kg``.
    """
    if detections.empty:
        return pd.DataFrame(
            columns=list(ENERGY_COLUMNS), index=pd.Index([], name="event_id"), dtype="float64"
        )

    passes = _passes(detections, pd.Timedelta(minutes=config.pass_gap_minutes))
    by_event = passes.groupby("event_id")["time"]
    gap_previous = by_event.diff().dt.total_seconds() / SECONDS_PER_HOUR
    gap_next = (-by_event.diff(-1)).dt.total_seconds() / SECONDS_PER_HOUR

    half = config.pass_window_hours / 2
    left = (gap_previous / 2).clip(upper=half).fillna(half)  # sin pasada anterior: media ventana
    right = (gap_next / 2).clip(upper=half).fillna(half)
    passes["fre_mj"] = passes["frp_pass"] * (left + right) * SECONDS_PER_HOUR

    result = passes.groupby("event_id").agg(n_passes=("time", "size"), fre_mj=("fre_mj", "sum"))
    result["max_pass_gap_hours"] = gap_next.groupby(passes["event_id"]).max().fillna(0.0)
    result["biomass_burned_kg"] = result["fre_mj"] * config.coefficient_kg_per_mj

    return result.loc[:, list(ENERGY_COLUMNS)]
