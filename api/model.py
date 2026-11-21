import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).parent

# the water model predicts P(flood in next 6h) from NOAA tide residuals at
# St. Petersburg, Tampa Bay. nothing to do with the board's water sensor.
STATION = "8726520"
DATUM = "MLLW"
COOPS_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
FEATURES = ["resid_mean", "resid_max", "resid_max_6h", "resid_max_24h"]

# it's only three dense layers, so i pull the weights out of the .keras zip
# and do the forward pass in numpy - no tensorflow needed on railway.
def _load_weights():
    z = zipfile.ZipFile(HERE / "water_model.keras")
    with h5py.File(io.BytesIO(z.read("model.weights.h5")), "r") as f:
        g = f["/layers"]
        return [
            (g["dense/vars/0"][:], g["dense/vars/1"][:]),
            (g["dense_1/vars/0"][:], g["dense_1/vars/1"][:]),
            (g["dense_2/vars/0"][:], g["dense_2/vars/1"][:]),
        ]


_LAYERS = _load_weights()
_scaler = json.loads((HERE / "water_model_scaler.json").read_text())
_mean = np.asarray(_scaler["mean"], dtype="float32")
_scale = np.asarray(_scaler["scale"], dtype="float32")
_replay = json.loads((HERE / "replay_row.json").read_text())


def _forward(feats):
    x = (np.asarray(feats, dtype="float32") - _mean) / _scale
    (w0, b0), (w1, b1), (w2, b2) = _LAYERS
    x = np.maximum(0.0, x @ w0 + b0)
    x = np.maximum(0.0, x @ w1 + b1)
    p = 1.0 / (1.0 + np.exp(-(x @ w2 + b2)))
    return float(np.reshape(p, -1)[-1])


def _fetch(product, begin, end):
    params = {
        "station": STATION, "product": product, "datum": DATUM,
        "begin_date": begin.strftime("%Y%m%d %H:%M"),
        "end_date": end.strftime("%Y%m%d %H:%M"),
        "time_zone": "gmt", "units": "metric", "format": "json",
        "application": "floodair",
    }
    r = requests.get(COOPS_URL, params=params, timeout=30)
    r.raise_for_status()
    rows = r.json().get("predictions" if product == "predictions" else "data", [])
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    df["v"] = pd.to_numeric(df["v"], errors="coerce")
    return df.set_index("t")[["v"]].sort_index()


# same four features as training: residual = observed minus predicted tide,
# then hourly mean/max and the rolling 6h/24h max of that.
def _live_features():
    now = datetime.now(timezone.utc)
    wl = _fetch("water_level", now - timedelta(hours=48), now)
    pred = _fetch("predictions", now - timedelta(hours=48), now)

    six = wl.rename(columns={"v": "wl"}).join(pred.rename(columns={"v": "pred"}), how="outer")
    six["resid"] = six["wl"] - six["pred"]
    hourly = pd.DataFrame({
        "resid_mean": six["resid"].resample("1h").mean(),
        "resid_max": six["resid"].resample("1h").max(),
    })
    hourly = hourly[hourly.index < pd.Timestamp(now).floor("h")].asfreq("1h")
    hourly["resid_max_6h"] = hourly["resid_max"].rolling(6, min_periods=1).max()
    hourly["resid_max_24h"] = hourly["resid_max"].rolling(24, min_periods=1).max()

    feats = hourly[FEATURES].dropna()
    return None if feats.empty else feats.iloc[-1].to_numpy(dtype="float32")


def _percent(feats):
    return None if feats is None else max(0.0, min(100.0, _forward(feats) * 100.0))


def predict(water, aqi, replay=False):
    # water/aqi from the board are ignored - the real model pulls live NOAA
    # tide data. air stays None until there's a pollution model.
    if replay:
        return {"flood": _percent([_replay[k] for k in FEATURES]), "air": None}
    try:
        flood = _percent(_live_features())
    except Exception:
        flood = None
    return {"flood": flood, "air": None}
