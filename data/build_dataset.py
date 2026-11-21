#!/usr/bin/env python3
"""
Build the two training datasets for the Tampa Bay monitor.

  flood      hourly table per tide station: backward-looking features +
             forward-looking labels (surge / high water / flash flood warning
             within the next H hours).
  pollution  one row per water-quality sample (location + time): measured
             parameters, danger labels, and "cascade" features (rain, surge
             residual) joined from the flood dataset.

Usage
-----
  pip install requests pandas numpy            # geopandas only if reading a .shp

  python build_datasets.py flood \
      --start 2018-01-01 --end 2025-12-31 \
      --flood-threshold-m 1.0 \
      --ff-warnings data/iem_tbw_flash_flood.csv

  python build_datasets.py pollution --start 2018-01-01 --end 2025-12-31 \
      --habs-csv data/fwc_habs.csv            # --habs-csv is optional

  python build_datasets.py all ...             # flood, then pollution

For a water-level-only flood target, use --target-mode highwater and pass a
station-specific --flood-threshold-m converted to MLLW. The same threshold is
applied to every station passed to --stations.

Things you must supply or verify (the script warns when they are missing)
-------------------------------------------------------------------------
  * --flood-threshold-m : a water level in metres above MLLW that counts as
        flooding. NWS/NOS flood levels per station are published by the CO-OPS
        Metadata API; convert them to MLLW metres before using them here.
  * --ff-warnings : historical NWS Flash Flood Warnings. Download from
        https://mesonet.agron.iastate.edu/request/gis/watchwarn.phtml
        (office TBW, phenomena Flash Flood, your date range, CSV or shapefile).
  * --habs-csv : red tide cell counts. FWC provides these on request
        (HABdata@MyFWC.com). Needs date, latitude, longitude, and cells/L columns.
  * Red tide thresholds below: confirm against the FWC abundance key.
  * Rainfall comes from Open-Meteo's ERA5-based archive as a convenient proxy.
        Swap in MRMS/Stage IV or SWFWMD gauge data for better flash flood signal.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("tampabay")

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
APP_NAME = "tampa_bay_monitor"

COOPS_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
WQP_RESULT_URL = "https://www.waterqualitydata.us/data/Result/search"   # legacy endpoint
WQP_STATION_URL = "https://www.waterqualitydata.us/data/Station/search"
OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"

# Confirm station IDs on the CO-OPS station map before relying on them.
STATIONS = ["8726520", "8726607"]        # St. Petersburg, Old Port Tampa
PRIMARY_STATION = "8726520"              # also supplies pressure + wind
DATUM = "MLLW"

RAIN_LAT, RAIN_LON = 27.70, -82.60       # approximate bay centroid
BAY_BBOX = (-82.90, 27.35, -82.35, 28.10)  # west, south, east, north (for WQP)

# Labels
SURGE_RESIDUAL_M = 0.30          # observed - predicted tide, metres
ENTEROCOCCI_POOR = 71            # per 100 mL, Florida marine "Poor"
DO_STRESS_MGL = 4.0
DO_HYPOXIA_MGL = 2.0
RED_TIDE_MEDIUM = 100_000        # cells/L (verify against FWC key)
RED_TIDE_HIGH = 1_000_000        # cells/L (FWC "high")

FF_UGC_PREFIXES = ("FLC057", "FLC103", "FLC081", "FLC101")  # Hillsborough, Pinellas, Manatee, Pasco

# WQP characteristic -> column. Names are matched exactly by WQP; run a
# no-filter query once to see which names your region actually uses.
WQP_CHARS = {
    "Chlorophyll a": "chl_a_ugL",
    "Dissolved oxygen (DO)": "do_mgL",
    "Salinity": "salinity_psu",
    "Temperature, water": "water_temp_c",
    "Turbidity": "turbidity_ntu",
    "Secchi depth": "secchi_m",
    "Total nitrogen, mixed forms": "tn_mgL",
    "Enterococcus": "enterococci_per100mL",
    "Fecal Coliform": "fecal_coliform_per100mL",
}
# unit (lowercase, no spaces) -> (factor, offset) to reach the column's unit
UNIT_RULES = {
    "chl_a_ugL": {"ug/l": (1, 0), "mg/m3": (1, 0), "mg/l": (1000, 0)},
    "do_mgL": {"mg/l": (1, 0), "ug/l": (0.001, 0)},
    "salinity_psu": {"ppt": (1, 0), "psu": (1, 0), "pss": (1, 0), "ppth": (1, 0)},
    "water_temp_c": {"degc": (1, 0), "degf": (5 / 9, -32 * 5 / 9)},
    "turbidity_ntu": {"ntu": (1, 0), "fnu": (1, 0), "ftu": (1, 0)},
    "secchi_m": {"m": (1, 0), "ft": (0.3048, 0), "cm": (0.01, 0)},
    "tn_mgL": {"mg/l": (1, 0), "ug/l": (0.001, 0)},
    "enterococci_per100mL": {"cfu/100ml": (1, 0), "mpn/100ml": (1, 0), "#/100ml": (1, 0)},
    "fecal_coliform_per100mL": {"cfu/100ml": (1, 0), "mpn/100ml": (1, 0), "#/100ml": (1, 0)},
}
# How to collapse several results for the same place/time (e.g. several depths)
AGG_RULES = {"do_mgL": "min", "enterococci_per100mL": "max", "fecal_coliform_per100mL": "max"}


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=5, backoff_factor=1.5,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = f"{APP_NAME}/0.1 (research)"
    return s


SESSION = _make_session()


def cached_get(url: str, params: dict, cache_dir: Path, *, as_json: bool,
               cacheable: bool = True, timeout: int = 180):
    """GET with an on-disk cache so reruns don't re-download finished chunks."""
    key = hashlib.sha1((url + json.dumps(params, sort_keys=True, default=str)).encode()).hexdigest()[:20]
    path = cache_dir / f"{key}.{'json' if as_json else 'csv'}"
    if cacheable and path.exists():
        text = path.read_text()
    else:
        time.sleep(1.0)
        r = SESSION.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        text = r.text
        ok = True
        if as_json:
            try:
                ok = "error" not in json.loads(text)   # CO-OPS reports errors with HTTP 200
            except json.JSONDecodeError:
                ok = False
        if cacheable and ok:
            cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    return json.loads(text) if as_json else text


