"""
Onglet "Configuration" — gestion multi-client / multi-magasin (Supabase).

Les clients Openbravo sont stockes dans Supabase (table client_configs),
STRICTEMENT PRIVES par utilisateur (user_id). Le mot de passe API est chiffre
(Fernet) au repos et dechiffre uniquement en memoire pour les appels API.

- Liste des clients de l'utilisateur connecte.
- Ajout / modification / suppression (prive a l'utilisateur).
- Rafraichissement des magasins (fetch_organizations) sur demande.
"""

import json
import streamlit as st

import db
import crypto
from utils import fetch_organizations, parse_organizations
import db_cache
from i18n import t


def _active_name(user_id):
    """Nom du client actif pour cet utilisateur (memorise en session)."""
    return st.session_state.get(f"active_client_{user_id}")


def _set_active(user_id, name):
    st.session_state[f"active_client_{user_id}"] = name


def render_config(*, user_id, lang="fr"):
    st.subheader("\u2699\ufe0f " + t("cfg_title", lang))
    st.caption(t("cfg_intro", lang))

    configs = db.list_client_configs(user_id)
    names = sorted(c["name"] for c in configs)
    by_name = {c["name"]: c for c in configs}

    # ============ Liste des clients existants ============
    st.markdown("### " + t("cfg_existing", lang))
    if not names:
        st.info(t("cfg_none", lang))
    else:
        active = _active_name(user_id)
        for nm in names:
            c = by_name[nm]
            is_active = (nm == active)
            with st.expander(("\u2b50 " if is_active else "") + nm, expanded=False):
                st.write(f"**{t('cfg_endpoint', lang)}** : `{c.get('endpoint','')}`")
                st.write(f"**{t('cfg_username', lang)}** : `{c.get('api_username','')}`")
                stores = c.get("stores", []) or []
                st.write(f"**{t('cfg_stores', lang)}** : {len(stores)}")
                if stores:
                    st.caption(", ".join(stores))

                col1, col2, col3 = st.columns(3)
                # Rafraichir les magasins (1 appel API ; mot de passe dechiffre en memoire)
                if col1.button("\U0001f504 " + t("cfg_refresh_stores", lang),
                               key=f"cfg_refresh_{nm}"):
                    db_cache.delete(f"organizations|{user_id}|{nm}")
                    pw_clair = crypto.decrypt_secret(c.get("api_password_encrypted", ""))
                    with st.spinner(t("cfg_refreshing", lang)):
                        raw = fetch_organizations(c["endpoint"], c["api_username"],
                                                  pw_clair, client_name=f"{user_id}|{nm}")
                        new_stores = parse_organizations(json.dumps(raw))
                    if new_stores:
                        db.update_client_stores(user_id, nm, new_stores)
                        st.success(t("cfg_stores_updated", lang).format(n=len(new_stores)))
                        st.rerun()
                    else:
                        st.warning(t("cfg_stores_empty", lang))
                # Definir comme actif
                if col2.button("\u2b50 " + t("cfg_set_active", lang),
                               key=f"cfg_active_{nm}", disabled=is_active):
                    _set_active(user_id, nm)
                    st.rerun()
                # Supprimer
                if col3.button("\U0001f5d1\ufe0f " + t("cfg_delete", lang),
                               key=f"cfg_delete_{nm}"):
                    db.delete_client_config(user_id, nm)
                    if _active_name(user_id) == nm:
                        _set_active(user_id, None)
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
                # Chiffrer le mot de passe API avant stockage. Si champ laisse
                # vide lors d'une MAJ, on conserve l'ancien mot de passe chiffre.
                existing = by_name.get(nm)
                if pwd:
                    enc = crypto.encrypt_secret(pwd)
                elif existing:
                    enc = existing.get("api_password_encrypted", "")
                else:
                    enc = crypto.encrypt_secret("")
                db.upsert_client_config(user_id, nm, endpoint, username, enc,
                                        stores=(existing or {}).get("stores", []))
                st.success(t("cfg_saved", lang).format(name=nm))
                # Charger les magasins dans la foulee (1 appel)
                pw_clair = crypto.decrypt_secret(enc)
                with st.spinner(t("cfg_refreshing", lang)):
                    raw = fetch_organizations(endpoint, username, pw_clair,
                                              client_name=f"{user_id}|{nm}")
                    new_stores = parse_organizations(json.dumps(raw))
                if new_stores:
                    db.update_client_stores(user_id, nm, new_stores)
                    st.info(t("cfg_stores_updated", lang).format(n=len(new_stores)))
                if not _active_name(user_id):
                    _set_active(user_id, nm)
                st.rerun()
