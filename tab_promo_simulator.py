"""
Onglet "Simulateur de promotions" (moteur Openbravo External Discount).

Flux :
  1. Saisie d'un n de ticket + sa date.
  2. Recuperation de l'order : 0 appel si deja en cache (session/DB), sinon 1
     seul fetch_orders du jour concerne.
  3. Rejoue le ticket dans le moteur (CALCULATE_DISCOUNTS) : 1 appel.
  4. Affiche 2 listes : remises DECLENCHEES vs promos DISPONIBLES non appliquees.
  5. Selection d'une promo -> diagnostic "pourquoi", via (sans appel) la
     comparaison OB-engine vs reponse, et (a la demande) VALIDATE_EXTERNAL_COUPON
     pour les coupons et GET_ITEM_DISCOUNTS par produit.

Budget d'appels : typiquement 1 (ticket en cache) ; +1 si ticket jamais charge ;
les diagnostics coupon/produit ne coutent un appel QUE si l'utilisateur clique.
"""

import json
import html as _html

import pandas as pd
import streamlit as st

from utils import (load_tab_data, fetch_orders, parse_orders,
                   fetch_order_by_document_no, per_ticket_promo_summary)
from i18n import t
import external_discount as ed

# AgGrid : tableau interactif (tri au clic, selection de ligne, tooltips au
# survol). Import conditionnel -> si le paquet n'est pas installe en local, on
# bascule sur un fallback st.dataframe (sans crash). Sur Streamlit Cloud il est
# installe via requirements.txt (streamlit-aggrid).
try:
    from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
    _HAS_AGGRID = True
except Exception:
    _HAS_AGGRID = False


# ---------------------------------------------------------------------------
#  Helpers d'affichage des icones promo/coupon avec tooltip (survol HTML natif)
# ---------------------------------------------------------------------------
def _icon_html(has_promo, has_coupon, promo_tt="", coupon_tt=""):
    """Rend les icones promo (🏷️) et coupon (🎟️) avec tooltip HTML `title=`.
    Renvoie une chaine HTML (a afficher via st.markdown(unsafe_allow_html=True))."""
    parts = []
    if has_promo:
        tip = _html.escape(promo_tt or "Promotion appliquee")
        parts.append(f'<span title="{tip}" style="cursor:help;">\U0001f3f7\ufe0f</span>')
    if has_coupon:
        tip = _html.escape(coupon_tt or "Coupon declenche")
        parts.append(f'<span title="{tip}" style="cursor:help;">\U0001f39f\ufe0f</span>')
    return " ".join(parts) if parts else '<span style="opacity:.3;">\u2014</span>'


def _fmt_hm(dt_str: str) -> str:
    """Extrait HH:MM d'une date ISO (ou renvoie la chaine telle quelle)."""
    s = (dt_str or "").replace("T", " ")
    # formats possibles : '2026-09-25 14:03:21', '2026-09-25T14:03:21+01:00'
    if len(s) >= 16 and s[10] == " ":
        return s[11:16]
    return s[:16]


def _sort_docs(summary: dict, sort_by: str, descending: bool, lang: str):
    """Trie les documentNo selon le critere choisi. Les tickets avec
    promo/coupon restent prioritaires UNIQUEMENT pour le tri par defaut."""
    keys = list(summary.keys())
    if sort_by == t("psim_sort_remise", lang):
        keys.sort(key=lambda d: summary[d]["remise_totale"], reverse=descending)
    elif sort_by == t("psim_sort_date", lang):
        keys.sort(key=lambda d: summary[d].get("datetime", ""), reverse=descending)
    elif sort_by == t("psim_sort_ticket", lang):
        keys.sort(key=lambda d: d, reverse=descending)
    elif sort_by == t("psim_sort_npromos", lang):
        keys.sort(key=lambda d: summary[d]["n_promos"], reverse=descending)
    elif sort_by == t("psim_sort_montant", lang):
        keys.sort(key=lambda d: summary[d].get("gross", 0.0), reverse=descending)
    else:
        # defaut : promo/coupon d'abord, puis remise decroissante
        keys.sort(key=lambda d: (not (summary[d]["has_promo"] or summary[d]["has_coupon"]),
                                 -summary[d]["remise_totale"]))
    return keys


