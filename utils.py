
import requests
import json
import os
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import timedelta, datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import streamlit as st
import logging

from geo_data import get_region_info
import db_cache
from i18n import t  # import module-level (utilise par load_tab_data)

logger = logging.getLogger("dashboard")


# ==========================================
# HACHAGE DATAFRAME POUR @st.cache_data
# ==========================================
# Hachage robuste d'un DataFrame : on serialise en CSV. Gere les colonnes
# contenant des listes (ex: 'Paiements') que le hachage natif de Streamlit ne
# sait pas traiter. Le cache se rafraichit des que le contenu du DataFrame
# change. Defini tot dans le fichier car utilise par de nombreux decorateurs.
def _hash_df(df):
    try:
        return df.to_csv(index=False).encode("utf-8")
    except Exception:
        return str(df.values.tolist()).encode("utf-8")


_DF_HASH = {pd.DataFrame: _hash_df}


# ==========================================
# CHARGEMENT MUTUALISE + GROS INDICATEUR
# ==========================================
#: Sentinelle renvoye par load_tab_data quand l'onglet n'a pas encore ete
#: charge (chargement paresseux a la demande). L'appelant DOIT faire un early
#: return des qu'il recoit cette valeur.
NOT_LOADED = object()


def load_tab_data(cache_key: str, loader, label: str, force: bool = False,
                  db_keys: list = None, require_click: bool = True,
                  load_label: str = None):
    """Charge des donnees pour un onglet avec cache session + GROS indicateur.

    - cache_key : cle unique en st.session_state (persiste entre navigations).
    - loader    : fonction sans argument qui renvoie les donnees (fait les
                  fetch_* ; ces derniers sont deja @st.cache_data, donc l'appel
                  reseau reel n'a lieu qu'une fois par (params, TTL)).
    - label     : texte affiche dans le gros indicateur de chargement.
    - force     : recharge meme si deja en cache (bouton Rafraichir).
    - require_click : si True (defaut), l'onglet NE charge RIEN tant que
                  l'utilisateur n'a pas clique sur le bouton 'Charger'. Evite
                  que les 10 onglets st.tabs chargent tous au demarrage (les
                  st.tabs ne sont pas paresseux : leur code s'execute a chaque
                  run). Renvoie la sentinelle NOT_LOADED tant qu'aucun clic.
    - load_label : libelle du bouton 'Charger' (defaut : traduction 'load_data').

    Chargement paresseux : on ne recharge PAS si la cle est deja en session,
    ce qui rend la navigation entre onglets instantanee.
    """
    # Marqueur "deja charge au moins une fois" (persiste tant que l'onglet
    # reste en session). Le force (Rafraichir) ne reinitialise pas ce marqueur.
    loaded_flag = f"{cache_key}__loaded"

    if force:
        if cache_key in st.session_state:
            del st.session_state[cache_key]
        # Invalider aussi les entrees de cache DB associees (bases globales TTL
        # long : gift_cards, coupons, business_partners...). Sinon le loader
        # relit la donnee figee en DB au lieu de rappeler l'API.
        for _k in (db_keys or []):
            try:
                db_cache.delete(_k)
            except Exception:
                pass

    # --- Chargement paresseux a la demande : bouton 'Charger' ---
    if require_click and loaded_flag not in st.session_state and cache_key not in st.session_state:
        _lang = st.session_state.get("_lang", "fr")
        btn_label = load_label or t("load_data", _lang)
        if st.button("\U0001f4e5 " + btn_label, type="primary",
                     key=f"load_btn_{cache_key}"):
            st.session_state[loaded_flag] = True
            # on continue le chargement ci-dessous (pas de return)
        else:
            st.caption(t("load_hint", _lang))
            return NOT_LOADED

    if cache_key not in st.session_state:
        st.session_state[loaded_flag] = True
        with st.status(f"⏳ {label}", expanded=True) as status:
            st.write(f"**{label}**")
            data = loader()
            st.session_state[cache_key] = data
            status.update(label=f"✅ {label}", state="complete", expanded=False)
    return st.session_state[cache_key]

# ==========================================
# LOG DES RÉPONSES API (pour analyse a posteriori)
# ==========================================
# Chaque réponse d'API est archivée en JSON horodaté dans ce dossier, afin de
# pouvoir diagnostiquer les problemes de donnees (ex: valeur reelle du champ
# 'store', ecarts de comptage) sans avoir a rejouer les appels.
API_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_logs")


