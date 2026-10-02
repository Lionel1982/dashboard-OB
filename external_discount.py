"""
Integration au moteur Openbravo "External Discounts" (Client API v3.0 / 26Q3).

But : rejouer un ticket existant dans le moteur de promotions pour obtenir la
VERITE du moteur (quelles remises se declenchent) puis diagnostiquer pourquoi
une remise donnee ne s'applique pas.

Trois webhooks de l'API sont encapsules ici :
  - CALCULATE_DISCOUNTS      -> rejoue le ticket complet, renvoie les remises
  - VALIDATE_EXTERNAL_COUPON -> valide un code coupon (existe ? applicable ?)
  - GET_ITEM_DISCOUNTS       -> promos disponibles pour un produit donne

PRINCIPE DE CONCEPTION : tout l'acces reseau au moteur passe par la SEULE
fonction call_external_discount(). Le jour ou l'endpoint reel est connu
(URL + X-API-KEY fournis par Balink/Openbravo), il n'y a qu'un seul endroit a
cabler. En attendant, un mode STUB rejoue localement a partir des promotions
deja presentes sur l'order (champ lines[].promotions) pour voir l'UX tout de
suite, sans aucun appel reseau.

Config attendue (config.json ou st.secrets["external_discount"]) :
    {
      "external_discount": {
        "url": "https://.../external-discount",   # endpoint HTTP
        "api_key": "....",                         # X-API-KEY
        "terminal_id": "SPT01",                    # terminal.ID (searchKey)
        "operator": "dashboard",                   # terminal.operator
        "touchpoint_type": "POS2 Terminal Type"    # terminal.touchpointType
      }
    }
Si "url" est absent ou vide -> mode STUB automatique.
"""

import json
import logging
import random
import uuid

import requests

logger = logging.getLogger("dashboard")

API_VERSION = "3.0.263990-26Q3.99"

# Actions (valeurs figees de l'API)
ACTION_CALCULATE = "CALCULATE_DISCOUNTS"
ACTION_VALIDATE_COUPON = "VALIDATE_EXTERNAL_COUPON"
ACTION_GET_ITEM = "GET_ITEM_DISCOUNTS"


# ==========================================================================
# CONFIG
# ==========================================================================
def load_ed_config(app_config: dict) -> dict:
    """Extrait la config External Discount depuis la config applicative.

    Cherche d'abord st.secrets["external_discount"], puis la cle
    "external_discount" de config.json. Renvoie un dict vide si rien :
    l'appelant tombera alors en mode STUB.
    """
    try:
        import streamlit as st
        sec = st.secrets.get("external_discount")
        if sec:
            return dict(sec)
    except Exception:
        pass
    if isinstance(app_config, dict):
        ed = app_config.get("external_discount")
        if isinstance(ed, dict):
            return ed
    return {}


def is_stub(ed_cfg: dict) -> bool:
    """Mode stub si aucune URL d'endpoint n'est configuree."""
    return not (ed_cfg or {}).get("url")


# ==========================================================================
# MAPPING  order Openbravo  ->  payload CALCULATE_DISCOUNTS
# ==========================================================================
def _line_taxes(line: dict) -> list:
    """Transforme les taxes d'une ligne order vers le format attendu."""
    out = []
    for tx in line.get("taxes", []) or []:
        out.append({
            "id": tx.get("id", ""),
            "lineNo": tx.get("lineNo"),
            "amount": tx.get("taxAmount", tx.get("amount")),
            "searchKey": tx.get("taxCode") or tx.get("tax") or "",
            "name": tx.get("tax") or tx.get("taxCode") or "",
            "net": tx.get("taxableAmount"),
            "rate": tx.get("rate"),
            "taxBase": tx.get("taxableAmount"),
        })
    return out


def _promotions_from_ob_engine(line: dict) -> list:
    """Expose les promotions DEJA calculees par le moteur OB sur la ligne.

    C'est la cle du diagnostic : on transmet au moteur ce que l'OB engine a
    deja applique (lines[].promotions sur l'order), au format
    promotionsFromOBEngine de l'API. Permet au moteur externe (et a nous) de
    comparer "ce que OB a calcule" vs "ce que le moteur externe renvoie".
    """
    out = []
    for p in line.get("promotions", []) or []:
        out.append({
            "id": p.get("discountId", "") or p.get("id", ""),
            "searchKey": p.get("searchKey", "") or p.get("name", ""),
            "name": p.get("name", ""),
            "discountType": p.get("discountType", ""),
            "value": p.get("searchKey", ""),
            "amount": float(p.get("totalAmount", 0) or 0),
            "unitDiscount": float(p.get("amountPerUnit", 0) or 0),
        })
    return out


