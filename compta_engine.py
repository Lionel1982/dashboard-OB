"""
============================================================
 compta_engine.py — Moteur comptable partage (SNC Portugal)
------------------------------------------------------------
 Extrait de export_compta_pt.py pour etre reutilisable a la
 fois par le script CLI ET par le dashboard Streamlit.

 SOURCE DE VERITE UNIQUE : toute correction du moteur profite
 aux deux usages.

 Les VALEURS COMPTABLES VARIABLES (codes journaux, comptes
 produit/TVA par taux, comptes de caisse, mapping des moyens
 de paiement, comptes d'ecart, service, seuil, echeance) sont
 lues depuis compta_config.json s'il existe, sinon on retombe
 sur les DEFAUTS SNC integres ci-dessous.
============================================================
"""
import os
import csv
import json
import logging
from collections import OrderedDict, defaultdict
from datetime import datetime

logger = logging.getLogger("dashboard")

# Cle de repli commune a plusieurs mappings (compte caisse, moyens de paiement).
# Definie en constante pour eviter d'ecrire le litteral entre crochets.
_DEFAULT_KEY = "__" + "default" + "__"

# ============================================================
#  DEFAUTS SNC (utilises si compta_config.json est absent)
# ============================================================
DEFAULTS = {
    "code_journal_ventes": "C2",
    "code_journal_od": "OD",
    "compte_produit_par_taux": {"23.0": "71111", "13.0": "71112",
                                 "6.0": "71113", "0.0": "71114"},
    "compte_produit_defaut": "71111",
    "compte_tva_par_taux": {"23.0": "2433211", "13.0": "2433212",
                            "6.0": "2433213", "0.0": None},
    "compte_tva_defaut": "2433211",
    "compte_caisse_par_terminal": {"__default__": "111"},
    "payment_method_map": {
        "cash": ["111", "NUMERARIO / ESPECES"],
        "manual bank card": ["12111", "TPE / CARTAO MANUAL"],
        "carte bancaire": ["12111", "CARTAO BANCARIO"],
        "credit card": ["12111", "CARTAO DE CREDITO"],
        "credit card manual": ["12111", "CARTAO MANUAL"],
        "multibanco": ["12111", "MULTIBANCO"],
        "mbway": ["12112", "MB WAY"],
        "transfer": ["12113", "TRANSFERENCIA"],
        "check": ["12114", "CHEQUE"],
        "differed check": ["12114", "CHEQUE DIFERIDO"],
        "traveller cheques": ["12115", "TRAVELLER CHEQUES"],
        "n time oney": ["12116", "ONEY (N FOIS)"],
        "edenred": ["2768", "EDENRED"],
        "cadhoc": ["2768", "CADHOC"],
        "cartao-presente": ["2768", "CARTAO OFERTA (UTILISATION)"],
        "gift card": ["2768", "CARTAO OFERTA"],
        "gift certificate": ["2768", "VALE OFERTA"],
        "credit note": ["2768", "NOTA DE CREDITO"],
        "voucher": ["2768", "VALE / VOUCHER"],
        "__default__": ["12119", "OUTROS RECEBIMENTOS"],
    },
    "compte_ecart_negatif": "6888",
    "compte_ecart_positif": "7888",
    "compte_service": "2768",
    "seuil_ecart_orders_cashup": 1.0,
    "echeance_defaut": "30/12/1899",
    "date_fmt_out": "%d/%m/%Y",
}

CONFIG_FILENAME = "compta_config.json"
CSV_HEADER = ["Code J.", "Date", "Piece", "Libelle", "Compte",
              "Debit", "Credit", "Solde", "Echeance"]


def _config_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILENAME)


def load_compta_config():
    """Charge la config comptable. Fusionne le fichier avec les defauts
    (les cles absentes du fichier prennent la valeur par defaut). Ne leve jamais.
    Convertit les cles de taux (str JSON) en float pour l'usage interne."""
    cfg = dict(DEFAULTS)
    path = _config_path()
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                user = json.load(f)
            cfg.update(user or {})
    except Exception as e:
        logger.warning("compta_config lecture: %s (defauts utilises)", e)
    return cfg


def save_compta_config(cfg: dict) -> bool:
    """Ecrit la config comptable. Renvoie True si OK."""
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.warning("compta_config ecriture: %s", e)
        return False


