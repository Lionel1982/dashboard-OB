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

import pandas as pd
import streamlit as st

from utils import load_tab_data, fetch_orders, parse_orders
from i18n import t
import external_discount as ed


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


def render_promo_simulator(*, base_url, username, password, selected_store,
                           selected_date, config=None, lang="fr"):
    st.subheader("\U0001f9ee " + t("psim_title", lang))
    st.caption(t("psim_intro", lang))

    ed_cfg = ed.load_ed_config(config or {})
    if ed.is_stub(ed_cfg):
        st.warning("\u26a0\ufe0f " + t("psim_stub_warning", lang))

    # ----- Saisie -----
    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        ticket_no = st.text_input(t("psim_ticket_no", lang), key="psim_ticket_no",
                                  help=t("psim_ticket_help", lang),
                                  placeholder="SPT01/0005197")
    with c2:
        sim_date = st.date_input(t("date", lang), selected_date, key="psim_date")
    with c3:
        st.write("")
        st.write("")
        run = st.button("\u25b6\ufe0f " + t("psim_run", lang), type="primary", key="psim_run_btn")

    if not run:
        if "psim_last" in st.session_state:
            _render_results(st.session_state["psim_last"], lang, ed_cfg,
                            base_url, username, password)
        return

    if not (ticket_no or "").strip():
        st.info(t("psim_ticket_no", lang))
        return

    # ----- Recuperation de l'order (0 appel si cache, sinon 1) -----
    date_str = sim_date.strftime("%Y-%m-%d")
    cache_key = f"promo_orders_{selected_store}_{date_str}"  # meme cle que l'onglet Promotions
    raw = load_tab_data(
        cache_key,
        lambda: fetch_orders(base_url, username, password, selected_store, date_str),
        t("psim_run", lang),
    )
    order = _find_order(raw, ticket_no)
    if order is None:
        st.error(t("psim_ticket_not_found", lang).format(
            d=sim_date.strftime("%d/%m/%Y"), s=selected_store))
        return

    # ----- Construction du payload + appel moteur (1 appel) -----
    payload = ed.order_to_calculate_payload(order, ed_cfg, store_name=selected_store)
    with st.spinner("\U0001f501 " + t("psim_run", lang) + "\u2026"):
        resp = ed.call_external_discount(payload, ed_cfg)

    if resp.get("result") != "SUCCESS":
        st.error(t("psim_engine_error", lang).format(
            msg=resp.get("additionalResponse", resp.get("errorCondition", "?"))))
        return

    bundle = {"order": order, "payload": payload, "resp": resp,
              "store": selected_store, "date_str": date_str}
    st.session_state["psim_last"] = bundle
    _render_results(bundle, lang, ed_cfg, base_url, username, password)


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