def order_to_calculate_payload(order: dict, ed_cfg: dict, *,
                               store_name: str = "", session_id=None) -> dict:
    """Construit le payload CALCULATE_DISCOUNTS a partir d'un order Openbravo.

    priceIncludesTax = True (les prix POS Intersport sont TTC) -> on envoie les
    prix gross. On embarque promotionsFromOBEngine pour le diagnostic.
    """
    org = order.get("organization", "")
    org_name = org.get("name") if isinstance(org, dict) else (org or store_name)
    org_sk = org.get("searchKey") if isinstance(org, dict) else (org or store_name)
    org_id = org.get("id") if isinstance(org, dict) else ""

    bp = order.get("businessPartner", {})
    bp_id = bp.get("id", "") if isinstance(bp, dict) else ""
    bp_sk = bp.get("searchKey", "") if isinstance(bp, dict) else ""

    lines = []
    for ln in order.get("lines", []) or []:
        pinfo = ln.get("product_info", {}) or {}
        lines.append({
            "id": ln.get("id", ""),
            "product": {
                "id": pinfo.get("id", "") or ln.get("product", ""),
                "_identifier": pinfo.get("name", ""),
                "upc": pinfo.get("upcEAN"),
                "searchKey": pinfo.get("searchKey", "") or ln.get("product", ""),
                "standardPrice": ln.get("grossListPrice", ln.get("grossUnitPrice")),
                "listPrice": ln.get("grossListPrice", ln.get("grossUnitPrice")),
                "useExtPromotions": pinfo.get("useExtPromotions", False),
                "uOM": ln.get("uOM", "Unit"),
            },
            "qty": ln.get("orderedQuantity", 0),
            "lineNo": ln.get("lineNo"),
            "baseGrossUnitPrice": ln.get("baseGrossUnitPrice", ln.get("grossUnitPrice")),
            "grossUnitPrice": ln.get("grossUnitPrice"),
            "taxes": _line_taxes(ln),
            "promotionsFromOBEngine": _promotions_from_ob_engine(ln),
        })

    # Coupons presents sur les promotions de l'order (deduits).
    coupons = []
    seen = set()
    for ln in order.get("lines", []) or []:
        for p in ln.get("promotions", []) or []:
            code = p.get("couponCode")
            if code and code not in seen:
                seen.add(code)
                coupons.append({"code": code, "discountGenerated": True})

    payload = {
        "action": ACTION_CALCULATE,
        "terminal": {
            "ID": ed_cfg.get("terminal_id", "") or order.get("terminal", ""),
            "operator": ed_cfg.get("operator", "dashboard"),
            "locale": ed_cfg.get("locale", "fr_FR"),
            "organization": org_sk,
            "touchpointType": ed_cfg.get("touchpoint_type", ""),
        },
        "serviceID": str(random.randint(10 ** 9, 10 ** 10 - 1)),
        "meta": {"apiVersion": API_VERSION, "X-API-KEY": ed_cfg.get("api_key", "")},
        "sessionId": session_id,
        "data": {
            "id": order.get("id", "") or order.get("documentNo", ""),
            "organization": {"id": org_id, "_identifier": org_name, "searchKey": org_sk},
            "lines": lines,
            "discountsFromUser": {
                "bytotalManualPromotions": [],
                "manualPromotions": [],
                "optionalPromotions": [],
                "coupons": coupons,
            },
            "priceIncludesTax": True,
            "orderType": 0,
            "isLayaway": bool(order.get("isLayaway", False)),
            "isQuotation": False,
            "businessPartner": {"id": bp_id, "searchKey": bp_sk},
            "currency": order.get("currency", ""),
            "currency$_identifier": "EUR",
        },
    }
    return payload


def build_validate_coupon_payload(order: dict, coupon_code: str, ed_cfg: dict,
                                  *, store_name: str = "", session_id=None) -> dict:
    """Payload VALIDATE_EXTERNAL_COUPON : reutilise le mapping ticket + le code."""
    base = order_to_calculate_payload(order, ed_cfg, store_name=store_name,
                                      session_id=session_id)
    base["action"] = ACTION_VALIDATE_COUPON
    base["data"]["couponCode"] = coupon_code
    return base


