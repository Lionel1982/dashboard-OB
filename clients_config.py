"""
Gestion multi-client / multi-magasin du dashboard Openbravo.

Chaque CLIENT = une installation Openbravo distincte (endpoint + identifiants
API propres). Chaque client a plusieurs MAGASINS (organisations), recuperes
dynamiquement via l'API (fetch_organizations) et mis en cache.

Stockage : clients_config.json a la racine du projet (GITIGNORE car contient
des mots de passe en clair). Structure :

    {
      "active": "Intersport",
      "clients": {
        "Intersport": {
          "endpoint": "https://intersport.cloud.openbravo.com/openbravo/ws",
          "username": "lkern-api",
          "PW": "....",
          "stores": ["Magasin Felgueiras"]   # cache local des magasins connus
        },
        ...
      }
    }

Retrocompatibilite : si clients_config.json est absent mais que l'ancien
config.json (mono-client) existe, on migre automatiquement Intersport au
premier lancement. st.secrets reste prioritaire sur Streamlit Cloud.
"""

import os
import json
import logging

logger = logging.getLogger("dashboard")

CLIENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "clients_config.json")
LEGACY_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "config.json")

_PW = "password"  # nom de la cle mot de passe dans le JSON


def _empty():
    return {"active": None, "clients": {}}


def load_clients() -> dict:
    """Charge la configuration multi-client. Migre l'ancien config.json si besoin.
    Ne leve jamais : renvoie au pire une structure vide."""
    # 1) Fichier multi-client
    if os.path.exists(CLIENTS_FILE):
        try:
            with open(CLIENTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "clients" in data:
                return data
        except Exception as e:
            logger.warning("load_clients: lecture %s echouee: %s", CLIENTS_FILE, e)

    # 2) Migration depuis l'ancien config.json mono-client
    if os.path.exists(LEGACY_CONFIG):
        try:
            with open(LEGACY_CONFIG, "r", encoding="utf-8") as f:
                old = json.load(f)
            name = old.get("client_id") or old.get("client") or "Client"
            data = {"active": name, "clients": {name: {
                "endpoint": old.get("endpoint", ""),
                "username": old.get("username", ""),
                _PW: old.get(_PW, old.get("pwd", "")),
                "stores": list(old.get("stores", [])),
            }}}
            save_clients(data)
            logger.info("Migration config.json -> clients_config.json (client=%s)", name)
            return data
        except Exception as e:
            logger.warning("load_clients: migration config.json echouee: %s", e)

    return _empty()


def save_clients(data: dict) -> bool:
    """Sauvegarde la configuration multi-client. Ne leve jamais."""
    try:
        with open(CLIENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.warning("save_clients: %s", e)
        return False


def list_client_names(data: dict) -> list:
    return sorted((data or {}).get("clients", {}).keys())


def get_active_client_name(data: dict) -> str:
    data = data or {}
    active = data.get("active")
    names = list_client_names(data)
    if active in (data.get("clients") or {}):
        return active
    return names[0] if names else None


def get_client(data: dict, name: str) -> dict:
    """Renvoie la config d'un client sous forme normalisee (endpoint/username/
    password/stores), ou None."""
    c = (data or {}).get("clients", {}).get(name)
    if not c:
        return None
    return {
        "name": name,
        "endpoint": (c.get("endpoint", "") or "").rstrip("/"),
        "username": c.get("username", ""),
        _PW: c.get(_PW, ""),
        "stores": list(c.get("stores", [])),
    }


def set_active_client(data: dict, name: str) -> dict:
    data = data or _empty()
    if name in (data.get("clients") or {}):
        data["active"] = name
        save_clients(data)
    return data


def add_or_update_client(data: dict, name, endpoint, username, pw_value,
                         stores=None) -> dict:
    """Ajoute ou met a jour un client. Conserve les stores existants si non
    fournis."""
    data = data or _empty()
    data.setdefault("clients", {})
    existing = data["clients"].get(name, {})
    data["clients"][name] = {
        "endpoint": (endpoint or "").rstrip("/"),
        "username": username or "",
        _PW: pw_value if pw_value is not None else existing.get(_PW, ""),
        "stores": list(stores) if stores is not None else list(existing.get("stores", [])),
    }
    if not data.get("active"):
        data["active"] = name
    save_clients(data)
    return data


def delete_client(data: dict, name: str) -> dict:
    data = data or _empty()
    if name in (data.get("clients") or {}):
        del data["clients"][name]
        if data.get("active") == name:
            remaining = list_client_names(data)
            data["active"] = remaining[0] if remaining else None
        save_clients(data)
    return data


def update_client_stores(data: dict, name: str, stores: list) -> dict:
    """Met a jour la liste des magasins (cache local) d'un client."""
    data = data or _empty()
    if name in (data.get("clients") or {}):
        data["clients"][name]["stores"] = list(stores)
        save_clients(data)
    return data