def _log_api_response(label: str, url: str, params: dict, data) -> None:
    """Archive une reponse API (label + url + params + payload) en JSON horodate."""
    try:
        os.makedirs(API_LOG_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        path = os.path.join(API_LOG_DIR, f"{ts}_{safe_label}.json")
        n = len(data) if isinstance(data, (list, dict)) else None
        payload = {
            "timestamp": datetime.now().isoformat(),
            "label": label,
            "url": url,
            "params": params,
            "n_items": n,
            "data": data,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        logger.info("API log ecrit : %s (%s items)", path, n)
    except Exception as e:  # ne jamais bloquer l'app pour un probleme de log
        logger.warning("Echec ecriture log API (%s) : %s", label, e)


def is_persist_log() -> bool:
    """Mode 'super debug' : conserver les logs entre redemarrages.

    Active si la variable d'environnement PERSIST_LOG vaut 1/true/yes/on.
    """
    return os.environ.get("PERSIST_LOG", "").strip().lower() in ("1", "true", "yes", "on")


def cleanup_old_logs() -> None:
    """Supprime les anciens logs API au demarrage, sauf si PERSIST_LOG est actif.

    A appeler UNE fois au lancement de l'app. Ne bloque jamais l'app.
    """
    if is_persist_log():
        logger.info("PERSIST_LOG actif → conservation des anciens logs API.")
        return
    try:
        if not os.path.isdir(API_LOG_DIR):
            return
        removed = 0
        for fname in os.listdir(API_LOG_DIR):
            if fname.endswith(".json"):
                try:
                    os.remove(os.path.join(API_LOG_DIR, fname))
                    removed += 1
                except Exception as e:
                    logger.warning("Echec suppression log %s : %s", fname, e)
        logger.info("Nettoyage logs API : %s fichier(s) supprime(s).", removed)
    except Exception as e:  # ne jamais bloquer l'app
        logger.warning("Echec nettoyage logs API : %s", e)

# ==========================================
# CONSTANTES
# ==========================================
JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
TIME_BUCKET_MAP = {"15 min": "15min", "30 min": "30min", "1 heure": "1h", "2 heures": "2h"}
COLOR_MAP_MIRROR = {"Ventes": "#2ca02c", "Retours": "#d62728"}
DEFAULT_CLIENT = "Intersport"
DEFAULT_STORES = ["Magasin Felgueiras"]
ANONYMOUS_BP_NAME = "Cliente anônimo"


# ==========================================
# HELPERS
# ==========================================
def _safe_str(val) -> str:
    if isinstance(val, dict):
        return val.get("name", "") or val.get("searchKey", "") or val.get("id", "")
    return str(val) if val else ""


def _safe_normalize_date(raw):
    if not raw or (isinstance(raw, float) and pd.isna(raw)):
        return None
    try:
        ts = pd.to_datetime(raw)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        return ts.normalize()
    except Exception:
        return None


def is_anonymous_bp(bp_name: str) -> bool:
    if not bp_name:
        return True
    return bp_name.strip().lower() == ANONYMOUS_BP_NAME.lower()


# ==========================================
# CONFIGURATION
# ==========================================
def load_config():
    try:
        sec = st.secrets["openbravo"]
        return {
            "endpoint": sec["url"], "username": sec["username"],
            "password": sec["password"],
            "client": sec.get("client", DEFAULT_CLIENT),
            "stores": list(sec.get("stores", DEFAULT_STORES)),
        }
    except (KeyError, FileNotFoundError):
        pass
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg.setdefault("client", DEFAULT_CLIENT)
        cfg.setdefault("stores", DEFAULT_STORES)
        return cfg
    except Exception as e:
        logger.error("Config introuvable : %s", e)
        st.error("⚠️ Configuration introuvable.")
        return None


# ==========================================
# HTTP
# ==========================================
def _create_http_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _fetch_paginated(base_url: str, params: dict, username: str, password: str,
                     label: str = "api") -> list:
    headers = {"Accept": "application/json"}
    all_data, session = [], _create_http_session()
    url, current_params = base_url, params
    try:
        while url:
            response = session.get(url, params=current_params, auth=(username, password),
                                   headers=headers, timeout=30)
            current_params = None
            if response.status_code != 200:
                logger.error("API %s — %s", response.status_code, response.text[:500])
                st.error(f"Erreur serveur (code {response.status_code}).")
                with st.expander("🔍 Debug", expanded=False):
                    st.code(f"URL : {response.url}", language="text")
                    st.code(response.text[:1000], language="json")
                break
            data = response.json()
            if isinstance(data, list):
                all_data.extend(data)
                url = None
            else:
                all_data.extend(data.get("data") or data.get("response", {}).get("data", []))
                links = data.get("links")
                url = links.get("next") if isinstance(links, dict) else None
    except requests.exceptions.Timeout:
        st.error("Le serveur met trop de temps à répondre.")
    except requests.exceptions.ConnectionError:
        st.error("Impossible de joindre le serveur Openbravo.")
    except Exception as e:
        logger.exception("Erreur inattendue")
        st.error(f"Erreur inattendue : {e}")
    # Archivage systematique de la reponse pour analyse a posteriori
    _log_api_response(label, base_url, params, all_data)
    return all_data


# ==========================================
# API — COMMANDES
# ==========================================
@st.cache_data(ttl=900, show_spinner=False)
def fetch_orders(base_url, username, password, store_name, order_date):
    # --- Flux DB-first (cf. db_cache.fetch_with_cache) ---
    # Jour demande -> API -> stockage DB -> lecture DB.
    # Jour passe   -> lecture directe DB, aucun appel API.
    # La cle n'inclut PAS le mot de passe.
    cache_key = f"orders|{store_name}|{order_date}"
    endpoint = f"{base_url}/org.openbravo.api.ExportService/Order/byOrgOrderDate"
    return db_cache.fetch_with_cache(
        cache_key,
        lambda: _fetch_paginated(
            endpoint, {"organization": store_name, "orderDate": order_date},
            username, password, label=f"orders_{store_name}_{order_date}"),
        frozen=db_cache.is_frozen_single(order_date),
        label=f"orders {store_name} {order_date}",
    )


def fetch_order_by_document_no(base_url, username, password, document_no):
    """Recupere UN ticket par son numero (endpoint Order/byDocumentNo).

    Pas besoin de date : le numero de document est unique. Un ticket passe est
    immuable -> cache DB fige en permanence (cle par documentNo). Renvoie la
    liste brute (0 ou 1+ orders) telle que l'API la fournit.
    """
    doc = (document_no or "").strip()
    cache_key = f"order_doc|{doc}"
    endpoint = f"{base_url}/org.openbravo.api.ExportService/Order/byDocumentNo"
    return db_cache.fetch_with_cache(
        cache_key,
        lambda: _fetch_paginated(
            endpoint, {"documentNo": doc},
            username, password, label=f"order_doc_{doc}"),
        frozen=True,
        label=f"order byDocumentNo {doc}",
    )


def fetch_organizations(base_url, username, password, client_name=""):
    """Recupere les ORGANISATIONS (magasins) d'un client via l'endpoint
    Organization (Master Data API). Base globale non datable -> cache DB a TTL
    long (24h), par client (la cle inclut le nom du client). Un seul chargement
    par jour, mutualise ; le bouton Rafraichir (parametrage) force le rechargement.
    """
    cache_key = f"organizations|{client_name or base_url}"
    endpoint = f"{base_url}/org.openbravo.api.ExportService/Organization"
    return db_cache.fetch_with_cache(
        cache_key,
        lambda: _fetch_paginated(endpoint, {}, username, password,
                                 label=f"organizations_{client_name}"),
        frozen=False,
        label=f"organizations {client_name} (TTL 24h)",
        ttl_seconds=86400,
    )


def parse_organizations(orgs_json: str) -> list:
    """Extrait la liste des magasins (name) exploitables depuis la reponse
    Organization. Ignore les organisations techniques (sans searchKey/name, ou
    marquees comme non-magasin). Renvoie une liste de noms triee, dedupliquee.

    On garde large : toute org ayant un 'name' non vide est candidate. Les
    installs Openbravo exposent typiquement l'org racine + les magasins ; on
    renvoie tout 'name' non vide, a l'utilisateur de choisir dans le selecteur.
    """
    try:
        orgs = json.loads(orgs_json)
    except Exception:
        orgs = []
    names = []
    for o in orgs or []:
        nm = (o.get("name") or "").strip()
        if nm:
            names.append(nm)
    # dedupe en conservant un tri alphabetique stable
    return sorted(set(names))


def _extract_bp_name(order: dict) -> str:
    bp = order.get("businessPartner", "")
    if isinstance(bp, dict):
        return (bp.get("commercialName") or bp.get("name")
                or bp.get("fiscalName") or "")
    return str(bp) if bp else ""


def _extract_bp_id(order: dict) -> str:
    bp = order.get("businessPartner", "")
    if isinstance(bp, dict):
        return bp.get("id", "")
    return ""


@st.cache_data
def parse_orders(orders_json: str) -> pd.DataFrame:
    orders = json.loads(orders_json)
    if not orders:
        return pd.DataFrame()
    records = []
    for o in orders:
        if o.get("isCancelled") or o.get("isVoid"):
            continue
        time_str = o.get("localCreationDate") or o.get("creationDate")
        if not time_str:
            continue
        bp_name = _extract_bp_name(o)
        bp_id = _extract_bp_id(o)
        gross = float(o.get("grossAmount", 0))
        payment_types = []
        for p in o.get("payments", []):
            ptype = p.get("paymentType")
            pm = p.get("paymentMethod")
            if isinstance(pm, dict) and pm.get("description"):
                ptype = pm["description"]
            if ptype:
                payment_types.append(ptype)
        # #4 : un ticket a exactement 0 EUR est anormal → on le signale.
        if gross == 0:
            logger.warning("Ticket a 0 EUR (montant brut nul) : %s",
                           o.get("documentNo", "N/A"))
        records.append({
            "Ticket": o.get("documentNo", "N/A"),
            "DateHeure": time_str,
            "Montant Brut": gross,
            "Montant Net": float(o.get("netAmount", 0)),
            "Terminal": o.get("terminal", "Inconnu"),
            "Paiements": payment_types or ["Inconnu"],
            "Nb Articles": len(o.get("lines", [])),
            "BP_ID": bp_id,
            "BP_Nom": bp_name,
            # Clé de jointure avec la base BP : l'order porte le searchKey du BP
            # dans businessPartner (string). On normalise pour le croisement.
            "BP_Key": str(bp_name).strip().lower(),
            "is_anonymous": is_anonymous_bp(bp_name),
            "Type": "Vente" if gross >= 0 else "Retour",
        })
    return pd.DataFrame(records)


def compute_order_stats(df_orders: pd.DataFrame, amount_type: str) -> dict:
    empty = {
        "total_orders": 0, "total_ca": 0.0,
        "nb_ventes": 0, "ca_ventes": 0.0,
        "nb_retours": 0, "ca_retours": 0.0,
        "anon_orders": 0, "anon_ca": 0.0,
        "identified_orders": 0, "identified_ca": 0.0,
        "pct_identified": 0.0,
    }
    if df_orders.empty:
        return empty
    total = len(df_orders)
    total_ca = df_orders[amount_type].sum()
    ventes = df_orders[df_orders["Type"] == "Vente"]
    retours = df_orders[df_orders["Type"] == "Retour"]
    anon = df_orders[df_orders["is_anonymous"]]
    identified = df_orders[~df_orders["is_anonymous"]]
    return {
        "total_orders": total,
        "total_ca": total_ca,
        "nb_ventes": len(ventes),
        "ca_ventes": ventes[amount_type].sum(),
        "nb_retours": len(retours),
        "ca_retours": retours[amount_type].sum(),
        "anon_orders": len(anon),
        "anon_ca": anon[amount_type].sum(),
        "identified_orders": len(identified),
        "identified_ca": identified[amount_type].sum(),
        "pct_identified": (len(identified) / total * 100) if total else 0.0,
    }


# ==========================================
# API — VENDEURS (rattachement vente -> vendeur)
# ==========================================
# Regle metier (user) : le vendeur d'une vente = le "sales representative" de
# l'order s'il existe, SINON le "created by" (le caissier qui a saisi la vente).
# L'API Openbravo peut nommer ces champs de plusieurs facons et les exposer
# soit comme objet {id,name,searchKey} soit comme chaine. On extrait donc de
# facon DEFENSIVE en testant plusieurs cles plausibles.

# Cles candidates pour le representant commercial (ordre de preference).
_SALESREP_KEYS = (
    "salesRepresentative", "salesrep", "salesRep", "salesPerson",
    "salesRepresentative_info", "cBpartnerSalesRep",
)
# Cles candidates pour le createur / caissier (ordre de preference).
_CREATEDBY_KEYS = (
    "createdBy", "createdby", "createdByUserContact", "createdByName",
    "createdByUser", "userContact", "cUser",
)


def _extract_person(order: dict, keys) -> str:
    """Renvoie un nom de personne lisible depuis la 1ere cle presente parmi
    `keys`. Gere objet {name/searchKey/id} ou chaine. '' si rien."""
    for k in keys:
        if k not in order:
            continue
        val = order.get(k)
        if not val:
            continue
        if isinstance(val, dict):
            name = (val.get("name") or val.get("searchKey")
                    or val.get("username") or val.get("id") or "")
            if name:
                return str(name).strip()
        else:
            return str(val).strip()
    return ""


def _order_seller(order: dict) -> tuple:
    """Determine le vendeur d'un order selon la regle metier.
    Renvoie (nom_vendeur, source) ou source in {'representant','caissier','inconnu'}."""
    rep = _extract_person(order, _SALESREP_KEYS)
    if rep:
        return rep, "representant"
    cashier = _extract_person(order, _CREATEDBY_KEYS)
    if cashier:
        return cashier, "caissier"
    return "(inconnu)", "inconnu"


@st.cache_data(show_spinner=False)
def parse_orders_sellers(orders_json: str) -> pd.DataFrame:
    """Parse les orders en vue de l'analyse par VENDEUR. Une ligne par ticket,
    avec le vendeur rattache (representant sinon caissier), le montant, le type
    (vente/retour) et le terminal. Ignore annules/void."""
    orders = json.loads(orders_json)
    if not orders:
        return pd.DataFrame()
    records = []
    for o in orders:
        if o.get("isCancelled") or o.get("isVoid"):
            continue
        gross = float(o.get("grossAmount", 0) or 0)
        net = float(o.get("netAmount", 0) or 0)
        seller, source = _order_seller(o)
        records.append({
            "Ticket": o.get("documentNo", "N/A"),
            "Vendeur": seller,
            "Source": source,   # representant / caissier / inconnu
            "Montant Brut": gross,
            "Montant Net": net,
            "Nb Articles": len(o.get("lines", []) or []),
            "Terminal": _safe_str(o.get("terminal", "")) or "Inconnu",
            "Type": "Vente" if gross >= 0 else "Retour",
            "is_return": bool(o.get("isReturn")) or gross < 0,
        })
    return pd.DataFrame(records)


def compute_seller_stats(df_sellers: pd.DataFrame, amount_type: str = "Montant Net") -> pd.DataFrame:
    """Agrege les ventes par vendeur. Renvoie un DataFrame trie par CA
    decroissant : vendeur, source, nb_tickets, nb_ventes, nb_retours, ca,
    panier_moyen, nb_articles, articles_moyen."""
    if df_sellers.empty:
        return pd.DataFrame()
    rows = []
    for seller, sub in df_sellers.groupby("Vendeur"):
        ventes = sub[sub["Type"] == "Vente"]
        retours = sub[sub["Type"] == "Retour"]
        nb_tickets = len(sub)
        ca = float(sub[amount_type].sum())
        nb_art = int(sub["Nb Articles"].sum())
        # source dominante pour ce vendeur (en general homogene)
        src = sub["Source"].mode()
        src = src.iloc[0] if len(src) else ""
        rows.append({
            "Vendeur": seller,
            "Source": src,
            "Nb Tickets": nb_tickets,
            "Nb Ventes": len(ventes),
            "Nb Retours": len(retours),
            "CA": round(ca, 2),
            "Panier Moyen": round(ca / nb_tickets, 2) if nb_tickets else 0.0,
            "Nb Articles": nb_art,
            "Articles/Ticket": round(nb_art / nb_tickets, 2) if nb_tickets else 0.0,
        })
    df_out = pd.DataFrame(rows).sort_values("CA", ascending=False).reset_index(drop=True)
    return df_out


# ==========================================
# API — BUSINESS PARTNERS
# ==========================================
@st.cache_data(ttl=900, show_spinner=False)
def fetch_all_business_partners(base_url, username, password):
    # Base BP GLOBALE (non datable : pas d'endpoint byCreationDateRange). Cache
    # DB a TTL long (24h) mutualise -> un seul chargement complet par jour,
    # reutilise par l'onglet Clients. Le bouton Rafraichir force le rechargement.
    endpoint = f"{base_url}/org.openbravo.api.ExportService/BusinessPartner"
    return db_cache.fetch_with_cache(
        "business_partners",
        lambda: _fetch_paginated(endpoint, {}, username, password,
                                 label="business_partners"),
        frozen=False,
        label="business_partners (base globale, TTL 24h)",
        ttl_seconds=86400,
    )


def _extract_org_name(bp: dict) -> str:
    org = bp.get("organization", "")
    if isinstance(org, dict):
        return org.get("name", "") or org.get("searchKey", "")
    return str(org) if org else ""


@st.cache_data
def parse_business_partners(bp_json: str, reference_date: str) -> dict:
    bps = json.loads(bp_json)
    if not bps:
        return {"df_bp": pd.DataFrame(), "df_locations": pd.DataFrame(),
                "total_bp": 0, "total_enrolled": 0, "enrollment_rate": 0.0,
                "available_orgs": []}

    all_orgs = sorted({_extract_org_name(bp) for bp in bps if _extract_org_name(bp)})
    bp_records, loc_records = [], []

    for bp in bps:
        bp_id = bp.get("id", "")
        bp_name = bp.get("commercialName") or bp.get("fiscalName") or "Sans nom"
        is_customer = bp.get("isCustomer", False)
        org_name = _extract_org_name(bp)
        subs = bp.get("loyaltySubscriptions", [])
        has_loyalty = len(subs) > 0
        is_anon = is_anonymous_bp(bp_name)

        # Extraction defensive du/des memberId pour croisement avec les
        # souscriptions (dont le payload byCreationDateRange n'expose que memberId).
        # On cherche a plusieurs endroits plausibles sans casser si absent.
        member_ids = set()
        top_mid = bp.get("memberId") or bp.get("memberid")
        if top_mid:
            member_ids.add(str(top_mid))
        for s in subs:
            if isinstance(s, dict):
                mid = s.get("memberId") or s.get("memberid")
                if mid:
                    member_ids.add(str(mid))

        locations = bp.get("locations", [])
        for loc in locations:
            zc = loc.get("zipCode", "")
            country_raw = loc.get("country", "")
            geo = get_region_info(zc, country_raw)
            loc_records.append({
                "bp_id": bp_id, "bp_name": bp_name, "has_loyalty": has_loyalty,
                "ville": loc.get("city", ""), "code_postal": zc,
                "région": loc.get("region", ""), "pays": country_raw,
                "region_code": geo["region_code"], "region_name": geo["region_name"],
                "country_code": geo["country"], "lat": geo["lat"], "lon": geo["lon"],
            })
        if not locations:
            loc_records.append({
                "bp_id": bp_id, "bp_name": bp_name, "has_loyalty": has_loyalty,
                "ville": "", "code_postal": "", "région": "", "pays": "",
                "region_code": None, "region_name": "Inconnu",
                "country_code": "UNKNOWN", "lat": None, "lon": None,
            })

        bp_records.append({
            "id": bp_id, "nom": bp_name, "client": is_customer,
            "organisation": org_name,
            "fidélité": has_loyalty, "nb_programmes": len(subs),
            "member_ids": sorted(member_ids),
            # searchKey = clé de jointure avec order.businessPartner
            # (l'order porte le searchKey du BP, PAS le commercialName).
            "search_key": bp.get("searchKey", ""),
            "anonyme": is_anon,
            "ville": locations[0].get("city", "") if locations else "",
            "code_postal": locations[0].get("zipCode", "") if locations else "",
        })

    df_bp = pd.DataFrame(bp_records)
    df_locations = pd.DataFrame(loc_records)
    df_bp_real = df_bp[~df_bp["anonyme"]].copy()
    total_bp = len(df_bp_real)
    total_enrolled = int(df_bp_real["fidélité"].sum()) if total_bp > 0 else 0
    enrollment_rate = total_enrolled / total_bp if total_bp > 0 else 0.0

    return {"df_bp": df_bp, "df_locations": df_locations,
            "total_bp": total_bp, "total_enrolled": total_enrolled,
            "enrollment_rate": enrollment_rate,
            "available_orgs": all_orgs}


# ==========================================
# API — SUBSCRIPTIONS (ExportService)
# ==========================================
@st.cache_data(ttl=900, show_spinner=False)
def fetch_subscriptions_by_date_range(base_url, username, password,
                                      start_date, end_date):
    """Souscriptions dont la date de création est dans [start_date, end_date].

    start_date / end_date au format 'YYYY-MM-DD'. Renvoie la meme structure
    JSON que /Subscription, donc parse_subscriptions() s'applique directement.
    """
    # --- Flux DB-first (cf. db_cache.fetch_with_cache) ---
    # Plage entierement passee -> lecture directe DB ; plage touchant
    # aujourd'hui -> API puis stockage DB (TTL court).
    cache_key = f"subs|{start_date}|{end_date}"
    endpoint = (f"{base_url}/org.openbravo.api.ExportService/"
                f"Subscription/byCreationDateRange")
    return db_cache.fetch_with_cache(
        cache_key,
        lambda: _fetch_paginated(
            endpoint, {"startDate": start_date, "endDate": end_date},
            username, password, label=f"subscriptions_{start_date}_{end_date}"),
        frozen=db_cache.is_frozen_range(start_date, end_date),
        label=f"subscriptions {start_date}..{end_date}",
    )


def parse_subscriptions(subs_json: str) -> pd.DataFrame:
    subs = json.loads(subs_json)
    if not subs:
        return pd.DataFrame()
    records = []
    for s in subs:
        cards = s.get("cards", [])
        # La date d'encartement reelle est 'creationDate' au niveau racine
        # (ex: "2026-09-28 14:48:10"). Le 'startingDate' present dans
        # loyaltyProgram est la date de lancement du programme, PAS l'encartement.
        creation_raw = s.get("creationDate", "")
        creation_dt = _safe_normalize_date(creation_raw)
        records.append({
            "sub_id": s.get("id", ""),
            "organization": _safe_str(s.get("organization", "")),
            "bp_id": _safe_str(s.get("businessPartner", "")),
            "programme": _safe_str(s.get("loyaltyProgram", "")),
            "catégorie": _safe_str(s.get("category", "")),
            "statut": s.get("status", ""),
            "points": s.get("totalPoints", 0),
            "store": _safe_str(s.get("store", "")),
            "terminal": _safe_str(s.get("terminal", "")),
            "carte": cards[0].get("cardNumber", "") if cards else "",
            "date_carte": cards[0].get("issueDate", "") if cards else "",
            # 'starting_date' pointe desormais sur la date de CREATION (encartement)
            "starting_date": creation_dt,
            "creation_date": creation_dt,
            "memberid": s.get("memberId", "") or s.get("memberid", ""),
            "currency": _safe_str(s.get("currency", "")),
        })
    return pd.DataFrame(records)


# ==========================================
# API — CARTES CADEAUX (GiftCard / GiftCardTransaction)
# ==========================================
@st.cache_data(ttl=900, show_spinner=False)
def fetch_gift_cards(base_url, username, password):
    """Toutes les cartes cadeaux (Master Data API).

    Base GLOBALE : pas d'endpoint de filtrage par date. Cache DB a TTL long
    (24h), mutualise, refresh manuel (comme les Business Partners).
    """
    endpoint = f"{base_url}/org.openbravo.api.ExportService/GiftCard"
    return db_cache.fetch_with_cache(
        "gift_cards",
        lambda: _fetch_paginated(endpoint, {}, username, password, label="gift_cards"),
        frozen=False, label="gift_cards (base globale, TTL 24h)", ttl_seconds=86400)


@st.cache_data(ttl=900, show_spinner=False)
def fetch_gift_card_transactions(base_url, username, password):
    """Toutes les transactions de cartes cadeaux (Transactional Data API).

    Base GLOBALE : pas de filtre par date. Cache DB a TTL long (24h).
    """
    endpoint = f"{base_url}/org.openbravo.api.ExportService/GiftCardTransaction"
    return db_cache.fetch_with_cache(
        "gift_card_transactions",
        lambda: _fetch_paginated(endpoint, {}, username, password,
                                 label="gift_card_transactions"),
        frozen=False, label="gift_card_transactions (base globale, TTL 24h)",
        ttl_seconds=86400)


def parse_gift_cards(gc_json: str) -> pd.DataFrame:
    """Parse les cartes cadeaux. Champs cles : amount (initial),
    currentAmount (solde), dateOrdered, expirationDate, active, cancelled."""
    cards = json.loads(gc_json)
    if not cards:
        return pd.DataFrame()
    records = []
    for c in cards:
        amount = float(c.get("amount", 0) or 0)
        current = float(c.get("currentAmount", 0) or 0)
        records.append({
            "id": c.get("id", ""),
            "searchKey": c.get("searchKey", ""),
            "montant_initial": amount,
            "solde": current,
            "consomme": round(amount - current, 2),
            "taux_util": round((amount - current) / amount, 4) if amount else 0.0,
            "type": _safe_str(c.get("type", "")),
            "bp": _safe_str(c.get("businessPartner", "")),
            "owner": _safe_str(c.get("owner", "")),
            "touchpoint": _safe_str(c.get("touchpoint", "")),
            "devise": _safe_str(c.get("currency", "")),
            "active": bool(c.get("active", False)),
            "cancelled": bool(c.get("cancelled", False)),
            "date_ordered": _safe_normalize_date(c.get("dateOrdered", "")),
            "date_expiration": _safe_normalize_date(c.get("expirationDate", "")),
        })
    return pd.DataFrame(records)


def parse_gift_card_transactions(gct_json: str) -> pd.DataFrame:
    """Parse les transactions de cartes cadeaux. amount > 0 = recharge/emission,
    amount < 0 = consommation (selon la convention Openbravo, a valider sur data reelle)."""
    txns = json.loads(gct_json)
    if not txns:
        return pd.DataFrame()
    records = []
    for t2 in txns:
        amount = float(t2.get("amount", 0) or 0)
        records.append({
            "id": t2.get("id", ""),
            "gift_card": _safe_str(t2.get("giftCard", "")),
            "montant": amount,
            "type_txn": "Émission/Recharge" if amount >= 0 else "Consommation",
            "sales_order": _safe_str(t2.get("salesOrder", "")),
            "touchpoint": _safe_str(t2.get("touchpoint", "")),
            "cancelled": bool(t2.get("cancelled", False)),
            "date_ordered": _safe_normalize_date(t2.get("dateOrdered", "")),
        })
    return pd.DataFrame(records)


def compute_giftcard_stats(df_gc: pd.DataFrame, reference_date: str,
                           dormant_days: int = 90) -> dict:
    """KPIs cartes cadeaux : actives, encours (solde total), emises du jour,
    taux d'utilisation moyen, cartes dormantes, bientot expirees."""
    empty = {"nb_actives": 0, "encours": 0.0, "emises_jour": 0,
             "taux_util_moyen": 0.0, "nb_dormantes": 0, "encours_dormant": 0.0,
             "nb_expirent_bientot": 0}
    if df_gc.empty:
        return empty
    ref = _safe_normalize_date(reference_date)
    valides = df_gc[(df_gc["active"]) & (~df_gc["cancelled"])].copy()
    nb_actives = int(len(valides))
    encours = float(valides["solde"].sum())
    emises_jour = int((df_gc["date_ordered"] == ref).sum()) if ref is not None else 0
    # taux util moyen ponderé par le montant initial
    tot_init = valides["montant_initial"].sum()
    taux_moyen = float(valides["consomme"].sum() / tot_init) if tot_init else 0.0
    # cartes dormantes : solde > 0 et emises depuis > dormant_days
    nb_dormantes, encours_dormant = 0, 0.0
    if ref is not None:
        seuil = ref - pd.Timedelta(days=dormant_days)
        dormantes = valides[(valides["solde"] > 0) &
                            (valides["date_ordered"].notna()) &
                            (valides["date_ordered"] < seuil)]
        nb_dormantes = int(len(dormantes))
        encours_dormant = float(dormantes["solde"].sum())
        # bientot expirees : expiration dans les 30 jours et solde > 0
        horizon = ref + pd.Timedelta(days=30)
        expir = valides[(valides["solde"] > 0) &
                        (valides["date_expiration"].notna()) &
                        (valides["date_expiration"] >= ref) &
                        (valides["date_expiration"] <= horizon)]
        nb_expirent = int(len(expir))
    else:
        nb_expirent = 0
    return {"nb_actives": nb_actives, "encours": encours, "emises_jour": emises_jour,
            "taux_util_moyen": taux_moyen, "nb_dormantes": nb_dormantes,
            "encours_dormant": encours_dormant, "nb_expirent_bientot": nb_expirent}


# ==========================================
# API — CAISSES (CashUp)
# ==========================================
@st.cache_data(ttl=900, show_spinner=False)
def fetch_cashups(base_url, username, password):
    """Tous les CashUp (Transactional Data API). Fallback si le filtre par
    date/org echoue."""
    endpoint = f"{base_url}/org.openbravo.api.ExportService/CashUp"
    return _fetch_paginated(endpoint, {}, username, password, label="cashups")


@st.cache_data(ttl=900, show_spinner=False)
def fetch_cashups_by_org_date(base_url, username, password, organization, date_str):
    """CashUp filtres par organisation + date (endpoint byCashUpOrg).

    Ne charge que les clotures du jour au lieu de toutes (perf). En cas
    d'echec (nom d'org non reconnu, endpoint indispo, 0 resultat), on RETOMBE
    sur le chargement complet fetch_cashups (robuste). La valeur exacte
    attendue par 'organizationName' est a confirmer via api_logs/.
    """
    endpoint = f"{base_url}/org.openbravo.api.ExportService/CashUp/byCashUpOrg"
    # CashUp est DATABLE (creationDate) -> figeage jour-passe comme les orders.
    cache_key = f"cashups|{organization}|{date_str}"
    # 1) Cache DB d'abord : jour passe fige -> 0 appel API.
    cached = db_cache.get(cache_key)
    if cached is not None:
        return cached
    # 2) Miss -> appel filtre par date.
    filtered = _fetch_paginated(
        endpoint,
        {"organizationName": organization, "creationDate": date_str},
        username, password, label=f"cashups_{organization}_{date_str}")
    if filtered:
        db_cache.put(cache_key, filtered,
                     frozen=db_cache.is_frozen_single(date_str))
        return filtered
    # 3) Fallback complet NON cache par date (donnee non filtree : figer sous la
    #    cle du jour serait faux). L'appelant filtre au parsing.
    logger.warning("byCashUpOrg vide/echec pour %s %s -> fallback CashUp complet",
                   organization, date_str)
    return fetch_cashups(base_url, username, password)


def _tax_rate_of(tax_info: dict):
    """Taux de TVA dynamique depuis une entree CashUpTaxInformation.
    Lit 'rate' ; repli sur le nom (ex 'IVA 23%'). Meme logique que export_compta_pt.
    """
    r = tax_info.get("rate")
    if r is not None:
        try:
            return round(float(str(r).replace("%", "").replace(",", ".")), 2)
        except (TypeError, ValueError):
            pass
    name = str(tax_info.get("name") or tax_info.get("tax") or "")
    digits = "".join(ch for ch in name if ch.isdigit() or ch == ".")
    try:
        return round(float(digits), 2) if digits else None
    except ValueError:
        return None


def parse_cashups(cashups_json: str, reference_date: str = "") -> pd.DataFrame:
    """Parse les CashUp (analyse comptable). Par cloture :
    - terminal, business_date, grossSales, grossReturns
    - tva : dict {taux: {"ht": base, "tva": montant, "ttc": ht+tva}} agrege par
      taux, ordertype 0=vente(+) / 1=retour(-), types 2/3 ignores (aligne sur
      grossSales-grossReturns). Meme logique que export_compta_pt.py.
    - paiements : dict {moyen: totalSales - totalReturns} (logique comptable OD)
    - mvts_caisse : liste (type, nom, moyen, montant) depuis cashManagementEvents
    """
    cashups = json.loads(cashups_json)
    if not cashups:
        return pd.DataFrame()
    ref = _safe_normalize_date(reference_date) if reference_date else None
    records = []
    for cu in cashups:
        bdate = _safe_normalize_date(cu.get("businessDate", "")
                                     or cu.get("creationDate", ""))
        if ref is not None and bdate != ref:
            continue

        # Ventilation TVA par taux (ordertype 0=vente, 1=retour, 2/3 ignores)
        tva = {}
        for ti in cu.get("CashUpTaxInformation", []) or []:
            rate = _tax_rate_of(ti)
            if rate is None:
                rate = 0.0
            otype = str(ti.get("ordertype"))
            if otype == "0":
                sign = 1
            elif otype == "1":
                sign = -1
            else:
                continue  # 2 (layaway) / 3 (void) ignores
            ht = sign * float(ti.get("taxableAmount", 0) or 0)
            mt = sign * float(ti.get("amount", 0) or 0)
            slot = tva.setdefault(rate, {"ht": 0.0, "tva": 0.0})
            slot["ht"] += ht
            slot["tva"] += mt
        for rate, s in tva.items():
            s["ht"] = round(s["ht"], 2)
            s["tva"] = round(s["tva"], 2)
            s["ttc"] = round(s["ht"] + s["tva"], 2)

        # Reglements par moyen = totalSales - totalReturns (logique comptable OD)
        pm = {}
        mvts = []
        for m in cu.get("paymentMethods", []) or []:
            nm = _safe_str(m.get("paymentMethod", "") or m.get("name", "")) or "Inconnu"
            net = (float(m.get("totalSales", 0) or 0)
                   - float(m.get("totalReturns", 0) or 0))
            pm[nm] = round(pm.get(nm, 0) + net, 2)
            for ev in m.get("cashManagementEvents", []) or []:
                amt = float(ev.get("amount", 0) or 0)
                if amt:
                    mvts.append((ev.get("type", ""),
                                 ev.get("name", "") or ev.get("cashManagementEventSearchKey", ""),
                                 nm, round(amt, 2)))

        records.append({
            "terminal": _safe_str(cu.get("terminal", "")) or "Inconnu",
            "piece": _safe_str(cu.get("documentNo", "")),
            "close_dt": _safe_str(cu.get("closeDateTime", "")),
            "gross_sales": float(cu.get("grossSales", 0) or 0),
            "net_sales": float(cu.get("netSales", 0) or 0),
            "gross_returns": float(cu.get("grossReturns", 0) or 0),
            "business_date": bdate,
            "user": _safe_str(cu.get("user", "")),
            "paiements": pm,
            "tva": tva,
            "mvts_caisse": mvts,
        })
    return pd.DataFrame(records)


def compute_cashup_stats(df_cu: pd.DataFrame) -> dict:
    """KPIs cloture de caisse (CashUp). Onglet centre sur les CLOTURES, PAS
    sur les tickets (voir Encaissements pour cela).

    - CA net = grossSales - grossReturns (retours DEDUITS, coherent avec
      Encaissements et le journal comptable).
    - CA brut (ventes) = grossSales.
    - Retours = grossReturns.
    - nb_clotures = nombre de cash-ups effectues.
    NB : totalRetailTransactions est un MONTANT (pas un compteur de tickets),
    donc PAS de KPI 'transactions'/'panier moyen' ici.
    """
    empty = {"ca_net": 0.0, "ca_brut": 0.0, "retours": 0.0, "nb_caisses": 0,
             "nb_clotures": 0, "tva_totale": 0.0, "ht_total": 0.0,
             "total_reglements": 0.0, "ecart": 0.0,
             "df_by_terminal": pd.DataFrame(), "df_payments": pd.DataFrame(),
             "df_tva": pd.DataFrame(), "df_mvts": pd.DataFrame()}
    if df_cu.empty:
        return empty
    by_term = (df_cu.groupby("terminal")
               .agg(ca_brut=("gross_sales", "sum"),
                    retours=("gross_returns", "sum"),
                    nb_clotures=("gross_sales", "count"))
               .reset_index())
    by_term["ca_net"] = (by_term["ca_brut"] - by_term["retours"]).round(2)
    by_term["ca_brut"] = by_term["ca_brut"].round(2)
    by_term["retours"] = by_term["retours"].round(2)
    by_term = by_term.sort_values("ca_net", ascending=False)
    ca_brut = float(df_cu["gross_sales"].sum())
    retours = float(df_cu["gross_returns"].sum())

    # Reglements par moyen (totalSales - totalReturns)
    agg_pm = {}
    for pm in df_cu["paiements"]:
        for k, v in (pm or {}).items():
            agg_pm[k] = agg_pm.get(k, 0) + v
    df_pm = pd.DataFrame([{"moyen": k, "montant": round(v, 2)}
                          for k, v in agg_pm.items() if v]) if agg_pm else pd.DataFrame()
    if not df_pm.empty:
        df_pm = df_pm.sort_values("montant", ascending=False)
    total_reglements = round(sum(agg_pm.values()), 2)

    # Ventilation TVA par taux (agregee tous terminaux)
    agg_tva = {}
    if "tva" in df_cu.columns:
        for tv in df_cu["tva"]:
            for rate, s in (tv or {}).items():
                slot = agg_tva.setdefault(rate, {"ht": 0.0, "tva": 0.0, "ttc": 0.0})
                slot["ht"] += s.get("ht", 0.0)
                slot["tva"] += s.get("tva", 0.0)
                slot["ttc"] += s.get("ttc", 0.0)
    df_tva = pd.DataFrame([
        {"taux": r, "base_ht": round(s["ht"], 2), "tva": round(s["tva"], 2),
         "ttc": round(s["ttc"], 2)}
        for r, s in sorted(agg_tva.items())
    ]) if agg_tva else pd.DataFrame()
    ht_total = round(sum(s["ht"] for s in agg_tva.values()), 2)
    tva_totale = round(sum(s["tva"] for s in agg_tva.values()), 2)
    ttc_total = round(sum(s["ttc"] for s in agg_tva.values()), 2)

    # Mouvements de caisse
    mvts = []
    if "mvts_caisse" in df_cu.columns:
        for lst in df_cu["mvts_caisse"]:
            for (typ, nom, moyen, montant) in (lst or []):
                mvts.append({"type": typ, "libelle": nom, "moyen": moyen,
                             "montant": montant})
    df_mvts = pd.DataFrame(mvts) if mvts else pd.DataFrame()

    # Controle d'equilibre : ventes TTC (CashUpTax) vs reglements
    # (les services/cartes cadeaux 0% sont hors CashUpTax mais dans les
    # reglements -> un ecart peut subsister, on l'affiche pour info)
    ecart = round(ttc_total - total_reglements, 2)

    return {
        "ca_net": round(ca_brut - retours, 2),
        "ca_brut": round(ca_brut, 2),
        "retours": round(retours, 2),
        "nb_caisses": int(df_cu["terminal"].nunique()),
        "nb_clotures": int(len(df_cu)),
        "ht_total": ht_total,
        "tva_totale": tva_totale,
        "ttc_total": ttc_total,
        "total_reglements": total_reglements,
        "ecart": ecart,
        "df_by_terminal": by_term, "df_payments": df_pm,
        "df_tva": df_tva, "df_mvts": df_mvts,
    }


def analyze_cashup_gap(orders_json: str) -> dict:
    """Analyse l'ecart ventes TTC vs reglements en croisant avec les Orders.

    Les services / cartes cadeaux (productType='S', 0% TVA) sont ENCAISSES
    (donc dans les reglements CashUp) mais ABSENTS de CashUpTaxInformation
    (ventes taxees). Ils expliquent typiquement l'ecart. Cette fonction
    extrait, depuis les Orders du jour, les lignes de service par produit :
    montant encaisse -> permet de rapprocher de l'ecart observe.

    Retourne : {"df_services": DataFrame(produit, type, quantite, montant),
                "total_services": float, "nb_lignes": int}.
    """
    orders = json.loads(orders_json)
    empty = {"df_services": pd.DataFrame(), "total_services": 0.0, "nb_lignes": 0}
    if not orders:
        return empty
    records = []
    for o in orders:
        if o.get("isCancelled") or o.get("isVoid"):
            continue
        for line in o.get("lines", []):
            pinfo = line.get("product_info", {}) or {}
            ptype = str(pinfo.get("productType", "")).upper()
            # Service = 'S'. On garde aussi les lignes 0% TVA sans type connu.
            disc = float(line.get("discountPercentage", 0) or 0)  # non utilise ici
            if ptype == "S":
                name = (pinfo.get("name") or line.get("productDescription")
                        or _safe_str(line.get("product", "")) or "Service")
                records.append({
                    "produit": name,
                    "type": ptype,
                    "quantite": float(line.get("orderedQuantity", 0) or 0),
                    "montant": round(float(line.get("grossAmount", 0) or 0), 2),
                    "ticket": o.get("documentNo", "N/A"),
                })
    if not records:
        return empty
    df = pd.DataFrame(records)
    by_prod = (df.groupby(["produit", "type"])
               .agg(quantite=("quantite", "sum"), montant=("montant", "sum"),
                    nb_lignes=("ticket", "count"))
               .reset_index().sort_values("montant", ascending=False))
    by_prod["montant"] = by_prod["montant"].round(2)
    return {"df_services": by_prod,
            "total_services": round(float(df["montant"].sum()), 2),
            "nb_lignes": int(len(df))}


def build_cashup_journal(df_cu: pd.DataFrame) -> pd.DataFrame:
    """Journal detaille par cloture (piece = terminal x jour), facon export
    comptable. Une ligne par (piece, rubrique) : PRODUIT/TVA par taux + chaque
    moyen de reglement. Colonnes : piece, date, terminal, rubrique, base_ht,
    tva, montant. Sert la vue 'journal des clotures' de l'onglet Caisses.
    """
    if df_cu.empty:
        return pd.DataFrame()
    rows = []
    for _, cu in df_cu.iterrows():
        piece = cu.get("piece", "") or cu.get("terminal", "")
        term = cu.get("terminal", "")
        bd = cu.get("business_date")
        date_str = bd.strftime("%d/%m/%Y") if bd is not None and hasattr(bd, "strftime") else ""
        # Lignes PRODUIT + TVA par taux
        for rate, s in sorted((cu.get("tva") or {}).items()):
            ht = round(s.get("ht", 0.0), 2)
            tv = round(s.get("tva", 0.0), 2)
            if ht or tv:
                rows.append({"piece": piece, "date": date_str, "terminal": term,
                             "rubrique": f"PRODUIT {rate:g}%", "base_ht": ht,
                             "tva": tv, "montant": round(ht + tv, 2)})
        # Lignes REGLEMENT par moyen
        for moyen, montant in (cu.get("paiements") or {}).items():
            m = round(montant, 2)
            if m:
                rows.append({"piece": piece, "date": date_str, "terminal": term,
                             "rubrique": f"REGLEMENT {moyen}", "base_ht": 0.0,
                             "tva": 0.0, "montant": m})
    return pd.DataFrame(rows)


@st.cache_data(hash_funcs=_DF_HASH, show_spinner=False)
def generate_cashup_terminal_chart(df_by_terminal, lang="fr"):
    """Barres : CA NET par caisse (degrade Viridis par montant de retours)."""
    from i18n import t
    if df_by_terminal.empty:
        return None
    fig = px.bar(df_by_terminal, x="terminal", y="ca_net", text_auto=".2f",
                 color="retours", color_continuous_scale="Viridis",
                 title=t("cash_terminal_title", lang),
                 labels={"retours": t("returns", lang)},
                 hover_data={"ca_brut": ":.2f", "retours": ":.2f"})
    fig.update_layout(xaxis_title="", yaxis_title=t("cash_ca", lang),
                      plot_bgcolor="rgba(0,0,0,0)", margin=dict(l=0, r=0, t=40, b=0),
                      coloraxis_colorbar=dict(title=t("returns", lang)))
    return fig


# ==========================================
# API — COUPONS (Master Data)
# ==========================================
@st.cache_data(ttl=900, show_spinner=False)
def fetch_coupons(base_url, username, password):
    """Tous les coupons (Master Data API).

    Vue globale des coupons actifs (choix metier). Cache DB a TTL long (24h),
    mutualise, refresh manuel. byCreationDateRange existe mais changerait le
    sens (coupons crees ce jour vs tous actifs).
    """
    endpoint = f"{base_url}/org.openbravo.api.ExportService/Coupon"
    return db_cache.fetch_with_cache(
        "coupons",
        lambda: _fetch_paginated(endpoint, {}, username, password, label="coupons"),
        frozen=False, label="coupons (base globale, TTL 24h)", ttl_seconds=86400)



@st.cache_data(hash_funcs=_DF_HASH, show_spinner=False)
def generate_vat_chart(df_tva, lang="fr"):
    """Barres groupees : base HT et TVA par taux (ventilation comptable)."""
    from i18n import t
    if df_tva.empty:
        return None
    df = df_tva.copy()
    df["taux_lbl"] = df["taux"].map(lambda r: f"{r:g}%")
    dfm = df.melt(id_vars="taux_lbl", value_vars=["base_ht", "tva"],
                  var_name="composante", value_name="montant")
    dfm["composante"] = dfm["composante"].map(
        {"base_ht": t("vat_base", lang), "tva": t("vat_amount", lang)})
    fig = px.bar(dfm, x="taux_lbl", y="montant", color="composante",
                 barmode="group", text_auto=".2f",
                 title=t("vat_chart_title", lang),
                 color_discrete_map={t("vat_base", lang): "#4B2E83",
                                     t("vat_amount", lang): "#2ca02c"})
    fig.update_layout(xaxis_title=t("vat_rate", lang), yaxis_title="€",
                      plot_bgcolor="rgba(0,0,0,0)", margin=dict(l=0, r=0, t=40, b=0),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                  xanchor="right", x=1))
    return fig

def parse_coupons(coupons_json: str) -> pd.DataFrame:
    """Parse les coupons. Champs cles : couponCode, promotion, couponAmount,
    remainingBalance, validFrom/To, creationDate, status.

    IMPORTANT (verifie sur donnees reelles) :
      - status = 'U' (Used / utilise) ou 'NU' (Not Used). C'est l'indicateur
        d'usage FIABLE (colle au back-office). numberOfUses vaut toujours 'NU'
        (chaine, pas un nombre) -> inexploitable, on ne s'en sert plus.
      - L'API Coupon n'expose NI 'updated' NI 'available'. La distinction
        partiel/entier est donc approchee via le solde (remainingBalance) :
        solde<=0 -> entier ; 0<solde<montant -> partiel ; solde>=montant -> non utilise.
    """
    coupons = json.loads(coupons_json)
    if not coupons:
        return pd.DataFrame()
    records = []
    for c in coupons:
        amount = float(c.get("couponAmount", 0) or 0)
        remaining = float(c.get("remainingBalance", 0) or 0)
        limit = c.get("numberOfUsesLimit", None)
        status = str(c.get("status", "") or "").strip().upper()
        used = (status == "U")   # indicateur d'usage fiable
        consomme = round(amount - remaining, 2)
        # Niveau d'utilisation. L'indicateur FIABLE est status U/NU : la
        # majorite des coupons ont couponAmount=0 (le solde ne reflete donc PAS
        # l'usage). Regle : non 'U' -> non utilise. 'U' -> utilise ; on precise
        # partiel/entier via le solde UNIQUEMENT pour les coupons a montant
        # non nul (ex. 'Coupon 5%'), sinon on considere 'entier'.
        if not used:
            niveau = "non_utilise"
        elif amount > 0 and 0 < remaining < amount:
            niveau = "partiel"
        else:
            niveau = "entier"
        records.append({
            "code": c.get("couponCode", ""),
            "promotion": _safe_str(c.get("promotion", "")),
            # Type de promotion/discount pour la ventilation (repli sur promotion).
            "type_promo": (_safe_str(c.get("otfCouponRule", ""))
                           or _safe_str(c.get("promotionSearchKey", ""))
                           or _safe_str(c.get("promotion", "")) or "(inconnu)"),
            "montant": amount,
            "solde": remaining,
            "consomme": consomme,
            "limite_utilisations": limit,
            "statut": status,
            "used": used,
            "niveau_utilisation": niveau,
            "usage_status": c.get("usageStatus", ""),
            "bp": _safe_str(c.get("businessPartner", "")),
            "active": bool(c.get("active", False)),
            "creation_date": _safe_normalize_date(c.get("creationDate", "")),
            "valid_from": _safe_normalize_date(c.get("validFrom", "")),
            "valid_to": _safe_normalize_date(c.get("validTo", "")),
        })
    return pd.DataFrame(records)


def compute_coupons_stats(df_coupons: pd.DataFrame, reference_date: str) -> dict:
    """KPIs coupons bases sur le PARC COMPLET (tous les coupons, pas seulement
    'active'), pour coller au back-office.

    Indicateur d'usage = status 'U' (Used). Niveaux via le solde.
    Compteurs du jour : crees ce jour (creationDate == ref). L'API Coupon
    n'exposant pas de date de mise a jour, le 'utilise ce jour' ne peut pas
    etre date avec fiabilite ; on expose donc l'etat GLOBAL (utilises total,
    partiels, entiers) + les creations du jour.
    """
    empty = {"nb_total": 0, "nb_utilises": 0, "nb_non_utilises": 0,
             "nb_partiels": 0, "nb_entiers": 0, "encours": 0.0,
             "taux_util": 0.0, "nb_crees_jour": 0, "nb_expirent_bientot": 0,
             "montant_total": 0.0, "consomme_total": 0.0}
    if df_coupons.empty:
        return empty
    ref = _safe_normalize_date(reference_date)
    df = df_coupons  # PARC COMPLET
    nb_total = int(len(df))
    nb_utilises = int(df["used"].sum())
    nb_non_utilises = nb_total - nb_utilises
    nb_partiels = int((df["niveau_utilisation"] == "partiel").sum())
    nb_entiers = int((df["niveau_utilisation"] == "entier").sum())
    encours = float(df["solde"].sum())
    montant_total = float(df["montant"].sum())
    consomme_total = float(df["consomme"].sum())
    taux = (nb_utilises / nb_total) if nb_total else 0.0
    nb_crees_jour = 0
    nb_expirent = 0
    if ref is not None:
        if "creation_date" in df.columns:
            nb_crees_jour = int((df["creation_date"] == ref).sum())
        horizon = ref + pd.Timedelta(days=30)
        exp = df[(df["solde"] > 0) & (df["valid_to"].notna()) &
                 (df["valid_to"] >= ref) & (df["valid_to"] <= horizon)]
        nb_expirent = int(len(exp))
    return {"nb_total": nb_total, "nb_utilises": nb_utilises,
            "nb_non_utilises": nb_non_utilises, "nb_partiels": nb_partiels,
            "nb_entiers": nb_entiers, "encours": encours,
            "taux_util": round(taux, 4), "nb_crees_jour": nb_crees_jour,
            "nb_expirent_bientot": nb_expirent,
            "montant_total": montant_total, "consomme_total": consomme_total}


def compute_coupons_by_promo(df_coupons: pd.DataFrame) -> pd.DataFrame:
    """Ventilation des KPI coupons par TYPE DE PROMOTION (type_promo).
    Memes indicateurs que le global, declines par type : total, utilises,
    non utilises, partiels, entiers, taux d'utilisation, encours."""
    if df_coupons.empty or "type_promo" not in df_coupons.columns:
        return pd.DataFrame()
    g = df_coupons.groupby("type_promo")
    rows = []
    for promo, sub in g:
        n = len(sub)
        nu = int(sub["used"].sum())
        rows.append({
            "type_promo": promo,
            "total": n,
            "utilises": nu,
            "non_utilises": n - nu,
            "partiels": int((sub["niveau_utilisation"] == "partiel").sum()),
            "entiers": int((sub["niveau_utilisation"] == "entier").sum()),
            "taux_util": round(nu / n, 4) if n else 0.0,
            "montant_total": round(float(sub["montant"].sum()), 2),
            "encours": round(float(sub["solde"].sum()), 2),
        })
    df_out = pd.DataFrame(rows).sort_values("total", ascending=False).reset_index(drop=True)
    return df_out


# ==========================================
# CROISEMENT ENCARTEMENTS / VENTES DU JOUR
# ==========================================
def build_daily_enrollment_report(df_orders: pd.DataFrame,
                                  bp_data: dict,
                                  df_today_subs: pd.DataFrame,
                                  selected_store: str = "") -> dict:
    """Rapport encartements du jour.

    IMPORTANT : le payload byCreationDateRange des souscriptions n'expose PAS
    businessPartner (bp_id vide). On NE croise donc PLUS les souscriptions avec
    les ventes par bp_id (ce croisement produisait un produit cartesien sur la
    chaine vide, d'ou les doublons). Le tableau 'Encartements du jour' liste
    simplement les souscriptions creees ce jour. Le nom Client est resolu de
    facon defensive via memberId <-> nom BP, avec fallback sur le memberId.

    Indicateur cible 'clients connus SANS fidelite' : pour chaque vente du jour,
    un BP non-anonyme SANS programme de fidelite est un client connu sans
    fidelite → potentiel d'encartement a forcer. Compte en BP DISTINCTS.

    NB (constat sur donnees reelles, cf. api_logs/) :
    - Les champs de fidelite cote ORDER (obrlpLoyaltyProgramIds,
      loyaltySubscriptions) sont VIDES → inexploitables.
    - order.businessPartner est une STRING = le searchKey du BP.
    - On croise donc order.businessPartner ↔ bp.search_key (normalise) pour
      recuperer la fidelite du client depuis la base BP.
    - Le payload subscription n'expose AUCUN champ magasin (seul
      organization='Intersport') → le filtre par magasin n'est pas possible ici,
      selected_store est ignore pour les souscriptions.
    """
    df_bp = bp_data["df_bp"]

    # ── Correspondance defensive memberId -> nom BP ──
    member_to_name = {}
    if not df_bp.empty and "member_ids" in df_bp.columns:
        for _, row in df_bp.iterrows():
            mids = row.get("member_ids") or []
            if isinstance(mids, (list, tuple)):
                for mid in mids:
                    if mid:
                        member_to_name[str(mid)] = row.get("nom", "")

    def _resolve_client(mid):
        mid = str(mid) if mid is not None else ""
        if not mid:
            return "Inconnu"
        # 1) nom BP si le memberId est connu ; 2) fallback : le memberId brut
        return member_to_name.get(mid, mid)

    # ── Nombre reel d'encartements du jour (dedup sur la souscription) ──
    if df_today_subs.empty:
        nb_enrolled = 0
    elif "sub_id" in df_today_subs.columns:
        nb_enrolled = int(df_today_subs.drop_duplicates(subset="sub_id").shape[0])
    else:
        nb_enrolled = int(len(df_today_subs))

    # ── Tableau 'Encartements du jour' = liste brute des souscriptions ──
    df_enrolled_detail = pd.DataFrame()
    if not df_today_subs.empty:
        cols = ["memberid", "programme", "catégorie", "carte",
                "store", "terminal", "statut", "points"]
        cols = [c for c in cols if c in df_today_subs.columns]
        df_enrolled_detail = df_today_subs[cols].copy()
        df_enrolled_detail = df_enrolled_detail.rename(columns={
            "store": "magasin_encartement",
            "terminal": "caisse_encartement",
        })
        # Colonne Client resolue via memberId (nom BP si connu, sinon memberId)
        if "memberid" in df_enrolled_detail.columns:
            df_enrolled_detail["BP_Nom"] = df_enrolled_detail["memberid"].map(_resolve_client)
        else:
            df_enrolled_detail["BP_Nom"] = "Inconnu"

    # ── 'Non encartes' : on ne croise plus par bp_id (vide) → non calculable ──
    df_not_enrolled_detail = pd.DataFrame()

    # ── Indicateur cible : clients CONNUS SANS fidelite (BP distincts) ──
    # Base = ventes du jour de clients CONNUS (non-anonymes). On croise le
    # searchKey (porte par order.businessPartner) avec la base BP pour recuperer
    # la fidelite. Un client connu dont le BP n'a AUCUNE souscription = cible.
    nb_known_no_loyalty = 0
    df_known_no_loyalty = pd.DataFrame()
    # Index searchKey normalise -> (nom lisible, fidelite)
    sk_to_info = {}
    if not df_bp.empty and "search_key" in df_bp.columns:
        for _, row in df_bp.iterrows():
            sk = str(row.get("search_key", "")).strip().lower()
            if sk:
                sk_to_info[sk] = {
                    "nom": row.get("nom", "") or sk,
                    "fidelite": bool(row.get("fidélité", False)),
                }
    if not df_orders.empty and "BP_Key" in df_orders.columns:
        df_known = df_orders[~df_orders["is_anonymous"]].copy()
        df_known = df_known[df_known["BP_Key"].astype(str) != ""]
        if not df_known.empty:
            # Fidelite par croisement searchKey ; nom lisible depuis la base BP
            df_known["_fidelite"] = df_known["BP_Key"].map(
                lambda k: sk_to_info.get(k, {}).get("fidelite", False))
            df_known["_nom"] = df_known["BP_Key"].map(
                lambda k: sk_to_info.get(k, {}).get("nom", k))
            df_target = df_known[~df_known["_fidelite"]].copy()
            if not df_target.empty:
                nb_known_no_loyalty = int(df_target["BP_Key"].nunique())
                df_known_no_loyalty = (
                    df_target.groupby(["BP_Key", "_nom"])
                    .agg(nb_tickets=("Ticket", "count"),
                         ca_brut=("Montant Brut", "sum"),
                         ca_net=("Montant Net", "sum"),
                         caisses=("Terminal",
                                  lambda x: ", ".join(sorted(set(str(t) for t in x)))))
                    .reset_index()
                    .rename(columns={"_nom": "BP_Nom"})
                    .sort_values("ca_brut", ascending=False)
                )

    return {
        "df_enrolled_today": df_enrolled_detail,
        "df_not_enrolled_today": df_not_enrolled_detail,
        "nb_enrolled": nb_enrolled,
        "nb_not_enrolled": len(df_not_enrolled_detail),
        "nb_known_no_loyalty": nb_known_no_loyalty,
        "df_known_no_loyalty": df_known_no_loyalty,
    }


# ==========================================
# GRAPHIQUES — COMMANDES
# ==========================================
def _build_time_slots(date_str, freq):
    start = pd.to_datetime(f"{date_str} 00:00:00")
    end = pd.to_datetime(f"{date_str} 23:59:59")
    return pd.DataFrame({"Tranche": pd.date_range(start, end, freq=freq).strftime("%H:%M")})


@st.cache_data(hash_funcs=_DF_HASH, show_spinner=False)
def generate_hourly_chart(df_source, date_str, freq, amount_type, selected_bucket, split_pos_neg):
    df = df_source.copy()
    df["DateHeureObj"] = pd.to_datetime(df["DateHeure"])
    df["Tranche"] = df["DateHeureObj"].dt.floor(freq).dt.strftime("%H:%M")
    df_all = _build_time_slots(date_str, freq)
    dd = pd.to_datetime(date_str).strftime("%d/%m/%Y")
    if split_pos_neg:
        grouped = df.groupby(["Tranche", "Type"])[amount_type].sum().reset_index()
        idx = pd.MultiIndex.from_product(
            [df_all["Tranche"], ["Vente", "Retour"]], names=["Tranche", "Type"]
        ).to_frame(index=False)
        df_final = pd.merge(idx, grouped, on=["Tranche", "Type"], how="left").fillna(0)
        fig = px.bar(df_final, x="Tranche", y=amount_type, color="Type", text_auto=".2f",
                     title=f"Répartition le {dd} (Miroir)",
                     color_discrete_map={"Vente": "#2ca02c", "Retour": "#d62728"})
    else:
        grouped = df.groupby("Tranche")[amount_type].sum().reset_index()
        df_final = pd.merge(df_all, grouped, on="Tranche", how="left").fillna(0)
        fig = px.bar(df_final, x="Tranche", y=amount_type, text_auto=".2f",
                     color=amount_type, color_continuous_scale="Viridis",
                     title=f"Répartition le {dd} (Normal)")
    fig.update_layout(barmode="relative",
                      xaxis=dict(type="category", tickmode="linear", dtick=1, tickangle=-45),
                      yaxis_title="Montant en €", plot_bgcolor="rgba(0,0,0,0)",
                      margin=dict(l=0, r=0, t=40, b=0))
    return fig, df_final


@st.cache_data(hash_funcs=_DF_HASH, show_spinner=False)
def generate_payment_pie(df_source, amount_type):
    df_e = df_source.explode("Paiements")
    grouped = df_e.groupby("Paiements")[amount_type].sum().reset_index()
    grouped = grouped[grouped[amount_type] != 0]
    fig = px.pie(grouped, names="Paiements", values=amount_type,
                 title="Par moyen de paiement", hole=0.35)
    fig.update_traces(textinfo="percent+label")
    return fig


@st.cache_data(hash_funcs=_DF_HASH, show_spinner=False)
def generate_basket_by_items_chart(df_source, amount_type, lang="fr"):
    """Panier moyen (€) par nombre d'articles — VENTES uniquement.

    Illustre l'effet du multi-achat sur le panier (levier cross-sell).
    Les barres sont colorees par le NOMBRE DE PANIERS (nb_tickets) via le
    degrade Viridis (meme code couleur que le CA brut), pas par le montant.
    Titre et axes traduits selon lang.
    Retourne (fig, stats) où stats contient articles_moyen et nb_ventes.
    """
    from i18n import t
    ventes = df_source[df_source["Type"] == "Vente"].copy()
    stats = {"articles_moyen": 0.0, "nb_ventes": 0}
    if ventes.empty:
        return None, stats
    stats["nb_ventes"] = int(len(ventes))
    stats["articles_moyen"] = round(float(ventes["Nb Articles"].mean()), 2)
    grp = (ventes.groupby("Nb Articles")
           .agg(panier_moyen=(amount_type, "mean"),
                nb_tickets=("Ticket", "count"))
           .reset_index())
    grp["panier_moyen"] = grp["panier_moyen"].round(2)
    fig = px.bar(
        grp, x="Nb Articles", y="panier_moyen", text_auto=".2f",
        color="nb_tickets", color_continuous_scale="Viridis",
        title=t("basket_chart_title", lang),
        labels={"nb_tickets": t("legend_nb_baskets", lang)},
        hover_data={"nb_tickets": True},
    )
    fig.update_layout(
        xaxis=dict(title=t("axis_nb_items", lang), type="category"),
        yaxis_title=t("axis_avg_basket", lang), plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=40, b=0),
        coloraxis_colorbar=dict(title=t("legend_nb_baskets", lang)))
    return fig, stats


