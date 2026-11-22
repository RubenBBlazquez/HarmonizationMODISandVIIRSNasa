"""
Estimated radiated energy and burned biomass of each event, from its satellite passes.

FRP (MW = MJ/s) is the power of the fire at the instant of the pass. To get to energy (FRE, MJ)
it has to be integrated over time, but satellites only see the fire a few times a day. A simple
and explicit approximation is used:

1. The detections of an event closer together than ``pass_gap_minutes`` form a **pass**.
2. The FRP of the pass is the sum of its detections; if MODIS and VIIRS saw the same fire in the
   same pass the **larger of the two sensors** is taken (they are not added, to avoid counting
   twice).
3. Each pass represents an interval of up to ``pass_window_hours`` hours centered on it; between
   two close passes the interval is split in half. An isolated pass represents the full
   window.
4. FRE = sum of FRP(pass) x hours(pass) x 3600; burned biomass = FRE x coefficient (kg/MJ).

It is an **estimate**: it assumes constant FRP within each window, ignores the daily cycle of
the fire and does not see what the satellites do not detect (small fires, clouds, smoke), so it
is useful to compare fires, not as an exact measure.
"""

from __future__ import annotations

import pandas as pd

from fire_etl.config import BiomassConfig

SECONDS_PER_HOUR = 3600
ENERGY_COLUMNS = ("n_passes", "max_pass_gap_hours", "fre_mj", "biomass_burned_kg")


def _passes(detections: pd.DataFrame, gap: pd.Timedelta) -> pd.DataFrame:
    """One row per pass: event, instant (the first) and FRP of the pass (MW)."""
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
    Passes, radiated energy and estimated burned biomass of each event.

    Parameters
    ----------
    detections : pd.DataFrame
        Detections of the events with ``event_id``, ``acq_datetime``, ``source`` and ``frp``.
    config : BiomassConfig
        Coefficient (kg/MJ) and pass and window parameters.

    Returns
    -------
    pd.DataFrame
        Indexed by ``event_id``: ``n_passes``, ``max_pass_gap_hours`` (largest gap between
        passes; 0 if there is only one), ``fre_mj`` and ``biomass_burned_kg``.
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
    left = (gap_previous / 2).clip(upper=half).fillna(half)  # no previous pass: half a window
    right = (gap_next / 2).clip(upper=half).fillna(half)
    passes["fre_mj"] = passes["frp_pass"] * (left + right) * SECONDS_PER_HOUR

    result = passes.groupby("event_id").agg(n_passes=("time", "size"), fre_mj=("fre_mj", "sum"))
    result["max_pass_gap_hours"] = gap_next.groupby(passes["event_id"]).max().fillna(0.0)
    result["biomass_burned_kg"] = result["fre_mj"] * config.coefficient_kg_per_mj

    return result.loc[:, list(ENERGY_COLUMNS)]