def _rate_key(rate):
    """float 23.0 -> cle '23.0' pour lookup dans les dicts JSON."""
    return f"{float(rate)}"


# ============================================================
#  HELPERS (repris a l'identique de export_compta_pt.py)
# ============================================================
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


def compte_produit(rate, cfg):
    m = cfg["compte_produit_par_taux"]
    k = _rate_key(rate)
    if k in m:
        return m[k]
    logger.warning("Taux produit %s absent -> defaut %s", rate, cfg["compte_produit_defaut"])
    return cfg["compte_produit_defaut"]


def compte_tva(rate, cfg):
    m = cfg["compte_tva_par_taux"]
    k = _rate_key(rate)
    if k in m:
        return m[k]
    logger.warning("Taux TVA %s absent -> defaut %s", rate, cfg["compte_tva_defaut"])
    return cfg["compte_tva_defaut"]


def compte_caisse(terminal_name, cfg):
    m = cfg["compte_caisse_par_terminal"]
    return m.get(terminal_name, m.get(_DEFAULT_KEY, "111"))


def map_payment(label, cfg, unknown_set=None):
    m = cfg["payment_method_map"]
    key = (label or "").strip().lower()
    if key in m:
        return tuple(m[key])
    if unknown_set is not None and label:
        unknown_set.add(label)
    return tuple(m[_DEFAULT_KEY])


def _order_business_date(o):
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
                base = sign * _to_float(ln.get("netAmount"))
                summary[key]["base"][(0.0, ptype)] += base
                if ptype == "S":
                    lbl = (pinfo.get("name") or "SERVICE").strip()
                    summary[key]["services"][lbl] += base
    if ignored_nodate:
        logger.warning("%d Order(s) sans date exploitable ignore(s)", ignored_nodate)
    return summary


# ============================================================
#  ECRITURES
# ============================================================
class Ecriture:
    __slots__ = ("code_j", "date", "piece", "libelle", "compte", "debit", "credit")

    def __init__(self, code_j, d, piece, libelle, compte, debit=0.0, credit=0.0):
        self.code_j = code_j
        self.date = d
        self.piece = piece
        self.libelle = libelle
        self.compte = compte
        self.debit = round(debit, 2)
        self.credit = round(credit, 2)


def _cashup_commercial_date(cu):
    """Journee COMMERCIALE d'un CashUp = businessDate (repli sur la date de
    cloture si absent). Sert UNIQUEMENT au controle d'ecart Orders vs CashUp :
    on compare la meme journee commerciale des deux cotes, alors que le
    rattachement COMPTABLE (dans quel jour tombe l'ecriture) reste sur
    closeDateTime via _cashup_business_date().
    """
    raw = cu.get("businessDate")
    if raw:
        try:
            return datetime.fromisoformat(str(raw)[:19]).date()
        except (ValueError, TypeError):
            try:
                return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass
    return _cashup_business_date(cu)


def _cashup_business_date(cu):
    """Date de rattachement d'un CashUp = DATE DE CLOTURE reelle ('Cash Up Date').

    On utilise closeDateTime en PRIORITE (= colonne 'Cash Up Date' de l'UI
    Openbravo), PAS businessDate : une caisse ouverte le soir et cloturee apres
    minuit porte un businessDate = veille, ce qui la ferait manquer de l'export
    du jour de cloture. Replis successifs si closeDateTime est absent
    (observe sur ~15% des clotures) : creationDate, puis localCreationDate,
    et en dernier recours businessDate.
    """
    for k in ("closeDateTime", "creationDate", "localCreationDate", "businessDate"):
        raw = cu.get(k)
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


def group_cashups(cashups, date_from=None, date_to=None):
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
        logger.warning("%d CashUp sans date exploitable ignore(s)", ignored)
    return OrderedDict(sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])))


