
import streamlit as st

from utils import (
    NOT_LOADED,
    load_tab_data,
    build_trend_series, generate_trend_chart, generate_dow_chart,
)
from i18n import t


def render_trends(*, base_url, username, password, selected_store,
                  selected_date, amount_type, lang="fr"):
    """Onglet Tendances - evolution CA sur 14 jours + CA par jour de semaine."""
    st.subheader("\U0001f4c8 " + t("trend_analysis", lang))

    # Colonne montant : Montant Brut / Net -> ca_brut / ca_net
    amount_col = "ca_net" if amount_type == "Montant Net" else "ca_brut"

    cache_key = f"trend_{selected_store}_{selected_date.strftime('%Y-%m-%d')}"
    df_trend = load_tab_data(
        cache_key,
        lambda: build_trend_series(base_url, username, password,
                                   selected_store, selected_date, n_days=14),
        t("trend_loading", lang))
    if df_trend is NOT_LOADED:
        return

    if df_trend.empty or df_trend[amount_col].sum() == 0:
        st.info(t("prod_no_data", lang))
        return

    total = float(df_trend[amount_col].sum())
    moy = total / len(df_trend) if len(df_trend) else 0
    best = df_trend.loc[df_trend[amount_col].idxmax()]

    k1, k2, k3 = st.columns(3)
    k1.metric("\U0001f4b0 " + t("trend_total", lang),
              f"{total:,.2f} \u20ac".replace(",", " "))
    k2.metric("\U0001f4ca " + t("trend_avg_day", lang),
              f"{moy:,.2f} \u20ac".replace(",", " "))
    k3.metric("\U0001f3c6 " + t("trend_best_day", lang),
              f"{best['label']}",
              f"{best[amount_col]:,.2f} \u20ac".replace(",", " "))
    st.divider()

    with st.spinner("\U0001f4ca " + t("trend_chart_title", lang) + "\u2026"):
        fig_trend = generate_trend_chart(df_trend, amount_col, lang)
    if fig_trend is not None:
        st.plotly_chart(fig_trend, use_container_width=True)
    st.divider()

    with st.spinner("\U0001f4ca " + t("dow_chart_title", lang) + "\u2026"):
        fig_dow = generate_dow_chart(df_trend, amount_col, lang)
    if fig_dow is not None:
        st.plotly_chart(fig_dow, use_container_width=True)