def date_chunks(start: date, end: date, days: int):
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=days - 1), end)
        yield cur, nxt
        cur = nxt + timedelta(days=1)


def _is_recent(d: date) -> bool:
    return d >= date.today() - timedelta(days=3)


# --------------------------------------------------------------------------- #
# NOAA CO-OPS
# --------------------------------------------------------------------------- #
def coops_fetch(station: str, product: str, start: date, end: date, cache_dir: Path,
                extra: dict | None = None) -> pd.DataFrame:
    """Download a CO-OPS product in 31-day chunks (the API limits request size)."""
    rows: list[dict] = []
    key = "predictions" if product == "predictions" else "data"
    for b, e in date_chunks(start, end, 31):
        params = {
            "station": station, "product": product,
            "begin_date": b.strftime("%Y%m%d"), "end_date": e.strftime("%Y%m%d"),
            "time_zone": "gmt", "units": "metric", "format": "json", "application": APP_NAME,
        }
        if product in ("water_level", "predictions"):
            params["datum"] = DATUM
        if extra:
            params.update(extra)
        try:
            payload = cached_get(COOPS_URL, params, cache_dir, as_json=True, cacheable=not _is_recent(e))
        except requests.RequestException as exc:
            raise RuntimeError(f"CO-OPS {station} {product} {b}..{e} failed: {exc}") from exc
        if "error" in payload:
            message = payload["error"].get("message")
            if product in {"water_level", "predictions"}:
                raise RuntimeError(f"CO-OPS {station} {product} {b}..{e}: {message}")
            log.warning("CO-OPS %s %s %s..%s unavailable: %s", station, product, b, e, message)
            continue
        rows.extend(payload.get(key, []))
    return pd.DataFrame(rows)


def tidy(df: pd.DataFrame, rename: dict[str, str]) -> pd.DataFrame:
    """Index by UTC time, keep/rename columns, coerce to numbers."""
    cols = list(rename.values())
    if df.empty:
        return pd.DataFrame(columns=cols, index=pd.DatetimeIndex([], tz="UTC", name="t"), dtype=float)
    out = df.copy()
    out["t"] = pd.to_datetime(out["t"], utc=True)
    out = out.set_index("t").reindex(columns=list(rename)).rename(columns=rename)
    out = out.apply(pd.to_numeric, errors="coerce")
    return out[~out.index.duplicated()].sort_index()


