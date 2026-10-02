
"""
Données géographiques pour la France et le Portugal.
Coordonnées des départements/districts, noms et fonctions de résolution.
"""

import logging

logger = logging.getLogger("dashboard")

# ==========================================
# FRANCE — Coordonnées par département
# ==========================================
FR_DEPT_COORDS = {
    "01": (46.07, 5.35), "02": (49.48, 3.61), "03": (46.37, 3.17),
    "04": (44.09, 6.24), "05": (44.66, 6.26), "06": (43.84, 7.11),
    "07": (44.75, 4.60), "08": (49.62, 4.63), "09": (42.93, 1.50),
    "10": (48.30, 4.08), "11": (43.11, 2.41), "12": (44.28, 2.68),
    "13": (43.49, 5.25), "14": (49.09, -0.37), "15": (45.05, 2.67),
    "16": (45.72, 0.16), "17": (45.90, -0.74), "18": (47.02, 2.50),
    "19": (45.37, 1.87), "2A": (41.87, 8.98), "2B": (42.43, 9.21),
    "21": (47.42, 4.66), "22": (48.44, -2.98), "23": (46.08, 2.03),
    "24": (45.14, 0.75), "25": (47.16, 6.35), "26": (44.68, 5.16),
    "27": (49.11, 1.22), "28": (48.31, 1.34), "29": (48.27, -4.22),
    "30": (44.03, 4.18), "31": (43.35, 1.20), "32": (43.69, 0.45),
    "33": (44.83, -0.69), "34": (43.59, 3.46), "35": (48.15, -1.64),
    "36": (46.81, 1.60), "37": (47.28, 0.73), "38": (45.26, 5.73),
    "39": (46.73, 5.72), "40": (43.98, -0.77), "41": (47.62, 1.33),
    "42": (45.73, 4.16), "43": (45.13, 3.65), "44": (47.28, -1.68),
    "45": (47.91, 2.15), "46": (44.62, 1.68), "47": (44.35, 0.46),
    "48": (44.53, 3.50), "49": (47.39, -0.62), "50": (48.95, -1.34),
    "51": (48.95, 3.94), "52": (48.13, 5.26), "53": (48.07, -0.77),
    "54": (48.78, 6.17), "55": (49.01, 5.38), "56": (47.83, -2.83),
    "57": (49.02, 6.60), "58": (47.12, 3.50), "59": (50.36, 3.26),
    "60": (49.42, 2.42), "61": (48.57, 0.09), "62": (50.49, 2.29),
    "63": (45.72, 3.14), "64": (43.28, -0.77), "65": (43.05, 0.15),
    "66": (42.60, 2.50), "67": (48.66, 7.55), "68": (47.88, 7.24),
    "69": (45.87, 4.64), "70": (47.62, 6.15), "71": (46.64, 4.42),
    "72": (47.93, 0.20), "73": (45.48, 6.39), "74": (46.07, 6.41),
    "75": (48.86, 2.35), "76": (49.66, 1.08), "77": (48.62, 2.98),
    "78": (48.81, 1.88), "79": (46.52, -0.26), "80": (49.92, 2.27),
    "81": (43.79, 2.15), "82": (44.08, 1.25), "83": (43.47, 6.22),
    "84": (44.05, 5.19), "85": (46.68, -1.33), "86": (46.66, 0.48),
    "87": (45.88, 1.26), "88": (48.18, 6.45), "89": (47.84, 3.57),
    "90": (47.63, 6.87), "91": (48.53, 2.27), "92": (48.83, 2.24),
    "93": (48.91, 2.48), "94": (48.77, 2.47), "95": (49.07, 2.17),
    "971": (16.25, -61.55), "972": (14.64, -61.02), "973": (3.92, -53.23),
    "974": (-21.11, 55.53), "976": (-12.78, 45.15),
}

