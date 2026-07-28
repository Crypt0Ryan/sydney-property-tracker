"""Sydney Property Tracker - v1 prototype.

Charts residential sale prices (overall Sydney + postcode overlay) from free
NSW property sales data, plus city-wide weekly auction clearance rate from
SQM Research. Bid/ask (listed-vs-sold) spread is not available: it needs
per-listing data from Domain/REA, both of which hard-block scraping at the
CDN level - see README for the tradeoff that was made here.
"""

import sys
from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

sys.path.append(str(Path(__file__).resolve().parent))
from postcodes import sydney_postcode_sql_filter  # noqa: E402

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sydney_property.duckdb"

# Categorical palette (validated, fixed order - see dataviz skill palette.md)
PALETTE = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
    "#e87ba4", "#008300", "#4a3aa7", "#e34948",
]
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"
SYDNEY_REF_COLOR = "#898781"  # muted gray for the "Sydney overall" reference line

# Settlement/registration of a sale into this dataset lags the contract date
# by weeks to a few months, so the most recent months always look
# artificially low until the final numbers land.
SETTLEMENT_LAG_DAYS = 90

GRANULARITY_SQL = {"Monthly": "month", "Quarterly": "quarter"}
SMOOTHING_OPTIONS = {"Monthly": [1, 3, 6, 12], "Quarterly": [1, 2, 4]}

st.set_page_config(page_title="Sydney Property Tracker", layout="wide")


@st.cache_resource
def get_con():
    return duckdb.connect(str(DB_PATH), read_only=True)


con = get_con()


def period_label(period: pd.Timestamp, granularity: str) -> str:
    if granularity == "quarter":
        q = (period.month - 1) // 3 + 1
        return f"Q{q} {period.year}"
    return period.strftime("%b %Y")


@st.cache_data
def load_date_bounds():
    return con.execute("SELECT MIN(contract_date), MAX(contract_date) FROM sales").fetchone()


@st.cache_data
def load_postcode_options() -> pd.DataFrame:
    return con.execute(
        """
        SELECT s.postcode, pl.primary_suburb, COUNT(*) AS total_sales
        FROM sales s
        LEFT JOIN postcode_lookup pl USING (postcode)
        GROUP BY 1, 2
        HAVING total_sales >= 30
        ORDER BY s.postcode
        """
    ).df()


@st.cache_data
def load_overall(granularity_sql: str, start_date, end_date) -> pd.DataFrame:
    filt = sydney_postcode_sql_filter()
    return con.execute(
        f"""
        SELECT
            date_trunc(?, contract_date)::DATE AS period,
            COUNT(*) AS num_sales,
            AVG(purchase_price) AS avg_price,
            MEDIAN(purchase_price) AS median_price
        FROM sales
        WHERE {filt}
          AND contract_date BETWEEN ? AND ?
        GROUP BY period
        ORDER BY period
        """,
        [granularity_sql, start_date, end_date],
    ).df()


@st.cache_data
def load_postcode_series(postcodes: list[int], granularity_sql: str, start_date, end_date) -> pd.DataFrame:
    return con.execute(
        """
        SELECT
            postcode,
            date_trunc(?, contract_date)::DATE AS period,
            COUNT(*) AS num_sales,
            AVG(purchase_price) AS avg_price,
            MEDIAN(purchase_price) AS median_price
        FROM sales
        WHERE postcode = ANY(?)
          AND contract_date BETWEEN ? AND ?
        GROUP BY postcode, period
        ORDER BY postcode, period
        """,
        [granularity_sql, postcodes, start_date, end_date],
    ).df()


@st.cache_data
def load_clearance(start_date, end_date) -> pd.DataFrame:
    return con.execute(
        """
        SELECT week_ending, total_scheduled, sold, clearance_rate
        FROM clearance_rate
        WHERE week_ending BETWEEN ? AND ?
        ORDER BY week_ending
        """,
        [start_date, end_date],
    ).df()


