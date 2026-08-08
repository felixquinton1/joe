"""Deterministic synthetic smoke test for Joe Autonomous (stdlib only)."""

import json
import math
import random
from pathlib import Path


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def main() -> None:
    random.seed(311)
    losses = []
    correct = 0
    size = 240
    for _ in range(size):
        left, right = random.gauss(0, 1), random.gauss(0, 1)
        asymmetry = abs(left - right)
        truth = int(1.1 * asymmetry + .35 * left - .9 + random.gauss(0, .35) > 0)
        probability = min(.999, max(.001, sigmoid(1.35 * asymmetry + .2 * left - 1.05)))
        losses.append(-(truth * math.log(probability) + (1 - truth) * math.log(1 - probability)))
        correct += int((probability >= .5) == bool(truth))
    metrics = {"log_loss": round(sum(losses) / size, 6), "accuracy": round(correct / size, 6), "samples": size, "dataset": "synthetic-only"}
    Path("metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics))


if __name__ == "__main__":
    main()
