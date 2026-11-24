"""Tiny offline example with unequal errors so mean distance differs from RMSE."""

from math import dist, sqrt


def main() -> None:
    truth = [(0, 0), (1, 2)]
    estimates = [(3, 4), (1, 2)]
    squared_errors = [dist(p, q) ** 2 for p, q in zip(truth, estimates)]
    rmse = sqrt(sum(squared_errors) / len(squared_errors))
    print(f"RMSE = {rmse:.3f}")


if __name__ == "__main__":
    main()