FR_DEPT_NAMES = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Hte-Provence",
    "05": "Hautes-Alpes", "06": "Alpes-Maritimes", "07": "Ardèche", "08": "Ardennes",
    "09": "Ariège", "10": "Aube", "11": "Aude", "12": "Aveyron",
    "13": "Bouches-du-Rhône", "14": "Calvados", "15": "Cantal", "16": "Charente",
    "17": "Charente-Maritime", "18": "Cher", "19": "Corrèze",
    "2A": "Corse-du-Sud", "2B": "Haute-Corse", "21": "Côte-d'Or",
    "22": "Côtes-d'Armor", "23": "Creuse", "24": "Dordogne", "25": "Doubs",
    "26": "Drôme", "27": "Eure", "28": "Eure-et-Loir", "29": "Finistère",
    "30": "Gard", "31": "Haute-Garonne", "32": "Gers", "33": "Gironde",
    "34": "Hérault", "35": "Ille-et-Vilaine", "36": "Indre", "37": "Indre-et-Loire",
    "38": "Isère", "39": "Jura", "40": "Landes", "41": "Loir-et-Cher",
    "42": "Loire", "43": "Haute-Loire", "44": "Loire-Atlantique", "45": "Loiret",
    "46": "Lot", "47": "Lot-et-Garonne", "48": "Lozère", "49": "Maine-et-Loire",
    "50": "Manche", "51": "Marne", "52": "Haute-Marne", "53": "Mayenne",
    "54": "Meurthe-et-Moselle", "55": "Meuse", "56": "Morbihan", "57": "Moselle",
    "58": "Nièvre", "59": "Nord", "60": "Oise", "61": "Orne",
    "62": "Pas-de-Calais", "63": "Puy-de-Dôme", "64": "Pyrénées-Atlantiques",
    "65": "Hautes-Pyrénées", "66": "Pyrénées-Orientales", "67": "Bas-Rhin",
    "68": "Haut-Rhin", "69": "Rhône", "70": "Haute-Saône", "71": "Saône-et-Loire",
    "72": "Sarthe", "73": "Savoie", "74": "Haute-Savoie", "75": "Paris",
    "76": "Seine-Maritime", "77": "Seine-et-Marne", "78": "Yvelines",
    "79": "Deux-Sèvres", "80": "Somme", "81": "Tarn", "82": "Tarn-et-Garonne",
    "83": "Var", "84": "Vaucluse", "85": "Vendée", "86": "Vienne",
    "87": "Haute-Vienne", "88": "Vosges", "89": "Yonne",
    "90": "Territoire de Belfort", "91": "Essonne", "92": "Hauts-de-Seine",
    "93": "Seine-Saint-Denis", "94": "Val-de-Marne", "95": "Val-d'Oise",
    "971": "Guadeloupe", "972": "Martinique", "973": "Guyane",
    "974": "La Réunion", "976": "Mayotte",
}

# ==========================================
# PORTUGAL — Coordonnées par district
# ==========================================
PT_DISTRICT_COORDS = {
    "Aveiro": (40.64, -8.65), "Beja": (38.02, -7.87),
    "Braga": (41.55, -8.43), "Bragança": (41.81, -6.76),
    "Castelo Branco": (39.82, -7.49), "Coimbra": (40.21, -8.43),
    "Évora": (38.57, -7.91), "Faro": (37.02, -7.93),
    "Guarda": (40.54, -7.27), "Leiria": (39.74, -8.81),
    "Lisboa": (38.72, -9.14), "Portalegre": (39.30, -7.43),
    "Porto": (41.15, -8.61), "Santarém": (39.24, -8.69),
    "Setúbal": (38.52, -8.89), "Viana do Castelo": (41.69, -8.83),
    "Vila Real": (41.30, -7.74), "Viseu": (40.66, -7.91),
    "Madeira": (32.65, -16.91), "Açores": (37.74, -25.68),
}

# Code postal PT (2 premiers chiffres) → district
PT_POSTAL_TO_DISTRICT = {
    "10": "Lisboa", "11": "Lisboa", "12": "Lisboa", "13": "Lisboa",
    "14": "Lisboa", "15": "Lisboa", "16": "Lisboa", "17": "Lisboa",
    "18": "Lisboa", "19": "Lisboa",
    "20": "Santarém", "21": "Santarém", "22": "Leiria", "23": "Leiria",
    "24": "Leiria", "25": "Leiria", "26": "Lisboa", "27": "Lisboa",
    "28": "Setúbal", "29": "Setúbal",
    "30": "Coimbra", "31": "Leiria", "32": "Coimbra", "33": "Coimbra",
    "34": "Coimbra", "35": "Viseu", "36": "Viseu",
    "37": "Aveiro", "38": "Aveiro", "39": "Aveiro",
    "40": "Porto", "41": "Porto", "42": "Porto", "43": "Porto",
    "44": "Porto", "45": "Porto", "46": "Porto",
    "47": "Braga", "48": "Braga", "49": "Viana do Castelo",
    "50": "Vila Real", "51": "Viseu", "52": "Bragança",
    "53": "Bragança", "54": "Vila Real", "55": "Vila Real",
    "60": "Castelo Branco", "61": "Castelo Branco", "62": "Castelo Branco",
    "63": "Guarda", "64": "Guarda",
    "70": "Évora", "71": "Évora", "72": "Évora",
    "73": "Portalegre", "74": "Portalegre",
    "75": "Setúbal", "76": "Beja", "77": "Beja", "78": "Beja", "79": "Beja",
    "80": "Faro", "81": "Faro", "82": "Faro", "83": "Faro", "84": "Faro",
    "85": "Faro", "86": "Faro", "87": "Faro", "88": "Faro", "89": "Faro",
    "90": "Madeira", "91": "Madeira", "92": "Madeira",
    "93": "Madeira", "94": "Madeira",
    "95": "Açores", "96": "Açores", "97": "Açores",
    "98": "Açores", "99": "Açores",
}


