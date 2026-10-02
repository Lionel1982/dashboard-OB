"""
Script de creation d'un compte (admin ou user) pour le dashboard Openbravo.

A lancer ponctuellement en local (acces aux Secrets Streamlit via st.secrets).
Usage :
    streamlit run create_admin.py

Il affiche un mini-formulaire : identifiant + mot de passe + role, et cree le
compte dans Supabase (mot de passe hache bcrypt). A utiliser pour creer le 1er
compte admin, puis les comptes utilisateurs (en attendant un ecran admin dedie).
"""

import streamlit as st

import db
import auth

st.set_page_config(page_title="Creation de compte", page_icon="\U0001f511")
st.title("\U0001f511 Creation d'un compte")
st.caption("Script d'administration \u2014 cree un compte dans Supabase "
           "(mot de passe hache bcrypt).")

with st.form("create_account"):
    username = st.text_input("Identifiant")
    pwd = st.text_input("Mot de passe", type="password")
    pwd2 = st.text_input("Confirmer le mot de passe", type="password")
    role = st.selectbox("Role", ["admin", "user"], index=0)
    ok = st.form_submit_button("Creer le compte", type="primary")

if ok:
    if not (username or "").strip():
        st.error("Identifiant obligatoire.")
    elif not pwd:
        st.error("Mot de passe obligatoire.")
    elif pwd != pwd2:
        st.error("Les deux mots de passe ne correspondent pas.")
    elif db.get_user_by_username(username.strip()):
        st.error(f"L'identifiant '{username}' existe deja.")
    else:
        h = auth.hash_password(pwd)
        row = db.create_user(username.strip(), h, role=role)
        if row:
            st.success(f"Compte '{username}' ({role}) cree avec succes. "
                       "Vous pouvez maintenant vous connecter dans l'app.")
        else:
            st.error("Echec de la creation (voir logs / connexion Supabase).")

st.divider()
st.subheader("Comptes existants")
users = db.list_users()
if users:
    st.dataframe(
        [{"id": u["id"], "identifiant": u["username"], "role": u.get("role"),
          "cree le": u.get("created_at", "")[:19]} for u in users],
        use_container_width=True, hide_index=True)
else:
    st.caption("Aucun compte pour l'instant (ou connexion Supabase indisponible).")