def _render_tickets_table(summary: dict, lang: str, docs=None):
    """Affiche le tableau HTML des tickets du jour avec icones + tooltips.
    `summary` = sortie de per_ticket_promo_summary. `docs` = ordre deja trie
    (sinon tri par defaut). Renvoie la liste des documentNo affiches."""
    if docs is None:
        docs = _sort_docs(summary, "", False, lang)
    rows = []
    for doc in docs:
        s = summary[doc]
        icons = _icon_html(s["has_promo"], s["has_coupon"],
                           s["promo_tooltip"], s["coupon_tooltip"])
        remise = f'{s["remise_totale"]:.2f} \u20ac' if s["remise_totale"] else "\u2014"
        montant = f'{s.get("gross", 0.0):.2f} \u20ac' if s.get("gross") else "\u2014"
        heure = _html.escape(_fmt_hm(s.get("datetime", "")))
        rows.append(
            f'<tr>'
            f'<td style="padding:4px 10px;font-family:monospace;">{_html.escape(doc)}</td>'
            f'<td style="padding:4px 10px;text-align:center;">{heure}</td>'
            f'<td style="padding:4px 10px;text-align:center;font-size:1.2em;">{icons}</td>'
            f'<td style="padding:4px 10px;text-align:right;">{remise}</td>'
            f'<td style="padding:4px 10px;text-align:right;">{montant}</td>'
            f'</tr>')
    table = (
        '<table style="border-collapse:collapse;width:100%;">'
        f'<thead><tr style="border-bottom:2px solid #ccc;">'
        f'<th style="padding:4px 10px;text-align:left;">{t("psim_col_ticket", lang)}</th>'
        f'<th style="padding:4px 10px;text-align:center;">{t("psim_col_hour", lang)}</th>'
        f'<th style="padding:4px 10px;text-align:center;">{t("psim_col_promos_coupons", lang)}</th>'
        f'<th style="padding:4px 10px;text-align:right;">{t("psim_col_remise", lang)}</th>'
        f'<th style="padding:4px 10px;text-align:right;">{t("psim_col_montant", lang)}</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>')
    st.markdown(table, unsafe_allow_html=True)
    return docs

def _summary_to_df(summary: dict):
    """Transforme le resume par ticket en DataFrame pour AgGrid.
    Colonnes : Ticket, Heure, Promo (icone), Coupon (icone), Detail promos,
    Coupons, Remise, Montant, + colonnes cachees pour tri/tooltip."""
    rows = []
    for doc, s in summary.items():
        rows.append({
            "ticket": doc,
            "heure": _fmt_hm(s.get("datetime", "")),
            "promo": "\U0001f3f7\ufe0f" if s["has_promo"] else "",
            "coupon": "\U0001f39f\ufe0f" if s["has_coupon"] else "",
            "detail_promos": s.get("promo_tooltip", ""),
            "coupons": ", ".join(s.get("coupons", [])),
            "remise": round(float(s.get("remise_totale", 0.0)), 2),
            "montant": round(float(s.get("gross", 0.0)), 2),
            "n_promos": s.get("n_promos", 0),
            "_datetime": s.get("datetime", ""),
        })
    return pd.DataFrame(rows)