def build_station_hourly(station: str, start: date, end: date, cache_dir: Path) -> pd.DataFrame:
    wl = tidy(coops_fetch(station, "water_level", start, end, cache_dir), {"v": "wl"})
    pr = tidy(coops_fetch(station, "predictions", start, end, cache_dir), {"v": "pred"})
    if wl.empty:
        log.warning("No water level data for station %s", station)
    six = wl.join(pr, how="outer")
    six["resid"] = six["wl"] - six["pred"]            # storm surge residual
    return pd.DataFrame({
        "wl_mean": six["wl"].resample("1h").mean(),
        "wl_max": six["wl"].resample("1h").max(),
        "pred_mean": six["pred"].resample("1h").mean(),
        "resid_mean": six["resid"].resample("1h").mean(),
        "resid_max": six["resid"].resample("1h").max(),
    })


def build_met_hourly(station: str, start: date, end: date, cache_dir: Path) -> pd.DataFrame:
    pres = tidy(coops_fetch(station, "air_pressure", start, end, cache_dir), {"v": "pressure"})
    wind = tidy(coops_fetch(station, "wind", start, end, cache_dir), {"s": "wspd", "g": "gust", "d": "wdir"})
    rad = np.deg2rad(wind["wdir"])
    sin_m = np.sin(rad).resample("1h").mean()
    cos_m = np.cos(rad).resample("1h").mean()
    return pd.DataFrame({
        "pressure": pres["pressure"].resample("1h").mean(),
        "wind_speed_mean": wind["wspd"].resample("1h").mean(),
        "wind_gust_max": wind["gust"].resample("1h").max(),
        "wind_dir_mean": (np.rad2deg(np.arctan2(sin_m, cos_m)) + 360) % 360,
    })


# --------------------------------------------------------------------------- #
# Rainfall (Open-Meteo ERA5 archive; replace with MRMS / gauges if you can)
# --------------------------------------------------------------------------- #
def fetch_rain(start: date, end: date, cache_dir: Path) -> pd.Series:
    end = min(end, date.today() - timedelta(days=6))   # archive lags a few days
    parts = []
    for b, e in date_chunks(start, end, 365):
        params = {"latitude": RAIN_LAT, "longitude": RAIN_LON, "start_date": b.isoformat(),
                  "end_date": e.isoformat(), "hourly": "precipitation", "timezone": "GMT"}
        try:
            payload = cached_get(OPEN_METEO_URL, params, cache_dir, as_json=True, cacheable=not _is_recent(e))
        except requests.RequestException as exc:
            log.warning("Open-Meteo %s..%s failed: %s", b, e, exc)
            continue
        h = payload.get("hourly", {})
        parts.append(pd.Series(h.get("precipitation", []),
                               index=pd.to_datetime(h.get("time", []), utc=True), dtype=float))
    if not parts:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    s = pd.concat(parts)
    return s[~s.index.duplicated()].sort_index().rename("rain_1h")


