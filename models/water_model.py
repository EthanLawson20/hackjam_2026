import json
import tensorflow as tf
import pandas as pd
import numpy as np
import keras
import seaborn as sns
from pathlib import Path

import matplotlib.pyplot as plt

dataset = pd.read_csv('./data/data/flood_dataset.csv')

print(dataset.head(5))

correlation_data = dataset.drop(columns=["time_utc", "split_group"], errors="ignore").copy()
if "station" in correlation_data:
    correlation_data["station"] = correlation_data["station"].astype("string")

categorical_columns = correlation_data.select_dtypes(
    include=["object", "category", "string"]
).columns.tolist()
for column in categorical_columns:
    if correlation_data[column].nunique(dropna=True) > 1:
        correlation_data = pd.get_dummies(
            correlation_data, columns=[column], prefix=column, dtype=int
        )
    else:
        print(f"Skipping {column}: only one category, so correlation is undefined.")
        correlation_data = correlation_data.drop(columns=column)

numeric_data = correlation_data.select_dtypes(include="number")
scales = numeric_data.std(ddof=0).replace(0, np.nan)
normalized_data = (numeric_data - numeric_data.mean()) / scales
correlations = normalized_data.corr()
target = "flood_risk_next_6h"
if target in correlations:
    print(correlations[target].drop(target).sort_values(key=abs, ascending=False))

plt.figure(figsize=(18, 14))
selected_features = [
    "resid_mean",
    "resid_max",
    "resid_max_6h",
    "resid_max_24h",
]

sns.heatmap(
    correlations.loc[selected_features, selected_features],
    annot=True,
    fmt=".2f",
    cmap="coolwarm",
    center=0,
)

def build_model(input_shape):
    model = keras.Sequential()
    model.add(keras.layers.InputLayer(input_shape=input_shape))
    model.add(keras.layers.Dense(64, activation="relu"))
    model.add(keras.layers.Dense(32, activation="relu"))
    model.add(keras.layers.Dense(1, activation="sigmoid"))  # Output layer for binary classification
    return model

def compile_model(model):
    model.compile(
        optimizer="adam",
        loss="binary_crossentropy",  # Loss function for binary classification
        metrics=["accuracy"],
    )
    return model

def train_model(model, X_train, y_train, epochs=50, batch_size=32):
    history = model.fit(X_train, y_train, epochs=epochs, batch_size=batch_size, validation_split=0.2)
    return history

model = build_model(input_shape=(len(selected_features),))
model = compile_model(model)
history = train_model(model, normalized_data[selected_features], dataset[target], 6)

print("Model training completed.")
print("Final training accuracy:", history.history['accuracy'][-1])

model.save('./models/water_model.keras')
scaler = {
    "features": selected_features,
    "mean": numeric_data[selected_features].mean().tolist(),
    "scale": scales[selected_features].tolist(),
}
Path(__file__).with_name("water_model_scaler.json").write_text(
    json.dumps(scaler, indent=2), encoding="utf-8"
)