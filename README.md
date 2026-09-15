# Measuring Transaction Volume.

This project is a fork from `https://github.com/clemensJul/measuring-transaction-volume/`

Analysis of transaction streams between wallets. Compares the tracked transaction volume between three algorithms and plots native Ethereum and ERC-20 transactions.

---

## Table of Contents
- Overview  
- Installation  
- Usage  
- Attribution

---

## Overview
Information about the total transaction volume on the Ethereum blockchain can be used in many different areas. This repository includes
- RPC Client code to extract data from a to you available Ethereum validator node. 
- Three different algorithms used to compute the transaction volume
- Plotting tools to visualise the results


The purpose of this repository is to compare the effectiveness of using a smart algorithm to count transaction volume. Limiting the transaction volume
could be a useful tool in game theoretic blockchain protocols. Instead of counting the total transaction volume a smarter algorithm could be deployed that
captures a more realistic loss of value that is possible in a given timeframe Δ

---

## Installation

```bash
git clone https://github.com/md-dan1/analysing-transaction-volume
cd analysing-transaction-volume
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Usage

All scripts are run from the directory `analysing-transaction-volume/`. Parameters (block range, tracked
tokens, delta windows, pricing mode) are defined in `config/config.yaml`.

A validator node and a CoinGecko API key for the daily price of the erc-20 tokens
are required. The Postgres path additionally required the credentials of a pre-populated
Ethereum database.
The price data to determine the value of each transaction is taken daily using a CoinGecko.

### .env variables
```
RCP_URL="localhost:xxxx"
COIN_GECKO_API_KEY="xxx"
PG_HOST="xxx"
PG_PORT="5432"
PG_DBNAME="xxx"
PG_USER="xxx"
PG_PASSWORD="xxx"
```

### Data collection

- `python3 src/collect_csv_db_batched.py`: main collector; runs all three algorithms
  (ttv, defi, asp) over every configured delta on blocks read from Postgres in batches and
  writes one CSV row per block (including warm-up).
- `python3 src/collect_csv_per_coin.py`: writes the plain per-block volume (ttv) split into one
  column per tracked coin, without a sliding window.

### Charts

Each of these scripts only read from CSV. Therefore they run without a database or network access.
Input path and chart options are set in the config section at the top of each script.

- `python3 src/csv_analytics/build_charts.py`: per-algorithm maximum and average volume
  stacked by delta, plus daily mean and daily max for a selected deltas.
- `python3 src/csv_analytics/build_volume_per_delta_chart.py`: average volume of one
  algorithm as a function of the window size delta.
- `python3 src/csv_analytics/build_volume_per_coins.py`: total volume against the number
  of tracked coins, summed over the per-coin CSV.

---

## Attribution

Price data powered by [CoinGecko API](https://www.coingecko.com)

