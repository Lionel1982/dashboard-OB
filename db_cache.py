"""
============================================================
 Cache SQLite persistant pour les appels API dates.
------------------------------------------------------------
 Objectif : eviter de rappeler l'API pour des donnees d'une
 journee PASSEE, qui ne changent plus jamais.

 FLUX (identique quel que soit l'appelant) :
   - donnee du JOUR demandee -> appel API -> stockage DB ->
     le front lit la valeur (issue de la DB).
   - donnee ANCIENNE -> lue directement depuis la DB, aucun
     appel API.

 Regles de fraicheur :
   - date strictement passee (cote magasin, Europe/Lisbon) =>
     stockage PERMANENT (jamais rappelee).
   - aujourd'hui (ou plage incluant aujourd'hui) => TTL court.
   - reponse VIDE => jamais figee (evite de graver un hoquet API).

 Aucune dependance externe : sqlite3 et zoneinfo sont natifs
 (Python 3.9+). Le fichier .db vit a cote du projet.
============================================================
"""
import os
import json
import time
import sqlite3
import logging
import threading
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from zoneinfo import ZoneInfo
    _STORE_TZ = ZoneInfo("Europe/Lisbon")
except Exception:  # secours si tzdata absent
    _STORE_TZ = None

logger = logging.getLogger("dashboard")

# Emplacement du fichier de cache : a cote de ce module.
_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_cache.db")

# TTL (secondes) pour les donnees encore "vivantes" (aujourd'hui).
_TODAY_TTL_SECONDS = 900  # 15 min

# ------------------------------------------------------------------
# JOURNAL DES CHARGEMENTS (pour visibilite front : API vs traitement)
# ------------------------------------------------------------------
# On garde en memoire les derniers chargements avec leur source et leurs
# timings, pour que le front puisse afficher "servi depuis la DB" ou
# "API : X s". Thread-safe (le tab Encaissements fait du multi-thread).
_LOAD_LOG = []
_LOAD_LOG_LOCK = threading.Lock()
_LOAD_LOG_MAX = 50


def _record_load(label, source, api_seconds, db_seconds, n_items):
    """Enregistre une ligne de chargement consultable par le front."""
    with _LOAD_LOG_LOCK:
        _LOAD_LOG.append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "source": source,          # 'db_frozen' | 'db_today' | 'api'
            "api_seconds": round(api_seconds, 3) if api_seconds is not None else None,
            "db_seconds": round(db_seconds, 3) if db_seconds is not None else None,
            "n_items": n_items,
        })
        if len(_LOAD_LOG) > _LOAD_LOG_MAX:
            del _LOAD_LOG[0:len(_LOAD_LOG) - _LOAD_LOG_MAX]


def get_last_loads(n: int = 10) -> list:
    """Renvoie les n derniers chargements (plus recent en dernier)."""
    with _LOAD_LOG_LOCK:
        return list(_LOAD_LOG[-n:])


def clear_load_log() -> None:
    with _LOAD_LOG_LOCK:
        _LOAD_LOG.clear()


def _store_today() -> date:
    """Date du jour cote magasin (Portugal), pour decider ce qui est fige."""
    if _STORE_TZ is not None:
        return datetime.now(_STORE_TZ).date()
    return datetime.now().date()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")  # meilleur en acces concurrent
    return conn


def init_db() -> None:
    """Cree la table de cache si absente. Idempotent, ne bloque jamais l'app."""
    try:
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_cache (
                    cache_key   TEXT PRIMARY KEY,
                    payload     TEXT NOT NULL,
                    n_items     INTEGER,
                    is_frozen   INTEGER NOT NULL DEFAULT 0,
                    fetched_at  TEXT NOT NULL,
                    expires_at  TEXT
                )
                """
            )
    except Exception as e:
        logger.warning("init_db cache: %s", e)


def _parse_ymd(d: str):
    """'YYYY-MM-DD' -> date, tolerant (renvoie None si invalide)."""
    if not d:
        return None
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def is_frozen_single(order_date: str) -> bool:
    """Une date simple est figee si elle est STRICTEMENT avant aujourd'hui (magasin)."""
    d = _parse_ymd(order_date)
    if d is None:
        return False
    return d < _store_today()