# --------------------------------------------------------------------------- #
# NWS flash flood warnings (file downloaded from the Iowa Environmental Mesonet)
# --------------------------------------------------------------------------- #
def load_ff_warnings(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() in (".shp", ".zip", ".geojson", ".gpkg"):
        import geopandas as gpd   # optional dependency
        df = pd.DataFrame(gpd.read_file(p).drop(columns="geometry", errors="ignore"))
    else:
        df = pd.read_csv(p, low_memory=False)
    df.columns = [c.upper() for c in df.columns]

    def pick(*names):
        for n in names:
            if n in df.columns:
                return n
        raise KeyError(f"None of {names} found in {list(df.columns)}")

    def parse(col):
        t = pd.to_datetime(df[col].astype(str), format="%Y%m%d%H%M", utc=True, errors="coerce")
        return t if t.notna().any() else pd.to_datetime(df[col], utc=True, errors="coerce")

    out = pd.DataFrame({"issued": parse(pick("ISSUED", "ISSUE", "INIT_ISS")),
                        "expired": parse(pick("EXPIRED", "EXPIRE", "INIT_EXP"))})
    mask = pd.Series(True, index=df.index)
    if "PHENOM" in df.columns:
        mask &= df["PHENOM"].astype(str).eq("FF")
    if "SIG" in df.columns:
        mask &= df["SIG"].astype(str).eq("W")
    if "NWS_UGC" in df.columns:
        mask &= df["NWS_UGC"].astype(str).str.startswith(FF_UGC_PREFIXES)
    elif "WFO" in df.columns:
        log.warning("No NWS_UGC column; filtering to WFO TBW (covers more than the bay counties).")
        mask &= df["WFO"].astype(str).str.upper().isin(["TBW", "KTBW"])
    out = out[mask].dropna()
    log.info("Loaded %d flash flood warnings", len(out))
    return out


def warnings_to_hourly_flag(warn: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    flag = pd.Series(0.0, index=index)
    for iss, exp in zip(warn["issued"], warn["expired"]):
        flag.loc[iss.floor("h"):exp.floor("h")] = 1.0
    return flag


# --------------------------------------------------------------------------- #
# Flood / surge dataset
# --------------------------------------------------------------------------- #
def forward_max(s: pd.Series, h: int) -> pd.Series:
    """Max of s over the NEXT h steps (t+1 .. t+h). NaN-skipping."""
    return s.shift(-1)[::-1].rolling(h, min_periods=1).max()[::-1]


def add_features_and_labels(df: pd.DataFrame, horizon: int, flood_threshold_m: float | None,
                            ff_flag: pd.Series | None, target_mode: str = "combined") -> pd.DataFrame:
    """df: hourly, regular UTC index, one station. Features look back; labels look forward."""
    if target_mode not in {"combined", "highwater", "surge"}:
        raise ValueError(f"Unknown flood target mode: {target_mode}")
    if target_mode == "highwater" and flood_threshold_m is None:
        raise ValueError("A flood threshold is required for the highwater target.")

    df = df.copy()
    df["resid_max_6h"] = df["resid_max"].rolling(6, min_periods=1).max()
    df["resid_max_24h"] = df["resid_max"].rolling(24, min_periods=1).max()
    df["resid_trend_3h"] = df["resid_mean"] - df["resid_mean"].shift(3)
    if "pressure" in df:
        df["pressure_trend_3h"] = df["pressure"] - df["pressure"].shift(3)
    for w in (1, 3, 6, 24, 72, 168):
        df[f"rain_{w}h"] = df["rain_1h"].rolling(w, min_periods=1).sum()
    df["month"] = df.index.month
    df["hour"] = df.index.hour

    labels = []
    fm = forward_max(df["resid_max"], horizon)
    df[f"surge_next_{horizon}h"] = fm.ge(SURGE_RESIDUAL_M).astype(float).where(fm.notna())
    if target_mode in {"combined", "surge"}:
        labels.append(f"surge_next_{horizon}h")

    if flood_threshold_m is not None:
        fm = forward_max(df["wl_max"], horizon)
        df[f"highwater_next_{horizon}h"] = fm.ge(flood_threshold_m).astype(float).where(fm.notna())
        if target_mode in {"combined", "highwater"}:
            labels.append(f"highwater_next_{horizon}h")
    if ff_flag is not None and target_mode == "combined":
        df[f"ffwarn_next_{horizon}h"] = forward_max(ff_flag.reindex(df.index).fillna(0.0), horizon)
        labels.append(f"ffwarn_next_{horizon}h")

    target = f"flood_risk_next_{horizon}h"
    df[target] = df[labels].max(axis=1, skipna=True).where(df[labels].notna().any(axis=1))
    df = df.iloc[:-horizon]                         # incomplete look-ahead window at the end
    df = df[df["wl_mean"].notna() | df["resid_mean"].notna()]
    df = df.dropna(subset=[
        "resid_mean", "resid_max", "resid_max_6h", "resid_max_24h", target,
    ])
    df["split_group"] = df.index.strftime("%Y-%m")  # group CV folds by month (or by storm)
    return df


def build_flood_dataset(start: date, end: date, out_dir: Path, *, horizon: int,
                        flood_threshold_m: float | None, ff_path: str | None,
                        stations: list[str] | None = None,
                        target_mode: str = "combined") -> pd.DataFrame:
    cache = out_dir / "raw_cache"
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23),
                        freq="1h")
    log.info("Fetching rainfall ...")
    rain = fetch_rain(start, end, cache).reindex(idx).rename("rain_1h")
    log.info("Fetching meteorology from %s ...", PRIMARY_STATION)
    met = build_met_hourly(PRIMARY_STATION, start, end, cache).reindex(idx)

    ff_flag = None
    if ff_path:
        ff_flag = warnings_to_hourly_flag(load_ff_warnings(ff_path), idx)
    else:
        log.warning("No --ff-warnings file: flash flood label omitted.")
    if flood_threshold_m is None:
        log.warning("No --flood-threshold-m: high-water label omitted.")

    frames = []
    for st in stations or STATIONS:
        log.info("Fetching water level for %s ...", st)
        base = build_station_hourly(st, start, end, cache).reindex(idx)
        base = base.join(met).join(rain)
        base = add_features_and_labels(base, horizon, flood_threshold_m, ff_flag, target_mode)
        base.insert(0, "station", st)
        frames.append(base)
    out = pd.concat(frames)
    out.index.name = "time_utc"
    path = out_dir / "flood_dataset.csv"
    out.to_csv(path)
    target = f"flood_risk_next_{horizon}h"
    log.info("Wrote %s  rows=%d  positive rate=%.3f", path, len(out), out[target].mean())
    return out


