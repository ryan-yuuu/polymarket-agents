"""Live Live Trade Dashboard for monitoring agent performance."""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ARCHIVE_DIR = DATA_DIR / "archive"


def archive_file(path: Path) -> None:
    """Move a trade CSV to the archive directory."""
    ARCHIVE_DIR.mkdir(exist_ok=True)
    path.rename(ARCHIVE_DIR / path.name)


def parse_filename(name: str) -> tuple[str, datetime]:
    """Extract agent_id and session date from {agent_id}.{epoch}.trades.csv."""
    stem = name.removesuffix(".trades.csv")
    agent_id, epoch_str = stem.rsplit(".", 1)
    return agent_id, datetime.fromtimestamp(int(epoch_str), tz=timezone.utc)


def discover_files() -> list[dict]:
    """Find all trade CSVs, sorted newest-first."""
    if not DATA_DIR.exists():
        return []
    files = []
    for path in DATA_DIR.glob("*.trades.csv"):
        try:
            agent_id, session_date = parse_filename(path.name)
            files.append({"path": path, "agent_id": agent_id, "session_date": session_date})
        except (ValueError, IndexError):
            continue
    return sorted(files, key=lambda f: f["session_date"], reverse=True)


@st.cache_data(ttl=10)
def load_trades(path: str) -> pd.DataFrame:
    """Load trades CSV with a 10s cache TTL for live updates."""
    return pd.read_csv(path, parse_dates=["timestamp"])


def main():
    st.set_page_config(page_title="Live Trade Dashboard", layout="wide")

    files = discover_files()

    if not files:
        st.title("Live Trade Dashboard")
        st.info("No trade files found in data/. Start an agent to generate trades.")
        return

    # ── Sidebar ──────────────────────────────────────────────────────────
    with st.sidebar:
        st.title("Live Trade Dashboard")

        selected_idx = st.radio(
            "Agent sessions",
            range(len(files)),
            format_func=lambda i: (
                f"{files[i]['agent_id']}  \n"
                f"{files[i]['session_date'].strftime('%b %d, %Y  %H:%M UTC')}"
            ),
            label_visibility="collapsed",
        )

        st.divider()
        auto_refresh = st.toggle("Auto-refresh", value=False)
        if auto_refresh:
            interval = st.select_slider(
                "Interval (s)", options=[10, 15, 30, 60], value=30
            )
            from streamlit_autorefresh import st_autorefresh

            st_autorefresh(interval=interval * 1000, key="auto_refresh")

    # ── Main content ─────────────────────────────────────────────────────
    selected = files[selected_idx]
    df = load_trades(str(selected["path"]))

    @st.dialog("Archive session")
    def confirm_archive_dialog(path: Path, agent_id: str):
        st.warning(f"Archive **{agent_id}** session?")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("Confirm", use_container_width=True):
                archive_file(path)
                st.cache_data.clear()
                st.rerun()
        with col_no:
            if st.button("Cancel", use_container_width=True):
                st.rerun()

    col_header, col_archive = st.columns([0.9, 0.1], vertical_alignment="center")
    with col_header:
        st.header(selected["agent_id"], divider=False)
    with col_archive:
        if st.button("Archive", key="archive_btn"):
            confirm_archive_dialog(selected["path"], selected["agent_id"])
    st.caption(
        selected["session_date"].strftime("Session started %B %d, %Y at %H:%M UTC")
    )

    sells = df[df["order_side"] == "sell"]
    initial = float(df["initial_balance"].iloc[0])
    current = float(sells["balance_after"].iloc[-1]) if len(sells) > 0 else initial
    pnl = current - initial
    pnl_pct = (pnl / initial) * 100

    # Metrics row
    num_settled = len(sells)
    ev = pnl / num_settled if num_settled > 0 else 0.0
    ev_str = f"-${abs(ev):.2f}" if ev < 0 else f"+${ev:.2f}"
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Balance",
        f"${current:.2f}",
        help="Current cash balance after all settled trades. Calculated from the balance_after column of the most recent sell order.",
    )
    pnl_str = f"-${abs(pnl):.2f}" if pnl < 0 else f"+${pnl:.2f}"
    c2.metric(
        "P&L",
        pnl_str,
        f"{pnl_pct:+.1f}%",
        help="Profit & Loss. Dollar difference between the current balance and the initial balance. The percentage shows P&L relative to the initial balance.",
    )
    c3.metric(
        "Settled Trades",
        str(num_settled),
        help="Number of settled trades.",
    )
    c4.metric(
        "EV per Trade",
        ev_str,
        help="Expected profit per trade. Total P&L divided by the number of settled trades. Indicates average profitability across all resolved positions.",
    )

    if len(sells) == 0:
        st.info(
            "No sell orders yet. Chart appears after the first position resolves."
        )
        return

    # ── Account value chart ──────────────────────────────────────────────
    # When multiple sells settle at the same timestamp, keep the last
    # balance_after (the final account state after all settlements).
    settled = sells.groupby("timestamp", as_index=False)["balance_after"].last()

    # Prepend initial balance at first trade timestamp as the starting point
    timestamps = [df["timestamp"].iloc[0]] + settled["timestamp"].tolist()
    values = [initial] + settled["balance_after"].tolist()

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=timestamps,
            y=values,
            mode="lines+markers",
            name="Account Value",
            line=dict(width=2),
            marker=dict(size=6),
            hovertemplate="<b>%{x|%H:%M:%S}</b><br>$%{y:.2f}<extra></extra>",
        )
    )
    fig.add_hline(
        y=initial,
        line_dash="dash",
        line_color="rgba(128,128,128,0.4)",
        annotation_text=f"Initial ${initial:.2f}",
        annotation_position="bottom right",
    )
    fig.update_layout(
        yaxis_title="Balance ($)",
        xaxis_title="Time",
        hovermode="x unified",
        height=420,
        margin=dict(l=0, r=0, t=10, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)


if __name__ == "__main__":
    main()
