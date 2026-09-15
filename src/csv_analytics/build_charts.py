"""
Builds two charts from the ETH transaction volume data CSV.

Script is completely independet from other files. Only the CSV has to be
provided.

Chart 1: two stacked bars per algorithm.
  MAX: global maximum of each column over all blocks, stacked by delta.
  AVG: mean of the daily means, stacked by delta.

Chart 2: for a few selected deltas, the daily mean and daily max
  (one subplot for the mean and one for the max, per delta).
"""

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from tqdm import tqdm


# --- Config ----------------------------------------------------------------

MAIN_PATH = Path(__file__).resolve().parents[2]
CSV_PATH = MAIN_PATH / "data/data_2025_cold.csv" # arbitrary path to the stored CSV

# Blocks to skip at the start (ramp-up until the largest delta window is filled)
RAMP_UP_NO_BLOCKS = 0
#RAMP_UP_NO_BLOCKS = 3000

ALGORITHMS = ["ttv", "defi", "asp"]

# All deltas present in the CSV (have to be same for every algorithm)
#DELTAS = [2, 8, 16, 64, 128, 7200, 50400] == long run ==
DELTAS = [2, 8, 16, 32, 64]

# Deltas shown in chart 2
#DELTAS_FOR_CHART2 = [16, 64, 7200, 50400]
DELTAS_FOR_CHART2 = [8, 16, 32, 64]

# Y-axis scaling
SCALE = 1_000_000
SCALE_LABEL = "M"

OUTPUT_DIR = MAIN_PATH / "data/plots"


def load_dataset(csv_path, ramp_up_blocks, algorithms, deltas):
    columns = [f"{algo}_{delta}" for algo in algorithms for delta in deltas]

    day_sum = defaultdict(lambda: defaultdict(float))
    day_count = defaultdict(lambda: defaultdict(int))
    day_max = defaultdict(lambda: defaultdict(float))
    global_max = defaultdict(float)

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=";")

        header = next(reader)
        col_idx = {name: i for i, name in enumerate(header)}

        missing = [c for c in columns if c not in col_idx]
        if missing:
            raise ValueError(f"Missing columns in CSV: {missing}")

        ts_idx = col_idx["timestamp"]

        # Skip ramp-up
        for _ in range(ramp_up_blocks):
            next(reader)

        for row in tqdm(reader, unit="line"):
            if not row:
                continue

            day = datetime.strptime(row[ts_idx], "%Y-%m-%d %H:%M:%S").date()

            for algo in algorithms:
                for delta in deltas:
                    key = (algo, delta)
                    value = float(row[col_idx[f"{algo}_{delta}"]])

                    day_sum[day][key] += value
                    day_count[day][key] += 1
                    if value > day_max[day][key]:
                        day_max[day][key] = value
                    if value > global_max[key]:
                        global_max[key] = value

    days = sorted(day_sum.keys())
    return days, day_sum, day_count, day_max, global_max


def daily_avg(day_sum, day_count, day, key):
    """Mean for a (algo, delta) on a single day."""
    n = day_count[day][key]
    return day_sum[day][key] / n if n else 0.0