# ==========================================
# GRAPHIQUES — CLIENTS
# ==========================================
@st.cache_data(hash_funcs=_DF_HASH, show_spinner=False)
def generate_client_map(df_locations):
    df_geo = df_locations.dropna(subset=["lat", "lon"]).copy()
    if df_geo.empty:
        return None
    agg = df_geo.groupby(["region_code", "region_name", "country_code", "lat", "lon"]).agg(
        nb_clients=("bp_id", "nunique"), nb_encartés=("has_loyalty", "sum")).reset_index()
    agg["taux"] = (agg["nb_encartés"] / agg["nb_clients"] * 100).round(1)
    agg["label"] = (agg["region_name"] + " (" + agg["country_code"] + ")<br>"
                    + agg["nb_clients"].astype(str) + " clients<br>Taux : "
                    + agg["taux"].astype(str) + "%")
    agg["bubble_size"] = agg["nb_clients"].clip(lower=3)
    center_lat = agg["lat"].median()
    center_lon = agg["lon"].median()
    fig = px.scatter_map(
        agg, lat="lat", lon="lon", size="bubble_size",
        color="taux", color_continuous_scale="RdYlGn",
        range_color=[0, 100], hover_name="label",
        hover_data={"nb_clients": True, "taux": True,
                    "bubble_size": False, "lat": False, "lon": False},
        map_style="open-street-map",
        center={"lat": center_lat, "lon": center_lon}, zoom=6,
        title="🗺️ Couverture géographique", size_max=40,
    )
    fig.update_layout(margin=dict(l=0, r=0, t=40, b=0), height=600,
                      coloraxis_colorbar=dict(title="Taux (%)"))
    return fig


