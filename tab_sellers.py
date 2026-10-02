
import streamlit as st
import json
import plotly.express as px
from datetime import timedelta

from utils import (
    load_tab_data,
    fetch_orders, parse_orders_sellers, compute_seller_stats, df_to_csv_bytes,
)
from i18n import t


def render_sellers(*, base_url, username, password, selected_store,
                   selected_date, amount_type="Montant Net", lang="fr"):
    """Onglet Vendeurs - stats de vente par vendeur.

    Regle de rattachement (metier) : le vendeur d'une vente = le sales
    representative de l'order s'il existe, sinon le 'created by' (caissier).
    """
    period = st.radio(t("period", lang) + " :",
                      [t("day", lang), t("yesterday", lang)],
                      horizontal=True, key="period_sellers")
    if period == t("yesterday", lang):
        selected_date = selected_date - timedelta(days=1)

    st.subheader("\U0001f9d1\u200d\U0001f4bc " + t("sellers_analysis", lang))
    st.caption(t("sellers_rule_hint", lang))
    date_str = selected_date.strftime("%Y-%m-%d")

    cache_key = f"sellers_{selected_store}_{date_str}"
    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        force = st.button("\U0001f504 " + t("refresh", lang), type="secondary",
                          key="btn_refresh_sellers")

    raw = load_tab_data(
        cache_key,
        lambda: fetch_orders(base_url, username, password, selected_store, date_str),
        t("sellers_analysis", lang), force=force)

    if not raw:
        st.warning(t("sellers_no_data", lang).format(
            d=selected_date.strftime("%d/%m/%Y"), s=selected_store))
        return

    df_s = parse_orders_sellers(json.dumps(raw))
    if df_s.empty:
        st.warning(t("sellers_no_data", lang).format(
            d=selected_date.strftime("%d/%m/%Y"), s=selected_store))
        return

    df_stats = compute_seller_stats(df_s, amount_type)

    # ── KPIs globaux ──
    nb_vendeurs = len(df_stats)
    ca_total = float(df_stats["CA"].sum())
    nb_tickets = int(df_stats["Nb Tickets"].sum())
    # Part des ventes rattachees via le representant vs le caissier
    nb_rep = int((df_s["Source"] == "representant").sum())
    nb_cash = int((df_s["Source"] == "caissier").sum())
    pct_rep = (nb_rep / len(df_s) * 100) if len(df_s) else 0.0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("\U0001f9d1\u200d\U0001f4bc " + t("sellers_count", lang), nb_vendeurs)
    k2.metric("\U0001f4b0 " + t("sellers_ca_total", lang),
              f"{ca_total:,.2f} \u20ac".replace(",", " "))
    k3.metric("\U0001f9fe " + t("sellers_tickets", lang),
              f"{nb_tickets:,}".replace(",", " "))
    k4.metric("\U0001f464 " + t("sellers_pct_rep", lang), f"{pct_rep:.0f} %",
              help=t("sellers_pct_rep_help", lang))
    st.divider()

    # ── Graphique : CA par vendeur (top 15) ──
    st.subheader("\U0001f4ca " + t("sellers_ca_by", lang))
    top = df_stats.head(15)
    fig = px.bar(top, x="Vendeur", y="CA", text="CA",
                 color="CA", color_continuous_scale="Blues")
    fig.update_traces(texttemplate="%{text:.0f}", textposition="outside")
    fig.update_layout(yaxis_title="\u20ac", xaxis_title="",
                      plot_bgcolor="rgba(0,0,0,0)", coloraxis_showscale=False,
                      margin=dict(t=10))
    st.plotly_chart(fig, use_container_width=True)
    st.divider()

    # ── Tableau detaille ──
    st.subheader("\U0001f4cb " + t("sellers_detail", lang))
    show = df_stats.copy()
    # Traduire la source
    _src_lbl = {"representant": t("sellers_src_rep", lang),
                "caissier": t("sellers_src_cashier", lang),
                "inconnu": t("sellers_src_unknown", lang), "": ""}
    show["Source"] = show["Source"].map(lambda s: _src_lbl.get(s, s))
    show.columns = [t("sellers_col_name", lang), t("sellers_col_source", lang),
                    t("sellers_col_tickets", lang), t("sellers_col_sales", lang),
                    t("sellers_col_returns", lang), t("sellers_col_ca", lang),
                    t("sellers_col_avg_basket", lang), t("sellers_col_items", lang),
                    t("sellers_col_items_per_ticket", lang)]
    # Formatage monetaire
    for c in [t("sellers_col_ca", lang), t("sellers_col_avg_basket", lang)]:
        show[c] = show[c].apply(lambda x: f"{x:,.2f}".replace(",", " "))
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.download_button("\U0001f4e5 " + t("sellers_dl", lang),
                       df_to_csv_bytes(df_stats),
                       f"Vendeurs_{selected_store}_{date_str}.csv", "text/csv",
                       key="csv_sellers")