# Code postal PT (4 premiers chiffres) -> commune precise (lat, lon, nom).
# Affine la geolocalisation au-dela du district (ex. Felgueiras vs Amarante,
# tous deux dans le district de Porto = prefixe 46). Fallback district sinon.
PT_POSTAL4_TO_CITY = {
    # ── Zone client Intersport (Nord, district de Porto) ──
    "4610": (41.36, -8.19, "Felgueiras"),
    "4650": (41.36, -8.19, "Felgueiras"),
    "4600": (41.27, -8.08, "Amarante"),
    "4660": (41.27, -8.08, "Amarante"),
    "4620": (41.29, -8.11, "Lousada"),
    "4580": (41.20, -8.33, "Paredes"),
    "4560": (41.17, -8.28, "Penafiel"),
    "4630": (41.18, -7.90, "Marco de Canaveses"),
    # ── Grand Porto ──
    "4000": (41.15, -8.61, "Porto"),
    "4050": (41.15, -8.62, "Porto"),
    "4100": (41.16, -8.65, "Porto"),
    "4150": (41.16, -8.67, "Porto"),
    "4200": (41.18, -8.59, "Porto"),
    "4250": (41.18, -8.62, "Porto"),
    "4300": (41.16, -8.57, "Porto"),
    "4350": (41.17, -8.58, "Porto"),
    "4400": (41.12, -8.61, "Vila Nova de Gaia"),
    "4430": (41.13, -8.61, "Vila Nova de Gaia"),
    "4450": (41.18, -8.70, "Matosinhos"),
    "4460": (41.20, -8.66, "Senhora da Hora"),
    "4470": (41.24, -8.67, "Maia"),
    "4480": (41.35, -8.74, "Vila do Conde"),
    "4490": (41.38, -8.75, "Povoa de Varzim"),
    "4500": (41.13, -8.60, "Espinho"),
    "4520": (40.99, -8.64, "Santa Maria da Feira"),
    "4535": (41.00, -8.55, "Lourosa"),
    "4700": (41.55, -8.43, "Braga"),
    "4710": (41.55, -8.42, "Braga"),
    "4715": (41.53, -8.44, "Braga"),
    "4750": (41.53, -8.61, "Barcelos"),
    "4760": (41.36, -8.56, "Vila Nova de Famalicao"),
    "4800": (41.44, -8.29, "Guimaraes"),
    "4810": (41.44, -8.29, "Guimaraes"),
    "4820": (41.42, -8.20, "Fafe"),
    "4900": (41.70, -8.83, "Viana do Castelo"),
    # ── Centre ──
    "3000": (40.21, -8.43, "Coimbra"),
    "3030": (40.20, -8.41, "Coimbra"),
    "3800": (40.64, -8.65, "Aveiro"),
    "3810": (40.64, -8.65, "Aveiro"),
    "2400": (39.74, -8.81, "Leiria"),
    "2000": (39.24, -8.69, "Santarem"),
    # ── Lisbonne & Sud ──
    "1000": (38.72, -9.14, "Lisboa"),
    "1050": (38.73, -9.15, "Lisboa"),
    "1100": (38.71, -9.13, "Lisboa"),
    "1200": (38.71, -9.15, "Lisboa"),
    "1500": (38.75, -9.18, "Lisboa"),
    "1700": (38.77, -9.13, "Lisboa"),
    "1800": (38.77, -9.11, "Lisboa"),
    "2600": (38.95, -9.03, "Vila Franca de Xira"),
    "2700": (38.80, -9.31, "Amadora"),
    "2750": (38.70, -9.42, "Cascais"),
    "2780": (38.69, -9.32, "Oeiras"),
    "2800": (38.66, -9.16, "Almada"),
    "2900": (38.52, -8.89, "Setubal"),
    "8000": (37.02, -7.93, "Faro"),
    "8500": (37.13, -8.54, "Portimao"),
    # ── Iles ──
    "9000": (32.65, -16.91, "Funchal"),
    "9500": (37.74, -25.68, "Ponta Delgada"),
}