# ==========================================
# GRAPHIQUES — CARTES CADEAUX
# ==========================================
@st.cache_data(hash_funcs=_DF_HASH, show_spinner=False)
def generate_giftcard_flow_chart(df_txn, reference_date, lang="fr"):
    """Émissions vs consommations de cartes cadeaux sur 7 jours (graphe miroir)."""
    from i18n import t
    if df_txn.empty or "date_ordered" not in df_txn.columns:
        return None
    last_7 = [pd.to_datetime(reference_date).normalize() - pd.Timedelta(days=i)
              for i in range(6, -1, -1)]
    rows = []
    for d in last_7:
        day = df_txn[df_txn["date_ordered"] == d]
        emis = float(day[day["montant"] >= 0]["montant"].sum())
        conso = float(day[day["montant"] < 0]["montant"].sum())
        label = d.strftime("%a %d/%m")
        rows.append({"jour": label, "type": t("gc_issued", lang), "montant": emis})
        rows.append({"jour": label, "type": t("gc_consumed", lang), "montant": conso})
    dfm = pd.DataFrame(rows)
    fig = px.bar(dfm, x="jour", y="montant", color="type", text_auto=".2f",
                 title=t("gc_flow_title", lang),
                 color_discrete_map={t("gc_issued", lang): "#2ca02c",
                                     t("gc_consumed", lang): "#d62728"})
    fig.update_layout(barmode="relative", plot_bgcolor="rgba(0,0,0,0)",
                      yaxis_title="€", xaxis_title="",
                      margin=dict(l=0, r=0, t=40, b=0))
    return fig


