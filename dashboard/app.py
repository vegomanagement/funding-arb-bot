"""Streamlit дашборд для funding-arb-bot и funding-squeeze-bot.

Запуск локально:
    cd funding-dashboard
    pip install -r requirements.txt
    cp .env.example .env
    streamlit run app.py
"""
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

# ── Конфигурация ──────────────────────────────────────────────────────────────

ARB_DB = os.getenv("ARB_DB_PATH", "../funding_arb.db")
SQZ_DB = os.getenv("SQUEEZE_DB_PATH", "../squeeze-bot/squeeze.db")

st.set_page_config(
    page_title="Funding Bot Dashboard",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Загрузка данных ───────────────────────────────────────────────────────────

@st.cache_data(ttl=30)  # обновляем каждые 30 секунд
def load_arb_data():
    if not Path(ARB_DB).exists():
        return pd.DataFrame(), pd.DataFrame()
    engine = create_engine(f"sqlite:///{ARB_DB}")
    try:
        positions = pd.read_sql("SELECT * FROM positions", engine)
        funding = pd.read_sql("SELECT * FROM funding_events", engine)
        return positions, funding
    except Exception:
        return pd.DataFrame(), pd.DataFrame()


@st.cache_data(ttl=30)
def load_squeeze_data():
    if not Path(SQZ_DB).exists():
        return pd.DataFrame()
    engine = create_engine(f"sqlite:///{SQZ_DB}")
    try:
        return pd.read_sql("SELECT * FROM squeeze_trades", engine)
    except Exception:
        return pd.DataFrame()


# ── Хелперы ───────────────────────────────────────────────────────────────────

def metric_card(label, value, delta=None, prefix="$"):
    if delta is not None:
        st.metric(label, f"{prefix}{value}", delta=f"{prefix}{delta:+.2f}" if delta else None)
    else:
        st.metric(label, f"{prefix}{value}")


def empty_chart_msg(msg="Нет данных"):
    st.info(msg)


# ── Страница: Arb Bot ─────────────────────────────────────────────────────────

def page_arb():
    st.header("🔄 Funding Arb Bot")
    positions, funding_events = load_arb_data()

    if positions.empty:
        st.warning("База данных arb бота не найдена или пуста. Запусти бот в paper режиме.")
        return

    # Конвертируем даты
    for col in ["opened_at", "closed_at"]:
        if col in positions.columns:
            positions[col] = pd.to_datetime(positions[col])

    closed = positions[positions["status"] == "closed"].copy()
    open_pos = positions[positions["status"] == "open"].copy()

    # ── Метрики сверху ────────────────────────────────────────────────────────
    col1, col2, col3, col4, col5 = st.columns(5)

    total_pnl = closed["total_pnl_usd"].sum() if not closed.empty else 0
    total_funding = closed["funding_received_usd"].sum() if not closed.empty else 0
    total_fees = closed["fees_paid_usd"].sum() if not closed.empty else 0
    win_rate = (
        (closed["total_pnl_usd"] > 0).mean() * 100 if not closed.empty else 0
    )

    with col1:
        st.metric("💰 Общий PnL", f"${total_pnl:+.2f}")
    with col2:
        st.metric("📈 Win Rate", f"{win_rate:.0f}%")
    with col3:
        st.metric("🔄 Сделок закрыто", len(closed))
    with col4:
        st.metric("📂 Открытых", len(open_pos))
    with col5:
        st.metric("💸 Funding получено", f"${total_funding:.2f}")

    st.divider()

    # ── Графики ───────────────────────────────────────────────────────────────
    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.subheader("📊 Equity Curve")
        if not closed.empty:
            closed_sorted = closed.sort_values("closed_at")
            closed_sorted["cumulative_pnl"] = closed_sorted["total_pnl_usd"].cumsum()
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=closed_sorted["closed_at"],
                y=closed_sorted["cumulative_pnl"],
                mode="lines+markers",
                name="PnL",
                line=dict(color="#00d4aa", width=2),
                fill="tozeroy",
                fillcolor="rgba(0, 212, 170, 0.1)",
            ))
            fig.update_layout(
                height=300, margin=dict(l=0, r=0, t=20, b=0),
                yaxis_title="PnL ($)", xaxis_title="",
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            empty_chart_msg("Нет закрытых сделок для графика")

    with col_right:
        st.subheader("🎯 Причины закрытия")
        if not closed.empty:
            reason_counts = closed["close_reason"].value_counts().reset_index()
            reason_counts.columns = ["reason", "count"]
            fig = px.pie(
                reason_counts, values="count", names="reason",
                color_discrete_sequence=px.colors.qualitative.Set3,
                hole=0.4,
            )
            fig.update_layout(
                height=300, margin=dict(l=0, r=0, t=20, b=0),
                showlegend=True,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            empty_chart_msg()

    # ── PnL по дням ───────────────────────────────────────────────────────────
    st.subheader("📅 PnL по дням")
    if not closed.empty:
        closed["date"] = closed["closed_at"].dt.date
        daily = closed.groupby("date")["total_pnl_usd"].sum().reset_index()
        daily.columns = ["date", "pnl"]
        fig = px.bar(
            daily, x="date", y="pnl",
            color="pnl",
            color_continuous_scale=["#ff4b4b", "#00d4aa"],
            color_continuous_midpoint=0,
        )
        fig.update_layout(
            height=250, margin=dict(l=0, r=0, t=10, b=0),
            yaxis_title="PnL ($)", xaxis_title="",
            showlegend=False,
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)

    # ── Открытые позиции ──────────────────────────────────────────────────────
    if not open_pos.empty:
        st.subheader(f"📂 Открытые позиции ({len(open_pos)})")
        display_cols = [
            "symbol", "long_exchange", "short_exchange",
            "size_usd", "entry_funding_diff_pct",
            "funding_received_usd", "total_pnl_usd", "opened_at",
        ]
        display_cols = [c for c in display_cols if c in open_pos.columns]
        st.dataframe(
            open_pos[display_cols].style.format({
                "size_usd": "${:.2f}",
                "entry_funding_diff_pct": "{:.4f}%",
                "funding_received_usd": "${:.4f}",
                "total_pnl_usd": "${:+.4f}",
            }),
            use_container_width=True,
        )

    # ── История сделок ────────────────────────────────────────────────────────
    st.subheader("📋 История сделок")
    if not closed.empty:
        show = closed.sort_values("closed_at", ascending=False).head(50)
        display_cols = [
            "symbol", "long_exchange", "short_exchange",
            "size_usd", "funding_received_usd", "fees_paid_usd",
            "total_pnl_usd", "close_reason", "closed_at",
        ]
        display_cols = [c for c in display_cols if c in show.columns]

        def color_pnl(val):
            color = "#00d4aa" if val > 0 else "#ff4b4b" if val < 0 else "gray"
            return f"color: {color}"

        styled = show[display_cols].style.applymap(
            color_pnl, subset=["total_pnl_usd"]
        ).format({
            "size_usd": "${:.2f}",
            "funding_received_usd": "${:.4f}",
            "fees_paid_usd": "${:.4f}",
            "total_pnl_usd": "${:+.4f}",
        })
        st.dataframe(styled, use_container_width=True)
    else:
        st.info("Нет закрытых сделок")


# ── Страница: Squeeze Bot ─────────────────────────────────────────────────────

def page_squeeze():
    st.header("⚡️ Funding Squeeze Bot")
    trades = load_squeeze_data()

    if trades.empty:
        st.warning("База данных squeeze бота не найдена или пуста.")
        return

    for col in ["opened_at", "closed_at"]:
        if col in trades.columns:
            trades[col] = pd.to_datetime(trades[col])

    closed = trades[trades["closed_at"].notna()].copy()
    open_t = trades[trades["closed_at"].isna()].copy()

    # ── Метрики ───────────────────────────────────────────────────────────────
    col1, col2, col3, col4, col5 = st.columns(5)
    total_pnl = closed["total_pnl_usd"].sum() if not closed.empty else 0
    win_rate = (closed["total_pnl_usd"] > 0).mean() * 100 if not closed.empty else 0
    avg_pnl = closed["total_pnl_usd"].mean() if not closed.empty else 0
    total_funding = closed["funding_received_usd"].sum() if not closed.empty else 0

    with col1:
        st.metric("💰 Общий PnL", f"${total_pnl:+.2f}")
    with col2:
        st.metric("🎯 Win Rate", f"{win_rate:.0f}%")
    with col3:
        st.metric("📊 Средний PnL", f"${avg_pnl:+.4f}")
    with col4:
        st.metric("🔄 Сделок", len(closed))
    with col5:
        st.metric("📂 Открытых", len(open_t))

    st.divider()

    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.subheader("📊 Equity Curve")
        if not closed.empty:
            s = closed.sort_values("closed_at").copy()
            s["cum_pnl"] = s["total_pnl_usd"].cumsum()
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=s["closed_at"], y=s["cum_pnl"],
                mode="lines+markers", name="PnL",
                line=dict(color="#ff9500", width=2),
                fill="tozeroy",
                fillcolor="rgba(255, 149, 0, 0.1)",
            ))
            fig.update_layout(
                height=300, margin=dict(l=0, r=0, t=20, b=0),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            empty_chart_msg()

    with col_right:
        st.subheader("🚦 TP vs SL vs Other")
        if not closed.empty:
            rc = closed["close_reason"].value_counts().reset_index()
            rc.columns = ["reason", "count"]
            color_map = {"tp": "#00d4aa", "sl": "#ff4b4b",
                         "timeout": "#ffa500", "funding_paid": "#7c7cff"}
            fig = px.bar(
                rc, x="reason", y="count",
                color="reason", color_discrete_map=color_map,
            )
            fig.update_layout(
                height=300, margin=dict(l=0, r=0, t=20, b=0),
                showlegend=False,
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            empty_chart_msg()

    # ── PnL по funding rate ───────────────────────────────────────────────────
    st.subheader("💡 PnL vs Funding Rate при входе")
    if not closed.empty:
        fig = px.scatter(
            closed, x="entry_funding_pct", y="total_pnl_usd",
            color="close_reason",
            color_discrete_map={"tp": "#00d4aa", "sl": "#ff4b4b",
                                "timeout": "#ffa500", "funding_paid": "#7c7cff"},
            hover_data=["symbol", "side"],
            labels={"entry_funding_pct": "Funding при входе (%)",
                    "total_pnl_usd": "PnL ($)"},
        )
        fig.add_hline(y=0, line_dash="dash", line_color="gray")
        fig.update_layout(
            height=300, margin=dict(l=0, r=0, t=10, b=0),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)

    # ── Таблица ───────────────────────────────────────────────────────────────
    st.subheader("📋 История сделок")
    if not closed.empty:
        show = closed.sort_values("closed_at", ascending=False).head(50)
        display_cols = [
            "symbol", "side", "entry_funding_pct",
            "funding_received_usd", "price_pnl_usd",
            "fees_usd", "total_pnl_usd", "close_reason", "closed_at",
        ]
        display_cols = [c for c in display_cols if c in show.columns]

        def color_pnl(val):
            color = "#00d4aa" if val > 0 else "#ff4b4b" if val < 0 else "gray"
            return f"color: {color}"

        styled = show[display_cols].style.applymap(
            color_pnl, subset=["total_pnl_usd", "price_pnl_usd"]
        ).format({
            "entry_funding_pct": "{:.4f}%",
            "funding_received_usd": "${:.4f}",
            "price_pnl_usd": "${:+.4f}",
            "fees_usd": "${:.4f}",
            "total_pnl_usd": "${:+.4f}",
        })
        st.dataframe(styled, use_container_width=True)


# ── Страница: Сводная ─────────────────────────────────────────────────────────

def page_overview():
    st.header("🏠 Общая сводка")

    positions, _ = load_arb_data()
    squeeze = load_squeeze_data()

    col1, col2, col3 = st.columns(3)

    arb_pnl = 0
    if not positions.empty:
        closed_arb = positions[positions["status"] == "closed"]
        arb_pnl = closed_arb["total_pnl_usd"].sum() if not closed_arb.empty else 0

    sqz_pnl = 0
    if not squeeze.empty:
        closed_sqz = squeeze[squeeze["closed_at"].notna()]
        sqz_pnl = closed_sqz["total_pnl_usd"].sum() if not closed_sqz.empty else 0

    total = arb_pnl + sqz_pnl

    with col1:
        st.metric("🔄 Arb Bot PnL", f"${arb_pnl:+.2f}")
    with col2:
        st.metric("⚡️ Squeeze Bot PnL", f"${sqz_pnl:+.2f}")
    with col3:
        st.metric("💰 Суммарный PnL", f"${total:+.2f}")

    # Совместный equity curve
    dfs = []
    if not positions.empty:
        closed_arb = positions[
            (positions["status"] == "closed") & positions["closed_at"].notna()
        ].copy()
        if not closed_arb.empty:
            closed_arb["closed_at"] = pd.to_datetime(closed_arb["closed_at"])
            closed_arb["bot"] = "Arb"
            dfs.append(closed_arb[["closed_at", "total_pnl_usd", "bot"]])

    if not squeeze.empty:
        closed_sqz = squeeze[squeeze["closed_at"].notna()].copy()
        if not closed_sqz.empty:
            closed_sqz["closed_at"] = pd.to_datetime(closed_sqz["closed_at"])
            closed_sqz["bot"] = "Squeeze"
            dfs.append(closed_sqz[["closed_at", "total_pnl_usd", "bot"]])

    if dfs:
        st.subheader("📊 Equity Curve — оба бота")
        combined = pd.concat(dfs).sort_values("closed_at")
        combined["cum_pnl"] = combined.groupby("bot")["total_pnl_usd"].cumsum()
        fig = px.line(
            combined, x="closed_at", y="cum_pnl", color="bot",
            color_discrete_map={"Arb": "#00d4aa", "Squeeze": "#ff9500"},
        )
        fig.update_layout(
            height=350, margin=dict(l=0, r=0, t=10, b=0),
            yaxis_title="PnL ($)", xaxis_title="",
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Запусти ботов в paper режиме чтобы увидеть статистику здесь")


# ── Навигация ─────────────────────────────────────────────────────────────────

st.title("💰 Funding Bot Dashboard")
st.caption(f"Данные обновляются каждые 30 секунд | {datetime.utcnow().strftime('%H:%M:%S')} UTC")

tab1, tab2, tab3 = st.tabs(["🏠 Сводка", "🔄 Arb Bot", "⚡️ Squeeze Bot"])

with tab1:
    page_overview()
with tab2:
    page_arb()
with tab3:
    page_squeeze()