def smooth(df: pd.DataFrame, cols: list[str], window: int) -> pd.DataFrame:
    if window <= 1:
        return df
    df = df.copy()
    for c in cols:
        df[c] = df[c].rolling(window, min_periods=1).mean()
    return df


def latest_change(df: pd.DataFrame, col: str) -> tuple[float, float] | tuple[None, None]:
    """Latest value and period-on-period % change, from unsmoothed data."""
    if len(df) < 2:
        return None, None
    latest = df[col].iloc[-1]
    prev = df[col].iloc[-2]
    if pd.isna(latest) or pd.isna(prev) or prev == 0:
        return latest, None
    return latest, (latest - prev) / prev * 100


def show_change_metric(label: str, df: pd.DataFrame, col: str) -> None:
    latest, pct = latest_change(df, col)
    if latest is None:
        return
    st.metric(label, f"${latest:,.0f}", f"{pct:+.1f}%" if pct is not None else None)


st.title("Sydney Property Tracker")
st.caption(
    "Sale prices from NSW property sales data (via nswpropertysalesdata.com, sourced from "
    "the NSW Valuer General bulk Property Sales Information). Auction clearance rate from "
    "SQM Research, city-wide only. Listed-vs-sold spread isn't available - see README."
)

min_date, max_date = load_date_bounds()
reliable_end = max_date - timedelta(days=SETTLEMENT_LAG_DAYS)

with st.sidebar:
    st.header("Filters")
    granularity = st.radio("Chart granularity", ["Monthly", "Quarterly"], index=0, horizontal=True)
    granularity_sql = GRANULARITY_SQL[granularity]
    date_range = st.slider(
        "Contract date range",
        min_value=min_date,
        max_value=max_date,
        value=(max_date.replace(year=max_date.year - 5), reliable_end),
        format="YYYY-MM",
        help=f"Defaults to excluding the last ~{SETTLEMENT_LAG_DAYS} days: sales lag "
        "behind their contract date before they're registered, so recent months "
        "look artificially low until the final numbers land. Drag past the default "
        "to see the (incomplete) latest data anyway.",
    )
    metric = st.radio("Metric", ["Average price", "Median price"], index=0)
    smoothing_window = st.select_slider(
        "Smoothing (rolling window)",
        options=SMOOTHING_OPTIONS[granularity],
        value=SMOOTHING_OPTIONS[granularity][1],
        help="Postcode-level prices are noisy at low sale volumes; this averages "
        "over a trailing window of periods.",
    )
    clearance_smoothing = st.select_slider(
        "Clearance rate smoothing (weeks)",
        options=[1, 4, 8, 12],
        value=4,
        help="Weekly clearance rate is noisy (especially over school holidays/low "
        "auction weeks); this averages over a trailing window of weeks.",
    )

metric_col = "avg_price" if metric == "Average price" else "median_price"

if date_range[1] > reliable_end:
    st.warning(
        f"Showing data past {reliable_end:%b %Y} — the most recent period(s) are likely "
        "incomplete (registration lag), not a real price move."
    )

# ---- Overall Sydney trend ----
st.subheader("Overall Sydney trend")
overall = load_overall(granularity_sql, date_range[0], date_range[1])
overall_chart = smooth(overall, ["avg_price", "median_price"], smoothing_window)
overall_label = [period_label(p, granularity_sql) for p in overall_chart["period"]]

period_unit = "MoM" if granularity_sql == "month" else "QoQ"
with st.sidebar:
    st.header(f"Latest change ({period_unit})")
    show_change_metric("Sydney overall", overall, metric_col)

