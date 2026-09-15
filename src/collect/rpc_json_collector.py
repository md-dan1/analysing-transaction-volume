import numpy as np
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from coingecko_sdk import Coingecko

from collect.rpc_client import RPCClient
from collect.db_connection import open_db

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ASSET_PLATFORM = "ethereum"
ETH_NAME = "ETH"
BIGINT_MAX = 2**63 - 1
MAX_PLAUSIBLE_AMOUNT = 2**200


class RpcJsonDataCollector:
    """
    Maps raw RPC block JSON (from RPCClient) into the per-block transfer
    format the algorithms consume
    """

    def __init__(self, config: dict):
        self.config = config

        self.rpc = RPCClient(config["RCP_URL"])

        # free tier
        # self.coin_gecko = Coingecko(
        #     demo_api_key=config["COIN_GECKO_API_KEY"],
        #     environment="demo",
        # )
        self.coin_gecko = Coingecko(
            pro_api_key=config["COIN_GECKO_API_KEY"],
            environment="pro"
            )
        self.db = open_db()

        # Token config
        self.active_coins = [c for c in config["token"] if c["active"]]
        self.eth_coin = None
        self.tracked_erc20 = {}

        for coin in self.active_coins:
            if coin["name"] == ETH_NAME:
                self.eth_coin = coin
            else:
                self.tracked_erc20[coin["address"].lower()] = coin

        self.current_prices = {}
        self.current_date = datetime.min

        # (coin name, date) pairs CoinGecko has no price for: avoids re-querying them
        self.missing_prices = set()
        self.unpriced_transfers = defaultdict(int)

    async def open(self):
        await self.rpc.open()

    async def close(self):
        await self.rpc.close()
        self.db.close()

        for name, count in self.unpriced_transfers.items():
            print(f"skipped {count} transfers of {name} (no price available)")

    async def get_usd_value(self, coin, datetime_of_block, amount):
        date = datetime_of_block.date()
        if (coin["name"], date) in self.missing_prices:
            self.unpriced_transfers[coin["name"]] += 1
            return None

        if self.current_date == date:
            price = self.current_prices.get(coin["name"])
            if price is not None:
                return np.float64(amount * price / (10 ** coin["decimals"]))

        else:
            self.current_prices.clear()
        row = self.db.execute(
            "SELECT * FROM coin_values WHERE coin = ? AND date = ?",
            (coin["name"], date.isoformat()),
        ).fetchone()

        if row is None:
            to_date = date + timedelta(days=91)

            if coin.get("coingecko_id"):
                # directly per Coin-ID no (necessary for some coins)
                resp = self.coin_gecko.coins.market_chart.get_range(
                    id=coin["coingecko_id"],
                    vs_currency="usd",
                    from_=date.isoformat(),
                    to=to_date.isoformat(),
                )

            elif coin["name"] == ETH_NAME:
                resp = self.coin_gecko.coins.market_chart.get_range(
                    id=ASSET_PLATFORM,
                    vs_currency="usd",
                    from_=date.isoformat(),
                    to=to_date.isoformat(),
                )
            else:
                resp = self.coin_gecko.coins.contract.market_chart.get_range(
                    id=ASSET_PLATFORM,
                    contract_address=coin["address"],
                    vs_currency="usd",
                    from_=date.isoformat(),
                    to=to_date.isoformat(),
                )
            rows_to_insert = [
                (
                    coin["name"],
                    datetime.fromtimestamp(price[0] / 1000, timezone.utc).date().isoformat(),
                    price[1],
                )
                for price in resp.prices
            ]

            if rows_to_insert:
                self.db.executemany(
                    """INSERT INTO coin_values (coin, date, usd_value)
                       VALUES (?, ?, ?)
                       ON CONFLICT (coin, date) DO UPDATE SET usd_value = excluded.usd_value""",
                    rows_to_insert,
                )

            # the range can start after 'date' if the token was listed later,
            # so only a point for exactly this day is a valid price
            row = next((r for r in rows_to_insert if r[1] == date.isoformat()), None)

        if row is None:
            if coin["name"] not in self.unpriced_transfers:
                print(f"no price for {coin['name']} on {date}, its transfers are skipped", flush=True)
            self.unpriced_transfers[coin["name"]] += 1
            self.missing_prices.add((coin["name"], date))
            return None

        self.current_date = date
        self.current_prices[coin["name"]] = row[2]
        return np.float64(amount * row[2] / (10 ** coin["decimals"]))

    async def get_block(self, block_number):
        raw = await self.rpc.process_block(block_number)

        bn = int(raw["number"], 16)
        dt = datetime.fromtimestamp(int(raw["timestamp"], 16), timezone.utc).replace(tzinfo=None)

        txs = []
        # hash = real on-chain tx hash; all legs of one TX share it (grouping key for the algos)
        for tx, receipt in zip(raw["transactions"], raw["receipts"]):
            # native ETH transfer, taken from the tx object (not logs)
            if self.eth_coin is not None:
                if (tx["from"] is not None and tx["to"] is not None
                        and tx["from"] != ZERO_ADDRESS and tx["to"] != ZERO_ADDRESS):
                    amount = int(tx["value"], 16)
                    usd_value = await self.get_usd_value(self.eth_coin, dt, amount) if amount > 0 else None
                    if usd_value is not None:
                        txs.append({
                            "hash": tx["hash"],
                            "coin": self.eth_coin["name"],
                            "from": tx["from"].lower(),
                            "to": tx["to"].lower(),
                            "amount": min(amount, BIGINT_MAX),
                            "eth_amount": amount / 10**18,
                            "usd_value": usd_value,
                            "is_dex_swap": False,
                        })

            # ERC-20 Transfer events, taken from the receipt logs
            for log in receipt["logs"]:
                topics = log.get("topics", [])
                if not topics or topics[0].lower() != TRANSFER_TOPIC:
                    continue

                coin = self.tracked_erc20.get(log["address"].lower())
                if coin is None:
                    continue  # not a tracked token

                from_addr = "0x" + topics[1][-40:].lower()
                to_addr   = "0x" + topics[2][-40:].lower()
                if from_addr == ZERO_ADDRESS or to_addr == ZERO_ADDRESS:
                    continue  # skip mint/burn

                amount = int(log["data"], 16)
                # ignore implausible amounts
                if amount >= MAX_PLAUSIBLE_AMOUNT:
                    print(f"skipped implausible amount: block {bn} tx {log['transactionHash']} {coin['name']} {amount}")
                    continue
                usd_value = await self.get_usd_value(coin, dt, amount)
                eth_price = await self.get_usd_value(self.eth_coin, dt, 10**18)
                if usd_value is None or eth_price is None:
                    continue
                txs.append({
                    "hash": log["transactionHash"],
                    "coin": coin["name"],
                    "from": from_addr,
                    "to": to_addr,
                    "amount": min(amount, BIGINT_MAX),
                    "eth_amount": usd_value / eth_price,
                    "usd_value": usd_value,
                    "is_dex_swap": False,
                })

        return bn, dt, txs