def _render_aggrid_tickets(summary: dict, lang: str):
    """Affiche le tableau interactif des tickets (AgGrid) : tri au clic,
    selection de ligne, tooltips au survol. Renvoie le documentNo selectionne
    (ou None). Fallback st.dataframe si AgGrid indisponible."""
    df = _summary_to_df(summary)
    if df.empty:
        st.caption(t("psim_no_tickets_short", lang))
        return None

    if not _HAS_AGGRID:
        # Fallback : st.dataframe natif (tri au clic) + selectbox pour le choix.
        st.info(t("psim_aggrid_missing", lang))
        show = df.rename(columns={
            "ticket": t("psim_col_ticket", lang), "heure": t("psim_col_hour", lang),
            "promo": "\U0001f3f7\ufe0f", "coupon": "\U0001f39f\ufe0f",
            "detail_promos": t("psim_col_detail", lang), "coupons": t("psim_col_coupon", lang),
            "remise": t("psim_col_remise", lang), "montant": t("psim_col_montant", lang),
            "n_promos": t("psim_sort_npromos", lang),
        }).drop(columns=["_datetime"])
        st.dataframe(show, use_container_width=True, hide_index=True)
        return st.selectbox(t("psim_select_ticket", lang), df["ticket"].tolist(),
                            key="psim_fallback_sel")

    # --- AgGrid : configuration ---
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(sortable=True, filter=True, resizable=True)
    gb.configure_selection(selection_mode="single", use_checkbox=False)
    # Colonnes lisibles + largeurs + tooltips
    gb.configure_column("ticket", header_name=t("psim_col_ticket", lang), width=160)
    gb.configure_column("heure", header_name=t("psim_col_hour", lang), width=90)
    gb.configure_column("promo", header_name="\U0001f3f7\ufe0f", width=70,
                        tooltipField="detail_promos")
    gb.configure_column("coupon", header_name="\U0001f39f\ufe0f", width=70,
                        tooltipField="coupons")
    gb.configure_column("detail_promos", header_name=t("psim_col_detail", lang),
                        tooltipField="detail_promos", flex=2)
    gb.configure_column("coupons", header_name=t("psim_col_coupon", lang), width=140,
                        tooltipField="coupons")
    gb.configure_column("remise", header_name=t("psim_col_remise", lang), width=110,
                        type=["numericColumn"],
                        valueFormatter="x.toLocaleString('fr-FR',{minimumFractionDigits:2,maximumFractionDigits:2})+' \u20ac'")
    gb.configure_column("montant", header_name=t("psim_col_montant", lang), width=110,
                        type=["numericColumn"],
                        valueFormatter="x.toLocaleString('fr-FR',{minimumFractionDigits:2,maximumFractionDigits:2})+' \u20ac'")
    gb.configure_column("n_promos", header_name=t("psim_sort_npromos", lang), width=100)
    gb.configure_column("_datetime", hide=True)
    grid_options = gb.build()

    grid = AgGrid(
        df, gridOptions=grid_options,
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=True,
        theme="streamlit", height=380, key="psim_aggrid")

    sel_rows = grid.get("selected_rows")
    # selected_rows peut etre un DataFrame (versions recentes) ou une liste
    if sel_rows is None:
        return None
    try:
        if hasattr(sel_rows, "empty"):   # DataFrame
            if sel_rows.empty:
                return None
            return sel_rows.iloc[0].get("ticket")
        if isinstance(sel_rows, list) and sel_rows:
            return sel_rows[0].get("ticket")
    except Exception:
        return None
    return None


def _find_order(raw_orders, ticket_no: str):
    """Retrouve l'order brut par documentNo dans la liste chargee."""
    tn = (ticket_no or "").strip().lower()
    for o in raw_orders or []:
        if str(o.get("documentNo", "")).strip().lower() == tn:
            return o
    return None


def _recap_order(order: dict, lang: str):
    """Affiche un petit recapitulatif du ticket."""
    bp = order.get("businessPartner", {})
    bp_name = ""
    if isinstance(bp, dict):
        bp_name = bp.get("name") or bp.get("commercialName") or bp.get("searchKey") or ""
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(t("psim_ticket_no", lang), order.get("documentNo", "N/A"))
    c2.metric(t("amount_gross", lang), f"{float(order.get('grossAmount', 0) or 0):,.2f} \u20ac".replace(",", " "))
    c3.metric(t("col_nb_items", lang), len(order.get("lines", []) or []))
    c4.metric(t("col_client", lang), bp_name or "\u2014")


def _simulate_and_render(order, *, selected_store, ed_cfg, lang,
                         base_url, username, password):
    """Rejoue un ticket dans le moteur External Discount puis affiche le resultat.
    Factorise pour les 2 modes de recherche (date / numero)."""
    payload = ed.order_to_calculate_payload(order, ed_cfg, store_name=selected_store)
    with st.spinner("\U0001f501 " + t("psim_run", lang) + "\u2026"):
        resp = ed.call_external_discount(payload, ed_cfg)
    if resp.get("result") != "SUCCESS":
        st.error(t("psim_engine_error", lang).format(
            msg=resp.get("additionalResponse", resp.get("errorCondition", "?"))))
        return
    bundle = {"order": order, "payload": payload, "resp": resp,
              "store": selected_store, "date_str": ""}
    st.session_state["psim_last"] = bundle
    _render_results(bundle, lang, ed_cfg, base_url, username, password)