fig_overall = make_subplots(
    rows=2,
    cols=1,
    shared_xaxes=True,
    row_heights=[0.72, 0.28],
    vertical_spacing=0.04,
)
fig_overall.add_trace(
    go.Scatter(
        x=overall_chart["period"],
        y=overall_chart[metric_col],
        mode="lines",
        line=dict(color=PALETTE[0], width=2),
        name="Sydney overall",
        customdata=overall_label,
        hovertemplate="%{customdata}<br>$%{y:,.0f}<extra></extra>",
    ),
    row=1,
    col=1,
)
fig_overall.add_trace(
    go.Bar(
        x=overall_chart["period"],
        y=overall_chart["num_sales"],
        marker=dict(color=INK_MUTED),
        name="Sales volume",
        customdata=overall_label,
        hovertemplate="%{customdata}<br>%{y:,.0f} sales<extra></extra>",
    ),
    row=2,
    col=1,
)
fig_overall.update_xaxes(showgrid=False, linecolor=BASELINE, showticklabels=False, row=1, col=1)
fig_overall.update_xaxes(showgrid=False, linecolor=BASELINE, tickfont=dict(color=INK_MUTED), row=2, col=1)
fig_overall.update_yaxes(
    title=metric,
    showgrid=True,
    gridcolor=GRID,
    zeroline=False,
    tickfont=dict(color=INK_MUTED),
    tickprefix="$",
    tickformat=",.0f",
    row=1,
    col=1,
)
fig_overall.update_yaxes(
    title=f"Sales/{granularity_sql}",
    showgrid=True,
    gridcolor=GRID,
    tickfont=dict(color=INK_MUTED),
    row=2,
    col=1,
)
fig_overall.update_layout(
    plot_bgcolor=SURFACE,
    paper_bgcolor=SURFACE,
    font=dict(color=INK_PRIMARY),
    margin=dict(l=10, r=10, t=10, b=10),
    height=440,
    showlegend=False,
    hovermode="x unified",
)
st.plotly_chart(fig_overall, use_container_width=True, theme=None)

# ---- Auction clearance rate ----
st.subheader("Auction clearance rate (Sydney, weekly)")

clearance = load_clearance(date_range[0], date_range[1])
if clearance.empty:
    st.info("No clearance rate data in this date range.")
