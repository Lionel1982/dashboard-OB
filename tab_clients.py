
import streamlit as st
import json
import plotly.express as px

from utils import (
    NOT_LOADED,
    load_tab_data,
    fetch_orders, parse_orders, compute_order_stats,
    fetch_all_business_partners, parse_business_partners,
    fetch_subscriptions_by_date_range, parse_subscriptions,
    build_daily_enrollment_report,
    generate_client_map, df_to_csv_bytes,
)
from i18n import t


def render_clients(*, base_url, username, password, client_name,
                   selected_store, selected_date, amount_type, lang="fr"):
    """Onglet 2 — Clients & Fidélité. Chargement automatique."""
    from datetime import timedelta

    # Switch rapide Veille / Jour : bascule la date de toute la page.
    period = st.radio(t("period", lang) + " :",
                      [t("day", lang), t("yesterday", lang)],
                      horizontal=True, key="period_cli")
    if period == t("yesterday", lang):
        selected_date = selected_date - timedelta(days=1)

    st.subheader("👥 " + t("tabs_clients", lang))
    st.markdown(f"*{t('client_api', lang)} : {client_name}* — **{selected_store}**")

    date_str = selected_date.strftime("%Y-%m-%d")
    range_start_str = (selected_date - timedelta(days=6)).strftime("%Y-%m-%d")

    # ── Chargement automatique (+ bouton refresh) ──
    cache_key = f"clients_{date_str}"
    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        force = st.button("🔄 " + t("refresh", lang), type="secondary", key="btn_refresh_clients")

    def _load():
        return {
            "bps": fetch_all_business_partners(base_url, username, password),
            "today_subs": fetch_subscriptions_by_date_range(
                base_url, username, password, date_str, date_str),
            "range_subs": fetch_subscriptions_by_date_range(
                base_url, username, password, range_start_str, date_str),
            "orders": fetch_orders(base_url, username, password, selected_store, date_str),
        }
    cached = load_tab_data(cache_key, _load, t("spinner_clients", lang), force=force,
                           db_keys=["business_partners"], prod_confirm=True)
    if cached is NOT_LOADED:
        return
    raw_all_bps = cached["bps"]
    raw_today_subs = cached["today_subs"]
    raw_range_subs = cached["range_subs"]
    raw_orders_bp = cached["orders"]

    if not raw_all_bps:
        st.warning(t("no_client", lang))
        return

    # ── Parse des données ──
    bp_data = parse_business_partners(json.dumps(raw_all_bps), date_str)
    df_bp = bp_data["df_bp"]
    df_locations = bp_data["df_locations"]
    total_bp = bp_data["total_bp"]
    total_enrolled = bp_data["total_enrolled"]
    enrollment_rate = bp_data["enrollment_rate"]
    available_orgs = bp_data.get("available_orgs", [])

    # Souscriptions 7 jours (sert la progression + les vues programmes/détail)
    df_range_subs = parse_subscriptions(json.dumps(raw_range_subs))
    # Souscriptions du jour : byCreationDateRange(J, J) renvoie DEJA uniquement
    # les creations du jour. Aucun filtre Python supplementaire (l'ancien filtre
    # sur startingDate etait redondant ET casse : startingDate racine n'existe
    # pas dans ce payload, la vraie date est creationDate).
    df_today_subs = parse_subscriptions(json.dumps(raw_today_subs))
    today_new = (len(df_today_subs.drop_duplicates(subset="sub_id"))
                 if not df_today_subs.empty else 0)

    df_orders_bp = parse_orders(json.dumps(raw_orders_bp))
    stats = compute_order_stats(df_orders_bp, amount_type)

    if available_orgs:
        with st.expander("ℹ️ Organisations disponibles dans la base", expanded=False):
            st.write(available_orgs)

    if df_bp.empty:
        st.warning(t("no_client", lang))
        return

    # ══════════════════════════════════════
    # LIGNE 1 : Total clients / Taux / Encartés
    # ══════════════════════════════════════
    k1, k2, k3 = st.columns(3)
    k1.metric("👥 " + t("total_clients", lang), f"{total_bp:,}".replace(",", " "))
    k2.metric("🎯 " + t("enrollment_rate", lang), f"{enrollment_rate:.1%}")
    k3.metric("💳 " + t("enrolled", lang), f"{total_enrolled:,}".replace(",", " "),
              delta_color="inverse")
    d1, d2 = st.columns(2)
    d1.metric(
        f"🛒 {t('clients_today', lang)} ({selected_store})",
        f"{stats['identified_orders']}",
        f"/ {stats['total_orders']} tickets ({stats['anon_orders']} anon.)",
    )
    d2.metric("🆕 " + t("enrollments_today", lang), today_new)
    st.divider()


    # ══════════════════════════════════════
    # DÉTAIL ENCARTEMENTS / NON-ENCARTÉS
    # ══════════════════════════════════════
    st.header(f"🔎 {t('day_detail', lang)} — {selected_date.strftime('%d/%m/%Y')}")
    st.caption(t("source_subscription", lang))

    enrollment_report = build_daily_enrollment_report(
        df_orders_bp, bp_data, df_today_subs, selected_store)
    df_enrolled = enrollment_report["df_enrolled_today"]
    df_not_enrolled = enrollment_report["df_not_enrolled_today"]
    nb_e = enrollment_report["nb_enrolled"]
    nb_ne = enrollment_report["nb_not_enrolled"]
    nb_known_nl = enrollment_report["nb_known_no_loyalty"]
    df_known_nl = enrollment_report["df_known_no_loyalty"]

    me1, me2 = st.columns(2)
    me1.metric("✅ Encartements du jour", nb_e)
    me2.metric("🎯 Clients connus SANS fidélité",
               nb_known_nl,
               help="Clients identifiés (non anonymes) ayant acheté "
                    "aujourd'hui sans programme de fidélité rattaché à leur "
                    "vente. Potentiel d'encartement à forcer.")

    # ── Tableau encartements ──
    st.subheader("✅ " + t("enrollments_today", lang))
    if df_enrolled.empty:
        st.info(t("no_enrollment", lang))
    else:
        df_e_show = df_enrolled.copy()
        cols_e = {
            "BP_Nom": t("col_client", lang), "memberid": t("col_member_no", lang),
            "programme": t("col_program", lang),
            "catégorie": t("col_category", lang), "carte": t("col_card_no", lang),
            "caisse_vente": t("col_sale_registers", lang),
            "nb_tickets": t("col_nb_tickets", lang), "ca_brut": t("col_ca_gross", lang),
            "ca_net": t("col_ca_net", lang), "nb_articles": t("col_nb_items", lang),
            "tickets_vente": t("col_ticket_nos", lang), "paiements": t("col_payments", lang),
        }
        cols_ok = [c for c in cols_e if c in df_e_show.columns]
        df_e_show = df_e_show[cols_ok].rename(columns={c: cols_e[c] for c in cols_ok})
        for col in [t("col_ca_gross", lang), t("col_ca_net", lang)]:
            if col in df_e_show.columns:
                df_e_show[col] = df_e_show[col].apply(
                    lambda x: f"{x:,.2f}".replace(",", " "))
        st.dataframe(df_e_show, use_container_width=True, hide_index=True)
        st.download_button(
            "📥 " + t("dl_enrollments", lang), df_to_csv_bytes(df_e_show),
            f"Encartements_{date_str}.csv", "text/csv",
            key="csv_enrolled_today")
    st.divider()

    # ── Tableau : clients CONNUS SANS fidélité (potentiel d'encartement) ──
    st.subheader("🎯 " + t("known_no_loyalty", lang))
    st.caption(t("known_no_loyalty_hint", lang).format(s=selected_store))
    if df_known_nl.empty:
        st.success("👍 " + t("all_known_have_loyalty", lang))
    else:
        df_knl_show = df_known_nl.copy()
        cols_knl = {
            "BP_Nom": t("col_client", lang),
            "caisses": t("col_cashdesk", lang),
            "nb_tickets": t("col_nb_tickets", lang), "ca_brut": t("col_ca_gross", lang),
            "ca_net": t("col_ca_net", lang),
        }
        cols_ok_knl = [c for c in cols_knl if c in df_knl_show.columns]
        df_knl_show = df_knl_show[cols_ok_knl].rename(
            columns={c: cols_knl[c] for c in cols_ok_knl})
        # Retours (CA Brut négatif) mis en rouge sur toute la ligne.
        _col_gross = t("col_ca_gross", lang)
        _col_net = t("col_ca_net", lang)
        def _highlight_retours(row):
            val = row.get(_col_gross, 0)
            is_retour = isinstance(val, (int, float)) and val < 0
            color = "color:#B02536; font-weight:600;" if is_retour else ""
            return [color] * len(row)
        styler = (df_knl_show.style
                  .apply(_highlight_retours, axis=1)
                  .format({_col_gross: lambda x: f"{x:,.2f}".replace(",", " "),
                           _col_net: lambda x: f"{x:,.2f}".replace(",", " ")}))
        st.dataframe(styler, use_container_width=True, hide_index=True)
        ca_potentiel = df_known_nl["ca_brut"].sum()
        st.warning("💡 " + t("potential_msg", lang).format(
            n=nb_known_nl, ca=f"{ca_potentiel:,.2f}".replace(",", " ")))
        st.download_button(
            "📥 " + t("dl_to_enroll", lang), df_to_csv_bytes(df_knl_show),
            f"AEncarter_{date_str}.csv", "text/csv",
            key="csv_known_no_loyalty")
    st.divider()

    # ══════════════════════════════════════
    # PROGRESSION 7 JOURS
    # ══════════════════════════════════════
    # ══════════════════════════════════════
    # CARTE GÉOGRAPHIQUE
    # ══════════════════════════════════════
    st.subheader("🗺️ " + t("geo_coverage", lang))
    with st.spinner("🗺️ Génération de la carte…"):
        fig_map = generate_client_map(df_locations)
    if fig_map:
        st.plotly_chart(fig_map, use_container_width=True)
        df_geo = df_locations.dropna(subset=["lat", "lon"]).copy()
        if not df_geo.empty:
            df_ds = (
                df_geo.groupby(["region_code", "region_name", "country_code"])
                .agg(nb_clients=("bp_id", "nunique"),
                     nb_encartés=("has_loyalty", "sum"))
                .reset_index())
            df_ds["taux"] = (
                df_ds["nb_encartés"] / df_ds["nb_clients"] * 100).round(1)
            df_ds = df_ds.sort_values(
                "nb_clients", ascending=False).reset_index(drop=True)
            df_ds.columns = [
                "Code", "Région/District", "Pays",
                "Clients", "Encartés", "Taux (%)"]
            with st.expander("📊 " + t("exp_region_detail", lang), expanded=False):
                st.dataframe(df_ds, use_container_width=True, hide_index=True)
                st.download_button(
                    "📥 CSV", df_to_csv_bytes(df_ds),
                    "Regions.csv", "text/csv", key="csv_reg")
    else:
        st.info(t("no_location", lang))
    st.divider()

    # ══════════════════════════════════════
    # DONUT + PROGRAMMES
    # ══════════════════════════════════════
    cd, cp = st.columns(2)
    with cd:
        st.subheader("🍩 " + t("enrolled_vs_not", lang))
        with st.spinner("🍩 Génération du graphique…"):
            fig_donut = px.pie(
                names=["Encartés", "Non encartés"],
                values=[total_enrolled, total_bp - total_enrolled],
                hole=0.5, color_discrete_sequence=["#2ca02c", "#d62728"])
            fig_donut.update_traces(textinfo="percent+value")
            fig_donut.update_layout(margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig_donut, use_container_width=True)
    with cp:
        st.subheader("🏷️ " + t("loyalty_programs", lang))
        st.caption(t("last_7_days", lang))
        if not df_range_subs.empty and "programme" in df_range_subs.columns:
            with st.spinner("🏷️ Génération du graphique…"):
                df_pr = (
                    df_range_subs.groupby("programme")["bp_id"].nunique()
                    .reset_index()
                    .rename(columns={"bp_id": "Clients"})
                    .sort_values("Clients", ascending=False))
                fig_pr = px.bar(
                    df_pr, x="programme", y="Clients", text_auto=True,
                    color="Clients", color_continuous_scale="Blues")
                fig_pr.update_layout(
                    xaxis_title="Programme", plot_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig_pr, use_container_width=True)
        else:
            st.info(t("no_program", lang))
    st.divider()

    # ══════════════════════════════════════
    # LISTE CLIENTS + SOUSCRIPTIONS
    # ══════════════════════════════════════
    with st.expander("📋 Liste complète des clients", expanded=False):
        df_show = df_bp[~df_bp["anonyme"]].copy()
        df_show = df_show[["id", "nom", "organisation", "client",
                           "fidélité", "nb_programmes", "ville", "code_postal"]]
        df_show.columns = ["ID", "Nom", "Organisation", "Client",
                           "Fidélité", "Nb Prog", "Ville", "CP"]
        df_show["Fidélité"] = df_show["Fidélité"].map(
            {True: "✅ Oui", False: "❌ Non"})
        df_show["Client"] = df_show["Client"].map(
            {True: "Oui", False: "Non"})
        st.dataframe(df_show, use_container_width=True, hide_index=True)
        st.download_button(
            "📥 " + t("dl_export_clients", lang), df_to_csv_bytes(df_show),
            f"Clients_{date_str}.csv", "text/csv", key="csv_clients")

    if not df_range_subs.empty:
        with st.expander("📋 " + t("exp_subs_detail", lang),
                         expanded=False):
            df_sub_show = df_range_subs[[
                "bp_id", "organization", "programme", "catégorie",
                "points", "statut", "carte", "store", "terminal",
                "starting_date"]].copy()
            df_sub_show.columns = [
                "ID BP", "Organisation", "Programme", "Catégorie",
                "Points", "Statut", "N° Carte", "Magasin", "Caisse",
                "Date début"]
            st.dataframe(
                df_sub_show, use_container_width=True, hide_index=True)
            st.download_button(
                "📥 " + t("dl_export_subs", lang),
                df_to_csv_bytes(df_sub_show),
                f"Souscriptions_{date_str}.csv",
                "text/csv", key="csv_subs")