def write_chart_csv(output_path, header, rows):
    """Write the plotted values next to the chart image (same file name)."""
    csv_path = output_path.with_suffix(".csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)
    print(f"Values written: {csv_path}")

def plot_chart1(days, day_sum, day_count, global_max,
                algorithms, deltas, output_path):

    # For each (algo, delta): global max, mean of daily means
    max_values = {}
    avg_values = {}
    for algo in algorithms:
        for delta in deltas:
            key = (algo, delta)
            max_values[key] = global_max[key]

            avgs = np.array(
                [daily_avg(day_sum, day_count, d, key) for d in days],
                dtype=float,
            )
            if avgs.size:
                avg_values[key] = float(avgs.mean())
            else:
                avg_values[key] = 0.0

    write_chart_csv(
        output_path,
        ["algorithm", "delta", f"max_{SCALE_LABEL}", f"avg_{SCALE_LABEL}"],
        [
            [algo, delta,
             max_values[(algo, delta)] / SCALE,
             avg_values[(algo, delta)] / SCALE]
            for algo in algorithms for delta in deltas
        ],
    )

    n_algos = len(algorithms)
    bar_width = 0.35
    base_positions = np.arange(n_algos)
    max_positions = base_positions - bar_width / 2
    avg_positions = base_positions + bar_width / 2

    # one color per delta for the stack segments
    cmap = plt.get_cmap("viridis", len(deltas))
    delta_colors = {delta: cmap(i) for i, delta in enumerate(deltas)}

    fig, ax = plt.subplots(figsize=(10, 6))

    # MAX bars, stacked by delta
    max_bottom = np.zeros(n_algos)
    for delta in deltas:
        heights = np.array(
            [max_values[(algo, delta)] / SCALE for algo in algorithms]
        )
        ax.bar(
            max_positions,
            heights,
            width=bar_width,
            bottom=max_bottom,
            color=delta_colors[delta],
            edgecolor="white",
            linewidth=0.5,
            label=f"Δ={delta}",
        )
        max_bottom += heights

    # AVG bars, stacked by delta
    avg_bottom = np.zeros(n_algos)
    for delta in deltas:
        heights = np.array(
            [avg_values[(algo, delta)] / SCALE for algo in algorithms]
        )
        ax.bar(
            avg_positions,
            heights,
            width=bar_width,
            bottom=avg_bottom,
            color=delta_colors[delta],
            edgecolor="white",
            linewidth=0.5,
            hatch="//"
        )
        avg_bottom += heights

    # MAX/AVG labels under the bars
    for i in range(n_algos):
        ax.text(max_positions[i], -0.02, "MAX",
                ha="center", va="top", fontsize=8,
                transform=ax.get_xaxis_transform())
        ax.text(avg_positions[i], -0.02, "AVG",
                ha="center", va="top", fontsize=8,
                transform=ax.get_xaxis_transform())

    # log scale for small deltas
    ax.set_yscale("log")

    ax.set_xticks(base_positions)
    ax.set_xticklabels(algorithms)
    ax.set_ylabel(f"Transaction Volume [{SCALE_LABEL}]")
    ax.set_title(
        "MAX (global) and AVG (daily basis), "
        "for every delta"
    )
    ax.legend(
        title="Delta",
        bbox_to_anchor=(1.02, 1.0),
        loc="upper left",
        frameon=False,
    )
    ax.grid(axis="y", linestyle=":", alpha=0.6)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_chart2(days, day_sum, day_count, day_max,
                algorithms, deltas_subset, output_path):
    """Per selected delta two subplots (AVG then MAX), one line per algorithm."""
    if not days:
        raise ValueError("No days in dataset")

    write_chart_csv(
        output_path,
        ["day", "delta", "algorithm", f"avg_{SCALE_LABEL}", f"max_{SCALE_LABEL}"],
        [
            [d, delta, algo,
             daily_avg(day_sum, day_count, d, (algo, delta)) / SCALE,
             day_max[d][(algo, delta)] / SCALE]
            for delta in deltas_subset for algo in algorithms for d in days
        ],
    )

    n_panels = 2 * len(deltas_subset)
    fig, axes = plt.subplots(
        n_panels, 1, figsize=(11, 2.6 * n_panels), sharex=True
    )
    if n_panels == 1:
        axes = [axes]

    algo_cmap = plt.get_cmap("tab10", max(len(algorithms), 3))
    algo_colors = {algo: algo_cmap(i) for i, algo in enumerate(algorithms)}

    for i, delta in enumerate(deltas_subset):
        ax_avg = axes[2 * i]
        ax_max = axes[2 * i + 1]

        for algo in algorithms:
            key = (algo, delta)
            avgs = [daily_avg(day_sum, day_count, d, key) / SCALE for d in days]
            maxes = [day_max[d][key] / SCALE for d in days]

            color = algo_colors[algo]
            ax_avg.plot(days, avgs,
                        marker="o", linestyle="-", color=color, label=algo)
            ax_max.plot(days, maxes,
                        marker="x", linestyle="-", color=color, label=algo)

        ax_avg.set_title(f"Delta = {delta} - AVG")
        ax_max.set_title(f"Delta = {delta} - MAX")
        ax_avg.set_ylabel(f"Volume [{SCALE_LABEL}]")
        ax_max.set_ylabel(f"Volume [{SCALE_LABEL}]")
        ax_avg.grid(linestyle=":", alpha=0.6)
        ax_max.grid(linestyle=":", alpha=0.6)
        ax_avg.legend(fontsize=8, ncol=len(algorithms), loc="upper left")
        ax_max.legend(fontsize=8, ncol=len(algorithms), loc="upper left")

    axes[-1].set_xlabel("Day")
    fig.suptitle(
        "Chart 2: Daily AVG and MAX for selected deltas",
        fontsize=13,
    )
    fig.autofmt_xdate()
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


# Main

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # The chart 2 selection must be a subset of DELTAS
    unknown = [d for d in DELTAS_FOR_CHART2 if d not in DELTAS]
    if unknown:
        raise ValueError(f"DELTAS_FOR_CHART2 has deltas not in DELTAS: {unknown}")

    print(f"Reading {CSV_PATH} (ramp-up: {RAMP_UP_NO_BLOCKS} blocks) ...")
    days, day_sum, day_count, day_max, global_max = load_dataset(
        CSV_PATH, RAMP_UP_NO_BLOCKS, ALGORITHMS, DELTAS
    )

    if days:
        print(f"Read {len(days)} days ({days[0]} ... {days[-1]})")
    else:
        print("Read 0 days")

    chart1_path = OUTPUT_DIR / "chart1_per_algorithm.png"
    chart2_path = OUTPUT_DIR / "chart2_daily_selected_deltas.png"

    plot_chart1(days, day_sum, day_count, global_max,
                ALGORITHMS, DELTAS, chart1_path)
    print(f"Chart 1 written: {chart1_path}")

    plot_chart2(days, day_sum, day_count, day_max,
                ALGORITHMS, DELTAS_FOR_CHART2, chart2_path)
    print(f"Chart 2 written: {chart2_path}")


if __name__ == "__main__":
    main()