def render_promo_simulator(*, base_url, username, password, selected_store,
                           selected_date, config=None, lang="fr"):
    st.subheader("\U0001f9ee " + t("psim_title", lang))
    st.caption(t("psim_intro", lang))

    ed_cfg = ed.load_ed_config(config or {})
    if ed.is_stub(ed_cfg):
        st.warning("\u26a0\ufe0f " + t("psim_stub_warning", lang))

    # ===== Choix du mode de recherche =====
    mode = st.radio(
        t("psim_search_mode", lang),
        [t("psim_mode_by_date", lang), t("psim_mode_by_docno", lang)],
        horizontal=True, key="psim_mode")

    # =========================================================
    #  MODE 1 — PAR DATE : tableau des tickets du jour + icones
    # =========================================================
    if mode == t("psim_mode_by_date", lang):
        c1, c2 = st.columns([2, 1])
        with c1:
            sim_date = st.date_input(t("date", lang), selected_date, key="psim_date")
        with c2:
            st.write("")
            st.write("")
            load = st.button("\U0001f4c5 " + t("psim_load_day", lang),
                             type="primary", key="psim_load_day_btn")

        date_str = sim_date.strftime("%Y-%m-%d")
        state_key = f"psim_day_{selected_store}_{date_str}"

        if load:
            cache_key = f"promo_orders_{selected_store}_{date_str}"
            raw = load_tab_data(
                cache_key,
                lambda: fetch_orders(base_url, username, password, selected_store, date_str),
                t("psim_load_day", lang),
            )
            st.session_state[state_key] = raw

        raw = st.session_state.get(state_key)
        # Ecran initial propre : aucun traitement tant qu'on n'a pas charge.
        if raw is None:
            st.info(t("psim_press_load", lang))
            return
        if not raw:
            st.warning(t("psim_no_tickets", lang).format(
                d=sim_date.strftime("%d/%m/%Y"), s=selected_store))
            return

        # Resume promos/coupons par ticket (0 appel : OrderLine.promotions[])
        summary = per_ticket_promo_summary(json.dumps(raw))
        n_promo = sum(1 for s in summary.values() if s["has_promo"])
        n_coupon = sum(1 for s in summary.values() if s["has_coupon"])
        total_remise = round(sum(s["remise_totale"] for s in summary.values()), 2)
        total_ca = round(sum(s.get("gross", 0.0) for s in summary.values()), 2)

        # ----- Metriques : total tickets, CA, total remises, promos/coupons -----
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(t("psim_m_tickets", lang), len(summary))
        m2.metric(t("psim_m_ca", lang), f'{total_ca:,.2f} \u20ac'.replace(",", " "))
        m3.metric(t("psim_m_remise", lang), f'{total_remise:,.2f} \u20ac'.replace(",", " "))
        m4.metric(t("psim_m_promo_coupon", lang), f"{n_promo} \U0001f3f7\ufe0f / {n_coupon} \U0001f39f\ufe0f")

        # ----- Tableau interactif AgGrid : tri au clic + selection de ligne -----
        st.caption(t("psim_grid_hint", lang))
        selected_ticket = _render_aggrid_tickets(summary, lang)

        # ----- Bouton d'action : rejouer la ligne selectionnee -----
        run_sel = st.button("\u25b6\ufe0f " + t("psim_run_selected", lang),
                            type="primary", key="psim_day_run",
                            disabled=not selected_ticket)
        if run_sel and selected_ticket:
            order = _find_order(raw, selected_ticket)
            if order is None:
                st.error(t("psim_ticket_not_found", lang).format(
                    d=sim_date.strftime("%d/%m/%Y"), s=selected_store))
                return
            _simulate_and_render(order, selected_store=selected_store, ed_cfg=ed_cfg,
                                 lang=lang, base_url=base_url, username=username,
                                 password=password)
        elif "psim_last" in st.session_state:
            _render_results(st.session_state["psim_last"], lang, ed_cfg,
                            base_url, username, password)
        return

    # =========================================================
    #  MODE 2 — PAR NUMERO D'ORDER : endpoint byDocumentNo
    # =========================================================
    c1, c2 = st.columns([3, 1])
    with c1:
        doc_no = st.text_input(t("psim_docno", lang), key="psim_docno",
                               help=t("psim_docno_help", lang),
                               placeholder="SPT01/0005197")
    with c2:
        st.write("")
        st.write("")
        run = st.button("\u25b6\ufe0f " + t("psim_run", lang), type="primary",
                        key="psim_docno_run")

    if not run:
        if "psim_last" in st.session_state:
            _render_results(st.session_state["psim_last"], lang, ed_cfg,
                            base_url, username, password)
        return

    if not (doc_no or "").strip():
        st.info(t("psim_docno", lang))
        return

    # Recuperation directe par numero (cache DB fige par documentNo)
    with st.spinner("\U0001f501 \u2026"):
        raw = fetch_order_by_document_no(base_url, username, password, doc_no)
    order = _find_order(raw, doc_no)
    # byDocumentNo renvoie l'order recherche : si _find_order ne matche pas
    # (ecart de casse/espaces), prendre le 1er resultat non annule.
    if order is None and raw:
        for o in raw:
            if not (o.get("isCancelled") or o.get("isVoid")):
                order = o
                break
    if order is None:
        st.error(t("psim_docno_not_found", lang).format(d=doc_no))
        return

    _simulate_and_render(order, selected_store=selected_store, ed_cfg=ed_cfg,
                         lang=lang, base_url=base_url, username=username,
                         password=password)

