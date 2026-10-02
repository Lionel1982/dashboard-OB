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
import auth
from utils import fetch_organizations, parse_organizations
import db_cache
from i18n import t


def _active_name(user_id):
    """Nom du client actif pour cet utilisateur (memorise en session)."""
    return st.session_state.get(f"active_client_{user_id}")


def _set_active(user_id, name):
    st.session_state[f"active_client_{user_id}"] = name


def render_config(*, user_id, role="user", lang="fr"):
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
            _env = (c.get("env") or "production").lower()
            _env_badge = {"production": "\U0001f7e3 production",
                          "preproduction": "\U0001f535 preproduction",
                          "test": "\U0001f535 test"}.get(_env, _env)
            with st.expander(("\u2b50 " if is_active else "") + nm + f"  \u2014 {_env_badge}",
                             expanded=False):
                st.write(f"**{t('cfg_env', lang)}** : {_env_badge}")
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
        env = st.selectbox(t("cfg_env", lang),
                           ["production", "preproduction", "test"],
                           key="cfg_new_env", help=t("cfg_env_help", lang))
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
                                        stores=(existing or {}).get("stores", []),
                                        env=env)
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


    # ============================================================
    #  GESTION DES COMPTES (ADMIN UNIQUEMENT)
    # ============================================================
    if role == "admin":
        st.divider()
        st.markdown("### \U0001f465 " + t("cfg_accounts", lang))
        st.caption(t("cfg_accounts_intro", lang))

        # --- Creer un compte ---
        with st.form("cfg_create_account", clear_on_submit=True):
            new_user = st.text_input(t("cfg_acc_username", lang), key="cfg_acc_user")
            new_pw = st.text_input(t("cfg_acc_pw", lang), type="password", key="cfg_acc_pw")
            new_pw2 = st.text_input(t("cfg_acc_pw2", lang), type="password", key="cfg_acc_pw2")
            new_role = st.selectbox(t("cfg_acc_role", lang), ["user", "admin"],
                                    key="cfg_acc_role")
            create = st.form_submit_button("\U0001f4be " + t("cfg_acc_create", lang),
                                           type="primary")
            if create:
                if not (new_user or "").strip() or not new_pw:
                    st.error(t("cfg_acc_missing", lang))
                elif new_pw != new_pw2:
                    st.error(t("cfg_acc_pw_mismatch", lang))
                elif db.get_user_by_username(new_user.strip()):
                    st.error(t("cfg_acc_exists", lang).format(name=new_user.strip()))
                else:
                    h = auth.hash_password(new_pw)
                    row = db.create_user(new_user.strip(), h, role=new_role)
                    if row:
                        st.success(t("cfg_acc_created", lang).format(
                            name=new_user.strip(), role=new_role))
                        st.rerun()
                    else:
                        st.error(t("cfg_acc_fail", lang))

        # --- Liste des comptes existants ---
        users = db.list_users()
        if users:
            st.caption(t("cfg_acc_existing", lang))
            st.dataframe(
                [{"id": u["id"], t("cfg_acc_username", lang): u["username"],
                  t("cfg_acc_role", lang): u.get("role", "user"),
                  t("cfg_acc_created", lang).split("{")[0].strip() or "cree":
                      (u.get("created_at", "") or "")[:19]}
                 for u in users],
                use_container_width=True, hide_index=True)