def is_frozen_range(start_date: str, end_date: str) -> bool:
    """Une plage est figee seulement si elle est ENTIEREMENT dans le passe,
    c.-a-d. end_date < aujourd'hui (magasin). Si la plage touche aujourd'hui
    ou le futur, elle reste 'vivante'."""
    e = _parse_ymd(end_date)
    if e is None:
        return False
    return e < _store_today()


def _get_raw(cache_key: str):
    """Lecture bas niveau. Renvoie (payload_obj, source) ou (None, None).
    source = 'db_frozen' ou 'db_today'."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT payload, is_frozen, expires_at FROM api_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None, None
        payload, is_frozen, expires_at = row
        if not is_frozen:
            if not expires_at:
                return None, None
            try:
                if datetime.fromisoformat(expires_at) <= datetime.now():
                    return None, None  # expiree
            except Exception:
                return None, None
        return json.loads(payload), ("db_frozen" if is_frozen else "db_today")
    except Exception as e:
        logger.warning("cache._get_raw(%s): %s", cache_key, e)
        return None, None


def get(cache_key: str):
    """Compat : renvoie le payload si present/valide, sinon None."""
    payload, _src = _get_raw(cache_key)
    return payload


def put(cache_key: str, data, frozen: bool, ttl_seconds: int = None) -> None:
    """Stocke une reponse.

    - frozen=True  : permanent (pas d'expiration).
    - frozen=False : TTL court (_TODAY_TTL_SECONDS).
    Regle de securite : on NE fige PAS une reponse vide (data faux/len 0),
    pour ne pas graver un hoquet API. Une reponse vide du jour est quand
    meme mise en cache court (evite le matraquage pendant 15 min).
    Ne leve jamais.
    """
    try:
        try:
            n_items = len(data) if data is not None else 0
        except Exception:
            n_items = None

        effective_frozen = bool(frozen and n_items)

        now = datetime.now()
        _ttl = ttl_seconds if ttl_seconds is not None else _TODAY_TTL_SECONDS
        expires_at = None if effective_frozen else (now + timedelta(seconds=_ttl)).isoformat()

        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO api_cache (cache_key, payload, n_items, is_frozen, fetched_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload=excluded.payload,
                    n_items=excluded.n_items,
                    is_frozen=excluded.is_frozen,
                    fetched_at=excluded.fetched_at,
                    expires_at=excluded.expires_at
                """,
                (cache_key, json.dumps(data, ensure_ascii=False, default=str),
                 n_items, 1 if effective_frozen else 0, now.isoformat(), expires_at),
            )
    except Exception as e:
        logger.warning("cache.put(%s): %s", cache_key, e)


def fetch_with_cache(cache_key: str, api_call, frozen: bool, label: str = "", ttl_seconds: int = None):
    """Coeur du flux demande (DB-first) avec instrumentation.

    Etapes :
      1. Tenter la DB. Si hit -> on RENVOIE la valeur de la DB (aucune API).
      2. Sinon -> appel API (chronometre), stockage DB, puis on relit la DB
         pour garantir que le front consomme bien la donnee telle que stockee.

    - cache_key : cle stable (ex: 'orders|Magasin|2026-09-25').
    - api_call  : fonction SANS argument -> renvoie la liste JSON de l'API.
    - frozen    : la donnee est-elle definitive (date passee) ? (=> permanent)
    - label     : libelle lisible pour le journal (visibilite front).

    Renvoie le payload (list/dict). Enregistre source + timings dans le journal.
    """
    label = label or cache_key

    # 1) DB d'abord
    t0 = time.perf_counter()
    payload, source = _get_raw(cache_key)
    db_seconds = time.perf_counter() - t0
    if payload is not None:
        n = len(payload) if hasattr(payload, "__len__") else None
        _record_load(label, source, api_seconds=None, db_seconds=db_seconds, n_items=n)
        logger.info("Cache HIT [%s] %s (%.3fs DB, %s items)", source, cache_key, db_seconds, n)
        return payload

    # 2) Miss -> API
    t1 = time.perf_counter()
    data = api_call()
    api_seconds = time.perf_counter() - t1

    # 3) Stockage DB
    put(cache_key, data, frozen=frozen, ttl_seconds=ttl_seconds)

    # 4) Relecture DB pour que le front lise bien la donnee "telle que stockee".
    #    (si la relecture echoue, on retombe sur 'data' fraichement recu.)
    t2 = time.perf_counter()
    stored, stored_src = _get_raw(cache_key)
    db_seconds = time.perf_counter() - t2
    final = stored if stored is not None else data
    n = len(final) if hasattr(final, "__len__") else None
    _record_load(label, "api", api_seconds=api_seconds, db_seconds=db_seconds, n_items=n)
    logger.info("Cache MISS %s -> API %.3fs (+DB %.3fs, %s items)",
                cache_key, api_seconds, db_seconds, n)
    return final


