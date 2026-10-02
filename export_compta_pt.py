#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
 export_compta_pt.py  —  Export comptable Portugais (Openbravo → CSV/XLS)
============================================================================

Génère un fichier d'écritures comptables (même structure que l'exemple
Dbg_VisuCompta*.xls) à partir de l'API Openbravo, EN SE BASANT SUR LE
CASHUP comme source principale :

    /org.openbravo.api.ExportService/CashUp                (source principale)
    /org.openbravo.api.ExportService/Order/byOrgOrderDateRange  (optionnel)

MAILLE = 1 PIÈCE PAR (JOUR × TERMINAL)
    Toutes les sessions de caisse (CashUp) d'un même businessDate et d'un
    même terminal sont regroupées dans une seule pièce comptable.

STRUCTURE DE L'EXPORT :
    Colonnes : Code J. | Date | Pièce | Libelle | Compte | Debit | Credit | Solde | Echeance

    - Journal "C2" (VENTES) — depuis CashUpTaxInformation (agrégé par taux) :
        * PRODUIT par taux de TVA (taxableAmount)   -> Credit
        * TVA par taux (amount)                     -> Credit
        * Compte de caisse (contrepartie, TTC)      -> Debit
    - Journal "OD" (ENCAISSEMENTS) — depuis paymentMethods :
        * Ventilation par moyen de paiement (sales - returns)
                                            -> Debit compte trésorerie
        * Contrepartie compte de caisse             -> Credit
    - Journal "OD" (MOUVEMENTS DE CAISSE) — depuis cashManagementEvents :
        * Dépôts / retraits d'espèces (deposit / drop)

    Chaque pièce est ÉQUILIBRÉE (Débit = Crédit).

NOTE SUR LES SERVICES :
    CashUpTaxInformation ne distingue pas produit / service (il ne fournit
    que le taux de TVA). La dissociation produit/service nécessiterait
    l'API Product (type de produit) et n'est donc PAS faite ici : une seule
    ligne "PRODUIT <taux>" est générée par taux.