def build_group_entries(day, terminal, cashups, unknown_pm, cfg, order_info=None):
    """Construit les ecritures d'une piece (jour x terminal). Logique identique
    a export_compta_pt.py, mais les comptes viennent de cfg."""
    ecr, warn = [], []
    d_str = day.strftime(cfg["date_fmt_out"])
    piece = f"{terminal}-{day.strftime('%Y%m%d')}"
    caisse = compte_caisse(terminal, cfg)

    base_par_taux = defaultdict(float)
    tva_par_taux = defaultdict(float)
    pay_counted = defaultdict(float)
    pay_net = defaultdict(float)
    cash_events = []

    for cu in cashups:
        for t in (cu.get("CashUpTaxInformation") or []):
            rate = _rate_of(t)
            if rate is None:
                warn.append(f"{piece}: taux TVA introuvable ({t.get('name')})")
                rate = 0.0
            otype = str(t.get("ordertype"))
            if otype == "0":
                sign = 1
            elif otype == "1":
                sign = -1
            else:
                montant = _to_float(t.get("taxableAmount")) + _to_float(t.get("amount"))
                if montant:
                    warn.append(f"{piece}: ligne ordertype={otype} ignoree "
                                f"({t.get('name')}, {montant:+.2f}) — encaissee hors CA taxe")
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

    # Journal ventes : PRODUIT (credit) + TVA (credit)
    total_ttc = 0.0
    for rate in sorted(base_par_taux):
        ht = round(base_par_taux[rate], 2)
        if ht > 0:
            ecr.append(Ecriture(cfg["code_journal_ventes"], d_str, piece,
                                 f"PRODUIT {rate:g}", compte_produit(rate, cfg), credit=ht))
            total_ttc += ht
        elif ht < 0:
            ecr.append(Ecriture(cfg["code_journal_ventes"], d_str, piece,
                                 f"PRODUIT {rate:g} (net retours)",
                                 compte_produit(rate, cfg), debit=abs(ht)))
            total_ttc += ht
    for rate in sorted(tva_par_taux):
        tva = round(tva_par_taux[rate], 2)
        cpt = compte_tva(rate, cfg)
        if cpt and tva > 0:
            ecr.append(Ecriture(cfg["code_journal_ventes"], d_str, piece,
                                 f"TVA {rate:g}", cpt, credit=tva))
            total_ttc += tva
        elif cpt and tva < 0:
            ecr.append(Ecriture(cfg["code_journal_ventes"], d_str, piece,
                                 f"TVA {rate:g} (net retours)", cpt, debit=abs(tva)))
            total_ttc += tva

    total_ttc = round(total_ttc, 2)
    if total_ttc > 0:
        ecr.append(Ecriture(cfg["code_journal_ventes"], d_str, piece,
                             terminal, caisse, debit=total_ttc))
    elif total_ttc < 0:
        ecr.append(Ecriture(cfg["code_journal_ventes"], d_str, piece,
                             terminal, caisse, credit=abs(total_ttc)))

    # Journal OD : moyens de paiement
    total_regle = 0.0
    for label in sorted(pay_net):
        montant = round(pay_net[label], 2)
        if not montant:
            continue
        cpt, lib = map_payment(label, cfg, unknown_pm)
        if montant >= 0:
            ecr.append(Ecriture(cfg["code_journal_od"], d_str, piece, lib, cpt, debit=montant))
        else:
            ecr.append(Ecriture(cfg["code_journal_od"], d_str, piece,
                                 f"{lib} (net retours)", cpt, credit=abs(montant)))
        total_regle += montant
    total_regle = round(total_regle, 2)
    if total_regle:
        if total_regle >= 0:
            ecr.append(Ecriture(cfg["code_journal_od"], d_str, piece,
                                 terminal, caisse, credit=total_regle))
        else:
            ecr.append(Ecriture(cfg["code_journal_od"], d_str, piece,
                                 terminal, caisse, debit=abs(total_regle)))

    services_grp = 0.0
    if order_info:
        services_grp = round(sum(order_info.get("services", {}).values()), 2)
    ecart_vente = round(total_ttc + services_grp - total_regle, 2)
    if abs(ecart_vente) >= 0.01:
        detail = f" (dont services/cartes cadeaux {services_grp:+.2f} deja pris en compte)" if services_grp else ""
        warn.append(f"{piece}: ecart ventes+services({round(total_ttc + services_grp, 2)}) "
                    f"vs paiements({total_regle}) = {ecart_vente}{detail}")

    # Journal OD : mouvements de caisse
    for typ, name, pm_label, amt in cash_events:
        lib = (name or f"MOUVEMENT {typ}").upper()
        cpt_bque, _ = map_payment("transfer", cfg)
        ecr.append(Ecriture(cfg["code_journal_od"], d_str, piece, lib, cpt_bque, debit=amt))
        ecr.append(Ecriture(cfg["code_journal_od"], d_str, piece,
                             f"{terminal} (especes)", caisse, credit=amt))

    # Journal ventes : SERVICES (cartes cadeaux) depuis les Orders
    if order_info:
        for lbl, montant in sorted(order_info.get("services", {}).items()):
            m = round(montant, 2)
            if not m:
                continue
            libelle = f"SERVICE {lbl}".strip()
            if m >= 0:
                ecr.append(Ecriture(cfg["code_journal_ventes"], d_str, piece,
                                     libelle, cfg["compte_service"], credit=m))
                ecr.append(Ecriture(cfg["code_journal_ventes"], d_str, piece,
                                     terminal, caisse, debit=m))
            else:
                ecr.append(Ecriture(cfg["code_journal_ventes"], d_str, piece,
                                     f"{libelle} (retour)", cfg["compte_service"], debit=abs(m)))
                ecr.append(Ecriture(cfg["code_journal_ventes"], d_str, piece,
                                     terminal, caisse, credit=abs(m)))

    # Controle d'ecart Orders vs CashUp (par taux)
    if order_info:
        ord_base_by_rate = defaultdict(float)
        for (rate, ptype), val in order_info.get("base", {}).items():
            if ptype != "S":
                ord_base_by_rate[rate] += val
        rates = set(base_par_taux) | set(ord_base_by_rate)
        seuil = cfg["seuil_ecart_orders_cashup"]
        for rate in sorted(rates):
            cb = round(base_par_taux.get(rate, 0.0), 2)
            ob = round(ord_base_by_rate.get(rate, 0.0), 2)
            ecart = round(ob - cb, 2)
            if abs(ecart) >= seuil:
                warn.append(f"{piece} taux {rate:g}%: base Orders={ob} vs CashUp={cb} (ecart {ecart:+.2f})")

    return ecr, warn