@st.cache_data(hash_funcs=_DF_HASH, show_spinner=False)
def generate_giftcard_usage_chart(df_gc, lang="fr"):
    """Repartition des cartes actives par tranche de taux d'utilisation."""
    from i18n import t
    valides = df_gc[(df_gc["active"]) & (~df_gc["cancelled"])].copy()
    if valides.empty:
        return None
    bins = [-0.01, 0.0, 0.25, 0.5, 0.75, 0.9999, 1.0]
    labels = ["0%", "1-25%", "26-50%", "51-75%", "76-99%", "100%"]
    valides["tranche"] = pd.cut(valides["taux_util"], bins=bins, labels=labels)
    grp = valides.groupby("tranche", observed=False).agg(
        nb_cartes=("id", "count"), encours=("solde", "sum")).reset_index()
    fig = px.bar(grp, x="tranche", y="nb_cartes", text_auto=True,
                 color="nb_cartes", color_continuous_scale="Viridis",
                 title=t("gc_usage_title", lang),
                 hover_data={"encours": ":.2f"})
    fig.update_layout(xaxis_title=t("gc_usage_axis", lang), yaxis_title="",
                      plot_bgcolor="rgba(0,0,0,0)",
                      margin=dict(l=0, r=0, t=40, b=0),
                      coloraxis_showscale=False)
    return fig