# --------------------------------------------------------------------------- #
# Pollution dataset
# --------------------------------------------------------------------------- #
WQP_SERVICES = {
    # name: (results url, station url, results profile)
    "wqx3": ("https://www.waterqualitydata.us/wqx3/Result/search",
             "https://www.waterqualitydata.us/wqx3/Station/search", "fullPhysChem"),
    "legacy": (WQP_RESULT_URL, WQP_STATION_URL, "narrowResult"),
}

# The legacy and WQX3 schemas name columns differently; everything is mapped to the legacy names.
WQP_COLUMN_ALIASES = {
    "CharacteristicName": ["CharacteristicName", "Result_Characteristic"],
    "ResultMeasureValue": ["ResultMeasureValue", "Result_Measure"],
    "ResultMeasure/MeasureUnitCode": ["ResultMeasure/MeasureUnitCode", "Result_MeasureUnit"],
    "ActivityStartDate": ["ActivityStartDate", "Activity_StartDate"],
    "ActivityStartTime/Time": ["ActivityStartTime/Time", "Activity_StartTime"],
    "MonitoringLocationIdentifier": ["MonitoringLocationIdentifier", "Location_Identifier"],
    "MonitoringLocationName": ["MonitoringLocationName", "Location_Name"],
    "LatitudeMeasure": ["LatitudeMeasure", "Location_Latitude", "Location_LatitudeStandardized"],
    "LongitudeMeasure": ["LongitudeMeasure", "Location_Longitude", "Location_LongitudeStandardized"],
}


def canonicalize_wqp(df: pd.DataFrame) -> pd.DataFrame:
    """Rename WQX3 columns to the legacy names used by the rest of the script."""
    rename = {}
    for canon, aliases in WQP_COLUMN_ALIASES.items():
        if canon in df.columns:
            continue
        for a in aliases:
            if a in df.columns:
                rename[a] = canon
                break
    return df.rename(columns=rename)


def _wqp_params(start: date, end: date) -> dict:
    w, s, e, n = BAY_BBOX
    return {"bBox": f"{w},{s},{e},{n}", "sampleMedia": "Water", "mimeType": "csv", "zip": "no",
            "startDateLo": start.strftime("%m-%d-%Y"), "startDateHi": end.strftime("%m-%d-%Y")}


def _wqp_get_text(url: str, params: dict, cache_dir: Path, cacheable: bool = True) -> str | None:
    """GET that logs WQP's actual error message instead of just 'Client Error'."""
    try:
        return cached_get(url, params, cache_dir, as_json=False, cacheable=cacheable)
    except requests.HTTPError as exc:
        r = exc.response
        body = (r.text or "")[:300].replace("\n", " ") if r is not None else ""
        warn = r.headers.get("Warning", "") if r is not None else ""
        log.warning("WQP HTTP %s from %s | body: %s | warning: %s",
                    r.status_code if r is not None else "?", url, body, warn)
    except requests.RequestException as exc:
        log.warning("WQP request failed: %s", exc)
    return None


