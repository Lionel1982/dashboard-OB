"""
Onglet "Configuration" — gestion multi-client / multi-magasin.

- Liste des clients configures (endpoint, utilisateur API).
- Ajout / modification / suppression d'un client (endpoint + user + mot de passe).
- Rafraichissement des magasins (organisations) d'un client via l'API
  (fetch_organizations), mis en cache. Appel API uniquement sur demande
  (bouton), conformement a la regle "ne recuperer les orgs qu'a la premiere
  connexion puis sur rafraichissement explicite".
"""

import streamlit as st

import clients_config as cc
from utils import fetch_organizations, parse_organizations
import db_cache
from i18n import t

_PW = "password"


def render_config(*, lang="fr"):
    st.subheader("\u2699\ufe0f " + t("cfg_title", lang))
    st.caption(t("cfg_intro", lang))

    data = st.session_state.get("clients_data") or cc.load_clients()
    names = cc.list_client_names(data)

    # ============ Liste des clients existants ============
    st.markdown("### " + t("cfg_existing", lang))
    if not names:
        st.info(t("cfg_none", lang))
    else:
        for nm in names:
            c = cc.get_client(data, nm)
            is_active = (nm == cc.get_active_client_name(data))
            with st.expander(("\u2b50 " if is_active else "") + nm, expanded=False):
                st.write(f"**{t('cfg_endpoint', lang)}** : `{c['endpoint']}`")
                st.write(f"**{t('cfg_username', lang)}** : `{c['username']}`")
                st.write(f"**{t('cfg_stores', lang)}** : {len(c['stores'])}")
                if c["stores"]:
                    st.caption(", ".join(c["stores"]))

                col1, col2, col3 = st.columns(3)
                # Rafraichir les magasins (1 appel API)
                if col1.button("\U0001f504 " + t("cfg_refresh_stores", lang),
                               key=f"cfg_refresh_{nm}"):
                    db_cache.delete(f"organizations|{nm}")
                    with st.spinner(t("cfg_refreshing", lang)):
                        raw = fetch_organizations(c["endpoint"], c["username"],
                                                  c[_PW], client_name=nm)
                        stores = parse_organizations(__import__("json").dumps(raw))
                    if stores:
                        data = cc.update_client_stores(data, nm, stores)
                        st.session_state["clients_data"] = data
                        st.success(t("cfg_stores_updated", lang).format(n=len(stores)))
                        st.rerun()
                    else:
                        st.warning(t("cfg_stores_empty", lang))
                # Definir comme actif
                if col2.button("\u2b50 " + t("cfg_set_active", lang),
                               key=f"cfg_active_{nm}", disabled=is_active):
                    data = cc.set_active_client(data, nm)
                    st.session_state["clients_data"] = data
                    st.rerun()
                # Supprimer
                if col3.button("\U0001f5d1\ufe0f " + t("cfg_delete", lang),
                               key=f"cfg_delete_{nm}"):
                    data = cc.delete_client(data, nm)
                    st.session_state["clients_data"] = data
                    st.success(t("cfg_deleted", lang).format(name=nm))
                    st.rerun()

    st.divider()

    # ============ Ajout / modification d'un client ============
    st.markdown("### " + t("cfg_add", lang))
    with st.form("cfg_add_form", clear_on_submit=False):
        nm = st.text_input(t("cfg_name", lang), key="cfg_new_name",
                           placeholder="Intersport")
        endpoint = st.text_input(t("cfg_endpoint", lang), key="cfg_new_endpoint",
                                 placeholder="https://....cloud.openbravo.com/openbravo/ws")
        username = st.text_input(t("cfg_username", lang), key="cfg_new_user",
                                 placeholder="xxx-api")
        pwd = st.text_input(t("cfg_pw", lang), type="password", key="cfg_new_pw")
        submitted = st.form_submit_button("\U0001f4be " + t("cfg_save", lang),
                                          type="primary")
        if submitted:
            if not (nm and endpoint and username):
                st.error(t("cfg_missing", lang))
            else:
                data = cc.add_or_update_client(data, nm, endpoint, username, pwd or None)
                st.session_state["clients_data"] = data
                st.success(t("cfg_saved", lang).format(name=nm))
                # Charger les magasins dans la foulee (1 appel)
                with st.spinner(t("cfg_refreshing", lang)):
                    c = cc.get_client(data, nm)
                    raw = fetch_organizations(c["endpoint"], c["username"],
                                              c[_PW], client_name=nm)
                    stores = parse_organizations(__import__("json").dumps(raw))
                if stores:
                    data = cc.update_client_stores(data, nm, stores)
                    st.session_state["clients_data"] = data
                    st.info(t("cfg_stores_updated", lang).format(n=len(stores)))
                st.rerun()