# ==========================================
# TENDANCES (evolution CA sur N jours)
# ==========================================
@st.cache_data(ttl=900, show_spinner=False)
def build_trend_series(base_url, username, password, store, end_date, n_days=14):
    """Serie CA/tickets par jour sur les n_days derniers jours (via fetch_orders).

    Reutilise fetch_orders (cache) pour chaque jour. Renvoie un DataFrame
    (date, ca_brut, ca_net, nb_tickets, jour_semaine).
    """
    from datetime import timedelta
    rows = []
    for i in range(n_days - 1, -1, -1):
        d = end_date - timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        raw = fetch_orders(base_url, username, password, store, ds)
        df = parse_orders(json.dumps(raw)) if raw else pd.DataFrame()
        ca_brut = float(df["Montant Brut"].sum()) if not df.empty else 0.0
        ca_net = float(df["Montant Net"].sum()) if not df.empty else 0.0
        nb = int(len(df)) if not df.empty else 0
        rows.append({"date": ds, "label": d.strftime("%a %d/%m"),
                     "ca_brut": round(ca_brut, 2), "ca_net": round(ca_net, 2),
                     "nb_tickets": nb, "dow": d.weekday()})
    return pd.DataFrame(rows)


@st.cache_data(hash_funcs=_DF_HASH, show_spinner=False)
def generate_trend_chart(df_trend, amount_col, lang="fr"):
    """Courbe d'evolution du CA sur la periode + barres nb tickets (axe secondaire)."""
    from i18n import t
    if df_trend.empty:
        return None
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=df_trend["label"], y=df_trend["nb_tickets"],
                         name=t("trend_tickets", lang), marker_color="#B7C9E2",
                         opacity=0.6), secondary_y=True)
    fig.add_trace(go.Scatter(x=df_trend["label"], y=df_trend[amount_col],
                             name=t("trend_ca", lang), mode="lines+markers+text",
                             line=dict(color="#2ca02c", width=3),
                             text=[f"{v:,.0f}".replace(",", " ") for v in df_trend[amount_col]],
                             textposition="top center"), secondary_y=False)
    fig.update_layout(title=t("trend_chart_title", lang), plot_bgcolor="rgba(0,0,0,0)",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                  xanchor="right", x=1),
                      margin=dict(l=0, r=0, t=60, b=0))
    fig.update_yaxes(title_text=t("trend_ca", lang), secondary_y=False)
    fig.update_yaxes(title_text=t("trend_tickets", lang), secondary_y=True)
    return fig


