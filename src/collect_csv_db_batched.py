"""
Main data-collection from db file: runs the three volume algorithms (ttv/defi/asp)
over the configured block range and delta windows, pulling blocks from Postgres in
batches and writing one CSV row per block.
"""

import asyncio
import csv
import datetime
import os
import signal
import sys
import yaml
from pathlib import Path
from tqdm import tqdm
from dotenv import load_dotenv

# from collect.data_manager import DataCollector
from collect.data_manager_pg import PostgresDataCollector
from collect.cancellation_token import CancellationToken
from processing.alg_cumulative_wealth_gain import CumulativeWealthGain
from processing.alg_transaction_counting import TransactionCounting
from processing.alg_defi_transactions import DefiTransactions

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "data" / "data_2025_ttv_defi_per_block.csv"


def get_config() -> dict:
    load_dotenv(PROJECT_ROOT / ".env")
    path = PROJECT_ROOT / "config" / "config.yaml"
    with path.open("r") as f:
        config = yaml.safe_load(f)
    config["COIN_GECKO_API_KEY"] = os.getenv("COIN_GECKO_API_KEY")
    config["RCP_URL"]     = os.getenv("RCP_URL")
    config["PG_HOST"]     = os.getenv("PG_HOST")
    config["PG_PORT"]     = os.getenv("PG_PORT", "5432")
    config["PG_DBNAME"]   = os.getenv("PG_DBNAME")
    config["PG_USER"]     = os.getenv("PG_USER")
    config["PG_PASSWORD"]  = os.getenv("PG_PASSWORD")
    config["LOG_INTERVAL"] = int(os.getenv("LOG_INTERVAL", "100"))
    config.setdefault("pricing_mode", "usd")
    return config


def get_resume_block(csv_path: Path) -> int | None:
    if not csv_path.exists():
        return None
    last_line = None
    with open(csv_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last_line = line
    if last_line is None:
        return None
    try:
        value = last_line.split(";")[0]
        return int(value) # raises ValueError if first element is not a valid block number
    except ValueError:
        return None # only the header exists


async def main():
    global CSV_PATH

    cancellation_token = CancellationToken()
    config = get_config()
    #dc = DataCollector(config=config)
    dc = PostgresDataCollector(config=config)
    loop = asyncio.get_running_loop()
    eth_pricing_mode = config["pricing_mode"] == "eth"

    # change CSV file name if pricing mode is ETH
    if eth_pricing_mode:
        CSV_PATH = CSV_PATH.with_stem(CSV_PATH.stem + "_ETH")

    def handle_interrupt():
        print("\nInterrupt received - finishing current batch and exiting...")
        cancellation_token.cancel()

    loop.add_signal_handler(signal.SIGINT, handle_interrupt)
    loop.add_signal_handler(signal.SIGTERM, handle_interrupt)

    await dc.open()

    try:
        all_deltas = config["analysis"]["transaction_counting"]  # identical across all three algorithms
        max_delta  = max(all_deltas)

        algo_defs = [
            ("ttv",  TransactionCounting,    config["analysis"]["transaction_counting"]),
            ("defi", DefiTransactions,       config["analysis"]["defi_transactions"]),
            ("asp",  CumulativeWealthGain,   config["analysis"]["cumulative_wealth_gain"]),
        ]

        algo_columns = []  # one list per algo
        for prefix, alg_class, deltas in algo_defs:
            instances = [(f"{prefix}_{n // 12}", alg_class(n, eth_pricing_mode)) for n in deltas]
            algo_columns.append(instances)

        # resume from last block
        last_block = get_resume_block(CSV_PATH)

        if last_block is not None:
            warm_up_blocks = max_delta // 12 # convert seconds delta to block delta
            warm_up_start  = max(config["start_block"], last_block + 1 - warm_up_blocks)
            resume_from    = last_block + 1
            file_mode      = "a"
            print(f"Resuming from block {resume_from} - warm-up starts at {warm_up_start} ({warm_up_blocks} blocks)")
        else:
            warm_up_start = config["start_block"]
            resume_from   = config["start_block"]
            file_mode     = "w"
            print("No existing CSV found - starting fresh")

        with open(CSV_PATH, file_mode, encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";")

            if file_mode == "w":
                header = ["block_number", "timestamp"]
                for instances in algo_columns:
                    header += [column for (column, _inst) in instances]
                writer.writerow(header)

            batches  = range(warm_up_start, config["end_block"], config["batch_size"])
            progress = tqdm(batches, desc="Processing blocks", unit="batch") if sys.stdout.isatty() else batches

            for batch_idx, batch_start in enumerate(progress):
                if cancellation_token.is_canceled():
                    break

                # logging if script is run in background
                if not sys.stdout.isatty() and batch_idx % config["LOG_INTERVAL"] == 0:
                    print(
                        f"[{datetime.datetime.now().isoformat(timespec='seconds')}] "
                        f"batch {batch_idx}/{len(batches)} - block {batch_start}",
                        flush=True,
                    )

                batch_end = min(batch_start + config["batch_size"], config["end_block"])

                # TODO DEX filter functionality not yet implemented
                blocks = await dc.get_blocks_online(batch_start, batch_end, with_dex=True)

                for block in blocks:
                    block_number = block[0]
                    timestamp    = block[1]
                    all_txs      = block[2] if block[2] is not None else []
                    ts_seconds   = timestamp.timestamp()

                    block_data = {"timestamp": ts_seconds, "transactions": all_txs}

                    row_values = []
                    for instances in algo_columns:
                        for (_column, inst) in instances:
                            row_values.append(inst.run_on_block(block_data))

                    # warm-up and already-written blocks: keep processing but skip the write
                    if block_number < resume_from:
                        continue

                    writer.writerow(
                        [block_number, datetime.datetime.fromtimestamp(ts_seconds, datetime.timezone.utc).replace(tzinfo=None)]
                        + row_values
                    )
            f.flush()

    finally:
        await dc.close()


if __name__ == "__main__":
    asyncio.run(main())