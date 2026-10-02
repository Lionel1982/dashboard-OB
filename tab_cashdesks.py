
import streamlit as st
import json
import plotly.express as px
from datetime import timedelta, date as _date

from utils import (
    NOT_LOADED,
    load_tab_data,
    fetch_cashups, fetch_cashups_by_org_date, parse_cashups, compute_cashup_stats,
    fetch_orders, analyze_cashup_gap,
    generate_cashup_terminal_chart, generate_vat_chart,
    build_cashup_journal, df_to_csv_bytes,
)
import compta_engine
from i18n import t


# ============================================================
#  EXPORT COMPTABLE : helpers + popin de configuration
# ============================================================
def _fetch_cashups_range(base_url, username, password, store, date_from, date_to):
    """Recupere les CashUp sur une plage [date_from, date_to] (dates incluses),
    jour par jour via l'endpoint par date (profite du cache DB).

    On elargit la fenetre de fetch de +/- 1 jour : l'endpoint filtre cote
    serveur sur creationDate, alors que le rattachement comptable se fait sur
    closeDateTime (date de cloture reelle). Une cloture faite apres minuit peut
    avoir creationDate et closeDateTime sur des jours differents ; le moteur
    (group_cashups) refiltre ensuite precisement sur closeDateTime."""
    all_cu = []
    seen = set()
    d = date_from - timedelta(days=1)
    while d <= date_to + timedelta(days=1):
        ds = d.strftime("%Y-%m-%d")
        try:
            day_cu = fetch_cashups_by_org_date(base_url, username, password, store, ds)
        except Exception:
            day_cu = []
        for cu in (day_cu or []):
            cid = cu.get("id") or (cu.get("terminal"), cu.get("businessDate"),
                                   cu.get("creationDate"))
            key = json.dumps(cid, default=str, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            all_cu.append(cu)
        d += timedelta(days=1)
    return all_cu


def _fetch_orders_range(base_url, username, password, store, date_from, date_to):
    """Recupere les Orders sur une plage, jour par jour (cache DB). Sert au
    calcul des services (cartes cadeaux) et au controle d'ecart."""
    all_ord = []
    d = date_from
    while d <= date_to:
        ds = d.strftime("%Y-%m-%d")
        try:
            day = fetch_orders(base_url, username, password, store, ds)
        except Exception:
            day = []
        all_ord.extend(day or [])
        d += timedelta(days=1)
    return all_ord


@st.dialog("Configuration comptable", width="large")
def _compta_config_dialog(lang="fr"):
    """Popin d'edition de la config comptable (compta_config.json)."""
    cfg = compta_engine.load_compta_config()
    st.caption(t("compta_cfg_hint", lang))

    st.markdown("**" + t("compta_cfg_journals", lang) + "**")
    c1, c2 = st.columns(2)
    cfg["code_journal_ventes"] = c1.text_input(
        t("compta_cfg_journal_sales", lang), value=cfg.get("code_journal_ventes", "C2"))
    cfg["code_journal_od"] = c2.text_input(
        t("compta_cfg_journal_od", lang), value=cfg.get("code_journal_od", "OD"))

    st.markdown("**" + t("compta_cfg_accounts_by_rate", lang) + "**")
    st.caption(t("compta_cfg_rate_hint", lang))
    prod = cfg.get("compte_produit_par_taux", {})
    tva = cfg.get("compte_tva_par_taux", {})
    all_rates = sorted(set(list(prod.keys()) + list(tva.keys())),
                       key=lambda x: float(x), reverse=True)
    rate_rows = [{"Taux (%)": float(rk), "Compte PRODUIT": prod.get(rk, ""),
                  "Compte TVA": tva.get(rk) or ""} for rk in all_rates]
    edited = st.data_editor(rate_rows, num_rows="dynamic", use_container_width=True,
                            key="compta_rate_editor")
    new_prod, new_tva = {}, {}
    for row in edited:
        try:
            rk = f"{float(row['Taux (%)'])}"
        except (TypeError, ValueError, KeyError):
            continue
        if row.get("Compte PRODUIT"):
            new_prod[rk] = str(row["Compte PRODUIT"]).strip()
        new_tva[rk] = (str(row["Compte TVA"]).strip() or None) if row.get("Compte TVA") else None
    if new_prod:
        cfg["compte_produit_par_taux"] = new_prod
    cfg["compte_tva_par_taux"] = new_tva

    c3, c4 = st.columns(2)
    cfg["compte_produit_defaut"] = c3.text_input(
        t("compta_cfg_prod_default", lang), value=cfg.get("compte_produit_defaut", "71111"))
    cfg["compte_tva_defaut"] = c4.text_input(
        t("compta_cfg_tva_default", lang), value=cfg.get("compte_tva_defaut", "2433211"))

    st.markdown("**" + t("compta_cfg_cash_accounts", lang) + "**")
    caisse = cfg.get("compte_caisse_par_terminal", {})
    caisse_rows = [{"Terminal": k, "Compte": v} for k, v in caisse.items()]
    caisse_ed = st.data_editor(caisse_rows, num_rows="dynamic",
                               use_container_width=True, key="compta_caisse_editor")
    new_caisse = {}
    for row in caisse_ed:
        term = str(row.get("Terminal", "")).strip()
        cpt = str(row.get("Compte", "")).strip()
        if term and cpt:
            new_caisse[term] = cpt
    if new_caisse:
        cfg["compte_caisse_par_terminal"] = new_caisse

    st.markdown("**" + t("compta_cfg_payments", lang) + "**")
    st.caption(t("compta_cfg_payments_hint", lang))
    pm = cfg.get("payment_method_map", {})
    pm_rows = [{"Libelle API (minuscules)": k, "Compte": v[0], "Libelle ecriture": v[1]}
               for k, v in pm.items()]
    pm_ed = st.data_editor(pm_rows, num_rows="dynamic",
                           use_container_width=True, key="compta_pm_editor")
    new_pm = {}
    for row in pm_ed:
        k = str(row.get("Libelle API (minuscules)", "")).strip().lower()
        cpt = str(row.get("Compte", "")).strip()
        lib = str(row.get("Libelle ecriture", "")).strip()
        if k and cpt:
            new_pm[k] = [cpt, lib]
    if new_pm:
        cfg["payment_method_map"] = new_pm

    st.markdown("**" + t("compta_cfg_misc", lang) + "**")
    c5, c6, c7 = st.columns(3)
    cfg["compte_ecart_negatif"] = c5.text_input(
        t("compta_cfg_gap_neg", lang), value=cfg.get("compte_ecart_negatif", "6888"))
    cfg["compte_ecart_positif"] = c6.text_input(
        t("compta_cfg_gap_pos", lang), value=cfg.get("compte_ecart_positif", "7888"))
    cfg["compte_service"] = c7.text_input(
        t("compta_cfg_service", lang), value=cfg.get("compte_service", "2768"))
    c8, c9 = st.columns(2)
    try:
        seuil_val = float(cfg.get("seuil_ecart_orders_cashup", 1.0))
    except (TypeError, ValueError):
        seuil_val = 1.0
    cfg["seuil_ecart_orders_cashup"] = c8.number_input(
        t("compta_cfg_threshold", lang), value=seuil_val, min_value=0.0, step=0.5)
    cfg["echeance_defaut"] = c9.text_input(
        t("compta_cfg_due_date", lang), value=cfg.get("echeance_defaut", "30/12/1899"))

    st.divider()
    b1, b2, b3 = st.columns([1, 1, 1])
    if b1.button("\U0001f4be " + t("compta_cfg_save", lang), type="primary",
                 use_container_width=True):
        if compta_engine.save_compta_config(cfg):
            st.success(t("compta_cfg_saved", lang))
            st.rerun()
        else:
            st.error(t("compta_cfg_save_error", lang))
    if b2.button("\u21a9\ufe0f " + t("compta_cfg_reset", lang), use_container_width=True):
        compta_engine.save_compta_config(dict(compta_engine.DEFAULTS))
        st.success(t("compta_cfg_reset_done", lang))
        st.rerun()
    if b3.button("\u2715 " + t("compta_cfg_close", lang), use_container_width=True):
        st.rerun()


def render_cashdesks(*, base_url, username, password, selected_store, selected_date, lang="fr"):
    """Onglet Caisses - analyse COMPTABLE des clotures (CashUp).

    Centre sur le cash-up au sens comptable : ventilation par taux de TVA,
    reglements par moyen de paiement (totalSales-totalReturns), mouvements de
    caisse, controle d'equilibre. Logique alignee sur export_compta_pt.py
    (ordertype 0=vente / 1=retour, types 2/3 ignores).
    """
    from datetime import timedelta

    period = st.radio(t("period", lang) + " :",
                      [t("day", lang), t("yesterday", lang)],
                      horizontal=True, key="period_cash")
    if period == t("yesterday", lang):
        selected_date = selected_date - timedelta(days=1)

    st.subheader("\U0001f4b0 " + t("cash_analysis", lang))
    st.caption(t("cash_net_hint", lang))
    date_str = selected_date.strftime("%Y-%m-%d")

    cache_key = f"cashups_{date_str}"
    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        force = st.button("\U0001f504 " + t("refresh", lang), type="secondary", key="btn_refresh_cash")

    raw = load_tab_data(
        cache_key,
        lambda: fetch_cashups_by_org_date(base_url, username, password,
                                          selected_store, date_str),
        t("cash_analysis", lang), force=force)
    if raw is NOT_LOADED:
        return

    df_cu = parse_cashups(json.dumps(raw), date_str)
    if df_cu.empty:
        st.info(t("cash_no_data", lang))
        return
    stats = compute_cashup_stats(df_cu)

    # ---- KPIs comptables ----
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("\U0001f4b0 " + t("cash_ca_net", lang),
              f"{stats['ca_net']:,.2f} \u20ac".replace(",", " "))
    k2.metric("\U0001f4c8 " + t("vat_total_ht", lang),
              f"{stats['ht_total']:,.2f} \u20ac".replace(",", " "))
    k3.metric("\U0001f9fe " + t("vat_total_vat", lang),
              f"{stats['tva_totale']:,.2f} \u20ac".replace(",", " "))
    k4.metric("\U0001f5c4\ufe0f " + t("cash_nb_closures", lang),
              f"{stats['nb_clotures']} / {stats['nb_caisses']}")
    st.divider()

    # ---- 1) Ventilation par taux de TVA ----
    st.subheader("\U0001f9fe " + t("vat_section", lang))
    df_tva = stats["df_tva"]
    if df_tva.empty:
        st.info(t("cash_no_data", lang))
    else:
        col_g, col_t = st.columns([3, 2])
        with col_g:
            with st.spinner("\U0001f4ca\u2026"):
                fig_v = generate_vat_chart(df_tva, lang)
            if fig_v is not None:
                st.plotly_chart(fig_v, use_container_width=True)
        with col_t:
            show = df_tva.copy()
            show["taux"] = show["taux"].map(lambda r: f"{r:g}%")
            show.columns = [t("vat_rate", lang), t("vat_base", lang),
                            t("vat_amount", lang), t("vat_ttc", lang)]
            for c in [t("vat_base", lang), t("vat_amount", lang), t("vat_ttc", lang)]:
                show[c] = show[c].apply(lambda x: f"{x:,.2f}".replace(",", " "))
            st.dataframe(show, use_container_width=True, hide_index=True)
            st.download_button("\U0001f4e5 " + t("vat_section", lang) + " CSV",
                               df_to_csv_bytes(df_tva),
                               f"TVA_{date_str}.csv", "text/csv", key="csv_vat")
    st.divider()

    # ---- 2) CA net par caisse + 3) Reglements par moyen de paiement ----
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("\U0001f4ca " + t("cash_terminal_title", lang))
        with st.spinner("\U0001f4ca\u2026"):
            fig = generate_cashup_terminal_chart(stats["df_by_terminal"], lang)
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.subheader("\U0001f4b3 " + t("reglements_section", lang))
        df_pm = stats["df_payments"]
        if not df_pm.empty:
            fig_pm = px.pie(df_pm, names="moyen", values="montant", hole=0.4)
            fig_pm.update_traces(textinfo="percent+label")
            fig_pm.update_layout(margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig_pm, use_container_width=True)
            show_pm = df_pm.copy()
            show_pm.columns = [t("col_pay_method", lang), t("col_amount", lang)]
            show_pm[t("col_amount", lang)] = show_pm[t("col_amount", lang)].apply(
                lambda x: f"{x:,.2f}".replace(",", " "))
            st.dataframe(show_pm, use_container_width=True, hide_index=True)
        else:
            st.info(t("cash_no_data", lang))
    st.divider()

    # ---- 4) Mouvements de caisse ----
    st.subheader("\U0001f4b8 " + t("cash_mvts_section", lang))
    df_mvts = stats["df_mvts"]
    if df_mvts.empty:
        st.info(t("cash_no_mvts", lang))
    else:
        show_m = df_mvts.copy()
        show_m.columns = [t("col_mvt_type", lang), t("col_mvt_label", lang),
                          t("col_pay_method", lang), t("col_amount", lang)]
        show_m[t("col_amount", lang)] = show_m[t("col_amount", lang)].apply(
            lambda x: f"{x:,.2f}".replace(",", " "))
        st.dataframe(show_m, use_container_width=True, hide_index=True)
    st.divider()

    # ---- Journal des clotures (detail par piece) ----
    st.subheader("\U0001f4d3 " + t("journal_section", lang))
    st.caption(t("journal_hint", lang))
    df_journal = build_cashup_journal(df_cu)
    if df_journal.empty:
        st.info(t("cash_no_data", lang))
    else:
        show_j = df_journal[["piece", "date", "terminal", "rubrique",
                             "base_ht", "tva", "montant"]].copy()
        show_j.columns = [t("col_piece", lang), t("date", lang),
                          t("cash_nb_registers", lang), t("col_rubrique", lang),
                          t("vat_base", lang), t("vat_amount", lang),
                          t("col_amount", lang)]
        for c in [t("vat_base", lang), t("vat_amount", lang), t("col_amount", lang)]:
            show_j[c] = show_j[c].apply(lambda x: f"{x:,.2f}".replace(",", " "))
        st.dataframe(show_j, use_container_width=True, hide_index=True)
        st.download_button("\U0001f4e5 " + t("dl_journal", lang),
                           df_to_csv_bytes(df_journal),
                           f"JournalClotures_{date_str}.csv", "text/csv",
                           key="csv_journal")
    st.divider()

    # ---- Controle d'equilibre ----
    st.subheader("\u2696\ufe0f " + t("balance_section", lang))
    if abs(stats["ecart"]) < 0.01:
        st.success("\u2705 " + t("balance_ok", lang))
    else:
        st.warning("\u26a0\ufe0f " + t("balance_diff", lang).format(
            ttc=f"{stats['ttc_total']:,.2f}".replace(",", " "),
            reg=f"{stats['total_reglements']:,.2f}".replace(",", " "),
            ecart=f"{stats['ecart']:,.2f}".replace(",", " ")))

        # Bouton Analyse : croise avec les Orders pour expliquer l'ecart via
        # les services / cartes cadeaux (productType='S', 0% TVA).
        if st.button(t("analyze_gap_btn", lang), type="primary", key="btn_analyze_gap"):
            with st.status("\U0001f50d " + t("gap_analysis_title", lang), expanded=True) as status:
                raw_ord = fetch_orders(base_url, username, password, selected_store, date_str)
                gap = analyze_cashup_gap(json.dumps(raw_ord))
                status.update(label="\u2705 " + t("gap_analysis_title", lang),
                              state="complete", expanded=True)
            st.caption(t("gap_analysis_hint", lang))
            if gap["df_services"].empty:
                st.info(t("gap_no_services", lang))
            else:
                total_serv = gap["total_services"]
                # Ecart residuel = |ecart| - services expliques
                residuel = round(abs(stats["ecart"]) - total_serv, 2)
                m1, m2 = st.columns(2)
                m1.metric("\U0001f381 " + t("gap_total_services", lang),
                          f"{total_serv:,.2f} \u20ac".replace(",", " "))
                m2.metric("\U0001f4d0 " + t("gap_residual", lang),
                          f"{residuel:,.2f} \u20ac".replace(",", " "))
                df_s = gap["df_services"].copy()
                df_s.columns = [t("col_product", lang), t("vat_rate", lang),
                                t("prod_qty", lang), t("col_amount", lang),
                                t("prod_nb_lines", lang)]
                df_s[t("col_amount", lang)] = df_s[t("col_amount", lang)].apply(
                    lambda x: f"{x:,.2f}".replace(",", " "))
                st.dataframe(df_s, use_container_width=True, hide_index=True)
                if abs(residuel) < 0.01:
                    st.success(t("gap_explained", lang))

    # ══════════════════════════════════════════════════════════
    #  EXPORT COMPTABLE COMPLET (moteur partage compta_engine)
    # ══════════════════════════════════════════════════════════
    st.divider()
    st.header("\U0001f4d2 " + t("export_compta_title", lang))
    st.caption(t("export_compta_hint", lang))

    # Bouton Configuration : HORS du formulaire (cliquable a tout moment).
    if st.button("\u2699\ufe0f " + t("export_compta_config_btn", lang),
                 key="btn_compta_cfg"):
        _compta_config_dialog(lang)

    # Periode + generation DANS un st.form : changer une date ne declenche
    # AUCUN rerun ; le traitement n'a lieu qu'au clic sur le bouton submit.
    default_from = selected_date.replace(day=1)  # 1er du mois par defaut
    with st.form("form_export_compta", border=True):
        col_from, col_to = st.columns(2)
        exp_from = col_from.date_input(t("export_from", lang), default_from,
                                       key="exp_compta_from")
        exp_to = col_to.date_input(t("export_to", lang), selected_date,
                                   key="exp_compta_to")
        submitted = st.form_submit_button(
            "\U0001f4e4 " + t("export_compta_generate", lang), type="primary")

    if submitted:
        if exp_from > exp_to:
            st.error(t("export_date_error", lang))
        else:
            with st.status("\u23f3 " + t("export_compta_running", lang), expanded=True) as status:
                st.write(t("export_compta_step_cashup", lang))
                cashups = _fetch_cashups_range(base_url, username, password,
                                               selected_store, exp_from, exp_to)
                st.write(t("export_compta_step_orders", lang))
                orders = _fetch_orders_range(base_url, username, password,
                                             selected_store, exp_from, exp_to)
                st.write(t("export_compta_step_build", lang))
                cfg = compta_engine.load_compta_config()
                rows, warnings = compta_engine.build_accounting_rows(
                    cashups, orders,
                    exp_from.strftime("%Y-%m-%d"), exp_to.strftime("%Y-%m-%d"), cfg)
                # Diagnostic de reconciliation (clotures manquantes / ecarts)
                diag = compta_engine.build_reconciliation_diagnostic(
                    cashups, orders,
                    exp_from.strftime("%Y-%m-%d"), exp_to.strftime("%Y-%m-%d"), cfg)
                status.update(label="\u2705 " + t("export_compta_done", lang),
                              state="complete", expanded=False)
            # Stocker le resultat en session pour qu'il survive aux reruns
            # (ex: clic sur un bouton de telechargement).
            st.session_state["compta_export_result"] = {
                "rows": rows, "warnings": warnings,
                "diag": diag,
                "orders": orders,
                "from": exp_from.strftime("%Y%m%d"), "to": exp_to.strftime("%Y%m%d"),
            }

    # ── Affichage du dernier resultat genere (persiste via session_state) ──
    _res = st.session_state.get("compta_export_result")
    if _res is not None:
        rows = _res["rows"]
        warnings = _res["warnings"]
        if not rows:
            st.warning(t("export_compta_no_data", lang))
        else:
            # Controle d'equilibre global
            tot_deb = sum(float(r[5]) for r in rows)
            tot_cred = sum(float(r[6]) for r in rows)
            nb_pieces = len({r[2] for r in rows})
            m1, m2, m3 = st.columns(3)
            m1.metric(t("export_compta_lines", lang), len(rows))
            m2.metric(t("export_compta_pieces", lang), nb_pieces)
            m3.metric(t("export_compta_balance", lang),
                      "\u2705" if abs(tot_deb - tot_cred) < 0.01 else "\u26a0\ufe0f",
                      f"{tot_deb:,.2f} / {tot_cred:,.2f}".replace(",", " "))

            # Apercu
            import pandas as _pd
            df_prev = _pd.DataFrame(rows, columns=compta_engine.CSV_HEADER)
            st.dataframe(df_prev, use_container_width=True, hide_index=True, height=320)

            # Telechargement CSV + Excel (9 colonnes, identique au script CLI)
            base_name = f"export_compta_{selected_store}_{_res['from']}_{_res['to']}"
            csv_bytes = compta_engine.rows_to_csv_bytes(rows)
            xlsx_bytes = compta_engine.rows_to_xlsx_bytes(rows)
            dl1, dl2 = st.columns(2)
            dl1.download_button("\U0001f4e5 " + t("export_compta_dl", lang) + " (CSV)",
                                csv_bytes, base_name + ".csv", "text/csv",
                                type="primary", key="dl_compta_csv",
                                use_container_width=True)
            if xlsx_bytes is not None:
                dl2.download_button(
                    "\U0001f4e5 " + t("export_compta_dl", lang) + " (Excel)",
                    xlsx_bytes, base_name + ".xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_compta_xlsx", use_container_width=True)
            else:
                dl2.caption(t("export_compta_xlsx_na", lang))

            # Avertissements (ecarts, moyens de paiement inconnus…)
            if warnings:
                with st.expander("\u26a0\ufe0f " + t("export_compta_warnings", lang)
                                 + f" ({len(warnings)})", expanded=False):
                    for w in warnings:
                        st.caption("• " + w)

            # ── Diagnostic de reconciliation (clotures manquantes / ecarts) ──
            diag = _res.get("diag") or []
            if diag:
                nb_missing = sum(1 for d in diag if d["statut"] == "cloture_manquante")
                nb_ecart = sum(1 for d in diag if d["statut"] == "ecart")
                nb_cu_only = sum(1 for d in diag if d["statut"] == "cashup_sans_orders")
                titre = "\U0001f50d " + t("diag_title", lang)
                if nb_missing:
                    titre += f" — {nb_missing} " + t("diag_missing_short", lang)
                with st.expander(titre, expanded=bool(nb_missing or nb_ecart)):
                    st.caption(t("diag_hint", lang))
                    c1, c2, c3 = st.columns(3)
                    c1.metric(t("diag_missing", lang), nb_missing)
                    c2.metric(t("diag_ecart", lang), nb_ecart)
                    c3.metric(t("diag_cu_only", lang), nb_cu_only)

                    import pandas as _pd2
                    _icons = {"cloture_manquante": "\U0001f6ab " + t("diag_st_missing", lang),
                              "ecart": "\u26a0\ufe0f " + t("diag_st_gap", lang),
                              "cashup_sans_orders": "\u2139\ufe0f " + t("diag_st_cu_only", lang),
                              "ok": "\u2705 " + t("diag_st_ok", lang)}
                    df_diag = _pd2.DataFrame(diag)
                    df_diag["statut"] = df_diag["statut"].map(lambda s: _icons.get(s, s))
                    df_diag = df_diag.rename(columns={
                        "date": t("date", lang), "terminal": t("cash_nb_registers", lang),
                        "nb_orders": "Nb Orders", "nb_cashups": "Nb CashUp",
                        "base_orders": t("vat_base", lang) + " Orders",
                        "base_cashup": t("vat_base", lang) + " CashUp",
                        "ecart": t("export_compta_balance", lang), "statut": t("diag_status", lang)})
                    st.dataframe(df_diag, use_container_width=True, hide_index=True)
                    if nb_missing:
                        st.warning(t("diag_missing_explain", lang))

                    # ── Analyse au niveau TICKET pour un (jour, terminal) ──
                    st.divider()
                    st.markdown("**\U0001f9fe " + t("diag_ticket_title", lang) + "**")
                    st.caption(t("diag_ticket_hint", lang))
                    # Proposer en priorite les groupes en ecart / cloture manquante
                    problem = [d for d in diag if d["statut"] in ("ecart", "cloture_manquante")]
                    candidates = problem or diag
                    options = [f"{d['date']} · {d['terminal']} "
                               f"({t('export_compta_balance', lang)} {d['ecart']:+.2f})"
                               for d in candidates]
                    sel = st.selectbox(t("diag_ticket_select", lang), options,
                                       key="diag_ticket_sel")
                    if sel:
                        chosen = candidates[options.index(sel)]
                        _orders = _res.get("orders") or []
                        tickets = compta_engine.get_ticket_detail(
                            _orders, chosen["date"], chosen["terminal"])
                        if not tickets:
                            st.info(t("diag_ticket_none", lang))
                        else:
                            import pandas as _pd3
                            df_tk = _pd3.DataFrame([{
                                "Ticket": tk["ticket"],
                                t("cash_nb_registers", lang): chosen["terminal"],
                                "Heure": tk["heure"],
                                t("col_mvt_type", lang): tk["type"],
                                t("vat_base", lang) + " (HT)": tk["base_ht_produits"],
                                "Services": tk["base_services"],
                                "Net": tk["net"], "Brut": tk["brut"],
                            } for tk in tickets])
                            st.dataframe(df_tk, use_container_width=True, hide_index=True,
                                         height=300)
                            tot_ht = sum(tk["base_ht_produits"] for tk in tickets)
                            tot_s = sum(tk["base_services"] for tk in tickets)
                            st.caption(
                                t("diag_ticket_totals", lang).format(
                                    n=len(tickets),
                                    ht=f"{tot_ht:,.2f}".replace(",", " "),
                                    s=f"{tot_s:,.2f}".replace(",", " ")))