@st.cache_data(hash_funcs=_DF_HASH, show_spinner=False)
def generate_dow_chart(df_trend, amount_col, lang="fr"):
    """CA moyen par jour de la semaine (agrege sur la periode)."""
    from i18n import t
    if df_trend.empty:
        return None
    jours = [t("dow_mon", lang), t("dow_tue", lang), t("dow_wed", lang),
             t("dow_thu", lang), t("dow_fri", lang), t("dow_sat", lang), t("dow_sun", lang)]
    grp = df_trend.groupby("dow")[amount_col].mean().reset_index()
    grp["jour"] = grp["dow"].map(lambda x: jours[int(x)])
    grp = grp.sort_values("dow")
    fig = px.bar(grp, x="jour", y=amount_col, text_auto=".0f",
                 color=amount_col, color_continuous_scale="Viridis",
                 title=t("dow_chart_title", lang))
    fig.update_layout(xaxis_title="", yaxis_title=t("trend_ca", lang),
                      plot_bgcolor="rgba(0,0,0,0)", margin=dict(l=0, r=0, t=40, b=0),
                      coloraxis_showscale=False)
    return fig


# ==========================================
# PERFORMANCE PRODUITS (depuis les lignes de commande)
# ==========================================
def parse_daily_products(orders_json: str) -> pd.DataFrame:
    """Extrait les lignes produits des ventes du jour depuis Order.lines.

    Aucun appel API : exploite les Orders deja charges. Chaque ligne donne
    produit, categorie, type, quantite, CA brut/net. Ignore annules/void.
    """
    orders = json.loads(orders_json)
    if not orders:
        return pd.DataFrame()
    records = []
    for o in orders:
        if o.get("isCancelled") or o.get("isVoid"):
            continue
        for line in o.get("lines", []):
            pinfo = line.get("product_info", {}) or {}
            name = (pinfo.get("name") or line.get("productDescription")
                    or _safe_str(line.get("product", "")) or "Inconnu")
            records.append({
                "produit": name,
                "categorie": pinfo.get("productCategoryName", "")
                             or pinfo.get("mainProductCategoryName", "") or "Sans categorie",
                "type": pinfo.get("productType", ""),
                "quantite": float(line.get("orderedQuantity", 0) or 0),
                "ca_brut": float(line.get("grossAmount", 0) or 0),
                "ca_net": float(line.get("netAmount", 0) or 0),
                "ticket": o.get("documentNo", "N/A"),
            })
    return pd.DataFrame(records)