else:
    clearance = smooth(clearance, ["clearance_rate"], clearance_smoothing)
    clearance_label = [d.strftime("%d %b %Y") for d in pd.to_datetime(clearance["week_ending"])]

    fig_clearance = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.04,
    )
    fig_clearance.add_trace(
        go.Scatter(
            x=clearance["week_ending"],
            y=clearance["clearance_rate"] * 100,
            mode="lines",
            line=dict(color=PALETTE[1], width=2),
            name="Clearance rate",
            customdata=clearance_label,
            hovertemplate="%{customdata}<br>%{y:.1f}% cleared<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig_clearance.add_trace(
        go.Bar(
            x=clearance["week_ending"],
            y=clearance["total_scheduled"],
            marker=dict(color=INK_MUTED),
            name="Auctions scheduled",
            customdata=clearance_label,
            hovertemplate="%{customdata}<br>%{y:,.0f} scheduled<extra></extra>",
        ),
        row=2,
        col=1,
    )
    fig_clearance.update_xaxes(showgrid=False, linecolor=BASELINE, showticklabels=False, row=1, col=1)
    fig_clearance.update_xaxes(showgrid=False, linecolor=BASELINE, tickfont=dict(color=INK_MUTED), row=2, col=1)
    fig_clearance.update_yaxes(
        title="Clearance rate",
        showgrid=True,
        gridcolor=GRID,
        zeroline=False,
        tickfont=dict(color=INK_MUTED),
        ticksuffix="%",
        range=[0, 100],
        row=1,
        col=1,
    )
    fig_clearance.update_yaxes(
        title="Auctions/week", showgrid=True, gridcolor=GRID, tickfont=dict(color=INK_MUTED), row=2, col=1
    )
    fig_clearance.update_layout(
        plot_bgcolor=SURFACE,
        paper_bgcolor=SURFACE,
        font=dict(color=INK_PRIMARY),
        margin=dict(l=10, r=10, t=10, b=10),
        height=440,
        showlegend=False,
        hovermode="x unified",
    )
    st.plotly_chart(fig_clearance, use_container_width=True, theme=None)
    st.caption(
        "Source: SQM Research weekly auction results for NSW. City-wide only - "
        "no per-postcode breakdown available from this source."
    )

# ---- Postcode overlay ----
st.subheader("Postcode overlay")

options_df = load_postcode_options()
options_df["label"] = options_df.apply(
    lambda r: f"{int(r.postcode)} - {r.primary_suburb}" if pd.notna(r.primary_suburb) else str(int(r.postcode)),
    axis=1,
)
label_to_postcode = dict(zip(options_df["label"], options_df["postcode"]))

default_labels = [lbl for lbl, pc in label_to_postcode.items() if pc in (2000, 2026, 2170)]

selected_labels = st.multiselect(
    "Postcodes to overlay (max 8)",
    options=list(label_to_postcode.keys()),
    default=default_labels,
    max_selections=8,
)
show_sydney_ref = st.checkbox("Show Sydney overall as reference line", value=True)

selected_postcodes = [int(label_to_postcode[lbl]) for lbl in selected_labels]

if selected_postcodes:
    pc_df = load_postcode_series(selected_postcodes, granularity_sql, date_range[0], date_range[1])

    fig_overlay = go.Figure()

    if show_sydney_ref:
        fig_overlay.add_trace(
            go.Scatter(
                x=overall_chart["period"],
                y=overall_chart[metric_col],
                mode="lines",
                line=dict(color=SYDNEY_REF_COLOR, width=2, dash="dash"),
                name="Sydney overall",
                customdata=overall_label,
                hovertemplate="Sydney overall<br>%{customdata}<br>$%{y:,.0f}<extra></extra>",
            )
        )

    with st.sidebar:
        for postcode in selected_postcodes:
            raw_series = pc_df[pc_df["postcode"] == postcode].sort_values("period")
            label = next(
                (lbl for lbl, pc in label_to_postcode.items() if pc == postcode), str(postcode)
            )
            show_change_metric(label, raw_series, metric_col)

    for i, postcode in enumerate(selected_postcodes):
        series = pc_df[pc_df["postcode"] == postcode].sort_values("period")
        series = smooth(series, ["avg_price", "median_price"], smoothing_window)
        label = next(
            (lbl for lbl, pc in label_to_postcode.items() if pc == postcode), str(postcode)
        )
        series_label = [period_label(p, granularity_sql) for p in series["period"]]
        color = PALETTE[i % len(PALETTE)]
        fig_overlay.add_trace(
            go.Scatter(
                x=series["period"],
                y=series[metric_col],
                mode="lines",
                line=dict(color=color, width=2),
                name=label,
                customdata=list(zip(series_label, series["num_sales"])),
                hovertemplate=label
                + "<br>%{customdata[0]}<br>$%{y:,.0f}<br>%{customdata[1]:.0f} sales<extra></extra>",
            )
        )

    fig_overlay.update_layout(
        plot_bgcolor=SURFACE,
        paper_bgcolor=SURFACE,
        font=dict(color=INK_PRIMARY),
        margin=dict(l=10, r=10, t=60, b=10),
        height=480,
        xaxis=dict(showgrid=False, linecolor=BASELINE, tickfont=dict(color=INK_MUTED)),
        yaxis=dict(
            title=metric,
            showgrid=True,
            gridcolor=GRID,
            zeroline=False,
            tickfont=dict(color=INK_MUTED),
            tickprefix="$",
            tickformat=",.0f",
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(color=INK_PRIMARY, size=13),
            bgcolor=SURFACE,
        ),
        hovermode="x unified",
    )
    st.plotly_chart(fig_overlay, use_container_width=True, theme=None)

    with st.expander("View as table"):
        table = pc_df.pivot(index="period", columns="postcode", values=metric_col)
        table = table.rename(columns={pc: next((lbl for lbl, p in label_to_postcode.items() if p == pc), pc) for pc in table.columns})
        st.dataframe(table.style.format("${:,.0f}"))
else:
    st.info("Pick at least one postcode above to see the overlay chart.")
