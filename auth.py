"""
Authentification de l'app multi-utilisateurs (comptes crees par l'admin).

- Mots de passe utilisateurs haches avec bcrypt (jamais en clair en base).
- Ecran de login BLOQUANT : tant que l'utilisateur n'est pas connecte, l'appel
  a require_login() affiche le formulaire et arrete le rendu (st.stop()).
- La session est portee par st.session_state (auth_user : dict de l'utilisateur
  connecte, sans le hash).

Pas d'inscription ouverte : seul l'admin cree des comptes (script creation ou
futur ecran admin).
"""

import logging
import bcrypt
import streamlit as st

import db

logger = logging.getLogger("dashboard")


def hash_password(plain: str) -> str:
    """Hache un mot de passe utilisateur avec bcrypt. Renvoie le hash (str)."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verifie un mot de passe contre son hash bcrypt. Ne leve jamais."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as e:
        logger.warning("verify_password: %s", e)
        return False


def current_user():
    """Renvoie l'utilisateur connecte (dict) ou None."""
    return st.session_state.get("auth_user")


def logout():
    """Deconnecte l'utilisateur courant (vide la session liee a l'auth)."""
    for k in ("auth_user",):
        st.session_state.pop(k, None)


def _login_form():
    """Affiche le formulaire de connexion. Renvoie True si connexion reussie."""
    st.title("\U0001f512 Connexion")
    st.caption("Dashboard Openbravo \u2014 acces reserve. Contactez l'administrateur "
               "pour obtenir un compte.")
    with st.form("login_form"):
        username = st.text_input("Identifiant", key="login_user")
        pwd = st.text_input("Mot de passe", type="password", key="login_pw")
        ok = st.form_submit_button("Se connecter", type="primary")
    if ok:
        user = db.get_user_by_username((username or "").strip())
        if user and verify_password(pwd or "", user.get("password_hash", "")):
            # On ne garde PAS le hash en session.
            st.session_state["auth_user"] = {
                "id": user["id"], "username": user["username"],
                "role": user.get("role", "user"),
            }
            st.rerun()
        else:
            st.error("Identifiant ou mot de passe incorrect.")
    return False


def require_login():
    """Garde d'authentification BLOQUANTE. A appeler tout en haut de l'app.
    Si non connecte : affiche le formulaire et arrete le rendu (st.stop()).
    Si connecte : renvoie l'utilisateur courant (dict)."""
    user = current_user()
    if user:
        return user
    _login_form()
    st.stop()
