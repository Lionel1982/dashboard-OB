
import streamlit as st
import json
import pandas as pd
import plotly.express as px
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import zipfile

from utils import (
    NOT_LOADED,
    load_tab_data,
    fetch_orders, parse_orders, compute_order_stats,
    generate_hourly_chart, generate_payment_pie,
    generate_basket_by_items_chart, df_to_csv_bytes,
    JOURS_FR,
)
from i18n import t


def render_encaissements(*, base_url, username, password, selected_store,
                         selected_date, amount_type, split_pos_neg,
                         selected_bucket, freq, lang="fr"):
    """Onglet 1 — Encaissements. Chargement automatique."""
    from datetime import timedelta

    # Switch rapide Veille / Jour : bascule la date de toute la page.
    period = st.radio(t("period", lang) + " :",
                      [t("day", lang), t("yesterday", lang)],
                      horizontal=True, key="period_enc")
    if period == t("yesterday", lang):
        selected_date = selected_date - timedelta(days=1)

    date_str = selected_date.strftime("%Y-%m-%d")

    # ── Chargement automatique (+ bouton refresh) ──
    cache_key = f"orders_{selected_store}_{date_str}"
    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        force = st.button("🔄 " + t("refresh", lang), type="secondary", key="btn_refresh_orders")

    raw_orders = load_tab_data(
        cache_key,
        lambda: fetch_orders(base_url, username, password, selected_store, date_str),
        t("tabs_takings", lang), force=force)
    if raw_orders is NOT_LOADED:
        return

    if not raw_orders:
        st.warning(t("no_orders_day", lang).format(d=selected_date.strftime("%d/%m/%Y"), s=selected_store))
        return

    df_raw = parse_orders(json.dumps(raw_orders))
    if df_raw.empty:
        st.warning(t("cannot_extract", lang))
        return

    # ── Filtres sidebar ──
    st.sidebar.divider()
    st.sidebar.header("🎯 " + t("filters_takings", lang))
    all_terminals = sorted(df_raw["Terminal"].unique().tolist())
    sel_terminals = st.sidebar.multiselect(t("terminals", lang) + " :", all_terminals, default=all_terminals)
    all_payments = sorted({p for sub in df_raw["Paiements"] for p in sub})
    sel_payments = st.sidebar.multiselect(t("payments", lang) + " :", all_payments, default=all_payments)

    df_f = df_raw[df_raw["Terminal"].isin(sel_terminals)].copy()
    df_f = df_f[df_f["Paiements"].apply(lambda x: any(i in sel_payments for i in x))]

    if df_f.empty:
        st.info(t("no_orders_filters", lang))
        return

    # ── Stats complètes ──
    stats = compute_order_stats(df_f, amount_type)

    # ── KPIs ──
    ca_total = stats["total_ca"]
    nb_tickets = stats["total_orders"]
    panier_moyen = ca_total / nb_tickets if nb_tickets else 0

    yesterday_str = (selected_date - timedelta(days=1)).strftime("%Y-%m-%d")
    with st.spinner("⏳ Chargement du CA de la veille…"):
        raw_y = fetch_orders(base_url, username, password, selected_store, yesterday_str)
        df_y = parse_orders(json.dumps(raw_y))
    ca_yesterday = df_y[amount_type].sum() if not df_y.empty else 0

    # Articles / panier moyen — VENTES uniquement
    df_ventes = df_f[df_f["Type"] == "Vente"]
    articles_moyen = round(float(df_ventes["Nb Articles"].mean()), 2) if not df_ventes.empty else 0.0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    # Le grand chiffre = CA de la date affichee ; le delta = ecart vs veille.
    c1.metric(
        f"{t('ca', lang)} {selected_date.strftime('%d/%m')} ({amount_type})",
        f"{ca_total:,.2f} €".replace(",", " "),
        f"{ca_total - ca_yesterday:+,.2f} € {t('vs_yesterday', lang)}".replace(",", " "),
    )
    c2.metric("🛒 " + t("sales", lang), stats["nb_ventes"],
              f"{stats['ca_ventes']:,.2f} €".replace(",", " "))
    c3.metric("↩️ " + t("returns", lang), stats["nb_retours"],
              f"{stats['ca_retours']:,.2f} €".replace(",", " "),
              delta_color="inverse")
    c4.metric("🧾 " + t("avg_basket", lang), f"{panier_moyen:,.2f} €".replace(",", " "))
    c5.metric("📦 " + t("items_per_basket", lang), f"{articles_moyen:.2f}")
    c6.metric(
        "👤 " + t("known_clients", lang),
        f"{stats['pct_identified']:.1f} %",
        f"{stats['identified_orders']}/{stats['total_orders']} tickets",
    )

    st.divider()

    # ── Graphique horaire ──
    st.subheader(f"📈 {t('detail_of', lang)} {selected_date.strftime('%d/%m/%Y')}")
    with st.spinner("📈 Génération du graphique horaire…"):
        fig_daily, _ = generate_hourly_chart(
            df_f, date_str, freq, amount_type, selected_bucket, split_pos_neg)
    st.plotly_chart(fig_daily, use_container_width=True)

    col_pie, col_top = st.columns(2)
    with col_pie:
        st.subheader("💳 " + t("payments", lang))
        with st.spinner("💳 Génération du graphique…"):
            fig_pay = generate_payment_pie(df_f, amount_type)
        st.plotly_chart(fig_pay, use_container_width=True)
    with col_top:
        st.subheader("🏆 " + t("top5_slots", lang))
        tmp = df_f.copy()
        tmp["DateHeureObj"] = pd.to_datetime(tmp["DateHeure"])
        tmp["Tranche"] = tmp["DateHeureObj"].dt.floor(freq).dt.strftime("%H:%M")
        top5 = (tmp.groupby("Tranche")[amount_type].sum()
                .reset_index().nlargest(5, amount_type))
        top5.columns = ["Tranche", f"{amount_type} (€)"]
        top5[f"{amount_type} (€)"] = top5[f"{amount_type} (€)"].apply(
            lambda x: f"{x:,.2f}".replace(",", " "))
        st.dataframe(top5, use_container_width=True, hide_index=True)

    st.divider()

    # ── Panier moyen par nombre d'articles (ventes) ──
    st.subheader("📦 " + t("basket_by_items", lang))
    st.caption(t("sales_only_hint", lang))
    with st.spinner("📦 Génération du graphique…"):
        fig_basket, basket_stats = generate_basket_by_items_chart(df_f, amount_type, lang)
    if fig_basket is not None:
        st.plotly_chart(fig_basket, use_container_width=True)
        st.caption(
            f"Panier moyen : **{basket_stats['articles_moyen']:.2f} articles** "
            f"sur {basket_stats['nb_ventes']} ventes.")
    else:
        st.info(t("no_sale_basket", lang))

    st.divider()

    # ── Détail des commandes (avec nom du BP et type) ──
    st.subheader("📝 " + t("orders_detail", lang))
    df_disp = df_f.copy()
    df_disp["DateHeureObj"] = pd.to_datetime(df_disp["DateHeure"])
    df_disp["Heure"] = df_disp["DateHeureObj"].dt.strftime("%H:%M:%S")
    df_disp["Paiements"] = df_disp["Paiements"].apply(lambda x: ", ".join(x))
    cols_show = [
        "Ticket", "Heure", "Type", "Terminal", "BP_Nom",
        "Montant Brut", "Montant Net", "Paiements", "Nb Articles",
    ]
    rename_map = {"BP_Nom": "Client"}
    df_disp = (df_disp[cols_show]
               .rename(columns=rename_map)
               .sort_values("Heure", ascending=False)
               .reset_index(drop=True))
    st.dataframe(df_disp, use_container_width=True, hide_index=True)
    st.download_button(
        "📥 CSV", df_to_csv_bytes(df_disp),
        f"Commandes_{selected_store}_{date_str}.csv", "text/csv")

    # ══════════════════════════════════════
    # ANALYSE DE LA SEMAINE
    # ══════════════════════════════════════
    st.divider()
    st.header("📅 " + t("week_analysis", lang))
    start_week = selected_date - timedelta(days=selected_date.weekday())
    week_dates = [start_week + timedelta(days=i) for i in range(7)]

    if not st.button(t("weekly_compile", lang), type="secondary"):
        return

    def _fetch_day(d):
        ds = d.strftime("%Y-%m-%d")
        return ds, fetch_orders(base_url, username, password, selected_store, ds)

    day_raw = {}
    bar = st.progress(0, text="Récupération…")
    with ThreadPoolExecutor(max_workers=4) as exe:
        futures = {exe.submit(_fetch_day, d): i for i, d in enumerate(week_dates)}
        done = 0
        for f in as_completed(futures):
            ds, r = f.result()
            day_raw[ds] = r
            done += 1
            bar.progress(done / 7, text=f"{done}/7")
    bar.progress(1.0, text="📊 Génération des graphiques hebdomadaires…")

    week_data, figs_sem = [], {}
    for i, d in enumerate(week_dates):
        ds = d.strftime("%Y-%m-%d")
        r = day_raw.get(ds, [])
        if not r:
            continue
        df_d = parse_orders(json.dumps(r))
        df_d = df_d[df_d["Terminal"].isin(sel_terminals)]
        df_d = df_d[df_d["Paiements"].apply(lambda x: any(it in sel_payments for it in x))]
        if df_d.empty:
            continue
        df_d["Date"] = ds
        df_d["Jour"] = JOURS_FR[i]
        week_data.append(df_d)
        fn, _ = generate_hourly_chart(df_d, ds, freq, amount_type, selected_bucket, False)
        fm, _ = generate_hourly_chart(df_d, ds, freq, amount_type, selected_bucket, True)
        figs_sem[f"Normal_{JOURS_FR[i]}_{ds}.png"] = fn
        figs_sem[f"Miroir_{JOURS_FR[i]}_{ds}.png"] = fm

    if not week_data:
        st.warning(t("no_data_week", lang))
        return

    df_w = pd.concat(week_data, ignore_index=True)
    total_w = df_w[amount_type].sum()

    cj1, cj2 = st.columns(2)
    with cj1:
        grp = df_w.groupby(["Date", "Jour"], sort=False)[amount_type].sum().reset_index()
        grp["Pct"] = (grp[amount_type] / total_w * 100).round(1).astype(str) + " %"
        fig_jn = px.bar(grp, x="Jour", y=amount_type, text="Pct",
                        title=f"Par Jour (Normal) — {total_w:,.2f} €",
                        color=amount_type, color_continuous_scale="Viridis")
        fig_jn.update_layout(yaxis_title="€", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_jn, use_container_width=True)
    with cj2:
        grpm = df_w.groupby(["Date", "Jour", "Type"], sort=False)[amount_type].sum().reset_index()
        fig_jm = px.bar(grpm, x="Jour", y=amount_type, color="Type", text_auto=".2f",
                        title=f"Par Jour (Miroir) — {total_w:,.2f} €",
                        color_discrete_map={"Vente": "#2ca02c", "Retour": "#d62728"})
        fig_jm.update_layout(barmode="relative", yaxis_title="€", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_jm, use_container_width=True)

    df_w["DateHeureObj"] = pd.to_datetime(df_w["DateHeure"])
    df_w["Tranche"] = df_w["DateHeureObj"].dt.floor(freq).dt.strftime("%H:%M")
    dum = pd.date_range("2000-01-01", "2000-01-01 23:59:59", freq=freq)
    df_at = pd.DataFrame({"Tranche": dum.strftime("%H:%M")})

    ch1, ch2 = st.columns(2)
    with ch1:
        gh = df_w.groupby("Tranche")[amount_type].sum().reset_index()
        dh = pd.merge(df_at, gh, on="Tranche", how="left").fillna(0)
        fig_hn = px.bar(dh, x="Tranche", y=amount_type, text_auto=".2f",
                        title=f"Par {selected_bucket} (Cumul Normal)",
                        color=amount_type, color_continuous_scale="Viridis")
        fig_hn.update_layout(xaxis=dict(type="category", tickangle=-45),
                             yaxis_title="€ cumulé", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_hn, use_container_width=True)
    with ch2:
        ghm = df_w.groupby(["Tranche", "Type"])[amount_type].sum().reset_index()
        idx = pd.MultiIndex.from_product(
            [df_at["Tranche"], ["Vente", "Retour"]], names=["Tranche", "Type"]
        ).to_frame(index=False)
        dhm = pd.merge(idx, ghm, on=["Tranche", "Type"], how="left").fillna(0)
        fig_hm = px.bar(dhm, x="Tranche", y=amount_type, color="Type", text_auto=".2f",
                        title=f"Par {selected_bucket} (Cumul Miroir)",
                        color_discrete_map={"Vente": "#2ca02c", "Retour": "#d62728"})
        fig_hm.update_layout(barmode="relative",
                             xaxis=dict(type="category", tickangle=-45),
                             yaxis_title="€ cumulé", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_hm, use_container_width=True)
    bar.empty()

    try:
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "a", zipfile.ZIP_DEFLATED, False) as zf:
            zf.writestr("Jour_Normal.png", fig_jn.to_image(format="png", width=1200, height=600))
            zf.writestr("Jour_Miroir.png", fig_jm.to_image(format="png", width=1200, height=600))
            zf.writestr("Heure_Normal.png", fig_hn.to_image(format="png", width=1400, height=600))
            zf.writestr("Heure_Miroir.png", fig_hm.to_image(format="png", width=1400, height=600))
            for fn, fg in figs_sem.items():
                zf.writestr(fn, fg.to_image(format="png", width=1400, height=600))
            dfe = df_w.drop(columns=["DateHeureObj", "Tranche"], errors="ignore").copy()
            dfe["Paiements"] = dfe["Paiements"].apply(
                lambda x: ", ".join(x) if isinstance(x, list) else x)
            zf.writestr("Donnees_Semaine.csv",
                        dfe.to_csv(index=False, sep=";", encoding="utf-8-sig"))
        st.success("✅ " + t("archive_ready", lang))
        st.download_button("📥 ZIP", zbuf.getvalue(),
                           f"Semaine_{start_week.strftime('%Y%m%d')}.zip",
                           "application/zip", type="primary")
    except Exception as e:
        st.error(t("zip_error", lang) + f" : {e}")

