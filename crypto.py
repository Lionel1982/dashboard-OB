"""
Chiffrement/dechiffrement des secrets API (mots de passe Openbravo) au repos.

Utilise Fernet (chiffrement symetrique authentifie, lib cryptography). La cle
Fernet est lue depuis st.secrets["encryption"]["fernet_key"] (jamais en base,
jamais sur Git). Chaque mot de passe API client est chiffre avant stockage en
base Supabase, et dechiffre uniquement en memoire au moment de l'appel API.
"""

import logging
import streamlit as st
from cryptography.fernet import Fernet

logger = logging.getLogger("dashboard")


def _get_fernet():
    """Construit l'objet Fernet depuis la cle des secrets. Leve une erreur
    claire si la cle est absente/invalide."""
    try:
        key = st.secrets["encryption"]["fernet_key"]
    except Exception as e:
        raise RuntimeError(
            "Cle de chiffrement absente : ajoutez [encryption].fernet_key "
            "dans les Secrets Streamlit."
        ) from e
    if isinstance(key, str):
        key = key.encode("utf-8")
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    """Chiffre une chaine (mot de passe API). Renvoie un token texte stockable."""
    if plaintext is None:
        plaintext = ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    """Dechiffre un token produit par encrypt_secret. Renvoie '' si echec
    (token vide/corrompu) sans lever, pour ne pas bloquer l'app."""
    if not token:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.warning("decrypt_secret: echec dechiffrement: %s", e)
        return ""
