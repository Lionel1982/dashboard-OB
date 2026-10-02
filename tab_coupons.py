
import streamlit as st
import json

from utils import (
    load_tab_data,
    fetch_coupons, parse_coupons, compute_coupons_stats,
    compute_coupons_by_promo, df_to_csv_bytes,
)
from i18n import t


def render_coupons(*, base_url, username, password, selected_date, lang="fr"):
    """Onglet Coupons - vue globale des coupons actifs."""
    from datetime import timedelta

    period = st.radio(t("period", lang) + " :",
                      [t("day", lang), t("yesterday", lang)],
                      horizontal=True, key="period_coupon")
    if period == t("yesterday", lang):
        selected_date = selected_date - timedelta(days=1)

    st.subheader("\U0001f39f\ufe0f " + t("coupon_analysis", lang))
    date_str = selected_date.strftime("%Y-%m-%d")

    cache_key = f"coupons_{date_str}"
    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        force = st.button("\U0001f504 " + t("refresh", lang), type="secondary", key="btn_refresh_coupon")

    raw = load_tab_data(cache_key, lambda: fetch_coupons(base_url, username, password),
                        t("coupon_analysis", lang), force=force, db_keys=["coupons"])

    df_c = parse_coupons(json.dumps(raw))
    if df_c.empty:
        st.warning(t("coupon_no_data", lang))
        return

    stats = compute_coupons_stats(df_c, date_str)

    # ── KPIs globaux (parc complet, aligne back-office) ──
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("\U0001f39f\ufe0f " + t("coupon_total", lang),
              f"{stats['nb_total']:,}".replace(",", " "))
    k2.metric("\u2705 " + t("coupon_used", lang),
              f"{stats['nb_utilises']:,}".replace(",", " "),
              f"{stats['taux_util']:.1%}")
    k3.metric("\u2b1c " + t("coupon_unused", lang),
              f"{stats['nb_non_utilises']:,}".replace(",", " "))
    k4.metric("\U0001f195 " + t("coupon_created_today", lang),
              f"{stats['nb_crees_jour']:,}".replace(",", " "))
    k5, k6, k7, k8 = st.columns(4)
    k5.metric("\U0001f534 " + t("coupon_fully_used", lang),
              f"{stats['nb_entiers']:,}".replace(",", " "))
    k6.metric("\U0001f7e1 " + t("coupon_partially_used", lang),
              f"{stats['nb_partiels']:,}".replace(",", " "))
    k7.metric("\U0001f4b0 " + t("coupon_outstanding", lang),
              f"{stats['encours']:,.2f} \u20ac".replace(",", " "))
    k8.metric("\u23f3 " + t("coupon_expiring", lang), stats["nb_expirent_bientot"])
    st.caption(t("coupon_daily_note", lang))
    st.divider()

    # ── Ventilation par type de promotion / discount ──
    st.subheader("\U0001f3f7\ufe0f " + t("coupon_by_promo", lang))
    df_promo = compute_coupons_by_promo(df_c)
    if not df_promo.empty:
        show_p = df_promo.copy()
        show_p["taux_util"] = show_p["taux_util"].apply(lambda x: f"{x:.1%}")
        for c in ["montant_total", "encours"]:
            show_p[c] = show_p[c].apply(lambda x: f"{x:,.2f}".replace(",", " "))
        show_p.columns = [t("col_promo_type", lang), t("coupon_total", lang),
                          t("coupon_used", lang), t("coupon_unused", lang),
                          t("coupon_fully_used", lang), t("coupon_partially_used", lang),
                          t("coupon_usage_rate", lang), t("col_discount", lang),
                          t("coupon_outstanding", lang)]
        st.dataframe(show_p, use_container_width=True, hide_index=True)
        st.download_button("\U0001f4e5 " + t("coupon_by_promo", lang) + " CSV",
                           df_to_csv_bytes(df_promo),
                           f"Coupons_par_promo_{date_str}.csv", "text/csv",
                           key="csv_coupons_promo")
    st.divider()

    st.subheader("\U0001f4cb " + t("coupon_list", lang))
    # Filtre d'affichage : tous / utilises / non utilises
    filtre = st.radio(t("coupon_filter", lang) + " :",
                      [t("coupon_filter_all", lang), t("coupon_used", lang),
                       t("coupon_unused", lang)],
                      horizontal=True, key="coupon_filter_radio")
    view = df_c.copy()
    if filtre == t("coupon_used", lang):
        view = view[view["used"]]
    elif filtre == t("coupon_unused", lang):
        view = view[~view["used"]]
    view = view.sort_values("solde", ascending=False)
    show = view[["code", "type_promo", "montant", "solde", "niveau_utilisation",
                 "statut", "valid_to"]].copy()
    show.columns = [t("col_coupon_code", lang), t("col_promo_type", lang),
                    t("col_discount", lang), t("col_balance", lang),
                    t("col_usage_level", lang), t("col_status", lang),
                    t("col_valid_to", lang)]
    for c in [t("col_discount", lang), t("col_balance", lang)]:
        show[c] = show[c].apply(lambda x: f"{x:,.2f}".replace(",", " "))
    st.caption(f"{len(show):,}".replace(",", " ") + " " + t("coupon_shown", lang))
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.download_button("\U0001f4e5 " + t("dl_coupons", lang), df_to_csv_bytes(show),
                       f"Coupons_{date_str}.csv", "text/csv", key="csv_coupons")