⚠️  À VÉRIFIER AVANT UTILISATION :
    1. Le PLAN COMPTABLE PORTUGAIS (SNC) est PRÉ-REMPLI (section « MAPPING
       DES COMPTES »). Vérifiez les sous-comptes avec votre comptable.
    2. Le mapping des MOYENS DE PAIEMENT (PAYMENT_METHOD_MAP) utilise les
       libellés réels renvoyés par l'API CashUp (Cash, Manual bank card,
       Multibanco, MBWay, Transfer, Traveller Chèques, Cartão-presente…).
    3. Les TAUX DE TVA sont lus dynamiquement (champ `rate` de l'API).

Ce script est prévu pour être lancé DEPUIS VOTRE MACHINE.

Usage :
    python export_compta_pt.py --org "Magasin Felgueiras" \
        --from 2026-08-01 --to 2026-08-31 --out export_compta.csv

    (par défaut : mois précédent, magasin de config.json / DEFAULT_STORE)
============================================================================
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Cache SQLite persistant partage avec le dashboard (module autonome, sans
# dependance Streamlit). Les plages entierement passees ne sont plus rappelees.
# On garantit que le dossier du script est dans sys.path pour que l'import
# fonctionne meme si le script est lance depuis un autre repertoire.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_cache
db_cache.init_db()

# Moteur comptable partage (source de verite unique, commun au dashboard).
import compta_engine

# Sortie immédiate (pas de bufferisation) pour voir la progression en direct,
# notamment sous Windows où stdout peut être bufferisé.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logger = logging.getLogger("export_compta_pt")

# ============================================================================
#  MAPPING DES COMPTES  —  PLAN COMPTABLE PORTUGAIS SNC (standard, pré-rempli)
# ============================================================================
# Plan comptable SNC portugais STANDARD (pré-rempli). Ces comptes suivent
# la nomenclature officielle SNC (Sistema de Normalização Contabilística) :
#   - Classe 71  : Vendas (ventes de marchandises)
#   - Compte 2433 : IVA - Imposto apurado / IVA liquidado (TVA collectée)
#   - Classe 11  : Caixa (espèces)
#   - Classe 12  : Depósitos à ordem (dépôts à vue : CB / Multibanco)
#   - Compte 2768 : Outros credores (cartes cadeaux / bons)
#   - Compte 6888 / 7888 : autres charges / produits (écarts de caisse)
# ⚠️ Vérifiez ces numéros avec votre comptable : le SNC laisse le détail
# des sous-comptes (chiffres après le compte principal) à la discrétion de
# chaque entreprise. Adaptez si votre plan interne diffère.

# --- Codes journaux -----------------------------------------------------------
CODE_JOURNAL_VENTES = "C2"          # code journal ventes (à adapter : ex "VD")
CODE_JOURNAL_OD     = "OD"          # code journal opérations diverses / banque

# Compte de PRODUIT par taux de TVA. Clé = taux (float). Valeur = compte SNC.
COMPTE_PRODUIT_PAR_TAUX = {
    23.0: "71111",   # Vendas — taxa normal (Continente 23%)
    13.0: "71112",   # Vendas — taxa intermédia (13%)
    6.0:  "71113",   # Vendas — taxa reduzida (6%)
    0.0:  "71114",   # Vendas — isentas / taxa 0
}
COMPTE_PRODUIT_DEFAUT = "71111"     # repli : taxa normal

# Compte de TVA collectée par taux.
COMPTE_TVA_PAR_TAUX = {
    23.0: "2433211",   # IVA liquidado — operações gerais, taxa normal 23%
    13.0: "2433212",   # IVA liquidado — taxa intermédia 13%
    6.0:  "2433213",   # IVA liquidado — taxa reduzida 6%
    0.0:  None,        # pas de ligne TVA pour taux 0 / exonéré
}
COMPTE_TVA_DEFAUT = "2433211"       # repli : IVA taxa normal

# --- Comptes de caisse / trésorerie ------------------------------------------
# Compte de caisse (contrepartie du journal ventes). Peut dépendre du terminal.
# Clé = nom du terminal (searchKey) renvoyé par l'API. "__default__" = repli.
COMPTE_CAISSE_PAR_TERMINAL = {
    "__default__": "111",   # Caixa (espèces)
}

# --- Journal OD : ventilation par MOYEN DE PAIEMENT --------------------------
# Clés = libellés RÉELS renvoyés par l'API CashUp (paymentMethods[].paymentMethod),
# en minuscules. "__default__" = repli.
PAYMENT_METHOD_MAP = {
    # libellé API (minuscules)      (compte SNC,  libellé écriture)
    "cash":                   ("111",   "NUMERÁRIO / ESPÈCES"),      # Caixa
    "manual bank card":       ("12111", "TPE / CARTÃO MANUAL"),      # Depósitos à ordem
    "carte bancaire":         ("12111", "CARTÃO BANCÁRIO"),
    "credit card":            ("12111", "CARTÃO DE CRÉDITO"),
    "credit card manual":     ("12111", "CARTÃO MANUAL"),
    "multibanco":             ("12111", "MULTIBANCO"),
    "mbway":                  ("12112", "MB WAY"),
    "transfer":               ("12113", "TRANSFERÊNCIA"),
    "check":                  ("12114", "CHEQUE"),
    "differed check":         ("12114", "CHEQUE DIFERIDO"),
    "traveller chèques":      ("12115", "TRAVELLER CHÈQUES"),
    "traveller cheques":      ("12115", "TRAVELLER CHÈQUES"),
    "n time oney":            ("12116", "ONEY (N FOIS)"),
    "edenred":                ("2768",  "EDENRED"),
    "cadhoc":                 ("2768",  "CADHOC"),
    "cartão-presente":        ("2768",  "CARTÃO OFERTA (UTILISATION)"),   # Outros credores
    "cartão-presente grátis": ("2768",  "CARTÃO OFERTA GRÁTIS"),
    "gift card":              ("2768",  "CARTÃO OFERTA"),
    "gift certificate":       ("2768",  "VALE OFERTA"),
    "credit note":            ("2768",  "NOTA DE CRÉDITO"),
    "voucher":                ("2768",  "VALE / VOUCHER"),
    "__default__":            ("12119", "OUTROS RECEBIMENTOS"),      # autre encaissement
}

# Comptes d'écart de règlement (arrondis / différences de caisse)
COMPTE_ECART_NEGATIF = "6888"   # Outros gastos e perdas — écart négatif (charge)
COMPTE_ECART_POSITIF = "7888"   # Outros rendimentos e ganhos — écart positif (produit)

# Compte pour les lignes SERVICE (S) issues des Orders (cartes cadeaux / bons).
# Les services sont majoritairement des "CARTAO - PRESENTE" (cartes cadeaux) à
# taux 0%, ABSENTS de CashUpTaxInformation. Ils sont donc ajoutés en lignes
# séparées depuis les Orders, sur un compte de type Outros credores.
COMPTE_SERVICE = "2768"   # Outros credores (cartes cadeaux / services hors-taxe)

# Seuil (en €) au-delà duquel un écart Orders vs CashUp est signalé (par
# jour × terminal × taux). En-dessous : ignoré (arrondis).
SEUIL_ECART_ORDERS_CASHUP = 1.0

# ============================================================================
#  CONFIG / API
# ============================================================================
DEFAULT_STORE = "Magasin Felgueiras"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DATE_FMT_OUT = "%d/%m/%Y"
ECHEANCE_DEFAUT = "30/12/1899"   # valeur constante observée dans l'exemple

CSV_HEADER = ["Code J.", "Date", "Pièce", "Libelle", "Compte",
              "Debit", "Credit", "Solde", "Echeance"]


def load_config(path=CONFIG_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _create_http_session():
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=1,
                    status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _fetch_paginated(base_url, endpoint, params, username, password):
    """Récupère toutes les pages d'un endpoint ExportService, avec progression."""
    url = f"{base_url}/org.openbravo.api.ExportService/{endpoint}"
    headers = {"Accept": "application/json"}
    all_data, session = [], _create_http_session()
    current_params = params
    page = 0
    t0 = time.time()
    logger.info("→ Appel API [%s] params=%s", endpoint,
                {k: v for k, v in (params or {}).items()})
    while url:
        page += 1
        tp = time.time()
        logger.info("   … page %d : requête en cours %s", page, url)
        resp = session.get(url, params=current_params, auth=(username, password),
                           headers=headers, timeout=60)
        current_params = None
        dt = time.time() - tp
        if resp.status_code != 200:
            logger.error("API %s — %s", resp.status_code, resp.text[:500])
            raise RuntimeError(f"Erreur API {resp.status_code} sur {url}")
        data = resp.json()
        if isinstance(data, list):
            batch = data
            all_data.extend(batch)
            url = None
        else:
            batch = (data.get("data")
                     or data.get("response", {}).get("data", []))
            all_data.extend(batch)
            links = data.get("links")
            url = links.get("next") if isinstance(links, dict) else None
        logger.info("   ✓ page %d : %d élément(s) en %.1fs "
                    "(cumul %d, %.1fs) %s",
                    page, len(batch), dt, len(all_data), time.time() - t0,
                    "→ page suivante…" if url else "→ terminé")
    logger.info("← [%s] %d élément(s) au total en %.1fs (%d page(s))",
                endpoint, len(all_data), time.time() - t0, page)
    return all_data


def fetch_orders(cfg, store, date_from, date_to):
    # Cache DB-first : une plage entierement passee (dateTo < aujourd'hui cote
    # magasin) est lue depuis la DB sans rappeler l'API.
    cache_key = f"export_orders|{store}|{date_from}|{date_to}"
    return db_cache.fetch_with_cache(
        cache_key,
        lambda: _fetch_paginated(
            cfg["endpoint"], "Order/byOrgOrderDateRange",
            {"organization": store, "dateFrom": date_from, "dateTo": date_to},
            cfg["username"], cfg["password"]),
        frozen=db_cache.is_frozen_range(date_from, date_to),
        label=f"export orders {store} {date_from}..{date_to}",
    )


def _order_business_date(o):
    """Date de rattachement d'un Order : businessDate, repli orderDate."""
    for k in ("businessDate", "orderDate", "creationDate"):
        raw = o.get(k)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(str(raw)[:19]).date()
        except (ValueError, TypeError):
            try:
                return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
    return None


def summarize_orders(orders):
    """Réduit les Orders à un résumé COMPACT par (date, terminal) :
      - services : {label -> montant HT cumulé}  (lignes productType == 'S')
      - base_by_rate_type : {(rate, 'I'|'S') -> base HT}  (pour le contrôle
        d'écart Orders vs CashUp)
    Gère isReturn (signe négatif) ; ignore annulés/void.
    Retourne : dict {(date, terminal) -> {'services': {...},
                                          'base': {(rate,type): montant}}}.
    """
    summary = defaultdict(lambda: {"services": defaultdict(float),
                                   "base": defaultdict(float)})
    ignored_nodate = 0
    for o in orders:
        if o.get("isCancelled") or o.get("isVoid"):
            continue
        d = _order_business_date(o)
        if d is None:
            ignored_nodate += 1
            continue
        term = _safe_str(o.get("terminal")) or "?"
        sign = -1 if o.get("isReturn") else 1
        key = (d, term)
        for ln in (o.get("lines") or []):
            pinfo = ln.get("product_info") or {}
            ptype = pinfo.get("productType", "I")
            # base HT par taux (depuis taxes[]) pour le contrôle
            taxes = ln.get("taxes") or []
            if taxes:
                for t in taxes:
                    try:
                        rate = round(float(str(t.get("rate")).replace("%", "")
                                           .replace(",", ".")), 2)
                    except (TypeError, ValueError):
                        rate = 0.0
                    base = sign * _to_float(t.get("taxableAmount"))
                    summary[key]["base"][(rate, ptype)] += base
                    if ptype == "S":
                        lbl = (pinfo.get("name") or "SERVICE").strip()
                        summary[key]["services"][lbl] += base
            else:
                # pas de taxes détaillées : repli sur netAmount, taux 0
                base = sign * _to_float(ln.get("netAmount"))
                summary[key]["base"][(0.0, ptype)] += base
                if ptype == "S":
                    lbl = (pinfo.get("name") or "SERVICE").strip()
                    summary[key]["services"][lbl] += base
    if ignored_nodate:
        logger.warning("%d Order(s) sans date exploitable ignoré(s) dans le résumé",
                       ignored_nodate)
    return summary


def fetch_cashup(cfg, store, date_from, date_to):
    """Récupère les CashUp. Essaie plusieurs variantes de paramètres de date
    (l'endpoint /CashUp accepte a priori organization ; le filtrage par date
    est appliqué côté client sur businessDate si l'API ne filtre pas)."""
    # L'appel reel (essaie plusieurs variantes de params jusqu'a succes).
    def _do_fetch():
        last_err = None
        for endpoint, params in [
            ("CashUp", {"organization": store,
                        "dateFrom": date_from, "dateTo": date_to}),
            ("CashUp", {"organization": store}),
        ]:
            try:
                return _fetch_paginated(cfg["endpoint"], endpoint, params,
                                        cfg["username"], cfg["password"])
            except Exception as e:
                last_err = e
                logger.warning("CashUp %s a échoué : %s", params, e)
        logger.error("Impossible de récupérer les CashUp : %s", last_err)
        return []

    # Cache DB-first : plage entierement passee -> lecture DB, aucun appel API.
    cache_key = f"export_cashup|{store}|{date_from}|{date_to}"
    return db_cache.fetch_with_cache(
        cache_key,
        _do_fetch,
        frozen=db_cache.is_frozen_range(date_from, date_to),
        label=f"export cashup {store} {date_from}..{date_to}",
    )


# ============================================================================
#  HELPERS
# ============================================================================
def _safe_str(val):
    if isinstance(val, dict):
        return (val.get("name") or val.get("searchKey")
                or val.get("description") or val.get("id") or "")
    return str(val) if val is not None else ""


def _to_float(val):
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return 0.0


def _rate_of(tax_info):
    """Taux de TVA DYNAMIQUE depuis une entrée CashUpTaxInformation."""
    r = tax_info.get("rate")
    if r is not None:
        try:
            return round(float(str(r).replace("%", "").replace(",", ".")), 2)
        except (TypeError, ValueError):
            pass
    # repli : extraire depuis le nom "IVA 23%"
    name = str(tax_info.get("name") or tax_info.get("tax") or "")
    digits = "".join(ch for ch in name if ch.isdigit() or ch == ".")
    try:
        return round(float(digits), 2) if digits else None
    except ValueError:
        return None


def compte_produit(rate):
    if rate in COMPTE_PRODUIT_PAR_TAUX:
        return COMPTE_PRODUIT_PAR_TAUX[rate]
    logger.warning("Taux produit %s absent du mapping -> compte défaut %s",
                   rate, COMPTE_PRODUIT_DEFAUT)
    return COMPTE_PRODUIT_DEFAUT


def compte_tva(rate):
    if rate in COMPTE_TVA_PAR_TAUX:
        return COMPTE_TVA_PAR_TAUX[rate]
    logger.warning("Taux TVA %s absent du mapping -> compte défaut %s",
                   rate, COMPTE_TVA_DEFAUT)
    return COMPTE_TVA_DEFAUT


def compte_caisse(terminal_name):
    return COMPTE_CAISSE_PAR_TERMINAL.get(
        terminal_name, COMPTE_CAISSE_PAR_TERMINAL["__default__"])


def map_payment(label, unknown_set=None):
    key = (label or "").strip().lower()
    if key in PAYMENT_METHOD_MAP:
        return PAYMENT_METHOD_MAP[key]
    if unknown_set is not None and label:
        unknown_set.add(label)
    return PAYMENT_METHOD_MAP["__default__"]


# ============================================================================
#  ÉCRITURES
# ============================================================================
class Ecriture:
    __slots__ = ("code_j", "date", "piece", "libelle", "compte",
                 "debit", "credit")

    def __init__(self, code_j, d, piece, libelle, compte, debit=0.0, credit=0.0):
        self.code_j = code_j
        self.date = d
        self.piece = piece
        self.libelle = libelle
        self.compte = compte
        self.debit = round(debit, 2)
        self.credit = round(credit, 2)


def _cashup_business_date(cu):
    """businessDate (YYYY-MM-DD) -> date. Repli sur creationDate."""
    for k in ("businessDate", "creationDate", "closeDateTime", "localCreationDate"):
        raw = cu.get(k)
        if not raw:
            continue
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(str(raw)[:len(fmt.replace("%Y","2026")
                                        .replace("%m","01").replace("%d","01")
                                        .replace("%H","00").replace("%M","00")
                                        .replace("%S","00"))], fmt).date()
            except (ValueError, TypeError):
                continue
        try:
            return datetime.fromisoformat(str(raw)[:19]).date()
        except (ValueError, TypeError):
            continue
    return None


def group_cashups(cashups, date_from=None, date_to=None):
    """Regroupe les CashUp par (businessDate, terminal).
    Filtre optionnel sur la plage [date_from, date_to] (bornes incluses).
    Retourne un OrderedDict trié : (date, terminal) -> liste de cashups."""
    df = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None
    dt = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None
    groups = defaultdict(list)
    ignored = 0
    for cu in cashups:
        d = _cashup_business_date(cu)
        if d is None:
            ignored += 1
            continue
        if (df and d < df) or (dt and d > dt):
            continue
        terminal = _safe_str(cu.get("terminal")) or "?"
        groups[(d, terminal)].append(cu)
    if ignored:
        logger.warning("%d CashUp sans date exploitable ignoré(s)", ignored)
    return OrderedDict(sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])))