def prefetch_orders_week(fetch_fn, store_name, any_date, max_workers: int = 4) -> dict:
    """Precharge en tache de fond les 7 jours de la SEMAINE de `any_date`.

    Objectif : remplir la DB pour un affichage instantane ensuite. Les jours
    passes seront figes (permanents) ; le jour courant sera en TTL court.

    - fetch_fn   : fonction (store_name, 'YYYY-MM-DD') -> liste JSON. En
                   pratique on passe un lambda qui appelle utils.fetch_orders
                   (lequel passe deja par fetch_with_cache -> remplit la DB).
    - store_name : magasin cible.
    - any_date   : date (datetime.date) ; on prend sa semaine (lundi->dimanche).
    - max_workers: parallelisme (defaut 4).

    Renvoie un dict {'YYYY-MM-DD': n_items} (n_items = -1 si erreur).
    Ne leve jamais : une erreur sur un jour n'interrompt pas les autres.
    """
    # Lundi de la semaine de any_date
    start_week = any_date - timedelta(days=any_date.weekday())
    week_dates = [(start_week + timedelta(days=i)).strftime("%Y-%m-%d")
                  for i in range(7)]

    results = {}

    def _one(ds):
        try:
            data = fetch_fn(store_name, ds)
            n = len(data) if hasattr(data, "__len__") else 0
            return ds, n
        except Exception as e:
            logger.warning("prefetch %s %s: %s", store_name, ds, e)
            return ds, -1

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as exe:
            futures = [exe.submit(_one, ds) for ds in week_dates]
            for f in as_completed(futures):
                ds, n = f.result()
                results[ds] = n
    except Exception as e:
        logger.warning("prefetch_orders_week: %s", e)
    return dict(sorted(results.items()))


def stats() -> dict:
    """Petit resume du cache (pour affichage/debug dans l'app)."""
    try:
        with _connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
            frozen = conn.execute("SELECT COUNT(*) FROM api_cache WHERE is_frozen=1").fetchone()[0]
        return {"total": total, "frozen": frozen, "today": total - frozen, "db_path": _DB_PATH}
    except Exception as e:
        logger.warning("cache.stats: %s", e)
        return {"total": 0, "frozen": 0, "today": 0, "db_path": _DB_PATH}


def delete(cache_key: str) -> bool:
    """Supprime UNE entree de cache par sa cle (pour forcer un rechargement
    cible, ex. bouton Rafraichir des Business Partners). Renvoie True si
    supprimee. Ne leve jamais."""
    try:
        with _connect() as conn:
            cur = conn.execute("DELETE FROM api_cache WHERE cache_key = ?", (cache_key,))
            return cur.rowcount > 0
    except Exception as e:
        logger.warning("cache.delete(%s): %s", cache_key, e)
        return False


def clear_today() -> int:
    """Supprime uniquement les entrees NON figees (donnees du jour).
    Utile pour un bouton 'Rafraichir aujourd'hui'. Renvoie le nb supprime."""
    try:
        with _connect() as conn:
            cur = conn.execute("DELETE FROM api_cache WHERE is_frozen=0")
            return cur.rowcount
    except Exception as e:
        logger.warning("cache.clear_today: %s", e)
        return 0


def clear_all() -> int:
    """Vide entierement le cache (figes compris). Renvoie le nb supprime."""
    try:
        with _connect() as conn:
            cur = conn.execute("DELETE FROM api_cache")
            return cur.rowcount
    except Exception as e:
        logger.warning("cache.clear_all: %s", e)
        return 0
