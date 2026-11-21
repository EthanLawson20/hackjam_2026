#!/usr/bin/env python3
"""Run the flood predictor and POST its result to a Railway-hosted API."""
from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import numpy as np
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = PROJECT_ROOT / "models"
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))

import test_prediction as predictor

log = logging.getLogger("railway")


def build_endpoint(base_url: str, path: str) -> str:
    base_url = base_url.rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("RAILWAY_API_URL must be an http or https base URL.")
    return urljoin(base_url + "/", path.lstrip("/"))


def _json_value(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def send_prediction(
    prediction: dict,
    endpoint_url: str,
    *,
    token: str | None,
    timeout: float,
) -> int:
    payload = {key: _json_value(value) for key, value in prediction.items()}
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.post(endpoint_url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.status_code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("RAILWAY_API_URL", ""),
                        help="Railway service base URL (or set RAILWAY_API_URL)")
    parser.add_argument("--endpoint", default=os.getenv("RAILWAY_PREDICTIONS_PATH", "/api/predictions"),
                        help="POST path (or set RAILWAY_PREDICTIONS_PATH)")
    parser.add_argument("--token", default=os.getenv("RAILWAY_API_TOKEN"),
                        help="optional bearer token (or set RAILWAY_API_TOKEN)")
    parser.add_argument("--timeout", type=float, default=15, help="POST timeout in seconds")
    parser.add_argument("--station", default=predictor.DEFAULT_STATION)
    parser.add_argument("--model", type=Path, default=MODEL_DIR / "water_model.keras")
    parser.add_argument("--scaler", type=Path, default=None,
                        help="joblib scaler used during training, if applicable")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--hours-back", type=int, default=48)
    parser.add_argument("--include-partial", action="store_true")
    parser.add_argument("--loop", type=float, default=None, metavar="MINUTES")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.url:
        parser.error("Set RAILWAY_API_URL or pass --url with your Railway service base URL.")
    if args.timeout <= 0 or (args.loop is not None and args.loop <= 0):
        parser.error("--timeout and --loop must be positive.")
    try:
        endpoint_url = build_endpoint(args.url, args.endpoint)
    except ValueError as exc:
        parser.error(str(exc))

    scaler = predictor.load_scaler(args.scaler)
    if scaler is None:
        parser.error(
            "No scaler artifact found. Retrain water_model.py to create "
            "models/water_model_scaler.json, or pass --scaler."
        )
    model = predictor.load_model(args.model)

    while True:
        try:
            prediction = predictor.run_once(
                model, scaler, args.station, args.hours_back, args.threshold, args.include_partial
            )
            status = send_prediction(
                prediction, endpoint_url, token=args.token, timeout=args.timeout
            )
            log.info("Posted prediction to %s (HTTP %d)", endpoint_url, status)
        except (requests.RequestException, RuntimeError) as exc:
            log.error("Prediction or delivery failed: %s", exc)
            if args.loop is None:
                raise

        if args.loop is None:
            break
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    main()