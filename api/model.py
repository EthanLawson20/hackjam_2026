def predict(water, aqi):
    # PLACEHOLDER - no teammate model wired in yet.
    # flood = water level as-is, air = aqi/3. main.py clamps to 0-100.
    return {
        "flood": water if water is not None else None,
        "air": (aqi / 3.0) if aqi is not None else None,
    }
