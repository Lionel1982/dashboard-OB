"""
Onglet "Creation de produit" — formulaire simple pour creer un produit via
l'ImportService Openbravo (POST, import asynchrone).

Hybride : categorie en menu deroulant (chargee via ProductCategory), TVA + UOM
en saisie texte avec valeurs par defaut. Dialog de confirmation avant la
creation reelle (ecriture dans Openbravo).
"""

import json
import streamlit as st

from utils import (
    NOT_LOADED, load_tab_data,
    fetch_product_categories, parse_product_categories, create_product,
)
from i18n import t


def _do_create(base_url, username, password, *, client_id, organization,
               search_key, name, product_category, tax_category, uom,
               description, product_type, lang):
    """Execute la creation + affiche le resultat."""
    with st.spinner(t("pc_creating", lang)):
        res = create_product(
            base_url, username, password,
            client_id=client_id, organization=organization,
            search_key=search_key, name=name,
            product_category=product_category, tax_category=tax_category,
            uom=uom, description=description, product_type=product_type)
    if res.get("ok"):
        rid = res.get("request_id")
        st.success(t("pc_created", lang).format(name=name)
                   + (f" (request id: {rid})" if rid else ""))
    else:
        # Afficher les erreurs renvoyees par l'API
        errs = res.get("errors") or []
        msg = "; ".join(
            (e.get("title") or e.get("detail") or str(e)) for e in errs
        ) if errs else f"HTTP {res.get('status')}"
        st.error(t("pc_create_failed", lang).format(msg=msg))


def render_product_create(*, base_url, username, password, client_id,
                          selected_store, env="production", lang="fr"):
    st.subheader("\U0001f4e6 " + t("pc_title", lang))
    st.caption(t("pc_intro", lang))

    # ----- Chargement des categories (menu deroulant) -----
    cache_key = f"prodcats_{client_id}"
    cats_raw = load_tab_data(
        cache_key,
        lambda: fetch_product_categories(base_url, username, password,
                                         client_name=str(client_id)),
        t("pc_load_cats", lang),
        db_keys=[f"product_categories|{client_id}"],
    )
    if cats_raw is NOT_LOADED:
        return
    cats = parse_product_categories(json.dumps(cats_raw))
    if not cats:
        st.warning(t("pc_no_cats", lang))

    cat_labels = [f"{nm} ({sk})" for sk, nm in cats]
    cat_by_label = {f"{nm} ({sk})": sk for sk, nm in cats}

    # ----- Formulaire -----
    with st.form("pc_form"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input(t("pc_name", lang), key="pc_name")
            search_key = st.text_input(t("pc_searchkey", lang), key="pc_sk",
                                       help=t("pc_searchkey_help", lang))
            cat_label = st.selectbox(t("pc_category", lang), cat_labels,
                                     key="pc_cat") if cat_labels else None
        with c2:
            uom = st.text_input(t("pc_uom", lang), value="unit", key="pc_uom",
                                help=t("pc_uom_help", lang))
            tax_category = st.text_input(t("pc_tax", lang), value="",
                                         key="pc_tax", help=t("pc_tax_help", lang))
            product_type = st.selectbox(t("pc_type", lang),
                                        ["I", "S"], key="pc_type",
                                        help=t("pc_type_help", lang))
        description = st.text_area(t("pc_desc", lang), key="pc_desc")
        submitted = st.form_submit_button("\U0001f4e6 " + t("pc_submit", lang),
                                          type="primary")

    if submitted:
        # Validation minimale des champs requis
        if not (name and search_key and cat_label and uom and tax_category):
            st.error(t("pc_missing", lang))
            return
        # Stocker les valeurs pour le dialog de confirmation
        st.session_state["pc_pending"] = {
            "name": name, "search_key": search_key,
            "product_category": cat_by_label.get(cat_label, ""),
            "cat_label": cat_label, "uom": uom, "tax_category": tax_category,
            "product_type": product_type, "description": description,
        }

    # ----- Dialog de confirmation (ecriture reelle) -----
    pending = st.session_state.get("pc_pending")
    if pending:
        is_prod = (env or "production").lower().startswith("prod")

        @st.dialog(t("pc_confirm_title", lang))
        def _confirm():
            if is_prod:
                st.warning("\u26a0\ufe0f " + t("pc_confirm_prod", lang))
            st.write(t("pc_confirm_body", lang))
            st.markdown(
                f"- **{t('pc_name', lang)}** : {pending['name']}\n"
                f"- **{t('pc_searchkey', lang)}** : {pending['search_key']}\n"
                f"- **{t('pc_category', lang)}** : {pending['cat_label']}\n"
                f"- **{t('pc_uom', lang)}** : {pending['uom']}\n"
                f"- **{t('pc_tax', lang)}** : {pending['tax_category']}\n"
                f"- **{t('pc_type', lang)}** : {pending['product_type']}\n"
                f"- **{t('cfg_select_client', lang)}** : {client_id} / {selected_store}")
            cc1, cc2 = st.columns(2)
            if cc1.button("\u2705 " + t("pc_confirm_yes", lang),
                          type="primary", key="pc_confirm_yes"):
                st.session_state.pop("pc_pending", None)
                _do_create(base_url, username, password,
                           client_id=client_id, organization=selected_store,
                           search_key=pending["search_key"], name=pending["name"],
                           product_category=pending["product_category"],
                           tax_category=pending["tax_category"], uom=pending["uom"],
                           description=pending["description"],
                           product_type=pending["product_type"], lang=lang)
                st.rerun()
            if cc2.button("\u274c " + t("pc_confirm_no", lang), key="pc_confirm_no"):
                st.session_state.pop("pc_pending", None)
                st.rerun()

        _confirm()
