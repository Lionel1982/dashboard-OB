import streamlit as st
import logging

from utils import (
    load_config, TIME_BUCKET_MAP, DEFAULT_CLIENT, DEFAULT_STORES,
    cleanup_old_logs, is_persist_log,
)
import db_cache
from i18n import t, LANGUAGES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s")
st.set_page_config(page_title="Dashboard Openbravo", page_icon="\U0001f4b6", layout="wide")

# Nettoyage des logs API au demarrage (une seule fois par session serveur)
# Sauf mode super debug : variable d'environnement PERSIST_LOG=1.
if "_logs_cleaned" not in st.session_state:
    cleanup_old_logs()
    st.session_state["_logs_cleaned"] = True

# Initialisation du cache SQLite persistant (idempotent, ne bloque jamais).
if "_cache_db_init" not in st.session_state:
    db_cache.init_db()
    st.session_state["_cache_db_init"] = True

config = load_config()
if not config:
    st.stop()

base_url = config["endpoint"].rstrip("/")
username = config["username"]
password = config["password"]
client_name = config.get("client", DEFAULT_CLIENT)
store_list = config.get("stores", DEFAULT_STORES)

# ==========================================
# SIDEBAR
# ==========================================
# Selecteur de langue
lang_label = st.sidebar.selectbox("\U0001f310 " + t("language", "fr") + " / Language",
                                  list(LANGUAGES.keys()), index=0)
lang = LANGUAGES[lang_label]

st.sidebar.caption(f"{t('client_api', lang)} : **{client_name}**")
st.sidebar.divider()
st.sidebar.header("\U0001f50d " + t("params", lang))
selected_store = st.sidebar.selectbox(t("store_sales", lang) + " :", store_list)
from datetime import date
selected_date = st.sidebar.date_input(t("date", lang), date.today())
st.sidebar.divider()
st.sidebar.header("\u2699\ufe0f " + t("display", lang))
amount_label = st.sidebar.radio(t("amount", lang) + " :",
                                [t("amount_gross", lang), t("amount_net", lang)])
# Mapping libelle traduit -> valeur interne stable ('Montant Brut'/'Montant Net')
amount_type = "Montant Brut" if amount_label == t("amount_gross", lang) else "Montant Net"
split_pos_neg = st.sidebar.checkbox(t("split_sales_returns", lang), value=False)
selected_bucket = st.sidebar.selectbox(t("bucket", lang) + " :",
                                       list(TIME_BUCKET_MAP.keys()), index=2)
freq = TIME_BUCKET_MAP[selected_bucket]
if is_persist_log():
    st.sidebar.info("\U0001f41e " + t("super_debug", lang))

st.title("\U0001f4ca " + t("app_title", lang))

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10 = st.tabs([
    "\U0001f4b6 " + t("tabs_takings", lang),
    "\U0001f465 " + t("tabs_clients", lang),
    "\U0001f381 " + t("tabs_giftcards", lang),
    "\U0001f3f7\ufe0f " + t("tabs_promotions", lang),
    "\U0001f39f\ufe0f " + t("tabs_coupons", lang),
    "\U0001f4e6 " + t("tabs_products", lang),
    "\U0001f4b0 " + t("tabs_cashdesks", lang),
    "\U0001f4c8 " + t("tabs_trends", lang),
    "\U0001f9d1\u200d\U0001f4bc " + t("tabs_sellers", lang),
    "\U0001f9ee " + t("tabs_promo_sim", lang)])

# Onglet 1
from tab_encaissements import render_encaissements
with tab1:
    render_encaissements(
        base_url=base_url, username=username, password=password,
        selected_store=selected_store, selected_date=selected_date,
        amount_type=amount_type, split_pos_neg=split_pos_neg,
        selected_bucket=selected_bucket, freq=freq, lang=lang,
    )

# Onglet 2
from tab_clients import render_clients
with tab2:
    render_clients(
        base_url=base_url, username=username, password=password,
        client_name=client_name, selected_store=selected_store,
        selected_date=selected_date, amount_type=amount_type, lang=lang,
    )


# Onglet 3
from tab_giftcards import render_giftcards
with tab3:
    render_giftcards(
        base_url=base_url, username=username, password=password,
        selected_date=selected_date, lang=lang,
    )


# Onglet 4 - Promotions
from tab_promotions import render_promotions
with tab4:
    render_promotions(
        base_url=base_url, username=username, password=password,
        selected_store=selected_store, selected_date=selected_date, lang=lang,
    )

