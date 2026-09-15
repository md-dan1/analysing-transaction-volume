"""
Per-coin breakdown collector from db: for each block it sums plain per-block TTV
separately for every active coin and writes one CSV row per block with one column
per coin.
"""

import asyncio
import csv
import datetime
import os
import signal
import yaml
from collections import defaultdict
from pathlib import Path
from tqdm import tqdm
from dotenv import load_dotenv

from collect.data_manager_pg import PostgresDataCollector
from collect.cancellation_token import CancellationToken

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "data" / "per_coin_volume.csv"


def get_config() -> dict:
    load_dotenv(PROJECT_ROOT / ".env")
    path = PROJECT_ROOT / "config" / "config.yaml"
    with path.open("r") as f:
        config = yaml.safe_load(f)
    config["COIN_GECKO_API_KEY"] = os.getenv("COIN_GECKO_API_KEY")
    config["PG_HOST"]     = os.getenv("PG_HOST")
    config["PG_PORT"]     = os.getenv("PG_PORT", "5432")
    config["PG_DBNAME"]   = os.getenv("PG_DBNAME")
    config["PG_USER"]     = os.getenv("PG_USER")
    config["PG_PASSWORD"] = os.getenv("PG_PASSWORD")
    config.setdefault("pricing_mode", "usd")
    return config


def block_volume_per_coin(transactions: list, eth_pricing_mode: bool) -> dict:
    value_key = "eth_amount" if eth_pricing_mode else "usd_value"
    sums = defaultdict(float)
    for tx in transactions:
        sums[tx["coin"]] += tx[value_key]
    return sums


def read_csv_state(csv_path: Path) -> tuple[list[str] | None, int | None]:
    if not csv_path.exists():
        return None, None
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader, None)
        if header is None:
            return None, None
        last_block = None
        for row in reader:
            if row:
                last_block = int(row[0])
    return header[2:], last_block


async def main():
    global CSV_PATH

    cancellation_token = CancellationToken()
    config = get_config()
    dc = PostgresDataCollector(config=config)
    loop = asyncio.get_running_loop()
    eth_pricing_mode = config["pricing_mode"] == "eth"

    if eth_pricing_mode:
        CSV_PATH = CSV_PATH.with_stem(CSV_PATH.stem + "_ETH")

    def handle_interrupt():
        print("\nInterrupt received - finishing current batch and exiting...")
        cancellation_token.cancel()

    loop.add_signal_handler(signal.SIGINT, handle_interrupt)
    loop.add_signal_handler(signal.SIGTERM, handle_interrupt)

    await dc.open()

    try:
        active_coins = [c["name"] for c in config["token"] if c["active"]]

        header_coins, last_block = read_csv_state(CSV_PATH)

        if last_block is None:
            coin_columns = active_coins
            resume_from  = config["start_block"]
            file_mode    = "w"
            print("No existing CSV found - starting fresh")
        else:
            # resume requires the exact same coin set
            if set(header_coins) != set(active_coins):
                raise Exception(
                    f"active coins in the config do not match the CSV columns "
                    f"(config: {sorted(active_coins)}, csv: {sorted(header_coins)})"
                )
            coin_columns = header_coins
            resume_from  = last_block + 1
            file_mode    = "a"
            print(f"Resuming from block {resume_from}")

        with open(CSV_PATH, file_mode, encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";")

            if file_mode == "w":
                writer.writerow(["block_number", "timestamp"] + coin_columns)

            batches  = range(resume_from, config["end_block"], config["batch_size"])
            progress = tqdm(batches, desc="Processing blocks", unit="batch")

            for batch_start in progress:
                if cancellation_token.is_canceled():
                    break

                batch_end = min(batch_start + config["batch_size"], config["end_block"])
                blocks = await dc.get_blocks_online(batch_start, batch_end, with_dex=True)

                for block_number, timestamp, txs in blocks:
                    sums = block_volume_per_coin(txs if txs is not None else [], eth_pricing_mode)
                    writer.writerow(
                        [block_number, datetime.datetime.fromtimestamp(timestamp.timestamp(), datetime.timezone.utc).replace(tzinfo=None)]
                        + [sums.get(coin, 0.0) for coin in coin_columns]
                    )
            f.flush()

    finally:
        await dc.close()


if __name__ == "__main__":
    asyncio.run(main())