def choose_wqp_service(cache_dir: Path, requested: str) -> str:
    """Probe the WQP endpoints and return the first one that answers."""
    probe = date(2023, 1, 1), date(2023, 12, 31)
    for name in (["wqx3", "legacy"] if requested == "auto" else [requested]):
        text = _wqp_get_text(WQP_SERVICES[name][1], _wqp_params(*probe) | {"characteristicName": "Salinity"},
                             cache_dir)
        if text is not None and text.strip():
            log.info("Using WQP service: %s", name)
            return name
        log.warning("WQP service %r did not return usable data", name)
    raise SystemExit("Neither WQP service answered. Open the station URL in a browser to see the error: "
                     + WQP_SERVICES["wqx3"][1] + "?bBox=-82.9,27.35,-82.35,28.1&characteristicName=Salinity&mimeType=csv")


def valid_characteristics(service: str, cache_dir: Path) -> list[str]:
    """WQP returns HTTP 400 for unrecognised characteristic names, so test each one."""
    st_url = WQP_SERVICES[service][1]
    probe = date(2023, 1, 1), date(2023, 12, 31)
    ok = []
    for name in WQP_CHARS:
        if _wqp_get_text(st_url, _wqp_params(*probe) | {"characteristicName": name}, cache_dir) is None:
            log.warning("Dropping characteristic %r (rejected by WQP)", name)
        else:
            ok.append(name)
    log.info("Using %d/%d characteristics", len(ok), len(WQP_CHARS))
    return ok


def fetch_wqp_results(start: date, end: date, cache_dir: Path, service: str, chars: list[str]) -> pd.DataFrame:
    url, _, profile = WQP_SERVICES[service]
    parts = []
    for b, e in date_chunks(start, end, 365):
        params = _wqp_params(b, e) | {"characteristicName": chars, "dataProfile": profile}
        text = _wqp_get_text(url, params, cache_dir, cacheable=not _is_recent(e))
        if text and text.strip():
            parts.append(canonicalize_wqp(pd.read_csv(io.StringIO(text), low_memory=False)))
            log.info("WQP %s..%s: %d results", b, e, len(parts[-1]))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fetch_wqp_stations(start: date, end: date, cache_dir: Path, service: str, chars: list[str]) -> pd.DataFrame:
    text = _wqp_get_text(WQP_SERVICES[service][1], _wqp_params(start, end) | {"characteristicName": chars}, cache_dir)
    if not text or not text.strip():
        return pd.DataFrame(columns=["MonitoringLocationIdentifier"])
    st = canonicalize_wqp(pd.read_csv(io.StringIO(text), low_memory=False))
    keep = ["MonitoringLocationIdentifier", "MonitoringLocationName", "LatitudeMeasure", "LongitudeMeasure"]
    return st[[c for c in keep if c in st.columns]].drop_duplicates("MonitoringLocationIdentifier")


def normalise_results(raw: pd.DataFrame) -> pd.DataFrame:
    """Map characteristic names to columns, convert units, localise timestamps."""
    needed = ["CharacteristicName", "ResultMeasureValue", "ResultMeasure/MeasureUnitCode",
              "ActivityStartDate", "MonitoringLocationIdentifier"]
    missing = [c for c in needed if c not in raw.columns]
    if missing:
        raise SystemExit(f"WQP response is missing columns {missing}. Columns received: {list(raw.columns)}")
    df = raw.copy()
    df["param"] = df["CharacteristicName"].map(WQP_CHARS)
    df = df[df["param"].notna()]
    df["value"] = pd.to_numeric(df["ResultMeasureValue"], errors="coerce")   # drops "<2"-style non-detects
    unit = (df["ResultMeasure/MeasureUnitCode"].astype(str).str.lower().str.replace(" ", "", regex=False))

    factors = np.full(len(df), np.nan)
    offsets = np.zeros(len(df))
    for param, rules in UNIT_RULES.items():
        m = (df["param"] == param).to_numpy()
        for u, (f, o) in rules.items():
            mm = m & (unit == u).to_numpy()
            factors[mm] = f
            offsets[mm] = o
    dropped = int((np.isnan(factors) & df["value"].notna().to_numpy()).sum())
    if dropped:
        log.info("Dropped %d results with unrecognised units", dropped)
    df["value"] = df["value"] * factors + offsets
    df = df[df["value"].notna() & (df["value"] >= 0)]

    time_col = "ActivityStartTime/Time"
    t = df[time_col].fillna("12:00:00") if time_col in df else "12:00:00"
    local = pd.to_datetime(df["ActivityStartDate"].astype(str) + " " + t, errors="coerce")
    local = local.dt.tz_localize("America/New_York", ambiguous="NaT", nonexistent="shift_forward")
    df["sample_time_utc"] = local.dt.tz_convert("UTC")
    return df.dropna(subset=["sample_time_utc"])


