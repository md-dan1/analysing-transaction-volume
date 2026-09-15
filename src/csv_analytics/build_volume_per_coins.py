"""Plot total volume (algorithm "ttv") vs number of coins.

Reads the per-coin CSV (collect_csv_per_coin.py; one row per block, one plain per-block volume column
per coin), so the total per coin is a simple column sum.
Coins are ranked by COIN_ORDER and added cumulatively: datapoint k is the
total volume of the first k coins.
"""

import csv
import os
from decimal import Decimal
from pathlib import Path

import matplotlib.pyplot as plt
from tqdm import tqdm

# --- Config ---

MAIN_PATH = Path(__file__).resolve().parents[2]
CSV_PATH = MAIN_PATH / "data/per_coin_volume.csv"

TIMEFRAME_DESC = "December 2025"
SELECTED_ALGORITHM = "ttv"      # only for display purposes

# ranking of the coins by their CSV column name; must contain every coin column exactly once. None: keeps the CSV column order
COIN_ORDER = None

SCALE = 1_000_000_000
SCALE_LABEL = "B USD"
CHART_SUBTITLE = f"total volume per coin set - {TIMEFRAME_DESC}"

OUTPUT_DIR = MAIN_PATH / "data/plots"
CHART_PATH = OUTPUT_DIR / "ttv_total_volume_per_coins.png"

# enables modeling of the curve
FIT_DATA = True # TODO


def coin_totals(csv_path):
    """Sum each coin column over all blocks; exact via Decimal."""
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        coins = header[2:]

        totals = [Decimal(0)] * len(coins)
        for row in tqdm(reader, unit="block"):
            for k in range(len(coins)):
                totals[k] += Decimal(row[k + 2])

    return coins, dict(zip(coins, totals))


def rank_coins(coins):
    if not COIN_ORDER:
        return list(coins)
    if sorted(COIN_ORDER) != sorted(coins):
        raise Exception(
            f"COIN_ORDER must rank every CSV coin exactly once "
            f"(csv: {sorted(coins)}, order: {sorted(COIN_ORDER)})"
        )
    return list(COIN_ORDER)


def plot(coins, totals):
    plt.suptitle(f"Total transaction volume vs number of coins - algorithm {SELECTED_ALGORITHM}", fontsize=12)
    plt.title(CHART_SUBTITLE, fontsize=8)
    plt.xlabel("Number of coins")
    plt.ylabel(f"Total volume [{SCALE_LABEL}]")
    plt.xticks(coins)
    plt.plot(coins, [t / SCALE for t in totals], marker="o", color="steelblue")
    plt.savefig(CHART_PATH, dpi=300, bbox_inches="tight")
    plt.close()

def fit_data(coins, totals):
    # TODO implement
    pass


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Reading {CSV_PATH} ...")
    csv_coins, totals_per_coin = coin_totals(CSV_PATH)
    ranked = rank_coins(csv_coins)

    coins = []
    totals = []
    running = Decimal(0)
    for n_coins, coin in enumerate(ranked, start=1):
        running += totals_per_coin[coin]
        coins.append(n_coins)
        totals.append(round(running))

    lines = ["added_coin,num_coins,total_volume_usd"]
    for coin, n_coins, total in zip(ranked, coins, totals):
        lines.append(f"{coin},{n_coins},{total}")

    out_file_path = os.path.splitext(CHART_PATH)[0] + ".csv"
    with open(out_file_path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines) + "\n")
    print(f"File written: {out_file_path}")

    if FIT_DATA == True:
        fit_data(coins, totals)

    plot(coins, totals)
    print(f"Chart written: {CHART_PATH}")
    print("Datapoints:", list(zip(coins, totals)))


if __name__ == "__main__":
    main()