def compute_rows(entries, cfg):
    """Solde cumule (debit - credit) reinitialise a chaque nouvelle piece."""
    rows = []
    solde = 0.0
    current_piece = None
    ech = cfg["echeance_defaut"]
    for e in entries:
        if e.piece != current_piece:
            solde = 0.0
            current_piece = e.piece
        solde = round(solde + e.debit - e.credit, 2)
        rows.append([e.code_j, e.date, e.piece, e.libelle, e.compte,
                     f"{e.debit:.2f}", f"{e.credit:.2f}", f"{solde:.2f}", ech])
    return rows


def rows_to_csv_bytes(rows, sep=";"):
    """Genere le CSV (bytes, encoding utf-8-sig) pret pour un download Streamlit."""
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep)
    w.writerow(CSV_HEADER)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def rows_to_xlsx_bytes(rows):
    """Genere le XLSX (bytes) pret pour un download Streamlit. Meme structure
    que le CSV (9 colonnes) ; Debit/Credit/Solde en nombres. Renvoie None si
    openpyxl est absent (l'appelant retombe alors sur le CSV)."""
    try:
        from openpyxl import Workbook
    except ImportError:
        logger.warning("openpyxl absent : export XLSX indisponible.")
        return None
    import io
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet 1"
    ws.append(CSV_HEADER)
    for r in rows:
        # r = [code_j, date, piece, libelle, compte, debit, credit, solde, echeance]
        ws.append([r[0], r[1], r[2], r[3], r[4],
                   float(r[5]), float(r[6]), float(r[7]), r[8]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _merge_order_info(infos):
    """Fusionne plusieurs entrees order_summary (une par journee commerciale)
    en un seul dict {'services': {...}, 'base': {...}}. Ignore les None.
    Utile quand un groupe de clotures couvre plusieurs businessDate."""
    merged = {"services": defaultdict(float), "base": defaultdict(float)}
    found = False
    for info in infos:
        if not info:
            continue
        found = True
        for lbl, val in info.get("services", {}).items():
            merged["services"][lbl] += val
        for key, val in info.get("base", {}).items():
            merged["base"][key] += val
    if not found:
        return None
    return {"services": dict(merged["services"]), "base": dict(merged["base"])}


def build_accounting_rows(cashups, orders, date_from, date_to, cfg=None):
    """Point d'entree haut niveau pour le dashboard.

    cashups / orders : listes JSON brutes (issues de fetch_cashups / fetch_orders).
    Renvoie (rows, warnings) : rows = lignes CSV 9 colonnes (prêtes), warnings = list[str].
    """
    if cfg is None:
        cfg = load_compta_config()
    order_summary = summarize_orders(orders) if orders else {}
    groups = group_cashups(cashups, date_from, date_to)
    all_entries, all_warn = [], []
    unknown_pm = set()
    matched_order_keys = set()
    for (day, terminal), cus in groups.items():
        # Rattachement comptable : (day = date de cloture, terminal).
        # Controle d'ecart Orders vs CashUp : on agrege les Orders de la ou des
        # journees COMMERCIALES (businessDate) couvertes par ce groupe de
        # clotures, meme journee commerciale des deux cotes (cf. decision user).
        biz_dates = {_cashup_commercial_date(cu) for cu in cus}
        oinfo = _merge_order_info(
            [order_summary.get((bd, terminal)) for bd in biz_dates])
        for bd in biz_dates:
            if (bd, terminal) in order_summary:
                matched_order_keys.add((bd, terminal))
        ecr, warn = build_group_entries(day, terminal, cus, unknown_pm, cfg, oinfo)
        all_entries.extend(ecr)
        all_warn.extend(warn)
    # Services presents dans les Orders mais SANS CashUp correspondant
    # (jour x terminal absent du CashUp) : ajout en pieces dediees, comme le CLI.
    if order_summary:
        missing = [k for k in order_summary
                   if k not in matched_order_keys and order_summary[k].get("services")]
        for (day, terminal) in sorted(missing):
            oinfo = order_summary[(day, terminal)]
            ecr, warn = build_group_entries(day, terminal, [], unknown_pm, cfg, oinfo)
            all_entries.extend(ecr)
            all_warn.extend(warn)
    if unknown_pm:
        all_warn.append("Moyens de paiement inconnus (compte defaut applique) : "
                        + ", ".join(sorted(unknown_pm)))
    rows = compute_rows(all_entries, cfg)
    return rows, all_warn


def build_reconciliation_diagnostic(cashups, orders, date_from, date_to, cfg=None):
    """Diagnostic de reconciliation Orders vs CashUp, par (jour x terminal).

    Objectif : expliquer les avertissements d'ecart en montrant, pour chaque
    groupe, s'il manque une cloture de caisse (CashUp) alors que des ventes
    (Orders) existent — cause typique des ecarts systematiques Orders > CashUp.

    Renvoie une liste de dicts tries, un par (jour, terminal) :
      date, terminal, nb_orders, nb_cashups, base_orders, base_cashup,
      ecart, statut.
    statut : 'cloture_manquante' (Orders sans CashUp),
             'cashup_sans_orders' (CashUp sans Orders),
             'ecart' (les deux presents mais base differente > seuil),
             'ok' (aligne).
    """
    if cfg is None:
        cfg = load_compta_config()
    seuil = cfg.get("seuil_ecart_orders_cashup", 1.0)

    # --- Agreger la base HT taxee (type produit 'I') des Orders par (jour, terminal) ---
    ord_base = defaultdict(float)
    ord_count = defaultdict(int)
    dfrom = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None
    dto = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None
    for o in (orders or []):
        if o.get("isCancelled") or o.get("isVoid"):
            continue
        d = _order_business_date(o)
        if d is None:
            continue
        if (dfrom and d < dfrom) or (dto and d > dto):
            continue
        term = _safe_str(o.get("terminal")) or "?"
        key = (d, term)
        ord_count[key] += 1
        sign = -1 if o.get("isReturn") else 1
        for ln in (o.get("lines") or []):
            pinfo = ln.get("product_info") or {}
            if pinfo.get("productType", "I") == "S":
                continue  # services hors base taxee
            for tx in (ln.get("taxes") or []):
                ord_base[key] += sign * _to_float(tx.get("taxableAmount"))

    # --- Agreger la base HT et le nb de CashUp par (JOURNEE COMMERCIALE, terminal) ---
    # Pour comparer Orders et CashUp sur la MEME base temporelle (businessDate),
    # on cle les CashUp par leur journee commerciale (_cashup_commercial_date),
    # PAS par la date de cloture. Le rattachement comptable (closeDateTime) reste
    # gere ailleurs ; ici c'est un controle de coherence des ventes.
    cu_base = defaultdict(float)
    cu_count = defaultdict(int)
    for cu in cashups:
        d = _cashup_commercial_date(cu)
        if d is None:
            continue
        if (dfrom and d < dfrom) or (dto and d > dto):
            continue
        terminal = _safe_str(cu.get("terminal")) or "?"
        cu_count[(d, terminal)] += 1
        for t in (cu.get("CashUpTaxInformation") or []):
            otype = str(t.get("ordertype"))
            if otype == "0":
                cu_base[(d, terminal)] += _to_float(t.get("taxableAmount"))
            elif otype == "1":
                cu_base[(d, terminal)] -= _to_float(t.get("taxableAmount"))

    # --- Fusionner toutes les cles ---
    all_keys = set(ord_count) | set(cu_count)
    rows = []
    for (day, terminal) in sorted(all_keys):
        no = ord_count.get((day, terminal), 0)
        nc = cu_count.get((day, terminal), 0)
        bo = round(ord_base.get((day, terminal), 0.0), 2)
        bc = round(cu_base.get((day, terminal), 0.0), 2)
        ecart = round(bo - bc, 2)
        if nc == 0 and no > 0:
            statut = "cloture_manquante"
        elif no == 0 and nc > 0:
            statut = "cashup_sans_orders"
        elif abs(ecart) >= seuil:
            statut = "ecart"
        else:
            statut = "ok"
        rows.append({
            "date": day.strftime("%Y-%m-%d"), "terminal": terminal,
            "nb_orders": no, "nb_cashups": nc,
            "base_orders": bo, "base_cashup": bc,
            "ecart": ecart, "statut": statut,
        })
    return rows


def get_ticket_detail(orders, day_str, terminal, date_from=None, date_to=None):
    """Detail au niveau TICKET pour un (jour, terminal) donne.

    Renvoie la liste des tickets (Orders) de ce terminal ce jour-la, avec pour
    chacun : n° ticket, heure, base HT taxee (produits 'I'), base services 'S',
    montant net/brut, retour ou non. Sert a identifier les tickets a l'origine
    d'un ecart Orders vs CashUp.

    day_str : 'YYYY-MM-DD' (le jour a analyser). terminal : nom exact.
    """
    target = None
    try:
        target = datetime.strptime(day_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return []
    tickets = []
    for o in (orders or []):
        if o.get("isCancelled") or o.get("isVoid"):
            continue
        d = _order_business_date(o)
        if d != target:
            continue
        term = _safe_str(o.get("terminal")) or "?"
        if term != terminal:
            continue
        sign = -1 if o.get("isReturn") else 1
        base_i = 0.0   # base HT taxee produits
        base_s = 0.0   # base services (cartes cadeaux)
        by_rate = defaultdict(float)
        for ln in (o.get("lines") or []):
            pinfo = ln.get("product_info") or {}
            ptype = pinfo.get("productType", "I")
            for tx in (ln.get("taxes") or []):
                try:
                    rate = round(float(str(tx.get("rate")).replace("%", "").replace(",", ".")), 2)
                except (TypeError, ValueError):
                    rate = 0.0
                amt = sign * _to_float(tx.get("taxableAmount"))
                if ptype == "S":
                    base_s += amt
                else:
                    base_i += amt
                    by_rate[rate] += amt
        time_str = o.get("localCreationDate") or o.get("creationDate") or ""
        heure = str(time_str)[11:19] if len(str(time_str)) >= 19 else ""
        tickets.append({
            "ticket": o.get("documentNo", "N/A"),
            "heure": heure,
            "type": "Retour" if o.get("isReturn") else "Vente",
            "base_ht_produits": round(base_i, 2),
            "base_services": round(base_s, 2),
            "detail_taux": {f"{r:g}%": round(v, 2) for r, v in sorted(by_rate.items())},
            "net": _to_float(o.get("netAmount")),
            "brut": _to_float(o.get("grossAmount")),
        })
    tickets.sort(key=lambda x: x["heure"])
    return tickets
