
import streamlit as st
import json

from utils import (
    NOT_LOADED,
    load_tab_data,
    fetch_orders, parse_daily_products, compute_products_summary,
    generate_top_products_chart, generate_category_chart, df_to_csv_bytes,
)
from i18n import t


def render_products(*, base_url, username, password, selected_store,
                    selected_date, lang="fr"):
    """Onglet Produits - performance produits du jour (top, categories)."""
    from datetime import timedelta

    period = st.radio(t("period", lang) + " :",
                      [t("day", lang), t("yesterday", lang)],
                      horizontal=True, key="period_prod")
    if period == t("yesterday", lang):
        selected_date = selected_date - timedelta(days=1)

    st.subheader("\U0001f4e6 " + t("prod_analysis", lang))
    date_str = selected_date.strftime("%Y-%m-%d")

    cache_key = f"orders|{selected_store}|{date_str}"
    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        force = st.button("\U0001f504 " + t("refresh", lang), type="secondary", key="btn_refresh_prod")

    raw = load_tab_data(cache_key, lambda: fetch_orders(base_url, username, password, selected_store, date_str), t("prod_analysis", lang), force=force, widget_suffix="prod")
    if raw is NOT_LOADED:
        return

    df_prod = parse_daily_products(json.dumps(raw))
    if df_prod.empty:
        st.info(t("prod_no_data", lang))
        return
    summ = compute_products_summary(df_prod)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("\U0001f4e6 " + t("prod_nb_products", lang), summ["nb_produits"])
    k2.metric("\U0001f522 " + t("prod_qty_total", lang), f"{summ['qte_totale']:.0f}")
    k3.metric("\U0001f4b0 " + t("prod_ca_total", lang),
              f"{summ['ca_total']:,.2f} \u20ac".replace(",", " "))
    k4.metric("\U0001f9fe " + t("prod_nb_lines", lang), summ["nb_lignes"])
    st.divider()

    col1, col2 = st.columns([3, 2])
    with col1:
        with st.spinner("\U0001f4ca " + t("prod_top_title", lang) + "\u2026"):
            fig_top = generate_top_products_chart(summ["df_by_product"], lang)
        if fig_top is not None:
            st.plotly_chart(fig_top, use_container_width=True)
    with col2:
        with st.spinner("\U0001f4ca " + t("prod_cat_title", lang) + "\u2026"):
            fig_cat = generate_category_chart(summ["df_by_category"], lang)
        if fig_cat is not None:
            st.plotly_chart(fig_cat, use_container_width=True)
    st.divider()

    df_show = summ["df_by_product"].copy()
    df_show.columns = [t("col_product", lang), t("prod_qty", lang),
                       t("prod_ca", lang), t("promo_tickets", lang)]
    df_show[t("prod_ca", lang)] = df_show[t("prod_ca", lang)].apply(
        lambda x: f"{x:,.2f}".replace(",", " "))
    st.dataframe(df_show, use_container_width=True, hide_index=True)
    st.download_button("\U0001f4e5 " + t("col_product", lang) + " CSV",
                       df_to_csv_bytes(df_show),
                       f"Produits_{date_str}.csv", "text/csv", key="csv_prod")
