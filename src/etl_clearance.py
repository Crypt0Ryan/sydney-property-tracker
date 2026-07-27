"""Fetch Sydney weekly auction clearance rate history from SQM Research.

Domain and realestate.com.au both hard-block scraping at the CDN/WAF level
(Akamai), which is what per-postcode auction results and bid/ask spread would
require. SQM Research (sqmresearch.com.au) publishes the same weekly
clearance-rate stat city-wide, isn't behind that protection, and embeds the
*entire* historical series directly in the auction-results page as a JS array
- so this is a single request, not a scrape loop.

Clearance rate here = (Sold Prior + Sold at Auction) / Total Scheduled, per
SQM's own published methodology (see /property/auction-methodology on their
site). City-wide only - no per-postcode breakdown, no listed-vs-sold spread.
"""

import json
import re
from pathlib import Path

import duckdb
import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "sydney_property.duckdb"

# state=NSW pins this to Sydney (SQM's own rCity field on this endpoint is
# "Sydney" for NSW) regardless of the requesting server's geo-IP.
URL = "https://sqmresearch.com.au/property/auction-results?state=NSW"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


def fetch_history() -> list[dict]:
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    match = re.search(r"const auction_history = (\[.*?\]);", resp.text)
    if not match:
        raise RuntimeError(
            "Couldn't find the auction_history data block on the SQM Research "
            "page - the page layout may have changed."
        )
    return json.loads(match.group(1))


def run() -> None:
    records = fetch_history()
    df = pd.DataFrame(records)
    df["week_ending"] = pd.to_datetime(df["enddate"]).dt.date
    df["clearance_rate"] = df["clearance"].astype(float)
    df = df.rename(columns={"rCity": "city", "ttl": "total_scheduled", "sold": "sold"})
    df = df[["week_ending", "city", "total_scheduled", "sold", "clearance_rate"]]

    con = duckdb.connect(str(DB_PATH))
    con.execute("CREATE OR REPLACE TABLE clearance_rate AS SELECT * FROM df ORDER BY week_ending")
    n_weeks, min_week, max_week = con.execute(
        "SELECT COUNT(*), MIN(week_ending), MAX(week_ending) FROM clearance_rate"
    ).fetchone()
    con.close()

    print(f"Loaded {n_weeks} weeks of Sydney clearance rate data ({min_week} to {max_week})")


if __name__ == "__main__":
    run()
