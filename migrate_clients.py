"""
Script de migration one-shot : clients_config.json -> Supabase (chiffre).

A lancer UNE FOIS en local, apres s'etre assure que le compte cible existe
dans Supabase (cree via create_admin.py). Usage :
    streamlit run migrate_clients.py

Pour chaque client de clients_config.json :
  - chiffre le mot de passe API avec crypto.encrypt_secret (Fernet),
  - insere/maj dans la table client_configs sous l'utilisateur choisi
    (db.upsert_client_config), strictement prive a ce user_id.

Idempotent : relancer n'ecrase que les memes (user_id, name).
"""

import os
import json
import streamlit as st

import db
import crypto

st.set_page_config(page_title="Migration clients", page_icon="\U0001f4e6")
st.title("\U0001f4e6 Migration clients_config.json \u2192 Supabase")
st.caption("Script one-shot : importe tes clients locaux dans Supabase, "
           "mot de passe API chiffre, prives a ton compte.")

CLIENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "clients_config.json")

# 1) Choix du compte cible
users = db.list_users()
if not users:
    st.error("Aucun compte dans Supabase. Cree d'abord ton compte via "
             "create_admin.py.")
    st.stop()

user_map = {f"{u['username']} (id={u['id']}, {u.get('role')})": u["id"] for u in users}
sel = st.selectbox("Compte cible (proprietaire des clients importes)",
                   list(user_map.keys()))
target_user_id = user_map[sel]

# 2) Apercu du fichier local
if not os.path.exists(CLIENTS_FILE):
    st.error(f"Fichier introuvable : {CLIENTS_FILE}")
    st.stop()

data = json.load(open(CLIENTS_FILE, encoding="utf-8"))
clients = (data or {}).get("clients", {})
st.write(f"**{len(clients)} client(s)** trouve(s) dans clients_config.json :")
for name, c in clients.items():
    st.write(f"- **{name}** : `{c.get('endpoint','')}` "
             f"(user API : `{c.get('username','')}`, "
             f"{len(c.get('stores', []))} magasins)")

# 3) Migration
if st.button("\U0001f680 Lancer la migration", type="primary"):
    _PW = "password"
    ok, fail = 0, 0
    for name, c in clients.items():
        try:
            enc = crypto.encrypt_secret(c.get(_PW, ""))
            row = db.upsert_client_config(
                target_user_id, name,
                c.get("endpoint", ""), c.get("username", ""),
                enc, stores=c.get("stores", []))
            if row:
                ok += 1
                st.success(f"\u2705 {name} migre.")
            else:
                fail += 1
                st.error(f"\u274c {name} : echec insertion.")
        except Exception as e:
            fail += 1
            st.error(f"\u274c {name} : {e}")
    st.info(f"Termine : {ok} migre(s), {fail} echec(s).")
    if ok and not fail:
        st.caption("Tu peux maintenant utiliser l'onglet Configuration dans "
                   "l'app (les clients viennent de Supabase). clients_config.json "
                   "n'est plus utilise.")
