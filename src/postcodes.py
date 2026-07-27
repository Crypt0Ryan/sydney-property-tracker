"""Approximate Greater Sydney postcode ranges (ABS GCCSA-ish), used only to
pick a sensible default set of postcodes for the "overall Sydney" view.
Any postcode present in the data can still be selected individually in the app.
"""

SYDNEY_POSTCODE_RANGES = [
    (2000, 2249),  # CBD, inner, eastern suburbs, north shore, inner west, northern beaches
    (2555, 2574),  # Camden, Campbelltown, Narellan
    (2740, 2770),  # Mount Druitt, Blacktown, outer west
    (2775, 2786),  # Blue Mountains (lower/mid)
]


def is_sydney_postcode(postcode: int) -> bool:
    return any(lo <= postcode <= hi for lo, hi in SYDNEY_POSTCODE_RANGES)


def sydney_postcode_sql_filter(column: str = "postcode") -> str:
    clauses = " OR ".join(f"({column} BETWEEN {lo} AND {hi})" for lo, hi in SYDNEY_POSTCODE_RANGES)
    return f"({clauses})"