def compute_products_summary(df_prod: pd.DataFrame) -> dict:
    """Synthese produits : top produits (par CA), ventes par categorie."""
    if df_prod.empty:
        return {"nb_lignes": 0, "nb_produits": 0, "ca_total": 0.0,
                "qte_totale": 0.0, "df_by_product": pd.DataFrame(),
                "df_by_category": pd.DataFrame()}
    by_prod = (df_prod.groupby("produit")
               .agg(quantite=("quantite", "sum"), ca_brut=("ca_brut", "sum"),
                    nb_tickets=("ticket", "nunique"))
               .reset_index().sort_values("ca_brut", ascending=False))
    by_prod["ca_brut"] = by_prod["ca_brut"].round(2)
    by_cat = (df_prod.groupby("categorie")
              .agg(quantite=("quantite", "sum"), ca_brut=("ca_brut", "sum"))
              .reset_index().sort_values("ca_brut", ascending=False))
    by_cat["ca_brut"] = by_cat["ca_brut"].round(2)
    return {
        "nb_lignes": int(len(df_prod)),
        "nb_produits": int(df_prod["produit"].nunique()),
        "ca_total": round(float(df_prod["ca_brut"].sum()), 2),
        "qte_totale": round(float(df_prod["quantite"].sum()), 2),
        "df_by_product": by_prod, "df_by_category": by_cat,
    }


@st.cache_data(hash_funcs=_DF_HASH, show_spinner=False)
def generate_top_products_chart(df_by_product, lang="fr", top_n=15):
    """Barres horizontales : top N produits par CA (degrade Viridis par quantite)."""
    from i18n import t
    if df_by_product.empty:
        return None
    df = df_by_product.head(top_n).copy()
    fig = px.bar(df, x="ca_brut", y="produit", orientation="h", text_auto=".2f",
                 color="quantite", color_continuous_scale="Viridis",
                 title=t("prod_top_title", lang),
                 labels={"quantite": t("prod_qty", lang)})
    fig.update_layout(xaxis_title=t("prod_ca", lang), yaxis_title="",
                      yaxis=dict(autorange="reversed"), plot_bgcolor="rgba(0,0,0,0)",
                      margin=dict(l=0, r=0, t=40, b=0),
                      coloraxis_colorbar=dict(title=t("prod_qty", lang)))
    return fig


@st.cache_data(hash_funcs=_DF_HASH, show_spinner=False)
def generate_category_chart(df_by_category, lang="fr"):
    """Camembert : repartition du CA par categorie."""
    from i18n import t
    if df_by_category.empty:
        return None
    df = df_by_category[df_by_category["ca_brut"] > 0].copy()
    fig = px.pie(df, names="categorie", values="ca_brut", hole=0.4,
                 title=t("prod_cat_title", lang))
    fig.update_traces(textinfo="percent+label")
    fig.update_layout(margin=dict(l=0, r=0, t=40, b=0))
    return fig


# ==========================================
# PROMOTIONS APPLIQUEES (depuis les lignes de commande)
# ==========================================
def parse_daily_promotions(orders_json: str) -> pd.DataFrame:
    """Extrait les promotions appliquees depuis OrderLine.promotions[].

    Ne fait AUCUN appel API : exploite les Orders deja charges. Chaque promo
    d'une ligne donne une observation (nom, type, montant remise, quantite,
    couponCode, ticket). Ignore les orders annules/void.
    """
    orders = json.loads(orders_json)
    if not orders:
        return pd.DataFrame()
    records = []
    for o in orders:
        if o.get("isCancelled") or o.get("isVoid"):
            continue
        ticket = o.get("documentNo", "N/A")
        terminal = o.get("terminal", "Inconnu")
        for line in o.get("lines", []):
            for promo in line.get("promotions", []) or []:
                records.append({
                    "promo_nom": promo.get("name", "") or promo.get("identifier", "")
                                 or promo.get("searchKey", "") or "Inconnu",
                    "type_remise": promo.get("discountType", ""),
                    "montant_remise": abs(float(promo.get("totalAmount", 0) or 0)),
                    "quantite": float(promo.get("quantity", 0) or 0),
                    "coupon": promo.get("couponCode", "") or "",
                    "ticket": ticket,
                    "terminal": terminal,
                    "discount_id": promo.get("discountId", "") or promo.get("searchKey", ""),
                })
    return pd.DataFrame(records)


def per_ticket_promo_summary(orders_json: str) -> dict:
    """Agrege, PAR ticket (documentNo), les promos et coupons appliques.

    Lit OrderLine.promotions[] (0 appel API). Sert a afficher dans la liste des
    tickets du jour une icone promo (si >=1 promo) et une icone coupon (si >=1
    couponCode), avec le detail pour le tooltip.

    Renvoie { documentNo: {
        "has_promo": bool, "has_coupon": bool,
        "n_promos": int, "remise_totale": float,
        "promos": [ {name, type, amount} ... ],
        "coupons": [ code, ... ],
        "promo_tooltip": str, "coupon_tooltip": str,
    } }
    """
    try:
        orders = json.loads(orders_json)
    except Exception:
        orders = []
    out = {}
    for o in orders or []:
        if o.get("isCancelled") or o.get("isVoid"):
            continue
        doc = o.get("documentNo", "N/A")
        dt = o.get("localCreationDate") or o.get("creationDate") or ""
        gross = abs(float(o.get("grossAmount", 0) or 0))
        promos, coupons, remise = [], [], 0.0
        for line in o.get("lines", []) or []:
            for promo in line.get("promotions", []) or []:
                nm = (promo.get("name", "") or promo.get("identifier", "")
                      or promo.get("searchKey", "") or "Inconnu")
                amt = abs(float(promo.get("totalAmount", 0) or 0))
                remise += amt
                promos.append({"name": nm,
                               "type": promo.get("discountType", ""),
                               "amount": amt})
                cc = promo.get("couponCode", "") or ""
                if cc:
                    coupons.append(cc)
        if not promos and not coupons:
            # ticket sans promo : on l'enregistre quand meme (icones vides)
            out[doc] = {"has_promo": False, "has_coupon": False, "n_promos": 0,
                        "remise_totale": 0.0, "promos": [], "coupons": [],
                        "promo_tooltip": "", "coupon_tooltip": "",
                        "datetime": dt, "gross": gross}
            continue
        # Tooltips : lignes "nom : montant" / liste des codes (dedupliques)
        uniq_coupons = sorted(set(coupons))
        promo_tt = " | ".join(
            f"{pr['name']} : {pr['amount']:.2f} EUR" if pr["amount"] else pr["name"]
            for pr in promos)
        coupon_tt = " | ".join(uniq_coupons)
        out[doc] = {
            "has_promo": len(promos) > 0,
            "has_coupon": len(uniq_coupons) > 0,
            "n_promos": len(promos),
            "remise_totale": round(remise, 2),
            "promos": promos,
            "coupons": uniq_coupons,
            "promo_tooltip": promo_tt,
            "coupon_tooltip": coupon_tt,
            "datetime": dt,
            "gross": gross,
        }
    return out


def compute_promotions_summary(df_promo: pd.DataFrame) -> dict:
    """Synthese des promotions du jour : nb applications, remise totale,
    nb promos distinctes, nb tickets concernes."""
    if df_promo.empty:
        return {"nb_applications": 0, "remise_totale": 0.0,
                "nb_promos": 0, "nb_tickets": 0, "df_by_promo": pd.DataFrame()}
    by_promo = (df_promo.groupby("promo_nom")
                .agg(nb_applications=("ticket", "count"),
                     remise_totale=("montant_remise", "sum"),
                     nb_tickets=("ticket", "nunique"))
                .reset_index()
                .sort_values("remise_totale", ascending=False))
    by_promo["remise_totale"] = by_promo["remise_totale"].round(2)
    return {
        "nb_applications": int(len(df_promo)),
        "remise_totale": round(float(df_promo["montant_remise"].sum()), 2),
        "nb_promos": int(df_promo["promo_nom"].nunique()),
        "nb_tickets": int(df_promo["ticket"].nunique()),
        "df_by_promo": by_promo,
    }


@st.cache_data(hash_funcs=_DF_HASH, show_spinner=False)
def generate_promotions_chart(df_by_promo, lang="fr"):
    """Barres : remise totale par promotion (degrade Viridis par nb applications)."""
    from i18n import t
    if df_by_promo.empty:
        return None
    df = df_by_promo.head(15).copy()  # top 15 pour lisibilite
    fig = px.bar(df, x="remise_totale", y="promo_nom", orientation="h",
                 text_auto=".2f", color="nb_applications",
                 color_continuous_scale="Viridis",
                 title=t("promo_chart_title", lang),
                 labels={"nb_applications": t("promo_nb_applied", lang)})
    fig.update_layout(
        xaxis_title=t("promo_total_discount", lang), yaxis_title="",
        yaxis=dict(autorange="reversed"), plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=40, b=0),
        coloraxis_colorbar=dict(title=t("promo_nb_applied", lang)))
    return fig


# ==========================================
# EXPORT
# ==========================================
def df_to_csv_bytes(df):
    return df.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")

