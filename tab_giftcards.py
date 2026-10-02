
import streamlit as st
import json
import pandas as pd

from utils import (
    load_tab_data,
    fetch_gift_cards, fetch_gift_card_transactions,
    parse_gift_cards, parse_gift_card_transactions,
    compute_giftcard_stats,
    generate_giftcard_flow_chart, generate_giftcard_usage_chart,
    df_to_csv_bytes,
)
from i18n import t


def render_giftcards(*, base_url, username, password, selected_date, lang="fr"):
    """Onglet 3 - Cartes cadeaux. Analyse de performance (encours, flux, dormantes)."""
    from datetime import timedelta

    # Switch rapide Veille / Jour
    period = st.radio(t("period", lang) + " :",
                      [t("day", lang), t("yesterday", lang)],
                      horizontal=True, key="period_gc")
    if period == t("yesterday", lang):
        selected_date = selected_date - timedelta(days=1)

    st.subheader("\U0001f381 " + t("gc_analysis", lang))
    date_str = selected_date.strftime("%Y-%m-%d")

    cache_key = f"giftcards_{date_str}"
    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        force = st.button("\U0001f504 " + t("refresh", lang), type="secondary", key="btn_refresh_gc")

    def _load():
        return {"gc": fetch_gift_cards(base_url, username, password),
                "txn": fetch_gift_card_transactions(base_url, username, password)}
    cached = load_tab_data(cache_key, _load, t("gc_analysis", lang), force=force,
                           db_keys=["gift_cards", "gift_card_transactions"])
    raw_gc, raw_txn = cached["gc"], cached["txn"]

    df_gc = parse_gift_cards(json.dumps(raw_gc))
    df_txn = parse_gift_card_transactions(json.dumps(raw_txn))

    if df_gc.empty:
        st.warning(t("gc_no_data", lang))
        return

    stats = compute_giftcard_stats(df_gc, date_str)

    # KPIs
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("\U0001f4b3 " + t("gc_active", lang), f"{stats['nb_actives']:,}".replace(",", " "))
    k2.metric("\U0001f4b0 " + t("gc_outstanding", lang),
              f"{stats['encours']:,.2f} \u20ac".replace(",", " "))
    k3.metric("\U0001f195 " + t("gc_issued_today", lang), stats["emises_jour"])
    k4.metric("\U0001f4ca " + t("gc_usage_rate", lang), f"{stats['taux_util_moyen']:.1%}")

    d1, d2 = st.columns(2)
    d1.metric("\U0001f634 " + t("gc_dormant", lang), stats["nb_dormantes"],
              f"{stats['encours_dormant']:,.2f} \u20ac".replace(",", " "))
    d2.metric("\u23f3 " + t("gc_expiring", lang), stats["nb_expirent_bientot"])
    st.divider()

    # Flux emissions vs consommations
    st.subheader("\U0001f4c8 " + t("gc_flow_title", lang))
    with st.spinner("\U0001f4c8 " + t("gc_flow_title", lang) + "\u2026"):
        fig_flow = generate_giftcard_flow_chart(df_txn, selected_date, lang)
    if fig_flow is not None:
        st.plotly_chart(fig_flow, use_container_width=True)
    else:
        st.info(t("gc_no_data", lang))
    st.divider()

    # Repartition par taux d'utilisation
    st.subheader("\U0001f4ca " + t("gc_usage_title", lang))
    with st.spinner("\U0001f4ca " + t("gc_usage_title", lang) + "\u2026"):
        fig_usage = generate_giftcard_usage_chart(df_gc, lang)
    if fig_usage is not None:
        st.plotly_chart(fig_usage, use_container_width=True)
    st.divider()

    # Cartes dormantes - potentiel de relance
    st.subheader("\U0001f634 " + t("gc_dormant_title", lang))
    st.caption(t("gc_dormant_hint", lang))
    ref = pd.to_datetime(date_str).normalize()
    seuil = ref - pd.Timedelta(days=90)
    valides = df_gc[(df_gc["active"]) & (~df_gc["cancelled"])]
    dormantes = valides[(valides["solde"] > 0) &
                        (valides["date_ordered"].notna()) &
                        (valides["date_ordered"] < seuil)].copy()
    if dormantes.empty:
        st.success(t("gc_no_dormant", lang))
    else:
        dormantes = dormantes.sort_values("solde", ascending=False)
        show = dormantes[["searchKey", "montant_initial", "solde",
                          "date_ordered", "date_expiration"]].copy()
        show.columns = [t("col_card_key", lang), t("col_initial", lang),
                        t("col_balance", lang), t("col_ordered", lang),
                        t("col_expiration", lang)]
        for c in [t("col_initial", lang), t("col_balance", lang)]:
            show[c] = show[c].apply(lambda x: f"{x:,.2f}".replace(",", " "))
        st.dataframe(show, use_container_width=True, hide_index=True)
        st.download_button("\U0001f4e5 " + t("dl_dormant_cards", lang),
                           df_to_csv_bytes(show),
                           f"CartesDormantes_{date_str}.csv", "text/csv",
                           key="csv_dormant_gc")
