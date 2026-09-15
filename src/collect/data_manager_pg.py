from importlib.metadata import pass_none

import asyncpg
import numpy as np
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
from coingecko_sdk import Coingecko
from collect.db_connection import open_db

ZERO_BYTES = bytes(20)  # 20 zero bytes = Ethereum zero address
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_PLATFORM = "ethereum"
ETH_NAME = "ETH"
BIGINT_MAX = 2**63 - 1
MAX_PLAUSIBLE_AMOUNT = 2**200


class PostgresDataCollector:
    """
    DataCollector that reads block/transaction data from a Postgres database.
    """

    def __init__(self, config: dict):
        self.config = config
        self.pg_pool = None

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

        # convert tracked ERC-20 addresses to bytea for SQL parameter
        self.tracked_token_bytes = [
            bytes.fromhex(addr[2:]) for addr in self.tracked_erc20.keys()
        ]

        self.current_prices = {}
        self.current_date = datetime.min

        # for (coin name, date) pairs CoinGecko has no price for -> avoid re-querying them
        self.missing_prices = set()
        self.unpriced_transfers = defaultdict(int)

    async def open(self):
        self.pg_pool = await asyncpg.create_pool(
            host=self.config["PG_HOST"],
            port=int(self.config.get("PG_PORT", 5432)),
            database=self.config["PG_DBNAME"],
            user=self.config["PG_USER"],
            password=self.config.get("PG_PASSWORD"),
            ssl="require",
            min_size=2,
            max_size=10,
        )

        print("DB opened")

    async def close(self):
        if self.pg_pool:
            await self.pg_pool.close()
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

            # the range can start after 'date' if the token was listed later
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

    async def get_blocks_online(self, start_block, end_block, with_dex=False):
        async with self.pg_pool.acquire() as conn:
            # get blocks
            block_rows = await conn.fetch(
                "SELECT block_number, ts FROM eth_block "
                "WHERE block_number >= $1 AND block_number < $2 "
                "ORDER BY ts",
                start_block, end_block,
            )
            if not block_rows:
                return []

            block_ts = {r["block_number"]: r["ts"] for r in block_rows}

            # fetch ETH native transfers (value > 0, no zero-addr)
            eth_rows = []
            if self.eth_coin is not None:
                eth_rows = await conn.fetch("""
                    SELECT t.block_number, t.tx_index,
                           '0x' || encode(a_from.addr, 'hex') AS from_addr,
                           '0x' || encode(a_to.addr, 'hex')   AS to_addr,
                           t.value                            AS amount
                    FROM eth_tx t
                             JOIN address a_from ON a_from.id = t.from_id
                             JOIN address a_to ON a_to.id = t.to_id
                    WHERE t.block_number >= $1
                      AND t.block_number < $2
                      AND t.value > 0
                      AND a_from.addr != $3
                      AND a_to.addr != $3
                      AND t.from_id != t.to_id
                    """, start_block, end_block, ZERO_BYTES)

            # fetch ERC-20 transfers for tracked tokens
            erc20_rows = []
            if self.tracked_token_bytes:
                erc20_rows = await conn.fetch("""
                    SELECT e.block_number, e.tx_index, e.log_index,
                           '0x' || encode(tok.addr, 'hex') AS token_addr,
                           '0x' || encode(a_from.addr, 'hex') AS from_addr,
                           '0x' || encode(a_to.addr, 'hex') AS to_addr,
                           e.amount
                    FROM erc20_transfer e
                    JOIN token tok ON tok.id = e.token_id
                    JOIN address a_from ON a_from.id = e.from_id
                    JOIN address a_to ON a_to.id = e.to_id
                    WHERE e.block_number >= $1 AND e.block_number < $2
                      AND tok.addr = ANY($3::bytea[])
                      AND a_from.addr != $4
                      AND a_to.addr   != $4
                      AND e.from_id   != e.to_id
                """, start_block, end_block, self.tracked_token_bytes, ZERO_BYTES)

        # build per-block transaction dicts
        block_txs = defaultdict(list)

        for row in eth_rows:
            bn = row["block_number"]
            ts = block_ts.get(bn)
            if ts is None:
                continue
            amount = int(row["amount"])
            usd_value = await self.get_usd_value(self.eth_coin, ts, amount)
            if usd_value is None:
                continue
            block_txs[bn].append({
                "hash": f"{bn}_{row['tx_index']}",
                "coin": self.eth_coin["name"],
                "from": row["from_addr"].lower(),
                "to": row["to_addr"].lower(),
                "amount": min(amount, BIGINT_MAX),
                "eth_amount": amount / 10**18,
                "usd_value": usd_value,
                "is_dex_swap": False,
            })

        for row in erc20_rows:
            token_addr = row["token_addr"].lower()
            coin = self.tracked_erc20.get(token_addr)
            if coin is None:
                continue
            bn = row["block_number"]
            ts = block_ts.get(bn)
            if ts is None:
                continue
            amount = int(row["amount"])
            # ignore implausible amounts
            if amount >= MAX_PLAUSIBLE_AMOUNT:
                print(f"skipped implausible amount: block {bn} tx {row['tx_index']} {coin['name']} {amount}")
                continue
            usd_value = await self.get_usd_value(coin, ts, amount)
            eth_price = await self.get_usd_value(self.eth_coin, ts, 10**18)
            if usd_value is None or eth_price is None:
                continue
            block_txs[bn].append({
                "hash": f"{bn}_{row['tx_index']}",
                "coin": coin["name"],
                "from": row["from_addr"].lower(),
                "to": row["to_addr"].lower(),
                "amount": min(amount, BIGINT_MAX),
                "eth_amount": usd_value / eth_price,
                "usd_value": usd_value,
                "is_dex_swap": False,
            })

        result = []
        for br in block_rows:
            bn = br["block_number"]
            txs = block_txs.get(bn, [])
            if not with_dex:
                raise NotImplementedError("dex swap filter is not implemented yet for this collector")
                txs = [tx for tx in txs if not tx["is_dex_swap"]]
            result.append((bn, br["ts"], txs))

        return result