def build_group_entries(day, terminal, cashups, unknown_pm, order_info=None):
    """Construit les écritures d'une pièce (jour × terminal).
    order_info (optionnel) : résumé des Orders pour ce (day, terminal) au format
    {'services': {label: montant}, 'base': {(rate,type): montant}} — utilisé pour
    (1) ajouter les lignes SERVICE (cartes cadeaux) absentes du CashUp et
    (2) contrôler l'écart Orders vs CashUp par taux.
    """
    ecr, warn = [], []
    d_str = day.strftime(DATE_FMT_OUT)
    # Numéro de pièce : TERMINAL + date compacte (ex SPT03-20260812)
    piece = f"{terminal}-{day.strftime('%Y%m%d')}"
    caisse = compte_caisse(terminal)

    # ---- Agrégation par taux (ventes) sur toutes les sessions du groupe ----
    base_par_taux = defaultdict(float)   # taxableAmount (HT)
    tva_par_taux = defaultdict(float)    # amount (TVA)
    # ---- Agrégation des moyens de paiement ----
    pay_counted = defaultdict(float)     # totalCounted par moyen (info)
    pay_net = defaultdict(float)         # totalSales - totalReturns par moyen
    # ---- Mouvements de caisse ----
    cash_events = []                     # (type, name, amount)

    for cu in cashups:
        for t in (cu.get("CashUpTaxInformation") or []):
            rate = _rate_of(t)
            if rate is None:
                warn.append(f"{piece}: taux TVA introuvable ({t.get('name')})")
                rate = 0.0
            # ordertype : '0' = vente, '1' = retour, '2' = vente différée /
            # layaway, '3' = ligne technique à zéro / void.
            # On ne garde que 0 (vente, crédit) et 1 (retour, déduit) : cela
            # égale exactement grossSales - grossReturns (validé 252/252
            # sessions). Les types 2/3 sont IGNORÉS (mais loggés s'ils portent
            # un montant non nul, car ils sont encaissés sans être du CA taxé).
            otype = str(t.get("ordertype"))
            if otype == "0":
                sign = 1
            elif otype == "1":
                sign = -1
            else:
                montant = _to_float(t.get("taxableAmount")) + _to_float(t.get("amount"))
                if montant:
                    warn.append(f"{piece}: ligne ordertype={otype} ignorée "
                                f"({t.get('name')}, {montant:+.2f}) — "
                                f"encaissée mais hors CA taxé")
                continue
            base_par_taux[rate] += sign * _to_float(t.get("taxableAmount"))
            tva_par_taux[rate] += sign * _to_float(t.get("amount"))
        for pm in (cu.get("paymentMethods") or []):
            label = pm.get("paymentMethod") or ""
            net = _to_float(pm.get("totalSales")) - _to_float(pm.get("totalReturns"))
            pay_net[label] += net
            pay_counted[label] += _to_float(pm.get("totalCounted"))
            for ev in (pm.get("cashManagementEvents") or []):
                amt = _to_float(ev.get("amount"))
                if amt:
                    cash_events.append((ev.get("type"),
                                        ev.get("name") or ev.get("cashManagementEventSearchKey"),
                                        label, amt))

    # ---- Journal C2 : PRODUIT (credit) + TVA (credit) ----
    total_ttc = 0.0
    for rate in sorted(base_par_taux):
        ht = round(base_par_taux[rate], 2)
        if ht > 0:
            ecr.append(Ecriture(CODE_JOURNAL_VENTES, d_str, piece,
                                 f"PRODUIT {rate:g}", compte_produit(rate),
                                 credit=ht))
            total_ttc += ht
        elif ht < 0:
            # retours nets > ventes pour ce taux : contre-passation au débit
            ecr.append(Ecriture(CODE_JOURNAL_VENTES, d_str, piece,
                                 f"PRODUIT {rate:g} (net retours)",
                                 compte_produit(rate), debit=abs(ht)))
            total_ttc += ht
    for rate in sorted(tva_par_taux):
        tva = round(tva_par_taux[rate], 2)
        cpt = compte_tva(rate)
        if cpt and tva > 0:
            ecr.append(Ecriture(CODE_JOURNAL_VENTES, d_str, piece,
                                 f"TVA {rate:g}", cpt, credit=tva))
            total_ttc += tva
        elif cpt and tva < 0:
            ecr.append(Ecriture(CODE_JOURNAL_VENTES, d_str, piece,
                                 f"TVA {rate:g} (net retours)", cpt,
                                 debit=abs(tva)))
            total_ttc += tva

    total_ttc = round(total_ttc, 2)
    # Contrepartie caisse (= ventes nettes TTC). Débit si positif, sinon crédit.
    if total_ttc > 0:
        ecr.append(Ecriture(CODE_JOURNAL_VENTES, d_str, piece,
                             terminal, caisse, debit=total_ttc))
    elif total_ttc < 0:
        ecr.append(Ecriture(CODE_JOURNAL_VENTES, d_str, piece,
                             terminal, caisse, credit=abs(total_ttc)))

    # ---- Journal OD : ventilation par moyen de paiement (sales - returns) ----
    total_regle = 0.0
    for label in sorted(pay_net):
        montant = round(pay_net[label], 2)
        if not montant:
            continue
        cpt, lib = map_payment(label, unknown_pm)
        if montant >= 0:
            ecr.append(Ecriture(CODE_JOURNAL_OD, d_str, piece,
                                 lib, cpt, debit=montant))
        else:
            # remboursement net (retours > ventes) : sens inversé
            ecr.append(Ecriture(CODE_JOURNAL_OD, d_str, piece,
                                 f"{lib} (net retours)", cpt, credit=abs(montant)))
        total_regle += montant
    total_regle = round(total_regle, 2)
    if total_regle:
        # contrepartie caisse : crédit si total positif, sinon débit
        if total_regle >= 0:
            ecr.append(Ecriture(CODE_JOURNAL_OD, d_str, piece,
                                 terminal, caisse, credit=total_regle))
        else:
            ecr.append(Ecriture(CODE_JOURNAL_OD, d_str, piece,
                                 terminal, caisse, debit=abs(total_regle)))

    # Contrôle : ventilation paiements ≈ ventes TTC ?
    # Les cartes cadeaux / services (0% TVA) sont ENCAISSÉS (dans paiements)
    # mais ABSENTS des ventes taxées (CashUpTaxInformation). On retranche donc
    # le montant des services du même groupe avant de juger l'écart.
    services_grp = 0.0
    if order_info:
        services_grp = round(sum(order_info.get("services", {}).values()), 2)
    # paiements attendus ≈ ventes taxées TTC + services encaissés
    ecart_vente = round(total_ttc + services_grp - total_regle, 2)
    if abs(ecart_vente) >= 0.01:
        detail = f" (dont services/cartes cadeaux {services_grp:+.2f} déjà pris en compte)" if services_grp else ""
        warn.append(f"{piece}: écart ventes+services({round(total_ttc + services_grp, 2)}) "
                    f"vs paiements({total_regle}) = {ecart_vente}{detail}")

    # ---- Journal OD : mouvements de caisse (dépôts / retraits d'espèces) ----
    # Convention exemple FR : sortie d'espèces (versement banque / retrait) ->
    # débit banque, crédit caisse. Un 'deposit' (dépôt en banque) diminue la
    # caisse ; un 'drop' (prélèvement) idem. On garde le libellé d'origine.
    for typ, name, pm_label, amt in cash_events:
        lib = (name or f"MOUVEMENT {typ}").upper()
        # compte de contrepartie : banque (versement) via mapping "__default__"
        # ou compte dédié. Ici on porte le mouvement sur la banque par défaut.
        cpt_bque, _ = PAYMENT_METHOD_MAP.get("transfer",
                                             PAYMENT_METHOD_MAP["__default__"])
        # deposit = versement des espèces à la banque -> débit banque / crédit caisse
        ecr.append(Ecriture(CODE_JOURNAL_OD, d_str, piece, lib,
                             cpt_bque, debit=amt))
        ecr.append(Ecriture(CODE_JOURNAL_OD, d_str, piece,
                             f"{terminal} (espèces)", caisse, credit=amt))

    # ---- Journal C2 : SERVICES (cartes cadeaux) depuis les Orders ----
    # Les services (productType 'S', ex "CARTAO - PRESENTE") sont à 0% et
    # ABSENTS de CashUpTaxInformation. On les ajoute en lignes distinctes,
    # ÉQUILIBRÉES en elles-mêmes (crédit 2768 = produit service / vente carte,
    # débit caisse en contrepartie), pour informer sans casser l'équilibre de
    # la pièce déjà bâtie sur le CashUp.
    if order_info:
        for lbl, montant in sorted(order_info.get("services", {}).items()):
            m = round(montant, 2)
            if not m:
                continue
            libelle = f"SERVICE {lbl}".strip()
            if m >= 0:
                ecr.append(Ecriture(CODE_JOURNAL_VENTES, d_str, piece,
                                     libelle, COMPTE_SERVICE, credit=m))
                ecr.append(Ecriture(CODE_JOURNAL_VENTES, d_str, piece,
                                     terminal, caisse, debit=m))
            else:
                ecr.append(Ecriture(CODE_JOURNAL_VENTES, d_str, piece,
                                     f"{libelle} (retour)", COMPTE_SERVICE,
                                     debit=abs(m)))
                ecr.append(Ecriture(CODE_JOURNAL_VENTES, d_str, piece,
                                     terminal, caisse, credit=abs(m)))

    # ---- Contrôle d'écart Orders vs CashUp (par taux) ----
    # Compare la base HT taxée des Orders (type 'I', hors services) à la base
    # du CashUp, taux par taux. Avertit au-delà du seuil ; n'altère pas les
    # écritures (base CashUp conservée).
    if order_info:
        # base Orders par taux, produits (I) uniquement (les S sont à 0% hors CashUp)
        ord_base_by_rate = defaultdict(float)
        for (rate, ptype), val in order_info.get("base", {}).items():
            if ptype != "S":
                ord_base_by_rate[rate] += val
        rates = set(base_par_taux) | set(ord_base_by_rate)
        for rate in sorted(rates):
            cb = round(base_par_taux.get(rate, 0.0), 2)
            ob = round(ord_base_by_rate.get(rate, 0.0), 2)
            ecart = round(ob - cb, 2)
            if abs(ecart) >= SEUIL_ECART_ORDERS_CASHUP:
                warn.append(f"{piece} taux {rate:g}%: base Orders={ob} vs "
                            f"CashUp={cb} (écart {ecart:+.2f})")

    return ecr, warn


