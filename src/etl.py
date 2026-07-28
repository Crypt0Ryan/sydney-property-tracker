"""Load the NSW property sales CSV (sourced from nswpropertysalesdata.com,
which itself cleans the NSW Valuer General bulk Property Sales Information)
into a local DuckDB file, filtered to residential sales, with monthly
aggregates by postcode ready for charting.

Each download from nswpropertysalesdata.com is a full replacement (the last
~6 years), not a delta - so this only ever reads the single most recently
dated CSV in data/, never a glob of all of them, otherwise old and new would
get UNIONed together and double-count every sale that appears in both.
"""

from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "sydney_property.duckdb"


def latest_csv() -> Path:
    candidates = sorted(DATA_DIR.glob("nsw-property-sales-data-*.csv"))
    if not candidates:
        raise FileNotFoundError(f"No nsw-property-sales-data-*.csv found in {DATA_DIR}")
    return candidates[-1]


def run() -> None:
    csv_path = latest_csv()
    con = duckdb.connect(str(DB_PATH))

    con.execute(
        f"""
        CREATE OR REPLACE TABLE sales AS
        SELECT
            TRY_CAST("Property post code" AS INTEGER) AS postcode,
            "Property locality" AS suburb,
            TRY_CAST("Contract date" AS DATE) AS contract_date,
            TRY_CAST("Purchase price" AS DOUBLE) AS purchase_price,
            "Primary purpose" AS primary_purpose,
            "Nature of property" AS nature_of_property,
            TRY_CAST("Area" AS DOUBLE) AS area,
            "Area type" AS area_type,
            "Zoning" AS zoning
        FROM read_csv_auto('{csv_path.as_posix()}', ALL_VARCHAR = TRUE)
        WHERE "Primary purpose" = 'Residence'
          AND TRY_CAST("Purchase price" AS DOUBLE) > 10000
          AND TRY_CAST("Contract date" AS DATE) >= DATE '2000-01-01'
          AND TRY_CAST("Contract date" AS DATE) <= CURRENT_DATE
          AND TRY_CAST("Property post code" AS INTEGER) IS NOT NULL
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TABLE postcode_monthly AS
        SELECT
            postcode,
            date_trunc('month', contract_date)::DATE AS month,
            COUNT(*) AS num_sales,
            AVG(purchase_price) AS avg_price,
            MEDIAN(purchase_price) AS median_price
        FROM sales
        GROUP BY postcode, month
        """
    )

    con.execute(
        """
        CREATE OR REPLACE TABLE postcode_lookup AS
        SELECT postcode, arg_max(suburb, cnt) AS primary_suburb
        FROM (
            SELECT postcode, suburb, COUNT(*) AS cnt
            FROM sales
            WHERE suburb IS NOT NULL
            GROUP BY postcode, suburb
        )
        GROUP BY postcode
        """
    )

    n_sales, n_postcodes = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT postcode) FROM sales"
    ).fetchone()
    date_min, date_max = con.execute(
        "SELECT MIN(contract_date), MAX(contract_date) FROM sales"
    ).fetchone()
    con.close()

    print(f"Loaded {n_sales:,} residential sales across {n_postcodes} postcodes")
    print(f"Contract date range: {date_min} to {date_max}")
    print(f"DuckDB file: {DB_PATH}")


if __name__ == "__main__":
    run()
