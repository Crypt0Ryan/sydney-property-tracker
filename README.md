# Sydney Property Tracker

Residential sale price charting (overall Sydney + postcode overlay) plus
city-wide weekly auction clearance rate. Bid/ask (listed-vs-sold) spread is
deliberately out of scope - see "Why no bid/ask spread" below.

## Data sources

- **Sale prices**: cleaned CSV from https://nswpropertysalesdata.com/ (itself
  built from the NSW Valuer General's free bulk Property Sales Information).
  Updated daily upstream; re-download periodically to refresh.
- **Clearance rate**: SQM Research (sqmresearch.com.au/property/auction-results),
  which publishes the full historical weekly Sydney clearance-rate series
  embedded directly in the page. City-wide only, no per-postcode breakdown.

### Why no bid/ask spread

That needs per-listing asking price + sold price, which only exists on
Domain/realestate.com.au. Both hard-block scraping at the CDN/WAF level
(Akamai) - a plain request gets a 403/429 before any parsing logic even runs.
Getting past that means a stealth headless browser fighting bot detection
indefinitely, with real risk of the scraping IP getting blocked. Decided
against it for now in favour of SQM's free, unblocked, city-wide clearance
rate feed. Revisit if per-postcode auction results / spread become important
enough to justify that engineering and risk.

## Setup

```
python -m venv .venv
./.venv/Scripts/pip install -r requirements.txt
```

## Refresh data

Sale prices:
```
curl -L -o data/archive.zip https://nswpropertysalesdata.com/data/archive.zip
cd data && unzip -o archive.zip && cd ..
./.venv/Scripts/python src/etl.py
```

Clearance rate (re-running just pulls the latest full history, no date params needed):
```
./.venv/Scripts/python src/etl_clearance.py
```

Stop the Streamlit app first if it's running - it holds the DuckDB file open
and the ETL scripts need write access.

This builds `data/sydney_property.duckdb` with:
- `sales` - filtered residential transactions (Primary purpose = Residence)
- `postcode_lookup` - postcode -> most common suburb name
- `clearance_rate` - weekly Sydney auction clearance rate, total scheduled, sold

## Run the app

```
./.venv/Scripts/python -m streamlit run src/app.py
```

Then open http://localhost:8501.

## Known simplifications (v1)

- "Sydney overall" uses an approximate Greater Sydney postcode range
  (`src/postcodes.py`), not an official ABS boundary file.
- Clearance rate is city-wide only, not broken down by postcode.
- Chart styling targets light mode only.