# ============================================================================
#  SOLDE CUMULÉ + ÉCRITURE FINALE
# ============================================================================
def compute_rows(entries):
    """Solde cumulé (debit - credit) réinitialisé à chaque nouvelle pièce."""
    rows = []
    solde = 0.0
    current_piece = None
    for e in entries:
        if e.piece != current_piece:
            solde = 0.0
            current_piece = e.piece
        solde = round(solde + e.debit - e.credit, 2)
        rows.append([
            e.code_j, e.date, e.piece, e.libelle, e.compte,
            f"{e.debit:.2f}", f"{e.credit:.2f}", f"{solde:.2f}",
            ECHEANCE_DEFAUT,
        ])
    return rows


def write_csv(rows, out_path, sep=";"):
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=sep)
        w.writerow(CSV_HEADER)
        w.writerows(rows)
    logger.info("Écrit %d lignes -> %s", len(rows), out_path)


def write_xls(rows, out_path):
    try:
        from openpyxl import Workbook
    except ImportError:
        logger.warning("openpyxl absent : export XLS ignoré.")
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet 1"
    ws.append(CSV_HEADER)
    for r in rows:
        ws.append([r[0], r[1], r[2], r[3], r[4],
                   float(r[5]), float(r[6]), float(r[7]), r[8]])
    wb.save(out_path)
    logger.info("Écrit %d lignes -> %s", len(rows), out_path)


