#!/usr/bin/env python3
"""
Fetch current Tampa Bay water level data from NOAA CO-OPS, build the same four
features used in training, and run water_model.keras to estimate
flood_risk_next_6h.

  pip install requests pandas numpy keras tensorflow joblib

  python predict_flood.py                               # one prediction
  python predict_flood.py --loop 60                     # repeat every 60 minutes
  python predict_flood.py --scaler scaler.joblib        # if you scaled features in training
  python predict_flood.py --threshold 0.3 --station 8726520

The features are computed exactly as in build_datasets.py:
  1. 6-minute observed water level minus 6-minute predicted tide  = residual (m, MLLW)
  2. hourly mean and max of the residual                           = resid_mean, resid_max
  3. rolling max of the hourly resid_max over 6 and 24 hours       = resid_max_6h, resid_max_24h

IMPORTANT: the order of SELECTED_FEATURES must match the column order used to
train the model, and any scaling (StandardScaler, etc.) must be applied the same way.
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

log = logging.getLogger("predict")

COOPS_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
APP_NAME = "tampa_bay_monitor"
DEFAULT_STATION = "8726520"     # St. Petersburg (confirm on the CO-OPS station map)
DATUM = "MLLW"                  # must match the datum used to build the training data

selected_features = [
    "resid_mean",
    "resid_max",
    "resid_max_6h",
    "resid_max_24h",
]


# --------------------------------------------------------------------------- #
# NOAA fetch
# --------------------------------------------------------------------------- #
def fetch_product(station: str, product: str, begin: datetime, end: datetime) -> pd.DataFrame:
    """Return a DataFrame indexed by UTC time with a single column 'v' (metres, MLLW)."""
    params = {
        "station": station, "product": product, "datum": DATUM,
        "begin_date": begin.strftime("%Y%m%d %H:%M"), "end_date": end.strftime("%Y%m%d %H:%M"),
        "time_zone": "gmt", "units": "metric", "format": "json", "application": APP_NAME,
    }
    r = requests.get(COOPS_URL, params=params, timeout=60)
    r.raise_for_status()
    payload = r.json()
    if "error" in payload:
        raise RuntimeError(f"CO-OPS {product} error: {payload['error'].get('message')}")
    rows = payload.get("predictions" if product == "predictions" else "data", [])
    if not rows:
        raise RuntimeError(f"CO-OPS returned no {product} rows for station {station}")
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    df["v"] = pd.to_numeric(df["v"], errors="coerce")
    return df.set_index("t")[["v"]].sort_index()


# --------------------------------------------------------------------------- #
# Features (must mirror build_datasets.py)
# --------------------------------------------------------------------------- #
def compute_features(wl: pd.DataFrame, pred: pd.DataFrame, now: datetime,
                     include_partial: bool = False) -> pd.DataFrame:
    six = wl.rename(columns={"v": "wl"}).join(pred.rename(columns={"v": "pred"}), how="outer")
    six["resid"] = six["wl"] - six["pred"]

    hourly = pd.DataFrame({
        "resid_mean": six["resid"].resample("1h").mean(),
        "resid_max": six["resid"].resample("1h").max(),
    })
    if not include_partial:                       # training used complete hours only
        hourly = hourly[hourly.index < pd.Timestamp(now).floor("h")]
    hourly = hourly.asfreq("1h")                  # regular hourly index; gaps become NaN

    hourly["resid_max_6h"] = hourly["resid_max"].rolling(6, min_periods=1).max()
    hourly["resid_max_24h"] = hourly["resid_max"].rolling(24, min_periods=1).max()
    return hourly[selected_features]


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
def load_model(path: Path):
    import keras
    if not path.exists():
        alt = Path(__file__).with_name(path.name)
        if alt.exists():
            path = alt
        else:
            raise SystemExit(f"Model file not found: {path.resolve()}")
    model = keras.models.load_model(path)
    log.info("Loaded %s  input_shape=%s  output_shape=%s", path, model.input_shape, model.output_shape)
    return model


def build_input(feats: pd.DataFrame, input_shape, scaler) -> np.ndarray:
    """Shape the latest feature rows to what the model expects: (1, n) or (1, T, n)."""
    if isinstance(input_shape, list):
        raise SystemExit("This script supports single-input models only.")
    n = len(selected_features)
    if input_shape[-1] != n:
        raise SystemExit(f"Model expects {input_shape[-1]} features per row but selected_features has {n}. "
                         "Make selected_features match your training columns, in the same order.")

    def scale(a: np.ndarray) -> np.ndarray:
        return scaler.transform(a) if scaler is not None else a

    if len(input_shape) == 2:                      # flat: (None, n) -> latest hour
        row = feats.iloc[[-1]].to_numpy(dtype="float32")
        if np.isnan(row).any():
            raise SystemExit(f"Latest hour has missing features:\n{feats.iloc[[-1]]}")
        return scale(row).astype("float32")

    if len(input_shape) == 3:                      # sequence: (None, T, n) -> last T hours
        steps = input_shape[1]
        if steps is None:
            raise SystemExit("Model has a variable-length sequence input; set a fixed length or edit build_input().")
        window = feats.iloc[-steps:]
        if len(window) < steps or window.isna().any().any():
            raise SystemExit(f"Need {steps} consecutive complete hours of features; got:\n{window}")
        return scale(window.to_numpy(dtype="float32"))[None, ...].astype("float32")

    raise SystemExit(f"Unsupported model input shape: {input_shape}")


def to_probability(raw) -> float:
    out = np.asarray(raw, dtype="float64")
    p = float(out[..., 1].reshape(-1)[-1]) if out.shape[-1] == 2 else float(out.reshape(-1)[-1])
    if not 0.0 <= p <= 1.0:                        # model emitted a logit
        log.warning("Output %.3f is outside [0, 1]; applying a sigmoid. Check your model's final activation.", p)
        p = 1.0 / (1.0 + np.exp(-p))
    return p


# --------------------------------------------------------------------------- #
# One prediction cycle
# --------------------------------------------------------------------------- #
def run_once(model, scaler, station: str, hours_back: int, threshold: float, include_partial: bool) -> dict:
    now = datetime.now(timezone.utc)
    begin = now - timedelta(hours=hours_back)
    wl = fetch_product(station, "water_level", begin, now)
    pred = fetch_product(station, "predictions", begin, now)

    age = now - wl.index.max().to_pydatetime()
    if age > timedelta(hours=2):
        log.warning("Latest water level reading is %.1f hours old; the station may be offline.",
                    age.total_seconds() / 3600)

    feats = compute_features(wl, pred, now, include_partial)
    if feats.dropna().empty:
        raise SystemExit("No complete feature rows. Try a larger --hours-back or check the station.")
    feats = feats.dropna(how="all")

    x = build_input(feats, model.input_shape, scaler)
    p = to_probability(model.predict(x, verbose=0))
    latest = feats.iloc[-1]
    result = {"station": station, "feature_hour_utc": feats.index[-1], "probability": p,
              "flood_risk_next_6h": int(p >= threshold), **latest.to_dict()}

    print(f"\nStation {station} | features for hour ending {feats.index[-1] + pd.Timedelta(hours=1):%Y-%m-%d %H:%M} UTC")
    for k in selected_features:
        print(f"  {k:15s} {latest[k]:+.3f} m")
    print(f"  P(flood_risk_next_6h) = {p:.3f}  ->  {'FLOOD RISK' if p >= threshold else 'normal'} "
          f"(threshold {threshold})")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--station", default=DEFAULT_STATION)
    ap.add_argument("--model", default="water_model.keras", type=Path)
    ap.add_argument("--scaler", default=None, help="joblib file for the scaler used in training")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--hours-back", type=int, default=48, help="history to fetch (needs >= 24 for resid_max_24h)")
    ap.add_argument("--include-partial", action="store_true", help="include the current incomplete hour")
    ap.add_argument("--loop", type=float, default=None, metavar="MINUTES", help="repeat every N minutes")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    model = load_model(a.model)
    scaler = None
    if a.scaler:
        import joblib
        scaler = joblib.load(a.scaler)
    else:
        log.warning("No --scaler given: features are passed to the model unscaled. If you scaled them in "
                    "training, predictions will be wrong.")

    while True:
        try:
            run_once(model, scaler, a.station, a.hours_back, a.threshold, a.include_partial)
        except (requests.RequestException, RuntimeError) as exc:
            log.error("Fetch failed: %s", exc)
        if a.loop is None:
            break
        time.sleep(a.loop * 60)


if __name__ == "__main__":
    main()