def _render_results(bundle, lang, ed_cfg, base_url, username, password):
    order = bundle["order"]
    payload = bundle["payload"]
    resp = bundle["resp"]

    if resp.get("_stub"):
        st.info("\U0001f9ea " + t("psim_stub_warning", lang))

    st.divider()
    st.markdown("### " + t("psim_ticket_recap", lang))
    _recap_order(order, lang)

    triggered = ed.extract_triggered_discounts(resp)
    available = ed.extract_available_optional(resp)
    ob_promos = ed.extract_ob_engine_promos(payload)

    # Index ligne -> libelle produit pour l'affichage
    line_label = {}
    for ln in order.get("lines", []) or []:
        pinfo = ln.get("product_info", {}) or {}
        line_label[ln.get("id", "")] = pinfo.get("name", "") or ln.get("product", "")

    # ----- Remises declenchees -----
    st.divider()
    st.markdown("### \u2705 " + t("psim_triggered", lang))
    if triggered:
        df = pd.DataFrame([{
            t("psim_col_name", lang): d["name"],
            t("psim_col_searchkey", lang): d["searchKey"],
            t("psim_col_amount", lang): f"{d['amount']:,.2f}".replace(",", " "),
            t("psim_col_line", lang): line_label.get(d["line_id"], d["line_id"] or "\u2014"),
            t("psim_col_coupon", lang): d.get("couponCode", "") or "\u2014",
            t("psim_col_source", lang): d["source"],
        } for d in triggered])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info(t("psim_none_triggered", lang))

    # ----- Promos disponibles non appliquees -----
    st.markdown("### \U0001f4a1 " + t("psim_available", lang))
    if available:
        dfa = pd.DataFrame([{
            t("psim_col_name", lang): p["name"],
            t("psim_col_searchkey", lang): p["searchKey"],
            t("psim_col_desc", lang): p.get("description", ""),
            t("psim_col_amount", lang): p.get("amount") if p.get("amount") is not None else p.get("percentage"),
            t("psim_col_coupon", lang): p.get("couponCode", "") or "\u2014",
        } for p in available])
        st.dataframe(dfa, use_container_width=True, hide_index=True)
    else:
        st.caption(t("psim_none_available", lang))

    # ----- Selection d'une promo pour diagnostic -----
    st.divider()
    st.markdown("### \U0001f50e " + t("psim_why", lang))

    options = []
    for d in triggered:
        options.append(("triggered", d["searchKey"] or d["name"], d))
    for p in available:
        options.append(("available", p["searchKey"] or p["name"], p))
    # Promos OB non redeclenchees : candidates "pourquoi ca ne s'applique pas"
    diff = ed.diff_ob_vs_engine(ob_promos, triggered)
    for p in diff["only_ob"]:
        options.append(("only_ob", p["searchKey"] or p["name"], p))

    if not options:
        st.caption(t("psim_none_triggered", lang))
    else:
        labels = {f"{kind} \u2014 {name}": (kind, name, obj) for kind, name, obj in options}
        choice = st.selectbox(t("psim_select_promo", lang), list(labels.keys()),
                              key="psim_promo_choice")
        kind, name, obj = labels[choice]

        if kind == "triggered":
            n_lines = sum(1 for d in triggered if (d["searchKey"] or d["name"]) == name)
            amt = sum(d["amount"] for d in triggered if (d["searchKey"] or d["name"]) == name)
            st.success(t("psim_why_applied", lang).format(
                amt=f"{amt:,.2f}".replace(",", " "), n=n_lines))
        else:
            st.error(t("psim_why_not_applied", lang))

        # Diagnostic coupon a la demande (si la promo porte un couponCode)
        coupon_code = (obj.get("couponCode") if isinstance(obj, dict) else "") or ""
        if coupon_code:
            if st.button("\U0001f39f\ufe0f " + t("psim_coupon_check", lang) + f" ({coupon_code})",
                         key="psim_coupon_btn"):
                vp = ed.build_validate_coupon_payload(order, coupon_code, ed_cfg,
                                                      store_name=bundle["store"])
                with st.spinner("\u2026"):
                    vr = ed.call_external_discount(vp, ed_cfg)
                vdata = vr.get("data", {})
                st.info(t("psim_coupon_ok", lang).format(
                    c=coupon_code, exists=vdata.get("couponExists"),
                    appl=vdata.get("couponApplicable"),
                    msg=vr.get("additionalResponse", "")))

    # ----- Comparaison OB-engine vs reponse moteur -----
    st.divider()
    with st.expander("\u2696\ufe0f " + t("psim_cmp_title", lang), expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.caption(t("psim_cmp_only_ob", lang))
            st.dataframe(pd.DataFrame(diff["only_ob"]) if diff["only_ob"] else pd.DataFrame(),
                         use_container_width=True, hide_index=True)
        with c2:
            st.caption(t("psim_cmp_only_engine", lang))
            st.dataframe(pd.DataFrame(diff["only_engine"]) if diff["only_engine"] else pd.DataFrame(),
                         use_container_width=True, hide_index=True)
        with c3:
            st.caption(t("psim_cmp_both", lang))
            st.dataframe(pd.DataFrame(diff["both"]) if diff["both"] else pd.DataFrame(),
                         use_container_width=True, hide_index=True)

    # ----- Promos disponibles par produit (GET_ITEM, a la demande) -----
    with st.expander("\U0001f4e6 " + t("psim_item_promos", lang), expanded=False):
        lines = order.get("lines", []) or []
        prod_opts = {}
        for ln in lines:
            pinfo = ln.get("product_info", {}) or {}
            lbl = pinfo.get("name", "") or ln.get("product", "")
            prod_opts[lbl] = ln
        if prod_opts:
            psel = st.selectbox(t("psim_item_select", lang), list(prod_opts.keys()),
                                key="psim_item_sel")
            if st.button("\U0001f50d " + t("psim_item_run", lang), key="psim_item_btn"):
                ip = ed.build_get_item_payload(prod_opts[psel], ed_cfg)
                with st.spinner("\u2026"):
                    ir = ed.call_external_discount(ip, ed_cfg)
                promos = ir.get("data", {}).get("availablePromos", []) or []
                if promos:
                    st.dataframe(pd.DataFrame(promos), use_container_width=True, hide_index=True)
                else:
                    st.caption(t("psim_item_none", lang))

    # ----- JSON bruts (sans appel) -----
    with st.expander("\U0001f9fe " + t("psim_raw", lang), expanded=False):
        st.code(json.dumps(resp, ensure_ascii=False, indent=2), language="json")
    with st.expander("\U0001f4e4 " + t("psim_payload_raw", lang), expanded=False):
        st.code(json.dumps(payload, ensure_ascii=False, indent=2), language="json")