def pivot_samples(df: pd.DataFrame) -> pd.DataFrame:
    """One row per monitoring location per local calendar day.

    Grouping by day (not exact time) keeps parameters together when some results
    lack a time of day. sample_time_utc is the earliest timestamp in the group.
    """
    df = df.copy()
    df["_day"] = df["sample_time_utc"].dt.tz_convert("America/New_York").dt.date
    keys = ["MonitoringLocationIdentifier", "_day"]
    out = df.groupby(keys)["sample_time_utc"].min().to_frame()
    for p in df["param"].unique():
        s = df[df["param"] == p].groupby(keys)["value"].agg(AGG_RULES.get(p, "median")).rename(p)
        out = out.join(s, how="left")
    return out.reset_index().drop(columns="_day")


def _haversine_km(lat1, lon1, lat2, lon2):
    p = np.pi / 180
    a = (np.sin((lat2 - lat1) * p / 2) ** 2
         + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * np.arcsin(np.sqrt(a))


def add_red_tide(samples: pd.DataFrame, habs_csv: str, radius_km: float = 20, window_days: int = 7) -> pd.DataFrame:
    """Max K. brevis count within radius/window of each sample. Column names are guessed."""
    h = pd.read_csv(habs_csv, low_memory=False)
    low = {c.lower(): c for c in h.columns}

    def col(*names):
        for n in names:
            if n in low:
                return low[n]
        raise KeyError(f"{habs_csv}: none of {names} in {list(h.columns)}")

    h = pd.DataFrame({
        "t": pd.to_datetime(h[col("date", "sample_date", "sampledate", "sample date")], utc=True, errors="coerce"),
        "lat": pd.to_numeric(h[col("latitude", "lat")], errors="coerce"),
        "lon": pd.to_numeric(h[col("longitude", "lon", "long")], errors="coerce"),
        "cells": pd.to_numeric(h[col("cells_per_l", "cellcount", "cells/l", "k_brevis", "count")], errors="coerce"),
    }).dropna().sort_values("t")
    lat, lon, cells = h["lat"].to_numpy(), h["lon"].to_numpy(), h["cells"].to_numpy()
    win = np.timedelta64(window_days, "D")

    result = np.full(len(samples), np.nan)
    st = samples["sample_time_utc"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy()
    ht = h["t"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy()
    for i, (t, la, lo) in enumerate(zip(st, samples["LatitudeMeasure"], samples["LongitudeMeasure"])):
        a, b = np.searchsorted(ht, t - win), np.searchsorted(ht, t + win)
        if b > a and not (np.isnan(la) or np.isnan(lo)):
            near = _haversine_km(la, lo, lat[a:b], lon[a:b]) <= radius_km
            result[i] = cells[a:b][near].max() if near.any() else 0.0
    samples["k_brevis_max_cells_L"] = result
    return samples


def add_pollution_labels(df: pd.DataFrame) -> pd.DataFrame:
    def flag(cond_col, fn):
        if cond_col not in df:
            return pd.Series(np.nan, index=df.index)
        return fn(df[cond_col]).astype(float).where(df[cond_col].notna())

    df["enterococci_poor"] = flag("enterococci_per100mL", lambda s: s >= ENTEROCOCCI_POOR)
    df["do_stress"] = flag("do_mgL", lambda s: s < DO_STRESS_MGL)
    df["do_hypoxia"] = flag("do_mgL", lambda s: s < DO_HYPOXIA_MGL)
    df["red_tide_medium_plus"] = flag("k_brevis_max_cells_L", lambda s: s >= RED_TIDE_MEDIUM)
    df["red_tide_high"] = flag("k_brevis_max_cells_L", lambda s: s >= RED_TIDE_HIGH)
    # "dangerous" = bacteria poor, DO stress, or high red tide. Edit to suit your definition.
    parts = df[["enterococci_poor", "do_stress", "red_tide_high"]]
    df["pollution_dangerous"] = parts.max(axis=1, skipna=True).where(parts.notna().any(axis=1))
    return df


def build_pollution_dataset(start: date, end: date, out_dir: Path, *, habs_csv: str | None,
                            horizon: int, wqp_service: str = "auto") -> pd.DataFrame:
    cache = out_dir / "raw_cache"
    flood_path = out_dir / "flood_dataset.csv"
    if not flood_path.exists():
        raise SystemExit(f"{flood_path} not found. Run the 'flood' step first (it supplies rain/surge features).")

    service = choose_wqp_service(cache, wqp_service)
    chars = valid_characteristics(service, cache)
    if not chars:
        raise SystemExit("WQP rejected every characteristic name; see the warnings above.")
    log.info("Fetching WQP results ...")
    raw = fetch_wqp_results(start, end, cache, service, chars)
    if raw.empty:
        raise SystemExit("WQP returned no results. Check the bbox, dates, and the warnings above.")
    samples = pivot_samples(normalise_results(raw))
    samples = samples.merge(fetch_wqp_stations(start, end, cache, service, chars),
                            on="MonitoringLocationIdentifier", how="left")
    for c in ("LatitudeMeasure", "LongitudeMeasure"):
        if c not in samples.columns:
            log.warning("No %s in station data; spatial features and red tide matching will be empty.", c)
            samples[c] = np.nan

    if habs_csv:
        samples = add_red_tide(samples, habs_csv)
    else:
        log.warning("No --habs-csv: red tide labels omitted.")

    # Cascade features from the flood dataset (primary station, nearest previous hour)
    fl = pd.read_csv(flood_path, parse_dates=["time_utc"])
    fl = fl[fl["station"].astype(str) == PRIMARY_STATION]
    keep = ["time_utc", "rain_24h", "rain_72h", "rain_168h", "resid_max_6h", "resid_max_24h", "wind_speed_mean"]
    fl = fl[[c for c in keep if c in fl.columns]].sort_values("time_utc")
    samples = samples.sort_values("sample_time_utc")
    samples = pd.merge_asof(samples, fl, left_on="sample_time_utc", right_on="time_utc",
                            direction="backward", tolerance=pd.Timedelta("2h")).drop(columns="time_utc")

    samples = add_pollution_labels(samples)
    samples = samples[samples["pollution_dangerous"].notna()] if "pollution_dangerous" in samples else samples
    samples["month"] = samples["sample_time_utc"].dt.month
    samples["split_group"] = samples["sample_time_utc"].dt.year.astype(str)       # year folds
    samples["spatial_group"] = (samples["LatitudeMeasure"].round(1).astype(str) + "_"
                                + samples["LongitudeMeasure"].round(1).astype(str))
    path = out_dir / "pollution_dataset.csv"
    samples.to_csv(path, index=False)
    log.info("Wrote %s  rows=%d  dangerous rate=%.3f", path, len(samples), samples["pollution_dangerous"].mean())
    return samples


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["flood", "pollution", "all"])
    ap.add_argument("--start", type=date.fromisoformat, default=date(2018, 1, 1))
    ap.add_argument("--end", type=date.fromisoformat, default=date.today() - timedelta(days=1))
    ap.add_argument("--out", default="data")
    ap.add_argument("--horizon", type=int, default=6, help="label look-ahead window in hours")
    ap.add_argument("--flood-threshold-m", type=float, default=None, help="flood water level, m above MLLW")
    ap.add_argument("--ff-warnings", default=None, help="IEM flash flood warning CSV/shapefile")
    ap.add_argument("--stations", nargs="+", default=STATIONS, help="CO-OPS station IDs for flood dataset")
    ap.add_argument("--target-mode", choices=["combined", "highwater", "surge"], default="combined",
                    help="combined event label, water-level threshold, or surge-only label")
    ap.add_argument("--habs-csv", default=None, help="FWC red tide cell counts CSV")
    ap.add_argument("--wqp-service", choices=["auto", "wqx3", "legacy"], default="auto",
                    help="which Water Quality Portal endpoint to use")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if a.step in ("flood", "all"):
        if a.target_mode == "highwater" and a.flood_threshold_m is None:
            ap.error("--flood-threshold-m is required when --target-mode=highwater.")
        build_flood_dataset(a.start, a.end, out_dir, horizon=a.horizon,
                            flood_threshold_m=a.flood_threshold_m, ff_path=a.ff_warnings,
                            stations=a.stations, target_mode=a.target_mode)
    if a.step in ("pollution", "all"):
        build_pollution_dataset(a.start, a.end, out_dir, habs_csv=a.habs_csv, horizon=a.horizon,
                                wqp_service=a.wqp_service)


if __name__ == "__main__":
    main()