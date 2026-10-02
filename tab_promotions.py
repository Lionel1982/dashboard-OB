
import streamlit as st
import json

from utils import (
    NOT_LOADED,
    load_tab_data,
    fetch_orders, parse_daily_promotions, compute_promotions_summary,
    generate_promotions_chart, df_to_csv_bytes,
)
from i18n import t


def render_promotions(*, base_url, username, password, selected_store,
                      selected_date, lang="fr"):
    """Onglet Promotions - promotions appliquees sur les ventes du jour."""
    from datetime import timedelta

    period = st.radio(t("period", lang) + " :",
                      [t("day", lang), t("yesterday", lang)],
                      horizontal=True, key="period_promo")
    if period == t("yesterday", lang):
        selected_date = selected_date - timedelta(days=1)

    st.subheader("\U0001f3f7\ufe0f " + t("promo_analysis", lang))
    date_str = selected_date.strftime("%Y-%m-%d")
    st.caption(t("promo_hint", lang).format(s=selected_store, d=selected_date.strftime("%d/%m/%Y")))

    cache_key = f"orders|{selected_store}|{date_str}"
    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        force = st.button("\U0001f504 " + t("refresh", lang), type="secondary", key="btn_refresh_promo")

    raw = load_tab_data(cache_key, lambda: fetch_orders(base_url, username, password, selected_store, date_str), t("promo_analysis", lang), force=force, widget_suffix="promo")
    if raw is NOT_LOADED:
        return

    df_promo = parse_daily_promotions(json.dumps(raw))
    summ = compute_promotions_summary(df_promo)

    if df_promo.empty:
        st.info(t("promo_no_data", lang))
        return

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("\U0001f3f7\ufe0f " + t("promo_nb_applied", lang), summ["nb_applications"])
    k2.metric("\U0001f4b0 " + t("promo_total_discount", lang),
              f"{summ['remise_totale']:,.2f} \u20ac".replace(",", " "))
    k3.metric("\U0001f522 " + t("promo_distinct", lang), summ["nb_promos"])
    k4.metric("\U0001f9fe " + t("promo_tickets", lang), summ["nb_tickets"])
    st.divider()

    with st.spinner("\U0001f4ca " + t("promo_chart_title", lang) + "\u2026"):
        fig = generate_promotions_chart(summ["df_by_promo"], lang)
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)
    st.divider()

    df_show = summ["df_by_promo"].copy()
    df_show.columns = [t("col_promo_name", lang), t("promo_nb_applied", lang),
                       t("col_discount", lang), t("promo_tickets", lang)]
    df_show[t("col_discount", lang)] = df_show[t("col_discount", lang)].apply(
        lambda x: f"{x:,.2f}".replace(",", " "))
    st.dataframe(df_show, use_container_width=True, hide_index=True)
    st.download_button("\U0001f4e5 " + t("col_promo_name", lang) + " CSV",
                       df_to_csv_bytes(df_show),
                       f"Promotions_{date_str}.csv", "text/csv", key="csv_promo")