# Onglet 5 - Coupons
from tab_coupons import render_coupons
with tab5:
    render_coupons(
        base_url=base_url, username=username, password=password,
        selected_date=selected_date, lang=lang,
    )


# Onglet 6 - Produits
from tab_products import render_products
with tab6:
    render_products(
        base_url=base_url, username=username, password=password,
        selected_store=selected_store, selected_date=selected_date, lang=lang,
    )

# Onglet 7 - Caisses
from tab_cashdesks import render_cashdesks
with tab7:
    render_cashdesks(
        base_url=base_url, username=username, password=password,
        selected_store=selected_store, selected_date=selected_date, lang=lang,
    )

# Onglet 8 - Tendances
from tab_trends import render_trends
with tab8:
    render_trends(
        base_url=base_url, username=username, password=password,
        selected_store=selected_store, selected_date=selected_date,
        amount_type=amount_type, lang=lang,
    )

# Onglet 9 - Vendeurs
from tab_sellers import render_sellers
with tab9:
    render_sellers(
        base_url=base_url, username=username, password=password,
        selected_store=selected_store, selected_date=selected_date,
        amount_type=amount_type, lang=lang,
    )

# Onglet 10 - Simulateur de promotions (moteur External Discount)
from tab_promo_simulator import render_promo_simulator
with tab10:
    render_promo_simulator(
        base_url=base_url, username=username, password=password,
        selected_store=selected_store, selected_date=selected_date,
        config=config, lang=lang,
    )

# ==========================================
# PANNEAU DIAGNOSTIC DU CACHE (sidebar)
# ==========================================
# Visibilite : d'ou viennent les donnees (DB figee / DB jour / API) et
# combien de temps a pris chaque etape (API vs relecture DB).
st.sidebar.divider()
with st.sidebar.expander("\U0001f5c4\ufe0f " + t("cache_panel", lang), expanded=False):
    _cst = db_cache.stats()
    cc1, cc2, cc3 = st.columns(3)
    cc1.metric(t("cache_frozen", lang), _cst.get("frozen", 0))
    cc2.metric(t("cache_today", lang), _cst.get("today", 0))
    cc3.metric(t("cache_total", lang), _cst.get("total", 0))

    # --- Prechargement de la semaine (remplit la DB -> affichage instantane) ---
    if st.button("\u26a1 " + t("cache_prefetch_week", lang), key="btn_prefetch_week"):
        from utils import fetch_orders as _fo
        with st.spinner(t("cache_prefetch_running", lang)):
            _res = db_cache.prefetch_orders_week(
                lambda store, ds: _fo(base_url, username, password, store, ds),
                selected_store, selected_date)
        _ok = sum(1 for v in _res.values() if v >= 0)
        _tot_items = sum(v for v in _res.values() if v >= 0)
        st.success(t("cache_prefetch_done", lang).format(
            days=_ok, items=_tot_items))
        st.rerun()

    loads = db_cache.get_last_loads(12)
    if loads:
        st.caption(t("cache_recent_loads", lang))
        _icon = {"db_frozen": "\U0001f9ca", "db_today": "\U0001f4c5", "api": "\U0001f310"}
        _lbl = {"db_frozen": t("src_db_frozen", lang),
                "db_today": t("src_db_today", lang),
                "api": t("src_api", lang)}
        rows = []
        for ld in reversed(loads):  # plus recent en haut
            src = ld.get("source", "")
            if src == "api":
                timing = f"API {ld.get('api_seconds', 0):.2f}s (+DB {ld.get('db_seconds', 0):.2f}s)"
            else:
                timing = f"DB {ld.get('db_seconds', 0):.3f}s"
            rows.append({
                "": _icon.get(src, "?"),
                t("cache_source", lang): _lbl.get(src, src),
                t("cache_detail", lang): ld.get("label", ""),
                t("cache_timing", lang): timing,
                "items": ld.get("n_items", ""),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption(t("cache_no_loads", lang))

    b1, b2 = st.columns(2)
    if b1.button("\U0001f504 " + t("cache_clear_today", lang), key="btn_clear_today"):
        n = db_cache.clear_today()
        db_cache.clear_load_log()
        st.success(t("cache_cleared_today", lang).format(n=n))
        st.rerun()
    if b2.button("\U0001f5d1\ufe0f " + t("cache_clear_all", lang), key="btn_clear_all"):
        n = db_cache.clear_all()
        db_cache.clear_load_log()
        st.success(t("cache_cleared_all", lang).format(n=n))
        st.rerun()