def build_get_item_payload(line_or_product: dict, ed_cfg: dict, *,
                           session_id=None, offset=0, limit=300) -> dict:
    """Payload GET_ITEM_DISCOUNTS pour un produit (depuis une ligne order)."""
    pinfo = line_or_product.get("product_info", line_or_product) or {}
    return {
        "action": ACTION_GET_ITEM,
        "terminal": {
            "ID": ed_cfg.get("terminal_id", ""),
            "operator": ed_cfg.get("operator", "dashboard"),
            "locale": ed_cfg.get("locale", "fr_FR"),
            "touchpointType": ed_cfg.get("touchpoint_type", ""),
        },
        "serviceID": str(random.randint(10 ** 9, 10 ** 10 - 1)),
        "meta": {"apiVersion": API_VERSION, "X-API-KEY": ed_cfg.get("api_key", "")},
        "sessionId": session_id,
        "data": {
            "offset": offset,
            "limit": limit,
            "product": {
                "id": pinfo.get("id", ""),
                "_identifier": pinfo.get("name", ""),
                "searchKey": pinfo.get("searchKey", ""),
                "upc": pinfo.get("upcEAN"),
                "standardPrice": line_or_product.get("grossListPrice"),
                "listPrice": line_or_product.get("grossListPrice"),
                "useExtPromotions": pinfo.get("useExtPromotions", True),
            },
        },
    }


# ==========================================================================
# TRANSPORT  (UN SEUL point a cabler)
# ==========================================================================
def call_external_discount(payload: dict, ed_cfg: dict, *, timeout: int = 30) -> dict:
    """Envoie un payload au moteur External Discount et renvoie la reponse JSON.

    Transport HTTP request/response (POST JSON). Si aucune URL n'est
    configuree, bascule en STUB local (voir _stub_response) : aucun appel
    reseau, reponse simulee a partir des donnees deja presentes dans le
    payload (promotionsFromOBEngine).

    *** A CABLER *** : si l'install Openbravo expose le moteur autrement
    (WebSocket, fonction JS), c'est ICI qu'il faut adapter l'envoi. Le reste
    de l'app n'a pas a changer.
    """
    if is_stub(ed_cfg):
        logger.info("External Discount en mode STUB (aucune URL configuree).")
        return _stub_response(payload)

    url = ed_cfg["url"]
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-API-KEY": ed_cfg.get("api_key", ""),
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            logger.error("External Discount %s - %s", resp.status_code, resp.text[:500])
            return {"messageType": "RESPONSE", "result": "FAILURE",
                    "errorCondition": str(resp.status_code),
                    "additionalResponse": resp.text[:500], "data": {}}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"messageType": "RESPONSE", "result": "FAILURE", "errorCondition": "TIMEOUT",
                "additionalResponse": "Le moteur de promotions met trop de temps a repondre.",
                "data": {}}
    except Exception as e:
        logger.exception("Echec appel External Discount")
        return {"messageType": "RESPONSE", "result": "FAILURE", "errorCondition": "EXCEPTION",
                "additionalResponse": str(e), "data": {}}


# ==========================================================================
# STUB  (rejoue localement a partir de promotionsFromOBEngine)
# ==========================================================================
def _stub_response(payload: dict) -> dict:
    """Fabrique une reponse plausible SANS reseau.

    - CALCULATE : chaque promotionsFromOBEngine devient un discount applique ;
      aucune promo "disponible mais non appliquee" (le stub ne connait pas les
      regles backend).
    - VALIDATE_COUPON : le coupon existe et est applicable s'il figure deja sur
      une promo de l'order, sinon existe=False.
    - GET_ITEM : renvoie une liste vide (pas de connaissance des regles).
    """
    action = payload.get("action")
    data = payload.get("data", {})
    sid = payload.get("sessionId") or str(uuid.uuid4())

    if action == ACTION_CALCULATE:
        lines_out = []
        for ln in data.get("lines", []):
            discs = []
            for p in ln.get("promotionsFromOBEngine", []) or []:
                discs.append({
                    "name": p.get("name", ""),
                    "searchKey": p.get("searchKey", ""),
                    "amt": p.get("amount", 0),
                    "_stub": True,
                })
            if discs:
                lines_out.append({"id": ln.get("id", ""), "discounts": discs})
        return {
            "messageType": "RESPONSE", "result": "SUCCESS", "errorCondition": "0000",
            "additionalResponse": "STUB : rejoue a partir des promotions de l'order.",
            "data": {"sessionId": sid, "ticket": {
                "id": data.get("id", ""),
                "discountengine": {
                    "discountsFromUser": {"bytotalManualPromotions": [], "manualPromotions": []},
                    "lines": lines_out,
                },
                "availableOptionalPromotions": [],
            }},
            "_stub": True,
        }

    if action == ACTION_VALIDATE_COUPON:
        code = data.get("couponCode", "")
        known = {c.get("code") for c in data.get("discountsFromUser", {}).get("coupons", [])}
        exists = code in known
        return {
            "messageType": "RESPONSE", "result": "SUCCESS", "errorCondition": "0000",
            "additionalResponse": ("STUB : coupon present sur l'order."
                                   if exists else "STUB : coupon inconnu de l'order."),
            "data": {"sessionId": sid, "couponCode": code,
                     "couponExists": exists, "couponApplicable": exists},
            "_stub": True,
        }

    if action == ACTION_GET_ITEM:
        return {
            "messageType": "RESPONSE", "result": "SUCCESS", "errorCondition": "0000",
            "additionalResponse": "STUB : regles produit non disponibles hors ligne.",
            "data": {"sessionId": sid, "availablePromos": []},
            "_stub": True,
        }

    return {"messageType": "RESPONSE", "result": "FAILURE", "errorCondition": "UNKNOWN_ACTION",
            "additionalResponse": f"Action inconnue : {action}", "data": {}, "_stub": True}


