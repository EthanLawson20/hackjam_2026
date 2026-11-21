from __future__ import annotations

import json
import logging
from pathlib import Path

import keras
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "data" / "data" / "flood_dataset.csv"
MODEL_PATH = Path(__file__).with_name("water_model.keras")
SCALER_PATH = Path(__file__).with_name("water_model_scaler.json")

SELECTED_FEATURES = [
    "resid_mean",
    "resid_max",
    "resid_max_6h",
    "resid_max_24h",
]
TARGET = "flood_risk_next_6h"
HORIZON_HOURS = 6
MAX_EPOCHS = 10
EARLY_STOPPING_PATIENCE = 20
BATCH_SIZE = 32

log = logging.getLogger("water_model")


def load_dataset(path: Path = DATASET_PATH) -> pd.DataFrame:
    dataset = pd.read_csv(path, parse_dates=["time_utc"])
    required = ["time_utc", "station", *SELECTED_FEATURES, TARGET]
    missing_columns = set(required) - set(dataset.columns)
    if missing_columns:
        raise ValueError(f"Dataset is missing required columns: {sorted(missing_columns)}")

    stations = dataset["station"].astype(str).unique()
    if len(stations) != 1 or stations[0] != "8726520":
        raise ValueError(
            "This model expects data for station 8726520 only. "
            "Use station-specific flood thresholds and features before training on multiple stations."
        )

    dataset = dataset.dropna(subset=required).sort_values("time_utc").reset_index(drop=True)
    labels = set(dataset[TARGET].unique())
    if not labels.issubset({0, 1, 0.0, 1.0}):
        raise ValueError(f"{TARGET} must contain only binary 0/1 labels.")
    if not dataset[TARGET].eq(1).any():
        raise ValueError(f"{TARGET} has no positive labels; cannot train a flood detector.")
    return dataset


def split_on_latest_event(
    dataset: pd.DataFrame, horizon_hours: int = HORIZON_HOURS
) -> tuple[pd.DataFrame, pd.DataFrame]:
    positive_times = (
        dataset.loc[dataset[TARGET].eq(1), "time_utc"]
        .drop_duplicates()
        .sort_values()
        .reset_index(drop=True)
    )
    run_starts = positive_times[positive_times.diff().gt(pd.Timedelta(hours=1))]
    validation_start = run_starts.iloc[-1] if not run_starts.empty else positive_times.iloc[0]
    training_end = validation_start - pd.Timedelta(hours=horizon_hours)

    training = dataset[dataset["time_utc"] < training_end]
    validation = dataset[dataset["time_utc"] >= validation_start]
    if not training[TARGET].eq(1).any() or not validation[TARGET].eq(1).any():
        raise ValueError(
            "The latest-event holdout needs positive examples in both training and validation. "
            "More independent flood events are needed."
        )
    if not validation[TARGET].eq(0).any():
        raise ValueError("The latest-event validation period has no negative examples.")
    return training, validation


def fit_scaler(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = values.mean(axis=0)
    scale = values.std(axis=0)
    if not np.isfinite(mean).all() or not np.isfinite(scale).all() or (scale == 0).any():
        raise ValueError("Training features must have finite, nonzero scales.")
    return mean, scale


def balanced_class_weights(labels: np.ndarray) -> dict[int, float]:
    counts = np.bincount(labels.astype("int32"), minlength=2)
    if (counts == 0).any():
        raise ValueError("Training requires both positive and negative labels.")
    total = int(counts.sum())
    return {0: total / (2 * counts[0]), 1: total / (2 * counts[1])}


def build_model(input_size: int) -> keras.Model:
    model = keras.Sequential(
        [
            keras.layers.Input(shape=(input_size,)),
            keras.layers.Dense(64, activation="relu"),
            keras.layers.Dense(32, activation="relu"),
            keras.layers.Dense(1, activation="sigmoid"),
        ]
    )
    model.compile(
        optimizer="adam",
        loss="binary_crossentropy",
        metrics=[
            keras.metrics.AUC(curve="PR", name="pr_auc"),
            keras.metrics.Precision(name="precision"),
            keras.metrics.Recall(name="recall"),
        ],
    )
    return model


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dataset = load_dataset()
    training, validation = split_on_latest_event(dataset)

    x_training_raw = training[SELECTED_FEATURES].to_numpy(dtype="float32")
    y_training = training[TARGET].to_numpy(dtype="int32")
    x_validation_raw = validation[SELECTED_FEATURES].to_numpy(dtype="float32")
    y_validation = validation[TARGET].to_numpy(dtype="int32")

    training_mean, training_scale = fit_scaler(x_training_raw)
    x_training = (x_training_raw - training_mean) / training_scale
    x_validation = (x_validation_raw - training_mean) / training_scale
    weights = balanced_class_weights(y_training)

    log.info(
        "Training rows=%d positives=%d; validation rows=%d positives=%d; class weights=%s",
        len(training),
        int(y_training.sum()),
        len(validation),
        int(y_validation.sum()),
        weights,
    )

    keras.utils.set_random_seed(42)
    validation_model = build_model(len(SELECTED_FEATURES))
    history = validation_model.fit(
        x_training,
        y_training,
        validation_data=(x_validation, y_validation),
        class_weight=weights,
        epochs=MAX_EPOCHS,
        batch_size=BATCH_SIZE,
        callbacks=[
            keras.callbacks.EarlyStopping(
                monitor="val_pr_auc",
                mode="max",
                patience=EARLY_STOPPING_PATIENCE,
                restore_best_weights=True,
            )
        ],
        verbose=2,
    )
    validation_metrics = validation_model.evaluate(
        x_validation, y_validation, return_dict=True, verbose=0
    )
    log.info("Latest-event holdout metrics at threshold 0.5: %s", validation_metrics)
    log.warning(
        "Class-weighted outputs are risk scores, not calibrated probabilities; "
        "tune the inference threshold using independent flood events."
    )

    validation_pr_auc = np.asarray(history.history["val_pr_auc"], dtype="float64")
    if not np.isfinite(validation_pr_auc).any():
        raise RuntimeError("Validation PR-AUC was not finite; refusing to save a model.")
    best_epoch = int(np.nanargmax(validation_pr_auc)) + 1
    log.info("Refitting on all rows for %d epoch(s).", best_epoch)

    x_all_raw = dataset[SELECTED_FEATURES].to_numpy(dtype="float32")
    y_all = dataset[TARGET].to_numpy(dtype="int32")
    full_mean, full_scale = fit_scaler(x_all_raw)
    x_all = (x_all_raw - full_mean) / full_scale
    full_weights = balanced_class_weights(y_all)

    keras.utils.set_random_seed(42)
    model = build_model(len(SELECTED_FEATURES))
    model.fit(
        x_all,
        y_all,
        class_weight=full_weights,
        epochs=best_epoch,
        batch_size=BATCH_SIZE,
        verbose=2,
    )
    model.save(MODEL_PATH)
    SCALER_PATH.write_text(
        json.dumps(
            {
                "features": SELECTED_FEATURES,
                "mean": full_mean.tolist(),
                "scale": full_scale.tolist(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info("Saved model to %s and scaler to %s", MODEL_PATH, SCALER_PATH)


if __name__ == "__main__":
    main()
