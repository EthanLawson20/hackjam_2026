# tiny web service around the model. the board calls /predict every few seconds
import os
import time

from fastapi import FastAPI, HTTPException

from model import predict

app = FastAPI()

# scores my teammate's model sent in, and when
pushed = {"flood": None, "air": None, "at": 0.0}
PUSH_GOOD_FOR = 3600   # seconds before i stop trusting a pushed score


def as_percent(score):
    if score is None:
        return None
    return round(max(0.0, min(100.0, float(score))), 1)


@app.get("/")
def home():
    return {"ok": True}


# the model sends its scores here: /push?flood=62.5&air=41  (either one or both)
@app.api_route("/push", methods=["GET", "POST"])
def push(flood: float | None = None, air: float | None = None):
    if flood is None and air is None:
        raise HTTPException(400, "send flood= and/or air=")
    if flood is not None:
        pushed["flood"] = as_percent(flood)
    if air is not None:
        pushed["air"] = as_percent(air)
    pushed["at"] = time.time()
    return {"ok": True, "flood": pushed["flood"], "air": pushed["air"]}


# e.g. /predict?water=55&aqi=120  (either one can be left off)
@app.get("/predict")
def get_prediction(water: float | None = None, aqi: float | None = None):
    scores = predict(water, aqi)
    out = {"flood": as_percent(scores.get("flood")), "air": as_percent(scores.get("air"))}
    source = "model.py"

    # anything the model pushed lately wins over model.py
    if time.time() - pushed["at"] < PUSH_GOOD_FOR:
        for name in ("flood", "air"):
            if pushed[name] is not None:
                out[name] = pushed[name]
                source = "pushed"

    out["source"] = source
    return out


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))