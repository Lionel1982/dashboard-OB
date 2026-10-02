"""
Connexion a Supabase (Postgres) pour l'app multi-utilisateurs du dashboard.

Centralise l'acces a la base : comptes utilisateurs (table users) et configs
clients Openbravo chiffrees (table client_configs). La cle service_role est lue
depuis st.secrets["supabase"] (jamais sur Git). Le client Supabase est mis en
cache ressource (st.cache_resource) pour ne pas reconstruire la connexion a
chaque run Streamlit.
"""

import logging
import streamlit as st

logger = logging.getLogger("dashboard")


@st.cache_resource(show_spinner=False)
def get_client():
    """Renvoie un client Supabase (singleton par session serveur). Leve une
    erreur claire si les secrets sont absents ou la lib non installee."""
    try:
        from supabase import create_client
    except Exception as e:
        raise RuntimeError(
            "Le paquet 'supabase' n'est pas installe (pip install supabase)."
        ) from e
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["service_key"]
    except Exception as e:
        raise RuntimeError(
            "Secrets Supabase absents : ajoutez [supabase].url et "
            "[supabase].service_key dans les Secrets Streamlit."
        ) from e
    return create_client(url, key)


# ------------------------------------------------------------------
#  UTILISATEURS (table users)
# ------------------------------------------------------------------
def get_user_by_username(username: str):
    """Renvoie la ligne utilisateur (dict) ou None."""
    try:
        res = (get_client().table("users")
               .select("*").eq("username", username).limit(1).execute())
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as e:
        logger.warning("get_user_by_username(%s): %s", username, e)
        return None


def create_user(username: str, password_hash: str, role: str = "user"):
    """Cree un compte utilisateur. Renvoie la ligne creee ou None."""
    try:
        res = (get_client().table("users")
               .insert({"username": username, "password_hash": password_hash,
                        "role": role}).execute())
        return (res.data or [None])[0]
    except Exception as e:
        logger.warning("create_user(%s): %s", username, e)
        return None


def list_users():
    """Liste tous les utilisateurs (pour l'admin)."""
    try:
        res = get_client().table("users").select("id,username,role,created_at").execute()
        return res.data or []
    except Exception as e:
        logger.warning("list_users: %s", e)
        return []


# ------------------------------------------------------------------
#  CONFIGS CLIENTS (table client_configs) — privees par user_id
# ------------------------------------------------------------------
def list_client_configs(user_id: int):
    """Configs clients d'un utilisateur donne (strictement privees)."""
    try:
        res = (get_client().table("client_configs")
               .select("*").eq("user_id", user_id).execute())
        return res.data or []
    except Exception as e:
        logger.warning("list_client_configs(%s): %s", user_id, e)
        return []


def upsert_client_config(user_id: int, name: str, endpoint: str,
                         api_username: str, api_password_encrypted: str,
                         stores=None, env="production"):
    """Ajoute ou met a jour une config client (unique par (user_id, name)).

    env : type d'environnement du client ('production', 'preproduction',
    'test'). Sert a adapter l'UI (couleur du selecteur) et a declencher
    l'alerte de confirmation avant un call sur base globale en production.
    """
    try:
        payload = {
            "user_id": user_id, "name": name, "endpoint": endpoint,
            "api_username": api_username,
            "api_password_encrypted": api_password_encrypted,
            "stores": stores or [],
            "env": env or "production",
        }
        res = (get_client().table("client_configs")
               .upsert(payload, on_conflict="user_id,name").execute())
        return (res.data or [None])[0]
    except Exception as e:
        logger.warning("upsert_client_config(%s,%s): %s", user_id, name, e)
        return None


def delete_client_config(user_id: int, name: str):
    """Supprime une config client d'un utilisateur."""
    try:
        (get_client().table("client_configs")
         .delete().eq("user_id", user_id).eq("name", name).execute())
        return True
    except Exception as e:
        logger.warning("delete_client_config(%s,%s): %s", user_id, name, e)
        return False


def update_client_stores(user_id: int, name: str, stores: list):
    """Met a jour la liste des magasins (cache) d'une config client."""
    try:
        (get_client().table("client_configs")
         .update({"stores": stores})
         .eq("user_id", user_id).eq("name", name).execute())
        return True
    except Exception as e:
        logger.warning("update_client_stores(%s,%s): %s", user_id, name, e)
        return False