# ==========================================================================
# EXTRACTION / NORMALISATION DES REPONSES
# ==========================================================================
def extract_triggered_discounts(calc_response: dict) -> list:
    """Liste a plat des remises DECLENCHEES par le moteur (reponse CALCULATE).

    Renvoie [{line_id, name, searchKey, amount, source}] ; source='line' pour
    les remises de ligne, 'bytotal'/'manual' pour les remises niveau ticket.
    """
    out = []
    data = (calc_response or {}).get("data", {}) or {}
    ticket = data.get("ticket", {}) or {}
    engine = ticket.get("discountengine", {}) or {}

    for ln in engine.get("lines", []) or []:
        lid = ln.get("id", "")
        for d in ln.get("discounts", []) or []:
            out.append({
                "line_id": lid,
                "name": d.get("name", ""),
                "searchKey": d.get("searchKey", ""),
                "amount": float(d.get("amt", d.get("obdiscAmt", 0)) or 0),
                "couponCode": d.get("couponCode", ""),
                "source": "line",
            })
    dfu = engine.get("discountsFromUser", {}) or {}
    for d in dfu.get("bytotalManualPromotions", []) or []:
        out.append({"line_id": "", "name": d.get("name", ""),
                    "searchKey": d.get("searchKey", ""),
                    "amount": float(d.get("disctTotalamountdisc", 0) or 0),
                    "couponCode": d.get("couponCode", ""), "source": "bytotal"})
    for d in dfu.get("manualPromotions", []) or []:
        out.append({"line_id": "", "name": d.get("name", ""),
                    "searchKey": d.get("searchKey", ""),
                    "amount": float(d.get("obdiscAmt", 0) or 0),
                    "couponCode": d.get("couponCode", ""), "source": "manual"})
    return out


def extract_available_optional(calc_response: dict) -> list:
    """Liste des promos DISPONIBLES mais non appliquees (availableOptionalPromotions)."""
    data = (calc_response or {}).get("data", {}) or {}
    ticket = data.get("ticket", {}) or {}
    engine = ticket.get("discountengine", {}) or {}
    out = []
    for p in engine.get("availableOptionalPromotions", []) or []:
        out.append({
            "name": p.get("name", ""),
            "searchKey": p.get("searchKey", ""),
            "description": p.get("description", ""),
            "amount": p.get("amount"),
            "percentage": p.get("percentage"),
            "discountApplied": bool(p.get("discountApplied", False)),
            "couponCode": p.get("couponCode", ""),
        })
    return out


def extract_ob_engine_promos(payload: dict) -> list:
    """Liste a plat des promos que l'OB engine avait calculees (depuis le payload).

    Sert de reference pour la comparaison OB-engine vs reponse moteur externe.
    """
    out = []
    for ln in payload.get("data", {}).get("lines", []) or []:
        for p in ln.get("promotionsFromOBEngine", []) or []:
            out.append({
                "line_id": ln.get("id", ""),
                "name": p.get("name", ""),
                "searchKey": p.get("searchKey", ""),
                "amount": float(p.get("amount", 0) or 0),
            })
    return out


def diff_ob_vs_engine(ob_promos: list, triggered: list) -> dict:
    """Compare les promos OB-engine (reference) et les remises declenchees.

    Renvoie {only_ob, only_engine, both} en se basant sur (searchKey|name).
    - only_ob    : OB avait la promo, le moteur externe ne la declenche PAS
                   -> candidat "pourquoi ca ne s'applique plus / pas".
    - only_engine: le moteur externe declenche une promo absente d'OB.
    - both       : presente des deux cotes (avec ecart de montant eventuel).
    """
    def key(d):
        return (d.get("searchKey") or d.get("name") or "").strip().lower()

    ob = {key(p): p for p in ob_promos if key(p)}
    eng = {key(p): p for p in triggered if key(p)}
    only_ob = [ob[k] for k in ob.keys() - eng.keys()]
    only_engine = [eng[k] for k in eng.keys() - ob.keys()]
    both = []
    for k in ob.keys() & eng.keys():
        both.append({"name": ob[k].get("name") or eng[k].get("name"),
                     "searchKey": k,
                     "amount_ob": ob[k].get("amount", 0),
                     "amount_engine": eng[k].get("amount", 0)})
    return {"only_ob": only_ob, "only_engine": only_engine, "both": both}