# ==========================================
# FONCTIONS DE RÉSOLUTION GÉOGRAPHIQUE
# ==========================================
def _clean_zipcode(zipcode: str) -> str:
    """Nettoie un code postal (supprime espaces, tirets)."""
    if not zipcode:
        return ""
    return zipcode.strip().replace(" ", "").replace("-", "")


def detect_country(zipcode: str, country_hint: str = "") -> str:
    """
    Détecte le pays à partir du code postal et/ou du champ country.
    Retourne "FR", "PT" ou "UNKNOWN".
    """
    hint = country_hint.lower() if country_hint else ""
    if any(k in hint for k in ["portugal", "pt", "portugais"]):
        return "PT"
    if any(k in hint for k in ["france", "fr", "français"]):
        return "FR"

    clean = _clean_zipcode(zipcode)
    if not clean:
        return "UNKNOWN"

    # Portugal : 4 chiffres ou 7 chiffres (XXXX-XXX → nettoyé = XXXXXXX)
    if len(clean) == 4 and clean.isdigit():
        return "PT"
    if len(clean) == 7 and clean.isdigit():
        return "PT"

    # France : 5 chiffres
    if len(clean) == 5 and clean.isdigit():
        return "FR"

    return "UNKNOWN"


def _validate_zipcode(raw: str, clean: str, country: str) -> None:
    """Signale (log) un code postal suspect sans bloquer la resolution.

    - FR : doit faire 5 chiffres (ou 2A/2B + 3). Longueur != 5 = suspect.
    - PT : doit faire 4 ou 7 chiffres (XXXX ou XXXX-XXX). Autre = suspect.
    """
    if not clean:
        return
    if country == "FR":
        core = clean
        if core[:2] in ("2A", "2B"):
            core = core[2:]
            if len(core) != 3 or not core.isdigit():
                logger.warning("Code postal FR suspect (Corse) : %r", raw)
            return
        if len(clean) != 5 or not clean.isdigit():
            logger.warning("Code postal FR suspect (attendu 5 chiffres) : %r", raw)
    elif country == "PT":
        if len(clean) not in (4, 7) or not clean.isdigit():
            logger.warning("Code postal PT suspect (attendu 4 ou 7 chiffres) : %r", raw)


def get_region_info(zipcode: str, country_hint: str = "") -> dict:
    """
    Retourne les infos de région à partir d'un code postal.
    {region_code, region_name, lat, lon, country}
    """
    result = {"region_code": None, "region_name": "Inconnu",
              "lat": None, "lon": None, "country": "UNKNOWN"}

    clean = _clean_zipcode(zipcode)
    if not clean:
        return result

    country = detect_country(zipcode, country_hint)
    result["country"] = country
    _validate_zipcode(zipcode, clean, country)

    if country == "FR":
        # France : préfixe 2 ou 3 chiffres (DOM-TOM)
        if clean.startswith("97") and len(clean) >= 5:
            prefix = clean[:3]
        elif clean[:2] in ("2A", "2B"):
            prefix = clean[:2]
        else:
            prefix = clean[:2]

        if prefix in FR_DEPT_COORDS:
            coords = FR_DEPT_COORDS[prefix]
            result["region_code"] = prefix
            result["region_name"] = FR_DEPT_NAMES.get(prefix, prefix)
            result["lat"] = coords[0]
            result["lon"] = coords[1]

    elif country == "PT":
        # Portugal : d'abord la commune via prefixe 4 chiffres (plus precis),
        # sinon fallback sur le district via prefixe 2 chiffres.
        prefix4 = clean[:4]
        if prefix4 in PT_POSTAL4_TO_CITY:
            lat, lon, city = PT_POSTAL4_TO_CITY[prefix4]
            result["region_code"] = prefix4
            result["region_name"] = city
            result["lat"] = lat
            result["lon"] = lon
        else:
            prefix = clean[:2]
            district = PT_POSTAL_TO_DISTRICT.get(prefix)
            if district and district in PT_DISTRICT_COORDS:
                coords = PT_DISTRICT_COORDS[district]
                result["region_code"] = prefix
                result["region_name"] = district
                result["lat"] = coords[0]
                result["lon"] = coords[1]

    return result