# ============================================================================
#  MAIN
# ============================================================================
def default_period():
    today = date.today()
    first_this = today.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    return first_prev.isoformat(), last_prev.isoformat()


def main():
    ap = argparse.ArgumentParser(description="Export comptable Portugais Openbravo (base CashUp)")
    ap.add_argument("--org", default=None, help="Nom du magasin (organization)")
    ap.add_argument("--from", dest="date_from", default=None, help="Date début YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", default=None, help="Date fin YYYY-MM-DD")
    ap.add_argument("--out", default="export_compta.csv", help="Fichier de sortie (.csv ou .xlsx)")
    ap.add_argument("--sep", default=";", help="Séparateur CSV (défaut ';')")
    ap.add_argument("--dump-json", default=None, help="Dossier où sauvegarder les payloads bruts (debug)")
    ap.add_argument("--no-orders", action="store_true",
                    help="Ne pas interroger les Orders (pas de dissociation "
                         "services ni de contrôle d'écart)")
    ap.add_argument("-v", "--verbose", action="store_true", help="Logs détaillés (DEBUG)")
    args = ap.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logging.getLogger().setLevel(logging.DEBUG)

    cfg = load_config()
    store = args.org or (cfg.get("stores", [DEFAULT_STORE])[0]
                         if isinstance(cfg.get("stores"), list) else DEFAULT_STORE)
    df_, dt_ = default_period()
    date_from = args.date_from or df_
    date_to = args.date_to or dt_

    logger.info("Magasin=%s  Période=%s -> %s  (base : CashUp)", store, date_from, date_to)
    n_steps = 3 if args.no_orders else 4

    logger.info("=== ÉTAPE 1/%d : récupération des CASHUP ===", n_steps)
    cashups = fetch_cashup(cfg, store, date_from, date_to)
    logger.info("%d CashUp récupéré(s)", len(cashups))

    if args.verbose and cashups:
        logger.debug("Structure 1er CashUp : clés = %s", list(cashups[0].keys()))
        if cashups[0].get("CashUpTaxInformation"):
            logger.debug("  CashUpTaxInformation[0] = %s", cashups[0]["CashUpTaxInformation"][0])
        if cashups[0].get("paymentMethods"):
            logger.debug("  paymentMethods[0] keys = %s", list(cashups[0]["paymentMethods"][0].keys()))

    # ---- Orders (pour services + contrôle d'écart) ----
    orders_raw = []
    if args.no_orders:
        logger.info("=== ÉTAPE 2/%d : Orders ignorés (--no-orders) ===", n_steps)
    else:
        logger.info("=== ÉTAPE 2/%d : récupération des ORDERS "
                    "(services + contrôle) ===", n_steps)
        orders_raw = fetch_orders(cfg, store, date_from, date_to)
        logger.info("%d commande(s) récupérée(s)", len(orders_raw))
        if args.dump_json:
            os.makedirs(args.dump_json, exist_ok=True)
            with open(os.path.join(args.dump_json, "orders.json"), "w",
                      encoding="utf-8") as f:
                json.dump(orders_raw, f, ensure_ascii=False, indent=2)
            logger.info("Payload Orders sauvegardé dans %s", args.dump_json)

    if args.dump_json:
        os.makedirs(args.dump_json, exist_ok=True)
        with open(os.path.join(args.dump_json, "cashup.json"), "w", encoding="utf-8") as f:
            json.dump(cashups, f, ensure_ascii=False, indent=2)
        logger.info("Payload CashUp sauvegardé dans %s", args.dump_json)

    logger.info("=== ÉTAPE %d/%d : regroupement + écritures + soldes "
                "(moteur partagé compta_engine) ===", n_steps, n_steps)
    # Passe les Orders BRUTS au moteur partagé : il fait summarize_orders,
    # group_cashups, build_group_entries, pièces Orders-only, et compute_rows.
    # SOURCE DE VÉRITÉ UNIQUE commune au dashboard.
    orders_for_engine = [] if args.no_orders else (orders_raw or [])
    rows, all_warn = compta_engine.build_accounting_rows(
        cashups, orders_for_engine, date_from, date_to)
    if not rows:
        logger.warning("Aucune écriture générée. Vérifiez la période / le magasin / "
                       "le payload (--dump-json pour inspecter).")

    ext = os.path.splitext(args.out)[1].lower()
    if ext in (".xlsx", ".xls"):
        write_xls(rows, args.out)
    else:
        write_csv(rows, args.out, sep=args.sep)

    tot_d = sum(float(r[5]) for r in rows)
    tot_c = sum(float(r[6]) for r in rows)
    logger.info("TOTAL Débit=%.2f  Crédit=%.2f  Écart=%.2f",
                tot_d, tot_c, round(tot_d - tot_c, 2))
    if all_warn:
        logger.warning("%d avertissement(s) :", len(all_warn))
        for w in all_warn[:25]:
            logger.warning("  - %s", w)
        if len(all_warn) > 25:
            logger.warning("  ... (%d autres)", len(all_warn) - 25)


if __name__ == "__main__":
    main()
