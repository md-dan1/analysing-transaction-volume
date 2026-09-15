"""
Builds a chart from the ETH transaction volume data CSV.

Script is completely independet from other files. Only the CSV has to be
provided.

The Chart displays the total volume per delta for a certein algorithm (ttv, defi, asp)
"""

# TODO implement pricing mode ETH

import csv
from collections import defaultdict
from tqdm import tqdm
from pathlib import Path
import matplotlib.pyplot as plt

# --- Config ----------------------------------------------------------------

MAIN_PATH = Path(__file__).resolve().parents[2]
CSV_PATH = MAIN_PATH / "data/data_2025_cold.csv"

# Blocks to skip at the start (ramp-up until the largest delta window is filled)
RAMP_UP_NO_BLOCKS = 0

SELECTED_ALGORITHM = "asp"

# ETH_STAKE is necessary if the CSV values are ETH based and mode is ETH
PRICING_MODE = "usd"                 # mode is either eth or usd; usd is default
#ETH_STAKE = 32

# Y-axis scaling
SCALE = 1_000_000                   # only relevant for usd mode
SCALE_LABEL = "M USD"
CHART_SUBTITLE = ""
OUTPUT_DIR = MAIN_PATH / "data/plots"
CHART_PATH = OUTPUT_DIR / "experimental.png"


def load_dataset(csv_path, ramp_up_blocks, selected_algorithm):

    delta_avg = defaultdict(float)

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=";")

        header = next(reader)
        col_idx = {name: i for i, name in enumerate(header)}
        columns = [col_name for col_name in header if col_name.startswith(selected_algorithm) ]

        print(f"coldix: {columns}")

        # Skip ramp-up
        for _ in range(ramp_up_blocks):
            next(reader)

        no_rows = 0
        for row in tqdm(reader, unit="block"):
            if not row:
                continue

            no_rows += 1
            for col in columns:
                delta_avg[col] += float(row[col_idx[col]])

        for col in columns:
            delta_avg[col] /= no_rows

    return delta_avg

def write_chart_csv(deltas, avgs):
    csv_path = CHART_PATH.with_suffix(".csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["delta", f"avg_{SCALE_LABEL.replace(" ", "_")}"])
        writer.writerows(zip(deltas, avgs))
    print(f"Values written: {csv_path}")


def plot(delta_avg):
    cmap = plt.get_cmap("viridis", len(delta_avg))
    delta_colors = {delta: cmap(i) for i, delta in enumerate(delta_avg)}

    plt.title(CHART_SUBTITLE, fontsize=8)
    plt.suptitle(f"AVG Transaction volume per delta - algorithm {SELECTED_ALGORITHM}", fontsize=12)
    plt.xlabel(f"Delta [Blocks]")
    plt.xticks(rotation=45, ha="right")

    if PRICING_MODE == "eth":
        #plt.ylabel("Avg. excess volume relative to stake [(V − S) / S]")
        plt.ylabel(f"Transaction volume [{SCALE_LABEL}]")
    else:
        plt.ylabel(f"Transaction volume [{SCALE_LABEL}]")

    deltas = [delta_str.replace(f"{SELECTED_ALGORITHM}_", "") for delta_str in delta_avg.keys()]
    if PRICING_MODE != "eth":
        avgs = [vol / SCALE for vol in delta_avg.values()]
    else:
        avgs = delta_avg.values()
    plt.plot(deltas, avgs, marker="o", color="steelblue")

    write_chart_csv(deltas, avgs)

    plt.savefig(CHART_PATH, dpi=300, bbox_inches="tight")
    plt.close()


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Reading {CSV_PATH} (ramp-up: {RAMP_UP_NO_BLOCKS} blocks) ...")

    delta_avg = load_dataset(CSV_PATH, RAMP_UP_NO_BLOCKS, SELECTED_ALGORITHM)
    print("Writing chart...")
    plot(delta_avg)
    print(f"Chart written: {CHART_PATH}")


if __name__ == "__main__":
    main()