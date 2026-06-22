"""
Scanner Fleet Management — Streamlit UI (CLOUD)
================================================
Version cloud : Azure SQL Database + pymssql.
Credentials lus depuis st.secrets (fichier .streamlit/secrets.toml).

Version locale : voir app.py (pyodbc + Windows Auth sur localhost).

Déployer : streamlit run app_cloud.py
"""

import streamlit as st
import pandas as pd
import pymssql
import json
import hashlib
import os
from datetime import date, datetime, timedelta
from contextlib import contextmanager
import plotly.express as px
import numpy as np

# ── Configuration (Azure SQL via st.secrets) ───────────────────────────────

SQL_SERVER_CONFIG = {
    "server": st.secrets["azure_sql"]["server"],
    "database": st.secrets["azure_sql"]["database"],
    "user": st.secrets["azure_sql"]["user"],
    "password": st.secrets["azure_sql"]["password"],
}

USE_SQL_SERVER = True

# ── Connexion DB ────────────────────────────────────────────────────────────

@contextmanager
def get_connection():
    cfg = SQL_SERVER_CONFIG
    conn = pymssql.connect(
        server=cfg["server"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
    )
    try:
        yield conn
    finally:
        conn.close()


def _sql_adapt(sql):
    """pymssql utilise %s au lieu de ? comme placeholder."""
    return sql.replace("?", "%s")


def run_query(sql, params=None):
    with get_connection() as conn:
        p = _convert_params(params)
        return pd.read_sql(_sql_adapt(sql), conn, params=tuple(p) if p else None)


def _convert_params(params):
    if not params:
        return params
    result = []
    for p in params:
        if hasattr(p, 'item'):
            result.append(p.item())
        else:
            result.append(p)
    return result


def run_execute(sql, params=None):
    with get_connection() as conn:
        cur = conn.cursor()
        p = _convert_params(params)
        cur.execute(_sql_adapt(sql), tuple(p) if p else None)
        conn.commit()
    # Vider le cache après chaque modification pour rafraîchir les données
    st.cache_data.clear()


def sql_top(query_body, n, order_by):
    return f"SELECT TOP {n} {query_body} {order_by}"


def sql_cast_text(column):
    return f"CAST({column} AS VARCHAR(50))"


# ── Initialisation table codes postaux ──────────────────────────────────────

def _get_province(cp):
    """Renvoie la province belge pour un code postal donné."""
    if 1000 <= cp <= 1299: return "Bruxelles-Capitale"
    elif 1300 <= cp <= 1499: return "Brabant wallon"
    elif 1500 <= cp <= 1999: return "Brabant flamand"
    elif 2000 <= cp <= 2999: return "Anvers"
    elif 3000 <= cp <= 3499: return "Brabant flamand"
    elif 3500 <= cp <= 3999: return "Limbourg"
    elif 4000 <= cp <= 4999: return "Liège"
    elif 5000 <= cp <= 5999: return "Namur"
    elif 6000 <= cp <= 6599: return "Hainaut"
    elif 6600 <= cp <= 6999: return "Luxembourg"
    elif 7000 <= cp <= 7999: return "Hainaut"
    elif 8000 <= cp <= 8999: return "Flandre occidentale"
    elif 9000 <= cp <= 9999: return "Flandre orientale"
    return None


def init_codes_postaux():
    """Crée et remplit DimCodesPostaux si elle n'existe pas."""
    with get_connection() as conn:
        cur = conn.cursor()
        if USE_SQL_SERVER:
            cur.execute("""
                IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'DimCodesPostaux')
                BEGIN
                    CREATE TABLE DimCodesPostaux (
                        Code_Postal INT PRIMARY KEY,
                        Province NVARCHAR(50) NOT NULL
                    )
                END
            """)
            conn.commit()
            cur.execute("SELECT COUNT(*) FROM DimCodesPostaux")
            count = cur.fetchone()[0]
            if count == 0:
                rows = [(cp, _get_province(cp)) for cp in range(1000, 10000) if _get_province(cp)]
                for batch_start in range(0, len(rows), 500):
                    batch = rows[batch_start:batch_start+500]
                    values = ",".join(f"({cp}, N'{prov}')" for cp, prov in batch)
                    cur.execute(f"INSERT INTO DimCodesPostaux (Code_Postal, Province) VALUES {values}")
                conn.commit()
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS DimCodesPostaux (
                    Code_Postal INTEGER PRIMARY KEY,
                    Province TEXT NOT NULL
                )
            """)
            cur.execute("SELECT COUNT(*) FROM DimCodesPostaux")
            count = cur.fetchone()[0]
            if count == 0:
                rows = [(cp, _get_province(cp)) for cp in range(1000, 10000) if _get_province(cp)]
                cur.executemany("INSERT INTO DimCodesPostaux VALUES (?, ?)", rows)
            conn.commit()


def init_via_maca():
    """Ajoute les colonnes Via_Maca et Via_Maca_Fin (BIT DEFAULT 1) à FactMovementsHistory,
    puis backfill les lignes existantes et corrige l'historique.
    Via_Maca = transport de la livraison (DateDebut)
    Via_Maca_Fin = transport de la récupération (DateFin)"""
    with get_connection() as conn:
        cur = conn.cursor()
        if USE_SQL_SERVER:
            # Élargir la colonne Action si nécessaire (VARCHAR 25 trop court pour 'déménagement (installation)')
            cur.execute("""
                IF EXISTS (
                    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_NAME = 'FactMovementsHistory' AND COLUMN_NAME = 'Action'
                      AND CHARACTER_MAXIMUM_LENGTH < 50
                )
                BEGIN
                    ALTER TABLE FactMovementsHistory ALTER COLUMN Action VARCHAR(50)
                END
            """)
            conn.commit()
            cur.execute("""
                IF NOT EXISTS (
                    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_NAME = 'FactMovementsHistory' AND COLUMN_NAME = 'Via_Maca'
                )
                BEGIN
                    ALTER TABLE FactMovementsHistory ADD Via_Maca BIT DEFAULT 1
                END
            """)
            conn.commit()
            # Ajouter Via_Maca_Fin (transport récupération)
            cur.execute("""
                IF NOT EXISTS (
                    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_NAME = 'FactMovementsHistory' AND COLUMN_NAME = 'Via_Maca_Fin'
                )
                BEGIN
                    ALTER TABLE FactMovementsHistory ADD Via_Maca_Fin BIT DEFAULT 1
                END
            """)
            conn.commit()
            # Backfill : les lignes existantes ont NULL → mettre à 1
            cur.execute("UPDATE FactMovementsHistory SET Via_Maca = 1 WHERE Via_Maca IS NULL")
            # Backfill Via_Maca_Fin : copier Via_Maca pour les lignes qui ont déjà un DateFin
            cur.execute("UPDATE FactMovementsHistory SET Via_Maca_Fin = Via_Maca WHERE Via_Maca_Fin IS NULL AND DateFin IS NOT NULL")
            cur.execute("UPDATE FactMovementsHistory SET Via_Maca_Fin = 1 WHERE Via_Maca_Fin IS NULL")
            # Correction historique : transféré → toujours Via_Maca = 0 et Via_Maca_Fin = 0
            cur.execute("UPDATE FactMovementsHistory SET Via_Maca = 0, Via_Maca_Fin = 0 WHERE Action = 'transféré'")
            # Correction historique : déménagements
            cur.execute("""
                UPDATE f SET Action = 'déménagement (fermeture)', Via_Maca = 0, Via_Maca_Fin = 0
                FROM FactMovementsHistory f
                WHERE f.Action = 'agence fermée'
                  AND f.DateFin IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM FactMovementsHistory f2
                    WHERE f2.Serial_num = f.Serial_num
                      AND f2.Action IN ('installé', 'déménagement (installation)')
                      AND f2.Movement_id > f.Movement_id
                      AND NOT EXISTS (
                        SELECT 1 FROM FactMovementsHistory f3
                        WHERE f3.Serial_num = f.Serial_num
                          AND f3.Action IN ('réparation/maintenance', 'stock')
                          AND f3.Movement_id > f.Movement_id
                          AND f3.Movement_id < f2.Movement_id
                      )
                  )
            """)
            cur.execute("""
                UPDATE f SET Action = 'déménagement (installation)', Via_Maca = 0
                FROM FactMovementsHistory f
                WHERE f.Action = 'installé'
                  AND EXISTS (
                    SELECT 1 FROM FactMovementsHistory f2
                    WHERE f2.Serial_num = f.Serial_num
                      AND f2.Action = 'déménagement (fermeture)'
                      AND f2.Movement_id < f.Movement_id
                      AND NOT EXISTS (
                        SELECT 1 FROM FactMovementsHistory f3
                        WHERE f3.Serial_num = f.Serial_num
                          AND f3.Action IN ('réparation/maintenance', 'stock')
                          AND f3.Movement_id > f2.Movement_id
                          AND f3.Movement_id < f.Movement_id
                      )
                  )
            """)
        else:
            cols = [row[1] for row in cur.execute("PRAGMA table_info(FactMovementsHistory)").fetchall()]
            if "Via_Maca" not in cols:
                cur.execute("ALTER TABLE FactMovementsHistory ADD COLUMN Via_Maca INTEGER DEFAULT 1")
            if "Via_Maca_Fin" not in cols:
                cur.execute("ALTER TABLE FactMovementsHistory ADD COLUMN Via_Maca_Fin INTEGER DEFAULT 1")
            cur.execute("UPDATE FactMovementsHistory SET Via_Maca = 1 WHERE Via_Maca IS NULL")
            cur.execute("UPDATE FactMovementsHistory SET Via_Maca_Fin = Via_Maca WHERE Via_Maca_Fin IS NULL AND DateFin IS NOT NULL")
            cur.execute("UPDATE FactMovementsHistory SET Via_Maca_Fin = 1 WHERE Via_Maca_Fin IS NULL")
            cur.execute("UPDATE FactMovementsHistory SET Via_Maca = 0, Via_Maca_Fin = 0 WHERE Action = 'transféré'")
        conn.commit()


# ── Initialisation démo SQLite ──────────────────────────────────────────────

def init_demo_db():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS DimScanners (
            Serial_num   INTEGER PRIMARY KEY,
            Mac_address  TEXT NOT NULL,
            Produit      TEXT NOT NULL DEFAULT '730ex plus',
            Localisation TEXT NOT NULL,
            Statut       TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS DimKantoren (
            Kantoor_id     INTEGER PRIMARY KEY,
            Kantoor_Bureau TEXT,
            Adresse        TEXT,
            C_Pos          INTEGER,
            Localite       TEXT,
            Apparition     DATE,
            Fermeture      DATE,
            Taal           TEXT,
            Status         TEXT NOT NULL DEFAULT 'open',
            Contactnaam    TEXT,
            Teln           TEXT,
            GSM            TEXT,
            Email          TEXT
        );
        CREATE TABLE IF NOT EXISTS FactMovementsHistory (
            Movement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            Serial_num  INTEGER,
            Kantoor_id  INTEGER,
            DateDebut   DATE,
            DateFin     DATE,
            Action      TEXT NOT NULL,
            Via_Maca    INTEGER DEFAULT 1,
            FOREIGN KEY (Serial_num)  REFERENCES DimScanners(Serial_num),
            FOREIGN KEY (Kantoor_id)  REFERENCES DimKantoren(Kantoor_id)
        );
        CREATE TABLE IF NOT EXISTS FactScannersMaintenance (
            Maintenance_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            Serial_num       INTEGER NOT NULL,
            Event_type       TEXT NOT NULL,
            Panne_detected   TEXT,
            Info_Maintenance TEXT,
            Copie            INTEGER,
            Return_date      DATE NOT NULL,
            End_Maintenance  DATE,
            FOREIGN KEY (Serial_num) REFERENCES DimScanners(Serial_num)
        );
        """)
        count = cur.execute("SELECT COUNT(*) FROM DimKantoren").fetchone()[0]
        if count == 0:
            cur.executescript("""
            INSERT INTO DimKantoren VALUES
                (1,'Agence Bruxelles Centre','Rue Neuve 45',1000,'Bruxelles','2020-01-15',NULL,'F','open','Jean Dupont','02 123 45 67','0471 12 34 56','bruxelles@dvv.be'),
                (2,'Kantoor Antwerpen','Meir 12',2000,'Antwerpen','2019-06-01',NULL,'N','open','Piet Janssen','03 234 56 78','0472 23 45 67','antwerpen@dvv.be'),
                (3,'Agence Liège','Place Saint-Lambert 8',4000,'Liège','2018-03-20',NULL,'F','open','Marie Martin','04 345 67 89','0473 34 56 78','liege@dvv.be');
            INSERT INTO DimScanners VALUES
                (10001,'AA:BB:CC:DD:EE:01','730ex plus','agence DVV','actif'),
                (10002,'AA:BB:CC:DD:EE:02','730ex plus','agence DVV','actif'),
                (10003,'AA:BB:CC:DD:EE:03','730ex plus','perdu','à rechercher'),
                (10004,'AA:BB:CC:DD:EE:04','730ex plus','Procedo','inactif');
            INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action) VALUES
                (10001, 1, '2020-01-15', NULL, 'installé'),
                (10002, 2, '2019-06-01', NULL, 'installé'),
                (10003, 3, '2021-05-10', NULL, 'installé'),
                (10004, NULL, '2024-07-01', NULL, 'stock');
            """)
        conn.commit()


# ── Helpers ─────────────────────────────────────────────────────────────────

ACTIONS = ["installé", "stock", "transféré", "réparation/maintenance",
           "agence fermée", "panne détectée", "retiré"]
EVENT_TYPES = ["Failure", "Maintenance"]

LOC_TO_STATUT = {
    "agence DVV": "actif",
    "perdu": "à rechercher",
    "atelier Procedo": "à réparer",
    "détruit": "fin de vie",
    "Maca Express": "à livrer",
    "Procedo": "inactif",
    "fournisseur": "retour garantie",
}
LOCALISATIONS = list(LOC_TO_STATUT.keys())
STATUTS_SCANNER = list(LOC_TO_STATUT.values()) + ["en transit retour"]


def get_statut_for_loc(loc, transit_retour=False):
    """Retourne le statut associé à une localisation.
    Si transit_retour=True et loc='Maca Express', retourne 'en transit retour'."""
    if transit_retour and loc == "Maca Express":
        return "en transit retour"
    return LOC_TO_STATUT.get(loc, "???")


@st.cache_data(ttl=300)
def get_open_agencies():
    return run_query(
        "SELECT Kantoor_id, Kantoor_Bureau, Adresse, Localite FROM DimKantoren WHERE Status = 'open' ORDER BY Kantoor_Bureau"
    )


@st.cache_data(ttl=300)
def get_all_agencies():
    return run_query("SELECT Kantoor_id, Kantoor_Bureau, Adresse, Localite, Status FROM DimKantoren ORDER BY Kantoor_Bureau")


@st.cache_data(ttl=300)
def get_active_scanners():
    return run_query(
        "SELECT Serial_num, Mac_address, Localisation, Statut FROM DimScanners WHERE Statut = 'actif' ORDER BY Serial_num"
    )


@st.cache_data(ttl=300)
def get_all_scanners():
    return run_query("SELECT * FROM DimScanners ORDER BY Serial_num")


@st.cache_data(ttl=300)
def get_stock_scanners():
    return run_query(
        "SELECT Serial_num, Mac_address, Localisation, Statut FROM DimScanners "
        "WHERE Statut IN ('à livrer', 'inactif') ORDER BY Serial_num"
    )


def agency_label(row):
    return f"{row['Kantoor_Bureau']} — {row['Localite']} (ID {row['Kantoor_id']})"


def scanner_label(df, sn):
    row = df.loc[df['Serial_num'] == sn]
    if not row.empty:
        return f"SN {sn} — {row['Localisation'].values[0]} ({row['Statut'].values[0]})"
    return f"SN {sn}"


def display_scanner_context(serial_num, prefix="ctx"):
    """Affiche le contexte d'un scanner : état actuel + 5 derniers mouvements + maintenance ouverte."""
    scanner_info = run_query(
        f"SELECT Serial_num, Mac_address, Localisation, Statut FROM DimScanners WHERE Serial_num = {serial_num}"
    )
    if scanner_info.empty:
        st.warning(f"Scanner {serial_num} introuvable dans DimScanners.")
        return

    si = scanner_info.iloc[0]

    # Agence actuelle si en agence
    agence_info = ""
    if si["Localisation"] == "agence DVV":
        ag = run_query(
            f"""SELECT k.Kantoor_id, k.Kantoor_Bureau, k.Localite
                FROM FactMovementsHistory m
                JOIN DimKantoren k ON m.Kantoor_id = k.Kantoor_id
                WHERE m.Serial_num = {serial_num} AND m.Action = 'installé' AND m.DateFin IS NULL"""
        )
        if not ag.empty:
            a = ag.iloc[0]
            agence_info = f" — **{a['Kantoor_Bureau']}**, {a['Localite']} (ID {a['Kantoor_id']})"

    st.info(
        f"**Scanner {serial_num}** — MAC : {si['Mac_address'] or 'N/A'}  \n"
        f"📍 Localisation : **{si['Localisation']}** — Statut : **{si['Statut']}**{agence_info}"
    )

    # 5 derniers mouvements
    last_mvts = run_query(
        sql_top(
            f"""m.Movement_id, m.Action, m.DateDebut, m.DateFin,
                   m.Kantoor_id, k.Kantoor_Bureau, k.Localite
            FROM FactMovementsHistory m
            LEFT JOIN DimKantoren k ON m.Kantoor_id = k.Kantoor_id
            WHERE m.Serial_num = {serial_num}""",
            5,
            "ORDER BY m.DateDebut DESC, m.Movement_id DESC"
        )
    )
    if not last_mvts.empty:
        st.caption("5 derniers mouvements :")
        st.dataframe(last_mvts, use_container_width=True, hide_index=True, key=f"df_{prefix}_mvt_{serial_num}")

    # Maintenance ouverte
    open_maint = run_query(
        f"""SELECT Maintenance_id, Event_type, Panne_detected, Info_Maintenance, Return_date
            FROM FactScannersMaintenance
            WHERE Serial_num = {serial_num} AND End_Maintenance IS NULL"""
    )
    if not open_maint.empty:
        st.warning(f"🔧 Maintenance ouverte (ID {open_maint.iloc[0]['Maintenance_id']}) — "
                   f"{open_maint.iloc[0]['Event_type']} — {open_maint.iloc[0]['Panne_detected'] or ''}")


def update_scanner_loc(sn, new_loc, transit_retour=False):
    new_statut = get_statut_for_loc(new_loc, transit_retour=transit_retour)
    run_execute(
        "UPDATE DimScanners SET Localisation = ?, Statut = ? WHERE Serial_num = ?",
        [new_loc, new_statut, sn],
    )


def filter_agencies(agencies_df, prefix):
    col_s1, col_s2 = st.columns(2)
    s_loc = col_s1.text_input("Rechercher par localité ou adresse", key=f"{prefix}_sloc")
    s_kid = col_s2.text_input("Rechercher par Kantoor ID", key=f"{prefix}_skid")
    filtered = agencies_df
    if s_loc:
        mask = (
            filtered["Localite"].astype(str).str.lower().str.contains(s_loc.lower()) |
            filtered["Adresse"].astype(str).str.lower().str.contains(s_loc.lower())
        )
        filtered = filtered[mask]
    if s_kid:
        filtered = filtered[filtered["Kantoor_id"].astype(str) == s_kid]
    return filtered


# ── Page config ─────────────────────────────────────────────────────────────

st.set_page_config(page_title="ProceDo — Gestion Parc Scanners", page_icon="📡", layout="wide")

# ── Authentification ───────────────────────────────────────────────────────
_AUTH_TOKEN = hashlib.sha256(st.secrets["auth"]["password"].encode()).hexdigest()[:16]

def check_password():
    """Affiche un écran de login et retourne True si le mot de passe est correct."""
    # Vérifier le token dans l'URL (persistant après refresh)
    params = st.query_params
    if params.get("token") == _AUTH_TOKEN:
        st.session_state["authenticated"] = True
        return True

    if st.session_state.get("authenticated"):
        return True

    login_container = st.empty()
    with login_container.container():
        col_logo1, col_logo2, col_logo3 = st.columns([1, 1, 1])
        with col_logo2:
            st.image("logo.png", use_container_width=True)
        st.markdown(
            "<p style='text-align:center;color:gray;'>Veuillez vous connecter pour accéder à l'application.</p>",
            unsafe_allow_html=True,
        )
        col1, col2, col3 = st.columns([1, 1.5, 1])
        with col2:
            password = st.text_input("Mot de passe", type="password", key="login_pw")
            if st.button("Se connecter", use_container_width=True):
                if password == st.secrets["auth"]["password"]:
                    st.session_state["authenticated"] = True
                    st.query_params["token"] = _AUTH_TOKEN
                    login_container.empty()
                    st.rerun()
                else:
                    st.error("Mot de passe incorrect.")
    return False

if not check_password():
    st.stop()

if not USE_SQL_SERVER:
    init_demo_db()

# Créer/remplir la table codes postaux → provinces (une seule fois par session)
if "codes_postaux_init" not in st.session_state:
    init_codes_postaux()
    st.session_state["codes_postaux_init"] = True

# Ajouter la colonne Via_Maca + backfill NULL → 1 (une seule fois par session)
if "via_maca_init_v4" not in st.session_state:
    init_via_maca()
    st.session_state["via_maca_init_v4"] = True

# ── Thème ProceDo ───────────────────────────────────────────────────────────

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap');
    html, body, [class*="st-"], .stMarkdown, .stTextInput label, .stSelectbox label,
    .stMultiSelect label, .stDateInput label, .stNumberInput label, .stTextArea label {
        font-family: 'Poppins', sans-serif !important;
    }
    h1 { color: #1B2A4A !important; font-weight: 700 !important; }
    h2, h3 { color: #1B2A4A !important; font-weight: 600 !important; }
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #F0F7FA 0%, #E3EEF5 100%) !important;
        border-right: 2px solid #00B4D8 !important;
        min-width: 300px !important;
        width: 300px !important;
    }
    section[data-testid="stSidebar"] .stRadio label span {
        font-size: 16px !important;
    }
    section[data-testid="stSidebar"] h1 { font-size: 1.8rem !important; }
    section[data-testid="stSidebar"] h2 { font-size: 1.4rem !important; }
    section[data-testid="stSidebar"] h3 { font-size: 1.2rem !important; }
    section[data-testid="stSidebar"] * { color: #1B2A4A !important; }
    section[data-testid="stSidebar"] .stRadio label span { color: #1B2A4A !important; font-weight: 400 !important; }
    section[data-testid="stSidebar"] .stRadio label[data-checked="true"] span,
    section[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label:has(input:checked) span {
        color: #00B4D8 !important; font-weight: 600 !important;
    }
    section[data-testid="stSidebar"] hr { border-color: #C0D8E8 !important; }
    .stButton > button[kind="primary"], .stFormSubmitButton > button {
        background-color: #00B4D8 !important; color: white !important; border: none !important;
        border-radius: 8px !important; font-weight: 600 !important;
        font-family: 'Poppins', sans-serif !important; transition: all 0.2s ease !important;
    }
    .stButton > button[kind="primary"]:hover, .stFormSubmitButton > button:hover {
        background-color: #0096B7 !important; transform: translateY(-1px) !important;
        box-shadow: 0 4px 12px rgba(0, 180, 216, 0.3) !important;
    }
    .stButton > button:not([kind="primary"]) {
        border-color: #00B4D8 !important; color: #00B4D8 !important;
        border-radius: 8px !important; font-family: 'Poppins', sans-serif !important;
    }
    div[data-testid="stMetric"] {
        background: linear-gradient(135deg, #F0F7FA 0%, #E3F2FD 100%);
        border-left: 4px solid #00B4D8; border-radius: 10px; padding: 8px 18px;
        box-shadow: 0 2px 8px rgba(27, 42, 74, 0.08);
    }
    div[data-testid="stMetric"] label { color: #1B2A4A !important; font-weight: 500 !important; font-size: 0.82rem !important; }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
        color: #00B4D8 !important; font-weight: 700 !important; font-size: 1.4rem !important;
    }
    .stTabs [data-baseweb="tab"] { font-family: 'Poppins', sans-serif !important; font-weight: 500 !important; }
    .stTabs [aria-selected="true"] { color: #00B4D8 !important; border-bottom-color: #00B4D8 !important; }
    .stDataFrame { border-radius: 10px; overflow: hidden; }
    div[data-testid="stForm"] {
        border: 1px solid #E0E8F0 !important; border-radius: 12px !important;
        padding: 20px !important; background: #FAFCFE !important;
    }
    .stTextInput input, .stNumberInput input, .stTextArea textarea {
        border-radius: 8px !important; font-family: 'Poppins', sans-serif !important;
    }
    .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {
        border-color: #00B4D8 !important; box-shadow: 0 0 0 1px #00B4D8 !important;
    }
    hr { border-color: #E0E8F0 !important; }
    .stRadio > div[role="radiogroup"] > label {
        background: #F8FBFD; border: 1px solid #E0E8F0; border-radius: 8px;
        padding: 10px 18px; margin-bottom: 4px; font-size: 1.1rem !important;
        transition: all 0.2s ease;
    }
    .stRadio > div[role="radiogroup"] > label p { font-size: 1.1rem !important; }
    .stRadio > div[role="radiogroup"] > label:hover { border-color: #00B4D8; background: #F0F9FC; }
    .sidebar-logo { margin-bottom: 20px; }
    [data-testid="collapsedControl"],
    [data-testid="stSidebarCollapse"],
    [data-testid="stSidebarCollapseButton"],
    .st-emotion-cache-1dp5vir,
    section[data-testid="stSidebar"] button[kind="header"] {
        display: none !important;
    }
</style>
""", unsafe_allow_html=True)

# ── Sidebar avec logo ───────────────────────────────────────────────────────

import os
logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
if os.path.exists(logo_path):
    st.sidebar.image(logo_path, use_container_width=True)
    st.sidebar.markdown("")
else:
    st.sidebar.markdown(
        '<h2 style="text-align:center; margin-bottom:5px;"><span style="color:#1B2A4A !important;">Proce</span><span style="color:#00B4D8 !important;">Do</span></h2>'
        '<p style="text-align:center; font-size:0.75rem; color:#5A7A96 !important; margin-top:0;">Capture | Automate | Optimize</p>',
        unsafe_allow_html=True
    )
st.sidebar.divider()

# ── Notification persistante ────────────────────────────────────────────────

def show_success(message, loc="default"):
    st.session_state["_success_msg"] = message
    st.session_state["_success_loc"] = loc
    st.rerun()


def display_success(loc="default"):
    if "_success_msg" in st.session_state and st.session_state.get("_success_loc") == loc:
        st.success(st.session_state["_success_msg"])
        del st.session_state["_success_msg"]
        del st.session_state["_success_loc"]

# ── Sidebar navigation ─────────────────────────────────────────────────────

page = st.sidebar.radio(
    "Navigation",
    ["Dashboard", "Actions fréquentes", "Scanners", "Agences", "Maintenance", "Logistique", "Mouvements"],
    label_visibility="collapsed"
)

st.sidebar.divider()

# ── Coordonnées entreprise ──
st.sidebar.markdown(
    """<div style="font-size:0.7rem; color:#5A7A96; line-height:1.7; padding: 2px 0;">
    <strong style="color:#1B2A4A;">Procedo SRL</strong><br>
    Chaussée de Louvain 775 — 1140 Evere<br>
    BE 0461.065.843<br>
    <a href="mailto:support@procedo.be" style="color:#00B4D8; text-decoration:none;">support@procedo.be</a>
    </div>""",
    unsafe_allow_html=True
)

st.sidebar.divider()
mode_label = "Azure SQL (cloud)"
st.sidebar.caption(f"🔌 Connecté : **{mode_label}**")

# ═════════════════════════════════════════════════════════════════════════════
#  DASHBOARD
# ═════════════════════════════════════════════════════════════════════════════

if page == "Dashboard":
    st.title("Dashboard — Parc Scanners")

    col1, col2, col3 = st.columns(3)
    total_scanners = run_query("SELECT COUNT(*) AS n FROM DimScanners")["n"][0]
    actifs = run_query("SELECT COUNT(*) AS n FROM DimScanners WHERE Statut = 'actif'")["n"][0]
    agences_open = run_query("SELECT COUNT(*) AS n FROM DimKantoren WHERE Status = 'open'")["n"][0]
    col1.metric("Total scanners", total_scanners)
    col2.metric("Actifs en agence", actifs)
    col3.metric("Agences ouvertes", agences_open)

    col4, col5, col6, col7 = st.columns(4)
    en_reparation = run_query("SELECT COUNT(*) AS n FROM DimScanners WHERE Statut = 'à réparer'")["n"][0]
    maca_a_livrer = run_query("SELECT COUNT(*) AS n FROM DimScanners WHERE Localisation = 'Maca Express' AND Statut = 'à livrer'")["n"][0]
    maca_transit = run_query("SELECT COUNT(*) AS n FROM DimScanners WHERE Localisation = 'Maca Express' AND Statut = 'en transit retour'")["n"][0]
    procedo_stock = run_query("SELECT COUNT(*) AS n FROM DimScanners WHERE Localisation = 'Procedo'")["n"][0]
    col4.metric("Atelier Procedo (à réparer)", en_reparation)
    col5.metric("Maca Express (à livrer)", maca_a_livrer)
    col6.metric("Maca Express (en transit retour)", maca_transit)
    col7.metric("Procedo (inactif)", procedo_stock)

    # ── Carte Belgique — scanners par province ──
    st.subheader("Scanners actifs par province")
    df_prov = run_query("""
        SELECT cp.Province, COUNT(s.Serial_num) AS Scanners
        FROM DimScanners s
        JOIN FactMovementsHistory f ON s.Serial_num = f.Serial_num
            AND f.DateFin IS NULL AND f.Action = 'installé'
        JOIN DimKantoren k ON f.Kantoor_id = k.Kantoor_id
        JOIN DimCodesPostaux cp ON k.C_Pos = cp.Code_Postal
        WHERE k.Status = 'open'
        GROUP BY cp.Province
        ORDER BY Scanners DESC
    """)

    if not df_prov.empty:
        geojson_path = os.path.join(os.path.dirname(__file__), "belgium_provinces.geojson")
        if os.path.exists(geojson_path):
            with open(geojson_path, "r", encoding="utf-8") as gf:
                belgium_geo = json.load(gf)

            # Ajouter les provinces sans scanners avec 0
            all_provinces = [f["properties"]["Province"] for f in belgium_geo["features"]]
            existing = set(df_prov["Province"].tolist())
            missing = [{"Province": p, "Scanners": 0} for p in all_provinces if p not in existing]
            if missing:
                import pandas as _pd
                df_map = _pd.concat([df_prov, _pd.DataFrame(missing)], ignore_index=True)
            else:
                df_map = df_prov.copy()

            try:
                fig_map = px.choropleth(
                    df_map,
                    geojson=belgium_geo,
                    locations="Province",
                    featureidkey="properties.Province",
                    color="Scanners",
                    color_continuous_scale=["#E8F4F8", "#00B4D8", "#1B2A4A"],
                    labels={"Scanners": "Nb scanners"},
                )
                fig_map.update_geos(
                    fitbounds="locations",
                    visible=False,
                    projection_type="mercator",
                )
                fig_map.update_layout(
                    margin={"r": 0, "t": 0, "l": 0, "b": 0},
                    height=550,
                    coloraxis_colorbar=dict(title="Scanners"),
                    dragmode=False,
                    geo=dict(
                        lonaxis_range=[2.3, 6.6],
                        lataxis_range=[49.4, 51.6],
                    ),
                )
                st.plotly_chart(fig_map, use_container_width=True)
            except Exception as e:
                st.error(f"Erreur carte : {e}")
        else:
            st.info("Fichier belgium_provinces.geojson introuvable.")

        st.dataframe(df_prov, use_container_width=True, hide_index=True, key="df_dashboard_prov")
    else:
        st.info("Aucun scanner actif en agence pour afficher la carte.")

    # ── Répartition combinée localisation (statut) ──
    st.subheader("Répartition des scanners")
    df_repart = run_query(
        "SELECT Localisation, Statut, COUNT(*) AS Nombre FROM DimScanners "
        "GROUP BY Localisation, Statut ORDER BY Nombre DESC"
    )
    if not df_repart.empty:
        df_repart["Label"] = df_repart["Localisation"] + " (" + df_repart["Statut"] + ")"
        # Ordre personnalisé (inversé car plotly affiche de bas en haut)
        ordre = [
            "perdu (à rechercher)",
            "fournisseur (retour garantie)",
            "détruit (fin de vie)",
            "Procedo (inactif)",
            "Maca Express (en transit retour)",
            "Maca Express (à livrer)",
            "atelier Procedo (à réparer)",
            "agence DVV (actif)",
        ]
        df_repart["Label"] = pd.Categorical(df_repart["Label"], categories=ordre, ordered=True)
        df_repart = df_repart.sort_values("Label")

        fig_repart = px.bar(
            df_repart,
            x="Nombre",
            y="Label",
            orientation="h",
            color="Statut",
            color_discrete_sequence=["#00B4D8", "#1B2A4A", "#48CAE4", "#0077B6", "#90E0EF", "#023E8A", "#ADE8F4", "#CAF0F8"],
            labels={"Nombre": "Nb scanners", "Label": ""},
        )
        fig_repart.update_layout(
            margin={"r": 10, "t": 10, "l": 10, "b": 10},
            height=420,
            showlegend=False,
            yaxis=dict(tickfont=dict(size=16, family="Poppins, sans-serif", color="#1B2A4A")),
        )
        st.plotly_chart(fig_repart, use_container_width=True)

    st.subheader("Derniers mouvements")
    df_mvt = run_query(sql_top(
        "m.Movement_id, m.Serial_num, k.Kantoor_Bureau, m.DateDebut, m.DateFin, m.Action "
        "FROM FactMovementsHistory m "
        "LEFT JOIN DimKantoren k ON m.Kantoor_id = k.Kantoor_id",
        15,
        "ORDER BY m.DateDebut DESC"
    ))
    st.dataframe(df_mvt, use_container_width=True, hide_index=True, key="df_dashboard_mvt")


# ═════════════════════════════════════════════════════════════════════════════
#  LOGISTIQUE
# ═════════════════════════════════════════════════════════════════════════════

elif page == "Logistique":
    st.title("Logistique & Transport")
    # ── Plage de dates disponibles en BDD ──
    _min_date_df = run_query("SELECT MIN(DateDebut) AS d FROM FactMovementsHistory")
    _db_min = None
    if not _min_date_df.empty and pd.notna(_min_date_df["d"].values[0]):
        _db_min = pd.to_datetime(_min_date_df["d"].values[0]).date()
    if _db_min is None:
        _db_min = date.today().replace(month=1, day=1)

    _current_year = date.today().year
    _years = list(range(_current_year, _db_min.year - 1, -1))
    _months_labels = {
        0: "Toute l'année",
        1: "Janvier", 2: "Février", 3: "Mars", 4: "Avril",
        5: "Mai", 6: "Juin", 7: "Juillet", 8: "Août",
        9: "Septembre", 10: "Octobre", 11: "Novembre", 12: "Décembre",
    }

    # ── Filtres : année (principal) + mois (optionnel) + dates personnalisées ──
    col_lf1, col_lf2, col_lf3 = st.columns([1, 1, 2])
    log_year = col_lf1.selectbox("Année", _years, index=0, key="log_year")
    log_month = col_lf2.selectbox(
        "Mois",
        list(_months_labels.keys()),
        format_func=lambda x: _months_labels[x],
        index=0,
        key="log_month"
    )

    # Calcul des bornes de date
    if log_month == 0:
        # Historique : beaucoup de DateFin au 31/12 (script d'import annuel)
        # 2021 : 01/01 → 30/12 (1ère année, pas de données avant)
        # 2022-2025 : 31/12/(n-1) → 30/12 (capter les 31/12 historiques sans doublon)
        # 2026 : 31/12/2025 → 31/12/2026 (année de transition)
        # 2027+ : 01/01 → 31/12 (tout géré par l'appli)
        if log_year <= 2021:
            log_date_from = date(log_year, 1, 1)
            log_date_to = min(date(log_year, 12, 30), date.today())
        elif log_year <= 2025:
            log_date_from = date(log_year - 1, 12, 31)
            log_date_to = min(date(log_year, 12, 30), date.today())
        elif log_year == 2026:
            log_date_from = date(2025, 12, 31)
            log_date_to = min(date(2026, 12, 31), date.today())
        else:
            log_date_from = date(log_year, 1, 1)
            log_date_to = min(date(log_year, 12, 31), date.today())
    else:
        import calendar
        log_date_from = date(log_year, log_month, 1)
        last_day = calendar.monthrange(log_year, log_month)[1]
        log_date_to = min(date(log_year, log_month, last_day), date.today())

    # Option dates personnalisées (override)
    with col_lf3:
        use_custom = st.checkbox("Dates personnalisées", key="log_custom_dates")
    if use_custom:
        col_cd1, col_cd2 = st.columns(2)
        log_date_from = col_cd1.date_input("Du", value=log_date_from, min_value=_db_min, max_value=date.today(), key="log_date_from")
        log_date_to = col_cd2.date_input("Au", value=log_date_to, min_value=_db_min, max_value=date.today(), key="log_date_to")

    st.caption(f"Période : **{log_date_from.strftime('%d/%m/%Y')}** → **{log_date_to.strftime('%d/%m/%Y')}**")

    # ── Requêtes : TOUS les vrais transports ──
    # Livraisons = mouvements avec Kantoor_id (installation en agence), exclut transferts et déménagements
    _excluded_actions = "('transféré', 'déménagement (fermeture)', 'déménagement (installation)')"
    # Récupérations = actions explicites de retrait physique d'un scanner d'une agence
    _recup_actions = "('panne détectée', 'agence fermée', 'retiré')"

    all_livraisons = run_query(
        f"""SELECT m.Movement_id, m.Serial_num, m.Kantoor_id,
                  k.Kantoor_Bureau, k.Localite,
                  m.DateDebut, m.Action, m.Via_Maca
           FROM FactMovementsHistory m
           LEFT JOIN DimKantoren k ON m.Kantoor_id = k.Kantoor_id
           WHERE m.Kantoor_id IS NOT NULL
             AND m.Action NOT IN {_excluded_actions}
             AND m.DateDebut BETWEEN ? AND ?
           ORDER BY m.DateDebut DESC""",
        [str(log_date_from), str(log_date_to)],
    )
    all_recuperations = run_query(
        f"""SELECT m.Movement_id, m.Serial_num, m.Kantoor_id,
                  k.Kantoor_Bureau, k.Localite,
                  m.Action, m.DateFin, m.Via_Maca_Fin
           FROM FactMovementsHistory m
           LEFT JOIN DimKantoren k ON m.Kantoor_id = k.Kantoor_id
           WHERE m.Kantoor_id IS NOT NULL
             AND m.DateFin IS NOT NULL
             AND m.Action IN {_recup_actions}
             AND m.DateFin BETWEEN ? AND ?
           ORDER BY m.DateFin DESC""",
        [str(log_date_from), str(log_date_to)],
    )

    # ── Filtrage Maca côté Python ──
    # Livraisons : Via_Maca = transport de la livraison (DateDebut)
    # Récupérations : Via_Maca_Fin = transport de la récupération (DateFin)
    maca_livraisons = all_livraisons[all_livraisons["Via_Maca"] == 1] if not all_livraisons.empty else all_livraisons
    maca_recuperations = all_recuperations[all_recuperations["Via_Maca_Fin"] == 1] if not all_recuperations.empty else all_recuperations

    # ── Calcul nombre de trajets Maca ──
    # Formule : MAX(livraisons_dedup, panne_dedup) + standalone_recup_dedup
    # - Les remplacements génèrent 1 livraison + 1 récupération 'panne détectée'
    #   mais les dates peuvent différer → MAX évite le double-comptage
    # - Les actions standalone (agence fermée, retiré) sont des trajets indépendants
    # - Les ouvertures/ajouts sont déjà dans les livraisons

    def _dedup_count(df, date_col):
        """Compte les (Kantoor_id, date) uniques dans un DataFrame."""
        if df.empty:
            return 0
        tmp = df[["Kantoor_id", date_col]].copy()
        tmp.columns = ["Kantoor_id", "date"]
        tmp["Kantoor_id"] = tmp["Kantoor_id"].astype(int)
        tmp["date"] = pd.to_datetime(tmp["date"]).dt.date
        return tmp.drop_duplicates().shape[0]

    # ── Période ──
    # Séparer récupérations panne (remplacements) vs standalone (fermeture/retrait)
    if not maca_recuperations.empty:
        _period_recup_panne = maca_recuperations[maca_recuperations["Action"] == "panne détectée"]
        _period_recup_standalone = maca_recuperations[maca_recuperations["Action"].isin(["agence fermée", "retiré"])]
    else:
        _period_recup_panne = maca_recuperations
        _period_recup_standalone = maca_recuperations
    nb_trajets = (
        max(_dedup_count(maca_livraisons, "DateDebut"),
            _dedup_count(_period_recup_panne, "DateFin"))
        + _dedup_count(_period_recup_standalone, "DateFin")
    )

    # ── Global (toutes dates) — même logique MAX ──
    _global_livr = run_query(
        f"""SELECT Kantoor_id, DateDebut
           FROM FactMovementsHistory
           WHERE Kantoor_id IS NOT NULL AND Via_Maca = 1
             AND Action NOT IN {_excluded_actions}"""
    )
    _global_recup_panne = run_query(
        """SELECT Kantoor_id, DateFin
           FROM FactMovementsHistory
           WHERE Kantoor_id IS NOT NULL AND DateFin IS NOT NULL AND Via_Maca_Fin = 1
             AND Action = 'panne détectée'"""
    )
    _global_recup_standalone = run_query(
        """SELECT Kantoor_id, DateFin
           FROM FactMovementsHistory
           WHERE Kantoor_id IS NOT NULL AND DateFin IS NOT NULL AND Via_Maca_Fin = 1
             AND Action IN ('agence fermée', 'retiré')"""
    )
    nb_trajets_global = (
        max(_dedup_count(_global_livr, "DateDebut"),
            _dedup_count(_global_recup_panne, "DateFin"))
        + _dedup_count(_global_recup_standalone, "DateFin")
    )

    # ── KPIs ──
    col_k1, col_k2, col_k3 = st.columns(3)
    col_k1.metric("📦 Livraisons Maca", len(maca_livraisons))
    col_k2.metric("🔄 Récupérations Maca", len(maca_recuperations))
    col_k3.metric("🚛 Trajets Maca", nb_trajets)

    col_k4, col_k5, col_k6 = st.columns(3)
    col_k4.metric("📦 Livraisons (total)", len(all_livraisons))
    col_k5.metric("🔄 Récupérations (total)", len(all_recuperations))
    col_k6.metric("🚛 Total trajets Maca", nb_trajets_global)

    # ── Filtres de recherche ──
    col_lsf1, col_lsf2, col_lsf3, col_lsf4 = st.columns(4)
    log_search_sn = col_lsf1.text_input("Rechercher par n° de série", key="log_search_sn")
    log_search_mvt = col_lsf2.text_input("Rechercher par n° de mouvement", key="log_search_mvt")
    log_search_loc = col_lsf3.text_input("Rechercher par localité ou adresse", key="log_search_loc")
    log_search_kid = col_lsf4.text_input("Rechercher par Kantoor ID", key="log_search_kid")

    _log_search_active = bool(log_search_mvt or log_search_sn or log_search_kid or log_search_loc)

    # Si recherche active → charger TOUTES les données (sans filtre de date)
    if _log_search_active:
        _search_livraisons = run_query(
            f"""SELECT m.Movement_id, m.Serial_num, m.Kantoor_id,
                      k.Kantoor_Bureau, k.Localite,
                      m.DateDebut, m.Action, m.Via_Maca
               FROM FactMovementsHistory m
               LEFT JOIN DimKantoren k ON m.Kantoor_id = k.Kantoor_id
               WHERE m.Kantoor_id IS NOT NULL
                 AND m.Action NOT IN {_excluded_actions}
               ORDER BY m.DateDebut DESC""",
        )
        _search_recuperations = run_query(
            f"""SELECT m.Movement_id, m.Serial_num, m.Kantoor_id,
                      k.Kantoor_Bureau, k.Localite,
                      m.Action, m.DateFin, m.Via_Maca_Fin
               FROM FactMovementsHistory m
               LEFT JOIN DimKantoren k ON m.Kantoor_id = k.Kantoor_id
               WHERE m.Kantoor_id IS NOT NULL
                 AND m.DateFin IS NOT NULL
                 AND m.Action IN {_recup_actions}
               ORDER BY m.DateFin DESC""",
        )
    else:
        _search_livraisons = all_livraisons
        _search_recuperations = all_recuperations

    def _filter_log(df):
        """Applique les filtres de recherche Logistique sur un DataFrame."""
        if df.empty:
            return df
        out = df.copy()
        if log_search_mvt:
            out = out[out["Movement_id"].astype(str) == log_search_mvt]
        if log_search_sn:
            out = out[out["Serial_num"].astype(str).str.contains(log_search_sn, case=False, na=False)]
        if log_search_kid:
            out = out[out["Kantoor_id"].astype(str) == log_search_kid]
        if log_search_loc:
            mask = pd.Series(False, index=out.index)
            for c in ["Localite", "Kantoor_Bureau"]:
                if c in out.columns:
                    mask |= out[c].astype(str).str.contains(log_search_loc, case=False, na=False)
            out = out[mask]
        return out

    filt_livraisons = _filter_log(_search_livraisons)
    filt_recuperations = _filter_log(_search_recuperations)

    # ── Tableaux éditables (dans un formulaire pour éviter les reruns intermédiaires) ──
    # ── Livraisons ──
    st.subheader("Livraisons")
    display_success("log_via_maca_liv")
    if filt_livraisons.empty:
        st.info("Aucune livraison sur cette période." if all_livraisons.empty else "Aucun résultat pour ces critères.")
    else:
        df_edit_liv = filt_livraisons[["Movement_id", "Serial_num", "Kantoor_Bureau", "Localite", "Action", "DateDebut", "Via_Maca"]].copy()
        df_edit_liv["Via_Maca"] = df_edit_liv["Via_Maca"].astype(bool)
        df_edit_liv = df_edit_liv.reset_index(drop=True)
        edited_liv = st.data_editor(
            df_edit_liv,
            use_container_width=True, hide_index=True, key=f"de_liv_{log_year}_{log_month}",
            disabled=["Movement_id", "Serial_num", "Kantoor_Bureau", "Localite", "Action", "DateDebut"],
            column_config={"Via_Maca": st.column_config.CheckboxColumn("Par Maca Express", default=True)},
        )
        _sp_liv, _btn_liv = st.columns([5.5, 1])
        with _btn_liv:
            _save_liv = st.button("Valider", key="btn_save_liv", type="primary", use_container_width=True)
        if _save_liv:
            changes_liv = []
            for i, row in edited_liv.iterrows():
                if row["Via_Maca"] != df_edit_liv.loc[i, "Via_Maca"]:
                    changes_liv.append((int(row["Via_Maca"]), int(row["Movement_id"])))
            if changes_liv:
                for val, mid in changes_liv:
                    run_execute("UPDATE FactMovementsHistory SET Via_Maca = ? WHERE Movement_id = ?", [val, mid])
                show_success(f"✅ {len(changes_liv)} livraison(s) mise(s) à jour.", "log_via_maca_liv")
            else:
                st.info("Aucune modification.")
        st.caption(f"{len(filt_livraisons)} livraison(s)" if len(filt_livraisons) == len(all_livraisons) else f"{len(filt_livraisons)} / {len(all_livraisons)} livraison(s)")

    st.divider()

    # ── Récupérations ──
    st.subheader("Récupérations")
    display_success("log_via_maca_rec")
    if filt_recuperations.empty:
        st.info("Aucune récupération sur cette période." if all_recuperations.empty else "Aucun résultat pour ces critères.")
    else:
        df_edit_rec = filt_recuperations[["Movement_id", "Serial_num", "Kantoor_Bureau", "Localite", "Action", "DateFin", "Via_Maca_Fin"]].copy()
        df_edit_rec["Via_Maca_Fin"] = df_edit_rec["Via_Maca_Fin"].astype(bool)
        df_edit_rec = df_edit_rec.reset_index(drop=True)
        edited_rec = st.data_editor(
            df_edit_rec,
            use_container_width=True, hide_index=True, key=f"de_rec_{log_year}_{log_month}",
            disabled=["Movement_id", "Serial_num", "Kantoor_Bureau", "Localite", "Action", "DateFin"],
            column_config={"Via_Maca_Fin": st.column_config.CheckboxColumn("Par Maca Express", default=True)},
        )
        _sp_rec, _btn_rec = st.columns([5.5, 1])
        with _btn_rec:
            _save_rec = st.button("Valider", key="btn_save_rec", type="primary", use_container_width=True)
        if _save_rec:
            changes_rec = []
            for i, row in edited_rec.iterrows():
                if row["Via_Maca_Fin"] != df_edit_rec.loc[i, "Via_Maca_Fin"]:
                    changes_rec.append((int(row["Via_Maca_Fin"]), int(row["Movement_id"])))
            if changes_rec:
                for val, mid in changes_rec:
                    run_execute("UPDATE FactMovementsHistory SET Via_Maca_Fin = ? WHERE Movement_id = ?", [val, mid])
                show_success(f"✅ {len(changes_rec)} récupération(s) mise(s) à jour.", "log_via_maca_rec")
            else:
                st.info("Aucune modification.")
        st.caption(f"{len(filt_recuperations)} récupération(s)" if len(filt_recuperations) == len(all_recuperations) else f"{len(filt_recuperations)} / {len(all_recuperations)} récupération(s)")



# ═════════════════════════════════════════════════════════════════════════════
#  SCANNERS
# ═════════════════════════════════════════════════════════════════════════════

elif page == "Scanners":
    st.title("Gestion des Scanners")

    tab_list, tab_add, tab_edit = st.tabs(["Liste", "Ajouter", "Modifier"])

    # ── Liste ──
    with tab_list:
        # Alerte si scanners perdus (à rechercher)
        nb_perdus_df = run_query("SELECT COUNT(*) AS n FROM DimScanners WHERE Statut = 'à rechercher'")
        nb_perdus = int(nb_perdus_df["n"].values[0]) if not nb_perdus_df.empty else 0
        if nb_perdus > 0:
            st.warning(f"⚠️ {nb_perdus} scanner(s) avec statut **'à rechercher'** (perdus). Filtrez par statut 'à rechercher' pour les voir.")

        col_s1, col_s2, col_s3 = st.columns(3)
        search_sn_list = col_s1.text_input("Rechercher par n° de série", key="sn_list_search")
        search_kid_list = col_s2.text_input("Rechercher par Kantoor ID", key="sn_kid_search")
        search_loc_list = col_s3.text_input("Rechercher par localité", key="sn_loc_search")
        col_f1, col_f2 = st.columns(2)
        filtre_statut = col_f1.multiselect("Filtrer par statut", STATUTS_SCANNER)
        filtre_loc = col_f2.multiselect("Filtrer par localisation", LOCALISATIONS)

        query = """
            SELECT DISTINCT s.*
            FROM DimScanners s
            LEFT JOIN FactMovementsHistory f ON s.Serial_num = f.Serial_num AND f.DateFin IS NULL AND f.Action = 'installé'
            LEFT JOIN DimKantoren k ON f.Kantoor_id = k.Kantoor_id
            WHERE 1=1
        """
        if search_sn_list:
            query += f" AND {sql_cast_text('s.Serial_num')} LIKE '%{search_sn_list}%'"
        if search_kid_list:
            query += f" AND {sql_cast_text('f.Kantoor_id')} = '{search_kid_list}'"
        if search_loc_list:
            query += f" AND LOWER(k.Localite) LIKE LOWER('%{search_loc_list}%')"
        if filtre_statut:
            placeholders = ",".join(f"'{s}'" for s in filtre_statut)
            query += f" AND s.Statut IN ({placeholders})"
        if filtre_loc:
            placeholders = ",".join(f"'{l}'" for l in filtre_loc)
            query += f" AND s.Localisation IN ({placeholders})"
        query += " ORDER BY s.Serial_num"

        df = run_query(query)
        st.dataframe(df, use_container_width=True, hide_index=True, height=600, key="df_scanners_liste")
        st.caption(f"{len(df)} scanner(s) affiché(s)")

    # ── Ajouter ──
    with tab_add:
        st.subheader("Nouveau scanner")

        check_sn = st.text_input("Vérifier un n° de série (8 chiffres)", key="check_sn_add", max_chars=8)
        if check_sn:
            if not check_sn.isdigit() or len(check_sn) != 8:
                st.warning("Le numéro de série doit contenir exactement 8 chiffres.")
            else:
                existing = run_query(f"SELECT * FROM DimScanners WHERE Serial_num = {check_sn}")
                if not existing.empty:
                    st.error(f"SN {check_sn} existe déjà en base :")
                    st.dataframe(existing, use_container_width=True, hide_index=True, key="df_scanners_check")
                else:
                    st.success(f"SN {check_sn} est disponible.")

        st.info("⚠️ **Attention** : le numéro de série ne pourra plus être modifié après création. Vérifiez-le bien avant de valider.")

        LOC_AJOUT = ["Procedo", "Maca Express", "agence DVV"]
        agencies_add = get_open_agencies()

        with st.form("add_scanner"):
            serial = st.text_input("Numéro de série (8 chiffres)", max_chars=8, key="add_sn_input")
            mac = st.text_input("Adresse MAC", placeholder="AA:BB:CC:DD:EE:FF")
            produit = st.text_input("Produit", value="730ex plus")
            localisation = st.selectbox("Localisation", LOC_AJOUT)
            st.caption(f"Statut associé : **{get_statut_for_loc(localisation)}**")

            ag_for_scanner = None
            install_date = None
            if localisation == "agence DVV":
                st.warning("Vous devez associer une agence. Si elle n'existe pas, créez-la d'abord dans la section Agences.")
                if agencies_add.empty:
                    st.error("Aucune agence ouverte disponible. Créez d'abord l'agence.")
                else:
                    ag_for_scanner = st.selectbox(
                        "Agence à associer",
                        agencies_add["Kantoor_id"].tolist(),
                        format_func=lambda x: agency_label(agencies_add[agencies_add["Kantoor_id"] == x].iloc[0]),
                        key="add_sn_agency"
                    )
                    install_date = st.date_input("Date d'installation", value=date.today(), key="add_sn_date")

            if st.form_submit_button("Ajouter le scanner"):
                if not serial.isdigit() or len(serial) != 8:
                    st.error("Le numéro de série doit contenir exactement 8 chiffres.")
                elif not mac:
                    st.error("L'adresse MAC est obligatoire.")
                elif localisation == "agence DVV" and ag_for_scanner is None:
                    st.error("Vous devez associer une agence pour la localisation 'agence DVV'.")
                else:
                    exists = run_query(f"SELECT COUNT(*) AS n FROM DimScanners WHERE Serial_num = {int(serial)}")
                    if exists["n"].values[0] > 0:
                        st.error(f"Le scanner SN {serial} existe déjà en base ! Impossible de l'ajouter.")
                    else:
                        try:
                            run_execute(
                                "INSERT INTO DimScanners (Serial_num, Mac_address, Produit, Localisation, Statut) VALUES (?,?,?,?,?)",
                                [int(serial), mac, produit, localisation, get_statut_for_loc(localisation)],
                            )
                            if localisation == "agence DVV" and ag_for_scanner is not None:
                                run_execute(
                                    "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action) VALUES (?,NULL,?,?,'stock')",
                                    [int(serial), str(install_date), str(install_date)],
                                )
                                run_execute(
                                    "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action) VALUES (?,?,?,NULL,'installé')",
                                    [int(serial), ag_for_scanner, str(install_date)],
                                )
                            else:
                                run_execute(
                                    "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action) VALUES (?,NULL,?,NULL,'stock')",
                                    [int(serial), str(date.today())],
                                )
                            show_success(f"✅ Scanner {serial} ajouté ({localisation}) + mouvement 'stock' créé.", "sn_add")
                        except Exception as e:
                            st.error(f"Erreur : {e}")
        display_success("sn_add")

    # ── Modifier ──
    with tab_edit:
        st.subheader("Modifier un scanner")
        search_sn_edit = st.text_input("Rechercher par n° de série", key="sn_edit_search")
        scanners = get_all_scanners()

        if scanners.empty:
            st.info("Aucun scanner en base.")
        else:
            filtered = scanners
            if search_sn_edit:
                filtered = scanners[scanners["Serial_num"].astype(str).str.contains(search_sn_edit)]

            if filtered.empty:
                st.warning(f"Aucun scanner trouvé pour '{search_sn_edit}'.")
            else:
                selected_serial = st.selectbox(
                    "Scanner",
                    filtered["Serial_num"].tolist(),
                    format_func=lambda x: f"SN {x} — {filtered.loc[filtered['Serial_num']==x, 'Statut'].values[0]}"
                )
                row = filtered[filtered["Serial_num"] == selected_serial].iloc[0]
                st.caption(f"Localisation actuelle : **{row['Localisation']}** ({row['Statut']})")
                st.caption("Pour changer la localisation, utilisez les **Actions fréquentes**.")

                with st.form("edit_scanner"):
                    new_mac = st.text_input("Adresse MAC", value=row["Mac_address"])
                    new_produit = st.text_input("Produit", value=row["Produit"])
                    if st.form_submit_button("Sauvegarder"):
                        run_execute(
                            "UPDATE DimScanners SET Mac_address=?, Produit=? WHERE Serial_num=?",
                            [new_mac, new_produit, selected_serial],
                        )
                        show_success(f"✅ Scanner SN {selected_serial} mis à jour.", "sn_edit")
                display_success("sn_edit")


# ═════════════════════════════════════════════════════════════════════════════
#  AGENCES
# ═════════════════════════════════════════════════════════════════════════════

elif page == "Agences":
    st.title("Gestion des Agences")

    # ── Filtres période (même pattern que Logistique) ──
    _ag_min_date_df = run_query("SELECT MIN(Apparition) AS d FROM DimKantoren WHERE Apparition IS NOT NULL")
    _ag_min = None
    if not _ag_min_date_df.empty and pd.notna(_ag_min_date_df["d"].values[0]):
        _ag_min = pd.to_datetime(_ag_min_date_df["d"].values[0]).date()
    if _ag_min is None:
        _ag_min = date.today().replace(month=1, day=1)

    _ag_current_year = date.today().year
    _ag_years = list(range(_ag_current_year, _ag_min.year - 1, -1))
    _ag_months_labels = {
        0: "Toute l'année",
        1: "Janvier", 2: "Février", 3: "Mars", 4: "Avril",
        5: "Mai", 6: "Juin", 7: "Juillet", 8: "Août",
        9: "Septembre", 10: "Octobre", 11: "Novembre", 12: "Décembre",
    }

    col_af1, col_af2, col_af3 = st.columns([1, 1, 2])
    ag_kpi_year = col_af1.selectbox("Année", _ag_years, index=0, key="ag_kpi_year")
    ag_kpi_month = col_af2.selectbox(
        "Mois",
        list(_ag_months_labels.keys()),
        format_func=lambda x: _ag_months_labels[x],
        index=0,
        key="ag_kpi_month"
    )

    if ag_kpi_month == 0:
        ag_date_from = date(ag_kpi_year, 1, 1)
        ag_date_to = min(date(ag_kpi_year, 12, 31), date.today())
    else:
        import calendar
        ag_date_from = date(ag_kpi_year, ag_kpi_month, 1)
        _ag_last_day = calendar.monthrange(ag_kpi_year, ag_kpi_month)[1]
        ag_date_to = min(date(ag_kpi_year, ag_kpi_month, _ag_last_day), date.today())

    with col_af3:
        ag_use_custom = st.checkbox("Dates personnalisées", key="ag_custom_dates")
    if ag_use_custom:
        col_ad1, col_ad2 = st.columns(2)
        ag_date_from = col_ad1.date_input("Du", value=ag_date_from, min_value=_ag_min, max_value=date.today(), key="ag_date_from")
        ag_date_to = col_ad2.date_input("Au", value=ag_date_to, min_value=_ag_min, max_value=date.today(), key="ag_date_to")

    st.caption(f"Période : **{ag_date_from.strftime('%d/%m/%Y')}** → **{ag_date_to.strftime('%d/%m/%Y')}**")

    # ── KPIs Agences ──
    # Ouvertures sur la période (hors déménagements)
    _nb_ouv = run_query(
        """SELECT COUNT(DISTINCT k.Kantoor_id) AS n
           FROM DimKantoren k
           WHERE k.Apparition BETWEEN ? AND ?
             AND NOT EXISTS (
               SELECT 1 FROM FactMovementsHistory m
               WHERE m.Kantoor_id = k.Kantoor_id AND m.Action = 'déménagement (installation)'
             )""",
        [str(ag_date_from), str(ag_date_to)]
    )
    nb_ouvertures = int(_nb_ouv["n"].values[0]) if not _nb_ouv.empty else 0

    # Fermetures sur la période (hors déménagements)
    _nb_ferm = run_query(
        """SELECT COUNT(DISTINCT k.Kantoor_id) AS n
           FROM DimKantoren k
           WHERE k.Fermeture BETWEEN ? AND ?
             AND NOT EXISTS (
               SELECT 1 FROM FactMovementsHistory m
               WHERE m.Kantoor_id = k.Kantoor_id AND m.Action = 'déménagement (fermeture)'
             )""",
        [str(ag_date_from), str(ag_date_to)]
    )
    nb_fermetures = int(_nb_ferm["n"].values[0]) if not _nb_ferm.empty else 0

    # Moyennes par an (exclut 2021-2022 = années d'installation + année en cours, hors déménagements)
    _yearly_ouv = run_query(
        """SELECT YEAR(k.Apparition) AS annee, COUNT(DISTINCT k.Kantoor_id) AS nb
           FROM DimKantoren k
           WHERE k.Apparition IS NOT NULL
             AND YEAR(k.Apparition) > 2022
             AND YEAR(k.Apparition) < YEAR(GETDATE())
             AND NOT EXISTS (
               SELECT 1 FROM FactMovementsHistory m
               WHERE m.Kantoor_id = k.Kantoor_id AND m.Action = 'déménagement (installation)'
             )
           GROUP BY YEAR(k.Apparition)"""
    )
    avg_ouv = round(_yearly_ouv["nb"].mean(), 1) if not _yearly_ouv.empty else 0

    _yearly_ferm = run_query(
        """SELECT YEAR(k.Fermeture) AS annee, COUNT(DISTINCT k.Kantoor_id) AS nb
           FROM DimKantoren k
           WHERE k.Fermeture IS NOT NULL
             AND YEAR(k.Fermeture) < YEAR(GETDATE())
             AND NOT EXISTS (
               SELECT 1 FROM FactMovementsHistory m
               WHERE m.Kantoor_id = k.Kantoor_id AND m.Action = 'déménagement (fermeture)'
             )
           GROUP BY YEAR(k.Fermeture)"""
    )
    avg_ferm = round(_yearly_ferm["nb"].mean(), 1) if not _yearly_ferm.empty else 0

    # Affichage KPIs
    col_k1, col_k2 = st.columns(2)
    col_k1.metric("🏢 Ouvertures", nb_ouvertures)
    col_k2.metric("🔒 Fermetures", nb_fermetures)

    col_k3, col_k4 = st.columns(2)
    col_k3.metric("📊 Moy. ouvertures/an", avg_ouv)
    col_k4.metric("📉 Moy. fermetures/an", avg_ferm)

    st.markdown("---")

    tab_list, tab_add, tab_edit, tab_close = st.tabs(["Liste", "Ouvrir une agence", "Modifier", "Clôturer une agence"])

    # ── Liste ──
    with tab_list:
        col_ag1, col_ag2, col_ag3 = st.columns(3)
        search_ag = col_ag1.text_input("Rechercher par localité ou adresse", key="ag_list_search")
        search_ag_kid = col_ag2.text_input("Rechercher par Kantoor ID", key="ag_list_kid")
        filtre_status = col_ag3.selectbox("Statut", ["Toutes", "open", "closed"], key="ag_list_status")
        query = """SELECT k.*, cp.Province
                   FROM DimKantoren k
                   LEFT JOIN DimCodesPostaux cp ON k.C_Pos = cp.Code_Postal
                   WHERE 1=1"""
        if filtre_status != "Toutes":
            query += f" AND k.Status = '{filtre_status}'"
        if search_ag:
            query += f" AND (LOWER(k.Localite) LIKE LOWER('%{search_ag}%') OR LOWER(k.Adresse) LIKE LOWER('%{search_ag}%'))"
        if search_ag_kid:
            query += f" AND {sql_cast_text('k.Kantoor_id')} = '{search_ag_kid}'"
        query += " ORDER BY k.Kantoor_Bureau"
        df = run_query(query)
        st.dataframe(df, use_container_width=True, hide_index=True, height=600, key="df_agences_liste")
        st.caption(f"{len(df)} agence(s)")

    # ── Ouvrir une agence ──
    with tab_add:
        st.subheader("Ouvrir une nouvelle agence")
        st.info("L'ouverture d'une agence nécessite l'association d'au moins un scanner.")

        search_new_ag = st.text_input("Rechercher si l'agence existe déjà (localité ou adresse)", key="check_ag_add")
        if search_new_ag:
            existing_ag = run_query(
                f"SELECT Kantoor_id, Kantoor_Bureau, Adresse, Localite, Status FROM DimKantoren "
                f"WHERE LOWER(Localite) LIKE LOWER('%{search_new_ag}%') OR LOWER(Adresse) LIKE LOWER('%{search_new_ag}%')"
            )
            if not existing_ag.empty:
                st.warning("Agence(s) similaire(s) trouvée(s) :")
                st.dataframe(existing_ag, use_container_width=True, hide_index=True, key="df_agences_check")
            else:
                st.success("Aucune agence correspondante — vous pouvez créer.")

        scanners_dispo = run_query(
            "SELECT Serial_num, Mac_address, Localisation, Statut FROM DimScanners "
            "WHERE Statut IN ('à livrer', 'inactif') ORDER BY Serial_num"
        )

        next_id_df = run_query("SELECT ISNULL(MAX(Kantoor_id), 0) + 1 AS next_id FROM DimKantoren")
        next_kantoor_id = int(next_id_df["next_id"].values[0])
        st.caption(f"Kantoor ID automatique : **{next_kantoor_id}**")

        with st.form("add_agency"):
            bureau = st.text_input("Nom bureau", placeholder="Agence Bruxelles Nord")
            adresse = st.text_input("Adresse")
            c_pos = st.number_input("Code postal", min_value=1000, max_value=9999, step=1)
            localite = st.text_input("Localité")
            taal = st.selectbox("Langue", ["F", "N", "D"])
            contact = st.text_input("Nom contact")
            tel = st.text_input("Téléphone")
            gsm = st.text_input("GSM")
            email = st.text_input("Email")
            apparition = st.date_input("Date d'ouverture", value=date.today())

            st.divider()
            st.markdown("**Scanner à associer (obligatoire)**")
            if scanners_dispo.empty:
                st.error("Aucun scanner disponible en stock. Ajoutez d'abord un scanner.")
                scanner_associe = None
            else:
                scanner_associe = st.selectbox(
                    "Scanner à installer",
                    scanners_dispo["Serial_num"].tolist(),
                    format_func=lambda x: f"SN {x} — {scanners_dispo.loc[scanners_dispo['Serial_num']==x, 'Localisation'].values[0]} ({scanners_dispo.loc[scanners_dispo['Serial_num']==x, 'Statut'].values[0]})"
                )

            if st.form_submit_button("Ouvrir l'agence"):
                cp_exists = run_query("SELECT COUNT(*) AS n FROM DimCodesPostaux WHERE Code_Postal = ?", [int(c_pos)])
                if scanner_associe is None:
                    st.error("Vous devez associer un scanner pour ouvrir une agence.")
                elif not bureau or not localite:
                    st.error("Le nom du bureau et la localité sont obligatoires.")
                elif cp_exists["n"].values[0] == 0:
                    st.error(f"Le code postal {int(c_pos)} n'existe pas dans la table des codes postaux belges.")
                else:
                    try:
                        run_execute(
                            """INSERT INTO DimKantoren
                               (Kantoor_id, Kantoor_Bureau, Adresse, C_Pos, Localite,
                                Apparition, Fermeture, Taal, Status, Contactnaam, Teln, GSM, Email)
                               VALUES (?,?,?,?,?,?,NULL,?,'open',?,?,?,?)""",
                            [next_kantoor_id, bureau, adresse, c_pos, localite,
                             str(apparition), taal, contact, tel, gsm, email],
                        )
                        # Via_Maca = 0 si le scanner vient de Procedo
                        _open_loc = scanners_dispo.loc[scanners_dispo["Serial_num"] == scanner_associe, "Localisation"].values[0]
                        _open_via_maca = 0 if _open_loc == "Procedo" else 1
                        run_execute(
                            "UPDATE FactMovementsHistory SET DateFin = ? WHERE Serial_num = ? AND DateFin IS NULL AND Action IN ('stock', 'réparation/maintenance')",
                            [str(apparition), scanner_associe],
                        )
                        run_execute(
                            "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action, Via_Maca) VALUES (?,?,?,NULL,'installé',?)",
                            [scanner_associe, next_kantoor_id, str(apparition), _open_via_maca],
                        )
                        update_scanner_loc(scanner_associe, "agence DVV")
                        show_success(f"✅ Agence {bureau} ({localite}) ouverte (ID {next_kantoor_id}) avec le scanner SN {scanner_associe} !", "ag_open")
                    except Exception as e:
                        st.error(f"Erreur : {e}")

        display_success("ag_open")

    # ── Modifier ──
    with tab_edit:
        st.subheader("Modifier une agence")

        col_e1, col_e2 = st.columns(2)
        search_ag_edit = col_e1.text_input("Rechercher par localité ou adresse", key="ag_edit_search")
        search_ag_edit_kid = col_e2.text_input("Rechercher par Kantoor ID", key="ag_edit_kid")
        agencies = get_all_agencies()

        if agencies.empty:
            st.info("Aucune agence en base.")
        else:
            filtered_ag = agencies
            if search_ag_edit:
                mask = (
                    agencies["Localite"].astype(str).str.lower().str.contains(search_ag_edit.lower()) |
                    agencies["Kantoor_Bureau"].astype(str).str.lower().str.contains(search_ag_edit.lower()) |
                    agencies["Adresse"].astype(str).str.lower().str.contains(search_ag_edit.lower())
                )
                filtered_ag = filtered_ag[mask]
            if search_ag_edit_kid:
                filtered_ag = filtered_ag[filtered_ag["Kantoor_id"].astype(str) == search_ag_edit_kid]

            if filtered_ag.empty:
                st.warning(f"Aucune agence trouvée pour '{search_ag_edit}'.")
            else:
                selected_id = st.selectbox(
                    "Agence",
                    filtered_ag["Kantoor_id"].tolist(),
                    format_func=lambda x: agency_label(filtered_ag[filtered_ag["Kantoor_id"] == x].iloc[0])
                )
                row = run_query("SELECT * FROM DimKantoren WHERE Kantoor_id = ?", [selected_id]).iloc[0]

                st.caption(f"Adresse : {row['Adresse']}, {row['C_Pos']} {row['Localite']}")
                st.info("En cas de changement d'adresse, veuillez utiliser l'option « Déménagement d'agence » dans la section Actions fréquentes.")

                with st.form("edit_agency"):
                    new_bureau = st.text_input("Nom", value=row["Kantoor_Bureau"] or "")
                    new_taal = st.selectbox("Langue", ["F", "N", "D"], index=["F", "N", "D"].index(row["Taal"]) if row["Taal"] in ["F", "N", "D"] else 0)
                    new_contact = st.text_input("Contact", value=row["Contactnaam"] or "")
                    new_tel = st.text_input("Tél", value=row["Teln"] or "")
                    new_gsm = st.text_input("GSM", value=row["GSM"] or "")
                    new_email = st.text_input("Email", value=row["Email"] or "")

                    if st.form_submit_button("Sauvegarder"):
                        run_execute(
                            """UPDATE DimKantoren
                               SET Kantoor_Bureau=?, Taal=?, Contactnaam=?, Teln=?, GSM=?, Email=?
                               WHERE Kantoor_id=?""",
                            [new_bureau, new_taal, new_contact, new_tel, new_gsm, new_email, selected_id],
                        )
                        show_success("✅ Agence mise à jour !", "ag_edit")

                display_success("ag_edit")

    # ── Clôturer une agence ──
    with tab_close:
        st.subheader("Clôturer une agence")
        st.info("La clôture rapatrie le(s) scanner(s) de l'agence vers Maca Express, clôt le(s) mouvement(s) en cours et crée une fiche maintenance pour chacun.")
        st.caption("💡 Si un scanner doit être repris par une autre agence, utilisez d'abord **Actions fréquentes > Transfert scanner** pour le transférer, puis revenez ici pour clôturer l'agence.")

        col_c1, col_c2 = st.columns(2)
        search_ag_close = col_c1.text_input("Rechercher par localité ou adresse", key="ag_close_search")
        search_ag_close_kid = col_c2.text_input("Rechercher par Kantoor ID", key="ag_close_kid")
        open_agencies = get_open_agencies()

        if open_agencies.empty:
            st.info("Aucune agence ouverte.")
        else:
            filtered_close = open_agencies
            if search_ag_close:
                mask = (
                    open_agencies["Localite"].astype(str).str.lower().str.contains(search_ag_close.lower()) |
                    open_agencies["Kantoor_Bureau"].astype(str).str.lower().str.contains(search_ag_close.lower()) |
                    open_agencies["Adresse"].astype(str).str.lower().str.contains(search_ag_close.lower())
                )
                filtered_close = filtered_close[mask]
            if search_ag_close_kid:
                filtered_close = filtered_close[filtered_close["Kantoor_id"].astype(str) == search_ag_close_kid]

            if filtered_close.empty:
                st.warning(f"Aucune agence ouverte trouvée pour '{search_ag_close}'.")
            else:
                ag_close_id = st.selectbox(
                    "Agence à clôturer",
                    filtered_close["Kantoor_id"].tolist(),
                    format_func=lambda x: agency_label(filtered_close[filtered_close["Kantoor_id"] == x].iloc[0]),
                    key="close_ag_select"
                )

                scanners_in_agency = run_query(
                    "SELECT f.Serial_num FROM FactMovementsHistory f WHERE f.Kantoor_id = ? AND f.DateFin IS NULL AND f.Action = 'installé'",
                    [ag_close_id],
                )
                if not scanners_in_agency.empty:
                    st.caption(f"Scanner(s) dans cette agence : {', '.join(str(s) for s in scanners_in_agency['Serial_num'].tolist())}")
                else:
                    st.caption("Aucun scanner actuellement installé dans cette agence.")

                close_date = st.date_input("Date de clôture", value=date.today(), key="close_ag_date")
                close_dest = st.selectbox(
                    "Destination du scanner",
                    ["Maca Express", "atelier Procedo"],
                    key="close_ag_dest"
                )

                if st.button("Clôturer l'agence", type="primary"):
                    ag_info = run_query("SELECT Kantoor_Bureau, Localite FROM DimKantoren WHERE Kantoor_id = ?", [ag_close_id]).iloc[0]
                    localite_name = ag_info["Localite"] or ag_info["Kantoor_Bureau"]

                    _close_via_maca = 0 if close_dest == "atelier Procedo" else 1

                    run_execute(
                        "UPDATE DimKantoren SET Status = 'closed', Fermeture = ? WHERE Kantoor_id = ?",
                        [str(close_date), ag_close_id],
                    )

                    for _, r in scanners_in_agency.iterrows():
                        sn = int(r["Serial_num"])
                        run_execute(
                            "UPDATE FactMovementsHistory SET DateFin = ?, Action = 'agence fermée', Via_Maca_Fin = ? WHERE Serial_num = ? AND Kantoor_id = ? AND DateFin IS NULL AND Action = 'installé'",
                            [str(close_date), _close_via_maca, sn, ag_close_id],
                        )
                        run_execute(
                            "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action, Via_Maca) VALUES (?,NULL,?,NULL,'réparation/maintenance',?)",
                            [sn, str(close_date), _close_via_maca],
                        )
                        if close_dest == "atelier Procedo":
                            update_scanner_loc(sn, "atelier Procedo")
                        else:
                            update_scanner_loc(sn, "Maca Express", transit_retour=True)
                        run_execute(
                            """INSERT INTO FactScannersMaintenance
                               (Serial_num, Event_type, Panne_detected, Info_Maintenance, Copie, Return_date, End_Maintenance)
                               VALUES (?,'Maintenance','aucune',?,NULL,?,NULL)""",
                            [sn, f"Fermeture agence {localite_name}", str(close_date)],
                        )

                    nb = len(scanners_in_agency)
                    _dest_label = "atelier Procedo (à réparer)" if close_dest == "atelier Procedo" else "Maca Express (en transit retour)"
                    show_success(f"✅ Agence {localite_name} clôturée. {nb} scanner(s) rapatrié(s) vers {_dest_label}.", "ag_close")

        display_success("ag_close")


# ═════════════════════════════════════════════════════════════════════════════
#  MOUVEMENTS
# ═════════════════════════════════════════════════════════════════════════════

elif page == "Mouvements":
    st.title("Historique des Mouvements")

    tab_list, tab_undo = st.tabs(["Historique", "Annuler la dernière action"])

    with tab_list:
        col1, col2, col3, col4 = st.columns(4)
        search_sn = col1.text_input("Rechercher par n° de série")
        search_mvt_id = col2.text_input("Rechercher par n° de mouvement")
        search_localite = col3.text_input("Rechercher par localité")
        search_mvt_kid = col4.text_input("Rechercher par Kantoor ID")
        col5, col6, col7 = st.columns(3)
        filter_action = col5.multiselect("Filtrer par action", ACTIONS)
        date_from = col6.date_input("Période (Date début) — du", value=None, key="mvt_date_from")
        date_to = col7.date_input("au", value=None, key="mvt_date_to")

        query = """
            SELECT m.Movement_id, m.Serial_num, m.Kantoor_id,
                   k.Kantoor_Bureau, k.Localite,
                   m.DateDebut, m.DateFin, m.Action
            FROM FactMovementsHistory m
            LEFT JOIN DimKantoren k ON m.Kantoor_id = k.Kantoor_id
            WHERE 1=1
        """
        if search_sn:
            query += f" AND {sql_cast_text('m.Serial_num')} LIKE '%{search_sn}%'"
        if search_mvt_id:
            query += f" AND {sql_cast_text('m.Movement_id')} = '{search_mvt_id}'"
        if search_localite:
            query += f" AND LOWER(k.Localite) LIKE LOWER('%{search_localite}%')"
        if search_mvt_kid:
            query += f" AND {sql_cast_text('m.Kantoor_id')} = '{search_mvt_kid}'"
        if filter_action:
            placeholders = ",".join(f"'{a}'" for a in filter_action)
            query += f" AND m.Action IN ({placeholders})"
        if date_from:
            query += f" AND m.DateDebut >= '{date_from}'"
        if date_to:
            query += f" AND m.DateDebut <= '{date_to}'"
        query += " ORDER BY m.DateDebut DESC"

        df = run_query(query)
        st.dataframe(df, use_container_width=True, hide_index=True, key="df_mouvements_hist")
        st.caption(f"{len(df)} mouvement(s)")

    # ── Annuler la dernière action ──
    with tab_undo:
        st.subheader("Annuler la dernière action")
        st.info("Annule la **dernière action** effectuée et restaure l'état précédent.")
        st.warning("⚠️ L'annulation est prévue pour corriger une erreur ponctuelle. Enchaîner plusieurs annulations peut entraîner des incohérences dans les données.")

        # ── Choix du type d'annulation ──
        AGENCY_ACTIONS = {"Clôture d'agence", "Ouverture d'agence", "Déménagement d'agence"}
        undo_type = st.radio(
            "Type d'action à annuler",
            ["Scanner défectueux (remplacement)",
             "Ajout, transfert ou retrait sans remplacement scanner",
             "Clôture d'agence", "Ouverture d'agence", "Déménagement d'agence"],
            horizontal=True, key="undo_type"
        )

        if undo_type in AGENCY_ACTIONS:
            # ══════ UNDO AGENCE (par Kantoor_id direct) ══════
            undo_ag_kid = st.text_input("Kantoor ID", key="undo_ag_kid")

            if undo_type == "Clôture d'agence":
                if not undo_ag_kid:
                    st.caption("Entrez le Kantoor ID de l'agence fermée à restaurer.")
                else:
                    _ag = run_query("SELECT Kantoor_id, Kantoor_Bureau, Localite, Adresse, Fermeture FROM DimKantoren WHERE Status = 'closed' AND Kantoor_id = ?", [undo_ag_kid])
                    if _ag.empty:
                        st.warning(f"Aucune agence fermée trouvée avec l'ID {undo_ag_kid}.")
                    else:
                        ag_undo_id = int(_ag.iloc[0]["Kantoor_id"])
                        st.success(f"Agence trouvée : **{_ag.iloc[0]['Kantoor_Bureau']}** — {_ag.iloc[0]['Localite']} (ID {ag_undo_id})")

                        # Trouver tous les mouvements 'agence fermée' ouverts (= closing) à cette agence pour le dernier lot
                        _close_mvts = run_query(
                            """SELECT m.Movement_id, m.Serial_num, m.Kantoor_id, m.DateFin, m.Action, m.Via_Maca
                               FROM FactMovementsHistory m
                               WHERE m.Kantoor_id = ? AND m.Action = 'agence fermée' AND m.DateFin IS NOT NULL
                               ORDER BY m.DateFin DESC""",
                            [ag_undo_id]
                        )
                        if _close_mvts.empty:
                            st.warning("Aucun mouvement de clôture trouvé pour cette agence.")
                        else:
                            # Prendre la date de clôture la plus récente
                            _last_close_date = _close_mvts.iloc[0]["DateFin"]
                            _batch = _close_mvts[_close_mvts["DateFin"] == _last_close_date]
                            _sns = _batch["Serial_num"].tolist()

                            st.caption(f"Clôture du {_last_close_date} — {len(_sns)} scanner(s) : {', '.join(str(s) for s in _sns)}")

                            # Vérifier que chaque scanner a un mouvement 'réparation/maintenance' ouvert après la clôture
                            _can_undo = True
                            _undo_details = []
                            for _, _cm in _batch.iterrows():
                                _sn = int(_cm["Serial_num"])
                                _close_mid = int(_cm["Movement_id"])
                                # Le mouvement réparation/maintenance créé par la clôture (le dernier ouvert)
                                _rep_mvt = run_query(
                                    sql_top(
                                        f"""m.Movement_id, m.Action, m.DateDebut
                                        FROM FactMovementsHistory m
                                        WHERE m.Serial_num = {_sn} AND m.DateFin IS NULL""",
                                        1, "ORDER BY m.Movement_id DESC"
                                    )
                                )
                                if _rep_mvt.empty or _rep_mvt.iloc[0]["Action"] not in ('réparation/maintenance', 'stock'):
                                    _can_undo = False
                                    st.error(f"Scanner {_sn} : le dernier mouvement ouvert n'est pas celui créé par la clôture. Undo impossible (déjà modifié depuis).")
                                    break
                                _rep_mid = int(_rep_mvt.iloc[0]["Movement_id"])
                                # Fiche maintenance associée
                                _maint = run_query(
                                    sql_top(
                                        f"""m.Maintenance_id
                                        FROM FactScannersMaintenance m
                                        WHERE m.Serial_num = {_sn}
                                          AND {sql_cast_text('m.Return_date')} = '{_last_close_date}'""",
                                        1, "ORDER BY m.Maintenance_id DESC"
                                    )
                                )
                                _maint_id = int(_maint.iloc[0]["Maintenance_id"]) if not _maint.empty else None
                                _undo_details.append({
                                    "sn": _sn, "close_mid": _close_mid, "rep_mid": _rep_mid, "maint_id": _maint_id
                                })

                            if _can_undo and _undo_details:
                                st.markdown("---")
                                st.markdown("**Aperçu de l'annulation :**")
                                for d in _undo_details:
                                    st.markdown(f"- Scanner **{d['sn']}** : supprimer mouvement #{d['rep_mid']}, restaurer mouvement #{d['close_mid']} → *installé*"
                                                + (f", supprimer maintenance #{d['maint_id']}" if d['maint_id'] else ""))
                                st.markdown(f"- **Agence ID {ag_undo_id}** → Status = *open*, Fermeture = *NULL*")
                                st.markdown("---")

                                _sp, _btn = st.columns([5, 1.5])
                                with _btn:
                                    _confirm = st.button("Confirmer l'annulation", type="primary", key="btn_undo_close_ag", use_container_width=True)
                                if _confirm:
                                    for d in _undo_details:
                                        run_execute("DELETE FROM FactMovementsHistory WHERE Movement_id = ?", [d["rep_mid"]])
                                        # Restaurer l'installé d'avant la clôture
                                        # Via_Maca (livraison) est inchangé, Via_Maca_Fin (récup) devient sans objet (DateFin = NULL)
                                        run_execute("UPDATE FactMovementsHistory SET DateFin = NULL, Action = 'installé' WHERE Movement_id = ?", [d["close_mid"]])
                                        run_execute("UPDATE DimScanners SET Localisation = 'agence DVV', Statut = 'actif' WHERE Serial_num = ?", [d["sn"]])
                                        if d["maint_id"]:
                                            run_execute("DELETE FROM FactScannersMaintenance WHERE Maintenance_id = ?", [d["maint_id"]])
                                    run_execute("UPDATE DimKantoren SET Status = 'open', Fermeture = NULL WHERE Kantoor_id = ?", [ag_undo_id])
                                    show_success(f"✅ Clôture annulée. Agence ID {ag_undo_id} rouverte, {len(_undo_details)} scanner(s) restauré(s) → agence DVV.", "mvt_undo")

            elif undo_type == "Ouverture d'agence":
                if not undo_ag_kid:
                    st.caption("Entrez le Kantoor ID de l'agence ouverte dont annuler l'ouverture.")
                else:
                    _ag = run_query("SELECT Kantoor_id, Kantoor_Bureau, Localite, Adresse, Apparition FROM DimKantoren WHERE Status = 'open' AND Kantoor_id = ?", [undo_ag_kid])
                    if _ag.empty:
                        st.warning(f"Aucune agence ouverte trouvée avec l'ID {undo_ag_kid}.")
                    else:
                        ag_undo_open_id = int(_ag.iloc[0]["Kantoor_id"])
                        st.success(f"Agence trouvée : **{_ag.iloc[0]['Kantoor_Bureau']}** — {_ag.iloc[0]['Localite']} (ID {ag_undo_open_id})")

                        # Scanners actuellement installés dans cette agence
                        _inst_mvts = run_query(
                            """SELECT m.Movement_id, m.Serial_num, m.DateDebut
                               FROM FactMovementsHistory m
                               WHERE m.Kantoor_id = ? AND m.DateFin IS NULL AND m.Action = 'installé'""",
                            [ag_undo_open_id]
                        )

                        _can_undo = True
                        _undo_details = []
                        if not _inst_mvts.empty:
                            st.caption(f"{len(_inst_mvts)} scanner(s) installé(s) : {', '.join(str(s) for s in _inst_mvts['Serial_num'].tolist())}")
                            for _, _im in _inst_mvts.iterrows():
                                _sn = int(_im["Serial_num"])
                                _inst_mid = int(_im["Movement_id"])
                                # Le mouvement précédent (stock ou réparation) doit exister
                                _prev = run_query(
                                    sql_top(
                                        f"""m.Movement_id, m.Action, m.DateFin
                                        FROM FactMovementsHistory m
                                        WHERE m.Serial_num = {_sn} AND m.Movement_id < {_inst_mid}""",
                                        1, "ORDER BY m.Movement_id DESC"
                                    )
                                )
                                if _prev.empty:
                                    _can_undo = False
                                    st.error(f"Scanner {_sn} : pas de mouvement précédent à restaurer. Undo impossible.")
                                    break
                                _prev_mid = int(_prev.iloc[0]["Movement_id"])
                                _prev_action = _prev.iloc[0]["Action"]
                                _undo_details.append({
                                    "sn": _sn, "inst_mid": _inst_mid, "prev_mid": _prev_mid, "prev_action": _prev_action
                                })
                        else:
                            st.caption("Aucun scanner dans cette agence.")

                        # Vérifier qu'aucun autre mouvement n'existe pour cette agence (sinon ce n'est plus un simple undo ouverture)
                        _all_mvts_ag = run_query(
                            "SELECT COUNT(*) AS n FROM FactMovementsHistory WHERE Kantoor_id = ?",
                            [ag_undo_open_id]
                        )
                        _total_mvts = int(_all_mvts_ag["n"].values[0])
                        if _total_mvts > len(_inst_mvts):
                            _can_undo = False
                            st.error("Cette agence a d'autres mouvements (clôtures, transferts, pannes...). L'annulation d'ouverture n'est possible que si l'agence vient d'être créée.")

                        if _can_undo:
                            st.markdown("---")
                            st.markdown("**Aperçu de l'annulation :**")
                            for d in _undo_details:
                                st.markdown(f"- Scanner **{d['sn']}** : supprimer mouvement #{d['inst_mid']}, restaurer mouvement #{d['prev_mid']} (*{d['prev_action']}*, DateFin → NULL)")
                            st.markdown(f"- **Supprimer l'agence ID {ag_undo_open_id}** de DimKantoren")
                            st.markdown("---")

                            _sp, _btn = st.columns([5, 1.5])
                            with _btn:
                                _confirm = st.button("Confirmer l'annulation", type="primary", key="btn_undo_open_ag", use_container_width=True)
                            if _confirm:
                                for d in _undo_details:
                                    run_execute("DELETE FROM FactMovementsHistory WHERE Movement_id = ?", [d["inst_mid"]])
                                    run_execute("UPDATE FactMovementsHistory SET DateFin = NULL WHERE Movement_id = ?", [d["prev_mid"]])
                                    # Restaurer localisation selon mouvement précédent
                                    if d["prev_action"] in ('stock', 'réparation/maintenance'):
                                        update_scanner_loc(d["sn"], "Maca Express", transit_retour=(d["prev_action"] == 'réparation/maintenance'))
                                    else:
                                        run_execute("UPDATE DimScanners SET Localisation = 'Procedo', Statut = 'inactif' WHERE Serial_num = ?", [d["sn"]])
                                run_execute("DELETE FROM DimKantoren WHERE Kantoor_id = ?", [ag_undo_open_id])
                                show_success(f"✅ Ouverture annulée. Agence ID {ag_undo_open_id} supprimée, {len(_undo_details)} scanner(s) restauré(s).", "mvt_undo")

            elif undo_type == "Déménagement d'agence":
                st.caption("Entrez le Kantoor ID de la **nouvelle** agence (issue du déménagement). L'annulation supprime cette agence et rouvre l'ancienne.")

                if not undo_ag_kid:
                    pass
                else:
                    _ag = run_query("SELECT Kantoor_id, Kantoor_Bureau, Localite, Adresse, Apparition FROM DimKantoren WHERE Status = 'open' AND Kantoor_id = ?", [undo_ag_kid])
                    if _ag.empty:
                        st.warning(f"Aucune agence ouverte trouvée avec l'ID {undo_ag_kid}.")
                    else:
                        new_ag_id = int(_ag.iloc[0]["Kantoor_id"])
                        st.success(f"Agence trouvée : **{_ag.iloc[0]['Kantoor_Bureau']}** — {_ag.iloc[0]['Localite']} (ID {new_ag_id})")

                        # Trouver les scanners installés dans la nouvelle agence (via déménagement)
                        _dem_inst = run_query(
                            """SELECT m.Movement_id, m.Serial_num, m.DateDebut, m.Via_Maca
                               FROM FactMovementsHistory m
                               WHERE m.Kantoor_id = ? AND m.DateFin IS NULL AND m.Action = 'déménagement (installation)'""",
                            [new_ag_id]
                        )

                        _can_undo = True
                        _undo_details = []
                        _old_ag_id = None

                        if not _dem_inst.empty:
                            for _, _di in _dem_inst.iterrows():
                                _sn = int(_di["Serial_num"])
                                _new_mid = int(_di["Movement_id"])
                                # Le mouvement précédent devrait être 'déménagement (fermeture)'
                                _prev = run_query(
                                    sql_top(
                                        f"""m.Movement_id, m.Kantoor_id, m.Action, m.Via_Maca, m.DateFin
                                        FROM FactMovementsHistory m
                                        WHERE m.Serial_num = {_sn} AND m.Movement_id < {_new_mid}""",
                                        1, "ORDER BY m.Movement_id DESC"
                                    )
                                )
                                if _prev.empty or _prev.iloc[0]["Action"] != 'déménagement (fermeture)':
                                    _can_undo = False
                                    st.error(f"Scanner {_sn} : le mouvement précédent n'est pas un déménagement (fermeture). Ce n'est peut-être pas un déménagement.")
                                    break
                                _close_mid = int(_prev.iloc[0]["Movement_id"])
                                _prev_kid = _prev.iloc[0]["Kantoor_id"]
                                if _old_ag_id is None:
                                    _old_ag_id = int(_prev_kid) if pd.notna(_prev_kid) else None
                                elif pd.notna(_prev_kid) and int(_prev_kid) != _old_ag_id:
                                    _can_undo = False
                                    st.error("Les scanners viennent d'agences différentes. Ce n'est pas un déménagement standard.")
                                    break
                                _undo_details.append({"sn": _sn, "new_mid": _new_mid, "close_mid": _close_mid})
                        else:
                            st.warning("Aucun scanner installé dans cette agence.")
                            _can_undo = False

                        # Vérifier que l'ancienne agence est bien fermée
                        if _can_undo and _old_ag_id:
                            _old_ag_info = run_query("SELECT Kantoor_Bureau, Localite, Status FROM DimKantoren WHERE Kantoor_id = ?", [_old_ag_id])
                            if _old_ag_info.empty or _old_ag_info.iloc[0]["Status"] != 'closed':
                                _can_undo = False
                                st.error(f"L'ancienne agence (ID {_old_ag_id}) n'est pas fermée. Undo impossible.")
                            else:
                                _old_name = f"{_old_ag_info.iloc[0]['Kantoor_Bureau']} — {_old_ag_info.iloc[0]['Localite']}"
                                st.caption(f"Ancienne agence : {_old_name} (ID {_old_ag_id})")

                        # Vérifier qu'il n'y a pas d'autres mouvements dans la nouvelle agence
                        if _can_undo:
                            _all_new = run_query("SELECT COUNT(*) AS n FROM FactMovementsHistory WHERE Kantoor_id = ?", [new_ag_id])
                            if int(_all_new["n"].values[0]) > len(_dem_inst):
                                _can_undo = False
                                st.error("La nouvelle agence a d'autres mouvements. L'annulation n'est possible que si elle vient d'être créée par déménagement.")

                        if _can_undo and _undo_details and _old_ag_id:
                            st.markdown("---")
                            st.markdown("**Aperçu de l'annulation :**")
                            for d in _undo_details:
                                st.markdown(f"- Scanner **{d['sn']}** : supprimer mouvement #{d['new_mid']}, restaurer mouvement #{d['close_mid']} → *installé* dans agence ID {_old_ag_id}")
                            st.markdown(f"- **Supprimer la nouvelle agence ID {new_ag_id}** de DimKantoren")
                            st.markdown(f"- **Rouvrir l'ancienne agence ID {_old_ag_id}** (Status → *open*, Fermeture → NULL)")
                            st.markdown("---")

                            _sp, _btn = st.columns([5, 1.5])
                            with _btn:
                                _confirm = st.button("Confirmer l'annulation", type="primary", key="btn_undo_dem", use_container_width=True)
                            if _confirm:
                                for d in _undo_details:
                                    run_execute("DELETE FROM FactMovementsHistory WHERE Movement_id = ?", [d["new_mid"]])
                                    # Restaurer l'installé d'avant le déménagement (préserver Via_Maca original)
                                    run_execute("UPDATE FactMovementsHistory SET DateFin = NULL, Action = 'installé' WHERE Movement_id = ?", [d["close_mid"]])
                                    run_execute("UPDATE DimScanners SET Localisation = 'agence DVV', Statut = 'actif' WHERE Serial_num = ?", [d["sn"]])
                                run_execute("DELETE FROM DimKantoren WHERE Kantoor_id = ?", [new_ag_id])
                                run_execute("UPDATE DimKantoren SET Status = 'open', Fermeture = NULL WHERE Kantoor_id = ?", [_old_ag_id])
                                show_success(f"✅ Déménagement annulé. Nouvelle agence ID {new_ag_id} supprimée, ancienne agence ID {_old_ag_id} rouverte, {len(_undo_details)} scanner(s) restauré(s).", "mvt_undo")

        elif undo_type == "Scanner défectueux (remplacement)":
            # ══════ UNDO SCANNER DÉFECTUEUX (2 scanners : défectueux + remplacement) ══════
            st.caption("Recherchez par le n° de série du scanner **défectueux** ou du scanner de **remplacement**.")
            undo_def_sn = st.text_input("N° de série du scanner", key="undo_def_sn")

            if undo_def_sn:
                # L'action "scanner défectueux" fait :
                # - Défectueux : mouvement 'installé' → Action='panne détectée', DateFin=date (FERMÉ)
                # - Défectueux : fiche maintenance créée
                # - Remplacement : mouvement stock fermé, nouveau 'installé' ouvert
                # Donc le défectueux n'a PAS de mouvement ouvert, son dernier = 'panne détectée' fermé

                _sn_match = run_query(
                    f"""SELECT DISTINCT Serial_num FROM DimScanners
                        WHERE {sql_cast_text('Serial_num')} LIKE '%{undo_def_sn}%'"""
                )
                if _sn_match.empty:
                    st.warning("Aucun scanner trouvé.")
                else:
                    _found_pairs = []
                    for _, _sm in _sn_match.iterrows():
                        _sn = int(_sm["Serial_num"])

                        # ── Cas 1 : SN saisi = scanner de remplacement (dernier mvt = 'installé' ouvert) ──
                        _last_open = run_query(
                            sql_top(
                                f"""m.Movement_id, m.Serial_num, m.Kantoor_id, m.Action, m.DateDebut,
                                    k.Kantoor_Bureau, k.Localite
                                FROM FactMovementsHistory m
                                LEFT JOIN DimKantoren k ON m.Kantoor_id = k.Kantoor_id
                                WHERE m.Serial_num = {_sn} AND m.DateFin IS NULL""",
                                1, "ORDER BY m.Movement_id DESC"
                            )
                        )
                        if not _last_open.empty and _last_open.iloc[0]["Action"] == 'installé' and pd.notna(_last_open.iloc[0]["Kantoor_id"]):
                            _mid = int(_last_open.iloc[0]["Movement_id"])
                            _kid = int(_last_open.iloc[0]["Kantoor_id"])
                            _date_str = str(pd.to_datetime(_last_open.iloc[0]["DateDebut"]).date())
                            _defect = run_query(
                                f"""SELECT m.Movement_id, m.Serial_num
                                    FROM FactMovementsHistory m
                                    WHERE m.Kantoor_id = {_kid}
                                      AND m.Action = 'panne détectée'
                                      AND CONVERT(VARCHAR(10), m.DateFin, 120) = '{_date_str}'
                                      AND m.Serial_num != {_sn}"""
                            )
                            if not _defect.empty:
                                _found_pairs.append({
                                    "replace_sn": _sn, "defect_sn": int(_defect.iloc[0]["Serial_num"]),
                                    "kid": _kid, "date": _date_str, "replace_mid": _mid,
                                    "agence": f"{_last_open.iloc[0]['Kantoor_Bureau'] or ''} — {_last_open.iloc[0]['Localite'] or ''}".strip(" —")
                                })
                                continue

                        # ── Cas 2 : SN saisi = scanner défectueux (dernier mvt = 'panne détectée' FERMÉ) ──
                        _last_any = run_query(
                            sql_top(
                                f"""m.Movement_id, m.Kantoor_id, m.Action, m.DateFin,
                                    k.Kantoor_Bureau, k.Localite
                                FROM FactMovementsHistory m
                                LEFT JOIN DimKantoren k ON m.Kantoor_id = k.Kantoor_id
                                WHERE m.Serial_num = {_sn}""",
                                1, "ORDER BY m.Movement_id DESC"
                            )
                        )
                        if not _last_any.empty and _last_any.iloc[0]["Action"] == 'panne détectée' and pd.notna(_last_any.iloc[0]["Kantoor_id"]):
                            _panne_mid = int(_last_any.iloc[0]["Movement_id"])
                            _panne_kid = int(_last_any.iloc[0]["Kantoor_id"])
                            _panne_date = _last_any.iloc[0]["DateFin"]
                            if pd.notna(_panne_date):
                                _panne_date_str = str(pd.to_datetime(_panne_date).date())
                                _repl = run_query(
                                    f"""SELECT m.Movement_id, m.Serial_num,
                                               k.Kantoor_Bureau, k.Localite
                                        FROM FactMovementsHistory m
                                        LEFT JOIN DimKantoren k ON m.Kantoor_id = k.Kantoor_id
                                        WHERE m.Kantoor_id = {_panne_kid}
                                          AND m.Action = 'installé' AND m.DateFin IS NULL
                                          AND CONVERT(VARCHAR(10), m.DateDebut, 120) = '{_panne_date_str}'
                                          AND m.Serial_num != {_sn}"""
                                )
                                if not _repl.empty:
                                    _found_pairs.append({
                                        "replace_sn": int(_repl.iloc[0]["Serial_num"]),
                                        "defect_sn": _sn, "kid": _panne_kid, "date": _panne_date_str,
                                        "replace_mid": int(_repl.iloc[0]["Movement_id"]),
                                        "agence": f"{_last_any.iloc[0]['Kantoor_Bureau'] or ''} — {_last_any.iloc[0]['Localite'] or ''}".strip(" —")
                                    })

                    if not _found_pairs:
                        st.warning("Aucun remplacement de scanner défectueux trouvé pour ce n° de série.")
                    else:
                        pair = _found_pairs[0]
                        st.success(f"Remplacement trouvé — Agence : **{pair['agence']}** (ID {pair['kid']}) — Date : {pair['date']}")
                        st.markdown(f"- Scanner défectueux : **SN {pair['defect_sn']}**")
                        st.markdown(f"- Scanner de remplacement : **SN {pair['replace_sn']}**")

                        _def_sn = pair["defect_sn"]
                        _repl_sn = pair["replace_sn"]
                        _repl_mid = pair["replace_mid"]

                        # Mouvement 'panne détectée' du défectueux (fermé, à restaurer → installé)
                        _def_panne = run_query(
                            sql_top(
                                f"""m.Movement_id, m.Action, m.Kantoor_id
                                FROM FactMovementsHistory m
                                WHERE m.Serial_num = {_def_sn} AND m.Action = 'panne détectée'""",
                                1, "ORDER BY m.Movement_id DESC"
                            )
                        )
                        # Fiche maintenance du défectueux
                        _def_maint = run_query(
                            sql_top(
                                f"""m.Maintenance_id, m.Event_type, m.Panne_detected
                                FROM FactScannersMaintenance m
                                WHERE m.Serial_num = {_def_sn}""",
                                1, "ORDER BY m.Maintenance_id DESC"
                            )
                        )
                        # Mouvement stock précédent du remplacement
                        _repl_prev = run_query(
                            sql_top(
                                f"""m.Movement_id, m.Action
                                FROM FactMovementsHistory m
                                WHERE m.Serial_num = {_repl_sn} AND m.Movement_id < {_repl_mid}""",
                                1, "ORDER BY m.Movement_id DESC"
                            )
                        )

                        _can_undo = True
                        if _def_panne.empty:
                            st.error(f"Scanner défectueux SN {_def_sn} : mouvement 'panne détectée' introuvable.")
                            _can_undo = False
                        if _repl_prev.empty:
                            st.error(f"Scanner remplacement SN {_repl_sn} : pas de mouvement précédent à restaurer.")
                            _can_undo = False

                        if _can_undo:
                            _def_panne_mid = int(_def_panne.iloc[0]["Movement_id"])
                            _def_maint_id = int(_def_maint.iloc[0]["Maintenance_id"]) if not _def_maint.empty else None
                            _repl_prev_mid = int(_repl_prev.iloc[0]["Movement_id"])
                            _repl_prev_action = _repl_prev.iloc[0]["Action"]

                            st.markdown("---")
                            st.markdown("**Aperçu de l'annulation :**")
                            st.markdown(f"- **SN {_def_sn}** (défectueux) : restaurer mouvement #{_def_panne_mid} → *installé* (DateFin → NULL) dans agence ID {pair['kid']}")
                            if _def_maint_id:
                                st.markdown(f"- Supprimer fiche maintenance #{_def_maint_id}")
                            st.markdown(f"- **SN {_repl_sn}** (remplacement) : supprimer mouvement #{_repl_mid} (installé), restaurer mouvement #{_repl_prev_mid} (*{_repl_prev_action}*, DateFin → NULL)")
                            st.markdown("---")

                            _sp, _btn = st.columns([5, 1.5])
                            with _btn:
                                _confirm = st.button("Confirmer l'annulation", type="primary", key="btn_undo_defect", use_container_width=True)
                            if _confirm:
                                # 1. Défectueux : restaurer panne détectée → installé, DateFin → NULL
                                # Via_Maca (livraison) inchangé, Via_Maca_Fin (récup) devient sans objet (DateFin → NULL)
                                run_execute(
                                    "UPDATE FactMovementsHistory SET DateFin = NULL, Action = 'installé' WHERE Movement_id = ?",
                                    [_def_panne_mid]
                                )
                                run_execute(
                                    "UPDATE DimScanners SET Localisation = 'agence DVV', Statut = 'actif' WHERE Serial_num = ?",
                                    [_def_sn]
                                )
                                if _def_maint_id:
                                    run_execute("DELETE FROM FactScannersMaintenance WHERE Maintenance_id = ?", [_def_maint_id])

                                # 2. Remplacement : supprimer installé, rouvrir stock/réparation
                                run_execute("DELETE FROM FactMovementsHistory WHERE Movement_id = ?", [_repl_mid])
                                run_execute("UPDATE FactMovementsHistory SET DateFin = NULL WHERE Movement_id = ?", [_repl_prev_mid])
                                if _repl_prev_action in ('stock', 'réparation/maintenance'):
                                    update_scanner_loc(_repl_sn, "Maca Express", transit_retour=(_repl_prev_action == 'réparation/maintenance'))
                                else:
                                    run_execute("UPDATE DimScanners SET Localisation = 'Procedo', Statut = 'inactif' WHERE Serial_num = ?", [_repl_sn])

                                show_success(
                                    f"✅ Remplacement annulé. SN {_def_sn} restauré en agence (ID {pair['kid']}), SN {_repl_sn} remis en stock.",
                                    "mvt_undo"
                                )

        else:
            # ══════ UNDO SCANNER SIMPLE (par SN direct, 1 scanner) ══════
            sn_undo = st.text_input("N° de série du scanner", key="undo_search_sn")

            if sn_undo:
                # Vérifier que le SN existe
                _sn_check = run_query("SELECT Serial_num FROM DimScanners WHERE Serial_num = ?", [sn_undo])
                if _sn_check.empty:
                    st.warning(f"Aucun scanner trouvé avec le SN {sn_undo}.")
                    sn_undo = None

            if sn_undo:
                sn_undo = int(sn_undo)
                display_scanner_context(int(sn_undo), prefix="undo")

                # Trouver les 2 derniers mouvements du scanner (par Movement_id DESC)
                last_two = run_query(
                    sql_top(
                        f"""m.Movement_id, m.Serial_num, m.Kantoor_id,
                            k.Kantoor_Bureau, k.Localite,
                            m.DateDebut, m.DateFin, m.Action, m.Via_Maca
                        FROM FactMovementsHistory m
                        LEFT JOIN DimKantoren k ON m.Kantoor_id = k.Kantoor_id
                        WHERE m.Serial_num = {sn_undo}""",
                        2,
                        "ORDER BY m.Movement_id DESC"
                    )
                )

                if last_two.empty:
                    st.error("Ce scanner n'a aucun mouvement enregistré.")
                elif len(last_two) < 2:
                    st.error(
                        "Ce scanner n'a qu'un seul mouvement. L'annulation supprimerait tout son historique. "
                        "Si nécessaire, corrigez-le manuellement via la section **Maintenance** ou contactez l'administrateur."
                    )
                else:
                    current = last_two.iloc[0]
                    previous = last_two.iloc[1]

                    cur_id = int(current["Movement_id"])
                    cur_action = current["Action"]
                    cur_date = current["DateDebut"]
                    cur_kantoor = current["Kantoor_id"]
                    cur_agence = f"{current['Kantoor_Bureau'] or ''} — {current['Localite'] or ''}".strip(" —")

                    prev_id = int(previous["Movement_id"])
                    prev_action = previous["Action"]
                    prev_kantoor = previous["Kantoor_id"]
                    prev_agence = f"{previous['Kantoor_Bureau'] or ''} — {previous['Localite'] or ''}".strip(" —")

                    # ── Vérification : le mouvement actuel doit être ouvert ──
                    if pd.notna(current["DateFin"]):
                        st.error(
                            f"Le dernier mouvement (#{cur_id} — {cur_action}) est déjà clôturé "
                            f"(DateFin = {current['DateFin']}). Il n'y a pas d'action récente à annuler.\n\n"
                            "Si vous devez corriger un mouvement ancien, veuillez contacter l'administrateur."
                        )
                    # ── Rediriger si action agence ou remplacement ──
                    elif cur_action == 'agence fermée':
                        st.warning("Ce mouvement est lié à une **clôture d'agence**. Utilisez le type *Clôture d'agence* ci-dessus.")
                    elif cur_action in ('installé', 'déménagement (installation)') and pd.notna(cur_kantoor) and prev_action in ('agence fermée', 'déménagement (fermeture)'):
                        st.warning("Ce mouvement semble lié à un **déménagement d'agence**. Utilisez le type *Déménagement d'agence* ci-dessus.")
                    else:
                        # ── Validation verte ──
                        _action_label = cur_action
                        if pd.notna(cur_kantoor):
                            st.success(f"Scanner **SN {sn_undo}** — Dernière action : *{_action_label}* — Agence : **{cur_agence}** (ID {int(cur_kantoor)}) — Date : {cur_date}")
                        else:
                            st.success(f"Scanner **SN {sn_undo}** — Dernière action : *{_action_label}* — Date : {cur_date}")

                        # ── Déterminer l'action originale du mouvement précédent ──
                        CLOSING_ACTIONS = {'panne détectée', 'retiré', 'agence fermée', 'transféré', 'déménagement (fermeture)'}
                        restore_action = 'installé' if prev_action in CLOSING_ACTIONS else prev_action

                        # ── Déterminer la nouvelle localisation du scanner ──
                        if restore_action == 'installé' and pd.notna(prev_kantoor):
                            new_loc = "agence DVV"
                            new_statut = "actif"
                            loc_label = f"agence DVV — {prev_agence} (ID {int(prev_kantoor)})"
                        elif restore_action == 'stock':
                            new_loc = "Maca Express"
                            new_statut = "à livrer"
                            loc_label = "Maca Express (à livrer)"
                        elif restore_action in ('réparation', 'réparation/maintenance'):
                            new_loc = "Maca Express"
                            new_statut = "en transit retour"
                            loc_label = "Maca Express (en transit retour)"
                        else:
                            new_loc = "Procedo"
                            new_statut = "inactif"
                            loc_label = "Procedo (inactif)"

                        # ── Vérifier impact maintenance ──
                        maint_to_delete = None
                        maint_to_reopen = None

                        if cur_action in ('réparation', 'réparation/maintenance'):
                            maint_match = run_query(
                                sql_top(
                                    f"""m.Maintenance_id, m.Event_type, m.Panne_detected
                                    FROM FactScannersMaintenance m
                                    WHERE m.Serial_num = {sn_undo}
                                      AND {sql_cast_text('m.Return_date')} = '{cur_date}'""",
                                    1,
                                    "ORDER BY m.Maintenance_id DESC"
                                )
                            )
                            if not maint_match.empty:
                                maint_to_delete = int(maint_match.iloc[0]["Maintenance_id"])

                        elif cur_action == 'stock' and restore_action in ('réparation', 'réparation/maintenance'):
                            maint_reopen = run_query(
                                sql_top(
                                    f"""m.Maintenance_id, m.End_Maintenance
                                    FROM FactScannersMaintenance m
                                    WHERE m.Serial_num = {sn_undo}
                                      AND m.End_Maintenance IS NOT NULL""",
                                    1,
                                    "ORDER BY m.Maintenance_id DESC"
                                )
                            )
                            if not maint_reopen.empty:
                                maint_to_reopen = int(maint_reopen.iloc[0]["Maintenance_id"])

                        # ── Aperçu ──
                        st.markdown("---")
                        st.markdown("**Aperçu de l'annulation :**")

                        preview_lines = []
                        preview_lines.append(
                            f"**Supprimer** le mouvement #{cur_id} — *{cur_action}* "
                            f"(début : {cur_date})"
                        )
                        restore_detail = ""
                        if prev_action in CLOSING_ACTIONS:
                            restore_detail = f", Action restaurée : *{prev_action}* → *installé*"
                        preview_lines.append(
                            f"**Restaurer** le mouvement #{prev_id} — *{restore_action}* "
                            f"(DateFin → NULL{restore_detail})"
                        )
                        preview_lines.append(f"**Scanner {sn_undo}** → {loc_label}")

                        if maint_to_delete:
                            mt = maint_match.iloc[0]
                            preview_lines.append(
                                f"**Supprimer** la fiche maintenance #{maint_to_delete} "
                                f"({mt['Event_type']} — {mt['Panne_detected'] or 'N/A'})"
                            )
                        if maint_to_reopen:
                            preview_lines.append(
                                f"**Rouvrir** la fiche maintenance #{maint_to_reopen} "
                                f"(End_Maintenance → NULL)"
                            )

                        for line in preview_lines:
                            st.markdown(f"- {line}")

                        st.markdown("---")

                        # ── Bouton de confirmation ──
                        _sp_undo, _btn_undo = st.columns([5, 1.5])
                        with _btn_undo:
                            confirm_undo = st.button(
                                "Confirmer l'annulation", type="primary",
                                key=f"btn_undo_{cur_id}", use_container_width=True
                            )

                        if confirm_undo:
                            run_execute(
                                "DELETE FROM FactMovementsHistory WHERE Movement_id = ?",
                                [cur_id]
                            )

                            if prev_action in CLOSING_ACTIONS:
                                run_execute(
                                    "UPDATE FactMovementsHistory SET DateFin = NULL, Action = 'installé' WHERE Movement_id = ?",
                                    [prev_id]
                                )
                            else:
                                run_execute(
                                    "UPDATE FactMovementsHistory SET DateFin = NULL WHERE Movement_id = ?",
                                    [prev_id]
                                )

                            if new_statut == "en transit retour":
                                update_scanner_loc(sn_undo, new_loc, transit_retour=True)
                            else:
                                run_execute(
                                    "UPDATE DimScanners SET Localisation = ?, Statut = ? WHERE Serial_num = ?",
                                    [new_loc, new_statut, sn_undo]
                                )

                            if maint_to_delete:
                                run_execute(
                                    "DELETE FROM FactScannersMaintenance WHERE Maintenance_id = ?",
                                    [maint_to_delete]
                                )
                            if maint_to_reopen:
                                run_execute(
                                    "UPDATE FactScannersMaintenance SET End_Maintenance = NULL WHERE Maintenance_id = ?",
                                    [maint_to_reopen]
                                )

                            show_success(
                                f"✅ Mouvement #{cur_id} annulé. Scanner {sn_undo} restauré → {loc_label}.",
                                "mvt_undo"
                            )

        # ── Message de succès affiché en bas de l'onglet ──
        display_success("mvt_undo")


# ═════════════════════════════════════════════════════════════════════════════
#  MAINTENANCE
# ═════════════════════════════════════════════════════════════════════════════

elif page == "Maintenance":
    st.title("Maintenance & Réparations")

    # ── Filtres période ──
    _mt_min_date_df = run_query("SELECT MIN(Return_date) AS d FROM FactScannersMaintenance WHERE Return_date IS NOT NULL")
    _mt_min = None
    if not _mt_min_date_df.empty and pd.notna(_mt_min_date_df["d"].values[0]):
        _mt_min = pd.to_datetime(_mt_min_date_df["d"].values[0]).date()
    if _mt_min is None:
        _mt_min = date.today().replace(month=1, day=1)

    _mt_current_year = date.today().year
    _mt_years = list(range(_mt_current_year, _mt_min.year - 1, -1))
    _mt_months_labels = {
        0: "Toute l'année",
        1: "Janvier", 2: "Février", 3: "Mars", 4: "Avril",
        5: "Mai", 6: "Juin", 7: "Juillet", 8: "Août",
        9: "Septembre", 10: "Octobre", 11: "Novembre", 12: "Décembre",
    }

    col_mf1, col_mf2, col_mf3 = st.columns([1, 1, 2])
    mt_kpi_year = col_mf1.selectbox("Année", _mt_years, index=0, key="mt_kpi_year")
    mt_kpi_month = col_mf2.selectbox(
        "Mois",
        list(_mt_months_labels.keys()),
        format_func=lambda x: _mt_months_labels[x],
        index=0,
        key="mt_kpi_month"
    )

    if mt_kpi_month == 0:
        mt_date_from = date(mt_kpi_year, 1, 1)
        mt_date_to = min(date(mt_kpi_year, 12, 31), date.today())
    else:
        import calendar
        mt_date_from = date(mt_kpi_year, mt_kpi_month, 1)
        _mt_last_day = calendar.monthrange(mt_kpi_year, mt_kpi_month)[1]
        mt_date_to = min(date(mt_kpi_year, mt_kpi_month, _mt_last_day), date.today())

    with col_mf3:
        mt_use_custom = st.checkbox("Dates personnalisées", key="mt_custom_dates")
    if mt_use_custom:
        col_md1, col_md2 = st.columns(2)
        mt_date_from = col_md1.date_input("Du", value=mt_date_from, min_value=_mt_min, max_value=date.today(), key="mt_date_from")
        mt_date_to = col_md2.date_input("Au", value=mt_date_to, min_value=_mt_min, max_value=date.today(), key="mt_date_to")

    st.caption(f"Période : **{mt_date_from.strftime('%d/%m/%Y')}** → **{mt_date_to.strftime('%d/%m/%Y')}**")

    # ── KPIs Maintenance ──
    # Nouvelles pannes (Return_date dans la période)
    _nb_new = run_query(
        "SELECT COUNT(*) AS n FROM FactScannersMaintenance WHERE Return_date BETWEEN ? AND ?",
        [str(mt_date_from), str(mt_date_to)]
    )
    nb_new_pannes = int(_nb_new["n"].values[0]) if not _nb_new.empty else 0

    # Réparations clôturées (End_Maintenance dans la période)
    _nb_closed = run_query(
        "SELECT COUNT(*) AS n FROM FactScannersMaintenance WHERE End_Maintenance BETWEEN ? AND ?",
        [str(mt_date_from), str(mt_date_to)]
    )
    nb_closed = int(_nb_closed["n"].values[0]) if not _nb_closed.empty else 0

    # Durée moyenne de réparation (jours, sur toutes les maintenances clôturées)
    _avg_dur = run_query(
        """SELECT AVG(CAST(DATEDIFF(DAY, Return_date, End_Maintenance) AS FLOAT)) AS avg_days
           FROM FactScannersMaintenance
           WHERE End_Maintenance IS NOT NULL AND Return_date IS NOT NULL"""
    )
    avg_duree = round(float(_avg_dur["avg_days"].values[0]), 1) if not _avg_dur.empty and pd.notna(_avg_dur["avg_days"].values[0]) else 0

    # Affichage KPIs
    col_k1, col_k2, col_k3 = st.columns(3)
    col_k1.metric("🔧 Nouvelles pannes", nb_new_pannes)
    col_k2.metric("✅ Réparations clôturées", nb_closed)
    col_k3.metric("⏱️ Durée moy. réparation", f"{avg_duree} j")

    st.markdown("---")

    tab_list, tab_edit_maint, tab_add = st.tabs(["Historique", "Modifier", "Déclarer"])

    with tab_list:
        col_m1, col_m2 = st.columns(2)
        search_maint_sn = col_m1.text_input("Rechercher par n° de série", key="maint_sn")
        search_maint_id = col_m2.text_input("Rechercher par n° de maintenance", key="maint_id_search")
        col_m3, col_m4 = st.columns(2)
        maint_date_from = col_m3.date_input("Return date début", value=None, key="maint_date_from")
        maint_date_to = col_m4.date_input("Return date fin", value=None, key="maint_date_to")

        only_open = st.checkbox("Afficher uniquement les maintenances ouvertes", value=False, key="maint_only_open")

        maint_query = """
            SELECT m.Maintenance_id, m.Serial_num, s.Mac_address,
                   m.Event_type, m.Panne_detected, m.Info_Maintenance,
                   m.Copie, m.Return_date, m.End_Maintenance
            FROM FactScannersMaintenance m
            JOIN DimScanners s ON m.Serial_num = s.Serial_num
            WHERE 1=1
        """
        if only_open:
            maint_query += " AND m.End_Maintenance IS NULL"
        if search_maint_id:
            maint_query += f" AND {sql_cast_text('m.Maintenance_id')} = '{search_maint_id}'"
        if search_maint_sn:
            maint_query += f" AND {sql_cast_text('m.Serial_num')} LIKE '%{search_maint_sn}%'"
        if maint_date_from:
            maint_query += f" AND m.Return_date >= '{maint_date_from}'"
        if maint_date_to:
            maint_query += f" AND m.Return_date <= '{maint_date_to}'"
        maint_query += " ORDER BY m.Return_date DESC"

        df = run_query(maint_query)
        st.dataframe(df.rename(columns={"Copie": "Copies"}), use_container_width=True, hide_index=True, key="df_maintenance_hist")
        st.caption(f"{len(df)} maintenance(s)")

        display_success("maint_close")

        open_repairs = df[df["End_Maintenance"].isna()]
        if not open_repairs.empty:
            st.subheader("Clôturer une réparation en cours")
            st.caption("Le scanner passe de 'atelier Procedo' (à réparer) → 'Procedo' (inactif).")
            with st.form("maint_close_repair"):
                maint_close_id = st.selectbox(
                    "Réparation à clôturer",
                    open_repairs["Maintenance_id"].tolist(),
                    format_func=lambda x: f"#{x} — SN {open_repairs.loc[open_repairs['Maintenance_id']==x, 'Serial_num'].values[0]}",
                    key="maint_close_repair_id"
                )
                end_date = st.date_input("Date fin réparation", value=date.today(), key="close_repair_date")

                st.divider()
                st.caption("Compléter la fiche avant clôture (facultatif) :")
                mc_panne = st.text_area("Description de la panne", placeholder="Ecran cassé, capteur défaillant...", key="maint_close_panne")
                mc_info = st.text_input("Info maintenance", placeholder="Pièce commandée, renvoyé fournisseur...", key="maint_close_info")
                mc_copie = st.text_input("Copies", value="", key="maint_close_copie")

                if st.form_submit_button("Clôturer la réparation", type="primary"):
                    sn_cloture = int(open_repairs.loc[open_repairs["Maintenance_id"] == maint_close_id, "Serial_num"].values[0])
                    _mc_updates = ["End_Maintenance = ?"]
                    _mc_params = [str(end_date)]
                    if mc_panne:
                        _existing_panne = open_repairs.loc[open_repairs["Maintenance_id"] == maint_close_id, "Panne_detected"].values[0]
                        if _existing_panne and str(_existing_panne).strip() and str(_existing_panne).strip() != "aucune":
                            _mc_updates.append("Panne_detected = ?")
                            _mc_params.append(f"{_existing_panne} | {mc_panne}")
                        else:
                            _mc_updates.append("Panne_detected = ?")
                            _mc_params.append(mc_panne)
                    if mc_info:
                        _existing_info = open_repairs.loc[open_repairs["Maintenance_id"] == maint_close_id, "Info_Maintenance"].values[0]
                        if _existing_info and str(_existing_info).strip() and str(_existing_info).strip() != "aucune":
                            _mc_updates.append("Info_Maintenance = ?")
                            _mc_params.append(f"{_existing_info} | {mc_info}")
                        else:
                            _mc_updates.append("Info_Maintenance = ?")
                            _mc_params.append(mc_info)
                    if mc_copie.strip():
                        if mc_copie.strip().isdigit():
                            _mc_updates.append("Copie = ?")
                            _mc_params.append(int(mc_copie.strip()))
                        else:
                            st.error("Copies doit être un nombre entier.")
                            st.stop()
                    _mc_params.append(maint_close_id)
                    run_execute(
                        f"UPDATE FactScannersMaintenance SET {', '.join(_mc_updates)} WHERE Maintenance_id = ?",
                        _mc_params,
                    )
                    run_execute(
                        "UPDATE FactMovementsHistory SET DateFin = ? WHERE Serial_num = ? AND DateFin IS NULL AND Action IN ('réparation/maintenance', 'stock')",
                        [str(end_date), sn_cloture],
                    )
                    run_execute(
                        "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action) VALUES (?,NULL,?,NULL,'stock')",
                        [sn_cloture, str(end_date)],
                    )
                    update_scanner_loc(sn_cloture, "Procedo")
                    show_success(f"✅ Réparation #{maint_close_id} clôturée. SN {sn_cloture} → Procedo (inactif).", "maint_close")

    with tab_edit_maint:
        st.subheader("Modifier ou compléter une maintenance")
        st.caption("Mettre à jour la description de panne, les infos de réparation ou le compteur copies.")

        search_maint_edit_sn = st.text_input("Rechercher par n° de série", key="maint_edit_sn")
        only_open_maint = st.checkbox("Afficher uniquement les maintenances ouvertes", value=True, key="maint_edit_only_open")

        if only_open_maint:
            all_maints = run_query("""
                SELECT m.Maintenance_id, m.Serial_num, m.Panne_detected, m.Info_Maintenance, m.Copie, m.Return_date, m.End_Maintenance
                FROM FactScannersMaintenance m
                WHERE m.End_Maintenance IS NULL
                ORDER BY m.Return_date DESC
            """)
        else:
            all_maints = run_query("""
                SELECT m.Maintenance_id, m.Serial_num, m.Panne_detected, m.Info_Maintenance, m.Copie, m.Return_date, m.End_Maintenance
                FROM FactScannersMaintenance m
                ORDER BY m.Return_date DESC
            """)

        if search_maint_edit_sn:
            all_maints = all_maints[all_maints["Serial_num"].astype(str).str.contains(search_maint_edit_sn)]

        if all_maints.empty:
            st.info("Aucune maintenance trouvée.")
        else:
            selected_maint = st.selectbox(
                "Maintenance à modifier",
                all_maints["Maintenance_id"].tolist(),
                format_func=lambda x: (
                    f"#{x} — SN {all_maints.loc[all_maints['Maintenance_id']==x, 'Serial_num'].values[0]} "
                    f"— {all_maints.loc[all_maints['Maintenance_id']==x, 'Panne_detected'].values[0]}"
                    f"{' (clôturée)' if pd.notna(all_maints.loc[all_maints['Maintenance_id']==x, 'End_Maintenance'].values[0]) else ''}"
                ),
                key="edit_maint_select"
            )
            maint_row = all_maints[all_maints["Maintenance_id"] == selected_maint].iloc[0]

            with st.form(f"edit_maintenance_form_{selected_maint}"):
                new_panne = st.text_area(
                    "Description de la panne",
                    value=maint_row["Panne_detected"] if maint_row["Panne_detected"] else "",
                    key=f"edit_maint_panne_{selected_maint}"
                )
                new_info = st.text_input(
                    "Info maintenance",
                    value=maint_row["Info_Maintenance"] if maint_row["Info_Maintenance"] else "",
                    key=f"edit_maint_info_{selected_maint}"
                )
                has_copie = pd.notna(maint_row["Copie"])
                current_copie = int(maint_row["Copie"]) if has_copie else None
                new_copie = st.text_input(
                    "Copies",
                    value=str(current_copie) if current_copie is not None else "",
                    key=f"edit_maint_copie_{selected_maint}"
                )

                if st.form_submit_button("Sauvegarder les modifications"):
                    copie_val = None
                    if new_copie.strip() != "":
                        if new_copie.strip().isdigit():
                            copie_val = int(new_copie.strip())
                        else:
                            st.error("Copies doit être un nombre entier.")
                            st.stop()
                    run_execute(
                        "UPDATE FactScannersMaintenance SET Panne_detected = ?, Info_Maintenance = ?, Copie = ? WHERE Maintenance_id = ?",
                        [new_panne or "aucune", new_info or "aucune", copie_val, selected_maint],
                    )
                    show_success(f"✅ Maintenance #{selected_maint} mise à jour avec succès.", "maint_edit")

            display_success("maint_edit")

    with tab_add:
        st.subheader("Déclarer une panne / maintenance")

        search_decl_sn = st.text_input("Rechercher par n° de série", key="maint_decl_sn")
        scanners = get_all_scanners()
        # Exclure fin de vie, retour garantie et à réparer (déjà en maintenance ouverte → utiliser Modifier)
        scanners = scanners[~scanners["Statut"].isin(["fin de vie", "retour garantie", "à réparer"])]
        if search_decl_sn:
            scanners = scanners[scanners["Serial_num"].astype(str).str.contains(search_decl_sn)]

        if scanners.empty:
            st.warning("Aucun scanner trouvé.")
        else:
            sn = st.selectbox(
                "Scanner",
                scanners["Serial_num"].tolist(),
                format_func=lambda x: f"SN {x} — {scanners.loc[scanners['Serial_num']==x, 'Localisation'].values[0]} ({scanners.loc[scanners['Serial_num']==x, 'Statut'].values[0]})",
                key="maint_decl_sn_select"
            )

            # Pré-remplir Info maintenance avec "Retour {localité}" si scanner actif
            _decl_statut = scanners.loc[scanners["Serial_num"] == sn, "Statut"].values[0]
            if _decl_statut == "actif":
                _decl_ag = run_query(
                    """SELECT k.Localite, k.Kantoor_Bureau FROM FactMovementsHistory f
                       JOIN DimKantoren k ON f.Kantoor_id = k.Kantoor_id
                       WHERE f.Serial_num = ? AND f.Action = 'installé' AND f.DateFin IS NULL
                       ORDER BY f.DateDebut DESC""",
                    [sn],
                )
                if not _decl_ag.empty:
                    _decl_loc = _decl_ag.iloc[0]["Localite"] or _decl_ag.iloc[0]["Kantoor_Bureau"]
                    if "maint_decl_prev_sn" not in st.session_state or st.session_state["maint_decl_prev_sn"] != sn:
                        st.session_state["maint_decl_info"] = f"Retour {_decl_loc}"
                        st.session_state["maint_decl_prev_sn"] = sn
            else:
                if "maint_decl_prev_sn" not in st.session_state or st.session_state["maint_decl_prev_sn"] != sn:
                    st.session_state["maint_decl_info"] = ""
                    st.session_state["maint_decl_prev_sn"] = sn

            with st.form("add_maintenance"):
                event = st.selectbox("Type", EVENT_TYPES)
                dest_decl = st.selectbox("Destination du scanner", ["Maca Express", "atelier Procedo"], key="maint_decl_dest")
                st.caption(f"Statut associé : **{get_statut_for_loc(dest_decl, transit_retour=True)}**")
                panne = st.text_area("Description de la panne", placeholder="Ecran cassé, capteur défaillant...")
                info = st.text_input("Info maintenance", key="maint_decl_info")
                copie_str = st.text_input("Copies", value="", key="maint_decl_copie")
                ret_date = st.date_input("Date retour/signalement", value=date.today())

                if st.form_submit_button("Enregistrer"):
                    copie_val = None
                    if copie_str.strip() != "":
                        if copie_str.strip().isdigit():
                            copie_val = int(copie_str.strip())
                        else:
                            st.error("Copies doit être un nombre entier.")
                            st.stop()

                    current_statut = scanners.loc[scanners["Serial_num"] == sn, "Statut"].values[0]
                    info_final = info
                    via_maca_decl = 0 if dest_decl == "atelier Procedo" else 1

                    if current_statut == "actif":
                        # Scanner en agence → clore 'installé' + récupérer localité
                        run_execute(
                            "UPDATE FactMovementsHistory SET DateFin = ?, Via_Maca_Fin = ? WHERE Serial_num = ? AND DateFin IS NULL AND Action = 'installé'",
                            [str(ret_date), via_maca_decl, sn],
                        )
                        ag_info = run_query(
                            """SELECT k.Localite, k.Kantoor_Bureau FROM FactMovementsHistory f
                               JOIN DimKantoren k ON f.Kantoor_id = k.Kantoor_id
                               WHERE f.Serial_num = ? AND f.Action = 'installé'
                               ORDER BY f.DateFin DESC""",
                            [sn],
                        )
                        if not ag_info.empty:
                            localite_ag = ag_info.iloc[0]["Localite"] or ag_info.iloc[0]["Kantoor_Bureau"]
                            info_final = info if info else f"Retour {localite_ag}"
                        run_execute(
                            "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action, Via_Maca) VALUES (?,NULL,?,NULL,'réparation/maintenance', ?)",
                            [sn, str(ret_date), via_maca_decl],
                        )

                    elif current_statut in ("inactif", "à livrer"):
                        # Scanner en stock → clore 'stock'
                        run_execute(
                            "UPDATE FactMovementsHistory SET DateFin = ? WHERE Serial_num = ? AND DateFin IS NULL AND Action = 'stock'",
                            [str(ret_date), sn],
                        )
                        run_execute(
                            "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action, Via_Maca) VALUES (?,NULL,?,NULL,'réparation/maintenance', ?)",
                            [sn, str(ret_date), via_maca_decl],
                        )

                    elif current_statut == "à rechercher":
                        # Scanner perdu retrouvé → clore mouvement ouvert + nouveau 'réparation/maintenance'
                        run_execute(
                            "UPDATE FactMovementsHistory SET DateFin = ? WHERE Serial_num = ? AND DateFin IS NULL",
                            [str(ret_date), sn],
                        )
                        run_execute(
                            "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action, Via_Maca) VALUES (?,NULL,?,NULL,'réparation/maintenance', ?)",
                            [sn, str(ret_date), via_maca_decl],
                        )
                        info_final = info if info else "Scanner retrouvé"

                    # Dans tous les cas : créer la fiche maintenance + passer à la destination choisie
                    update_scanner_loc(sn, dest_decl, transit_retour=True)
                    run_execute(
                        """INSERT INTO FactScannersMaintenance
                           (Serial_num, Event_type, Panne_detected, Info_Maintenance, Copie, Return_date, End_Maintenance)
                           VALUES (?,?,?,?,?,?,NULL)""",
                        [sn, event, panne or "aucune", info_final or "aucune", copie_val, str(ret_date)],
                    )
                    show_success(f"✅ Panne déclarée pour SN {sn} → {dest_decl} ({get_statut_for_loc(dest_decl, transit_retour=True)}).", "maint_add")

        display_success("maint_add")

    # ── Supprimer une maintenance ──

# ═════════════════════════════════════════════════════════════════════════════
#  ACTIONS FRÉQUENTES
# ═════════════════════════════════════════════════════════════════════════════

elif page == "Actions fréquentes":

    # Nettoyage AVANT les widgets : garder une seule catégorie active
    if "act_last_cat" not in st.session_state:
        st.session_state["act_last_cat"] = None

    _cat_keys = {"agences": "cat_agences", "scanners": "cat_scanners", "stock": "cat_stock"}
    for cat_name, cat_key in _cat_keys.items():
        if cat_key in st.session_state and st.session_state[cat_key] is not None and cat_name != st.session_state.get("act_last_cat"):
            for other_name, other_key in _cat_keys.items():
                if other_name != cat_name and other_key in st.session_state:
                    st.session_state[other_key] = None
            st.session_state["act_last_cat"] = cat_name
            break

    _title_style = (
        'style="color:#1B2A4A; font-weight:700; font-size:1.5rem; '
        'margin-bottom:12px; padding:4px 0; font-family:Poppins,sans-serif; '
        'border-bottom:2px solid #00B4D8;"'
    )

    col_cat1, col_cat2, col_cat3 = st.columns(3)

    with col_cat1:
        st.markdown(f'<div {_title_style}>📍 Agences</div>', unsafe_allow_html=True)
        act_agences = st.radio(
            "Agences", [
                "Ouvrir une agence",
                "Clôturer une agence",
                "Déménagement d'agence",
            ],
            index=None, label_visibility="collapsed", key="cat_agences",
        )

    with col_cat2:
        st.markdown(f'<div {_title_style}>🔄 Scanners en agence</div>', unsafe_allow_html=True)
        act_scanners = st.radio(
            "Scanners", [
                "Scanner défectueux (remplacement)",
                "Ajouter scanner dans agence existante",
                "Retrait scanner sans remplacement",
                "Transfert scanner (agence → agence)",
            ],
            index=None, label_visibility="collapsed", key="cat_scanners",
        )

    with col_cat3:
        st.markdown(f'<div {_title_style}>🔧 Stock & Maintenance</div>', unsafe_allow_html=True)
        act_stock = st.radio(
            "Stock", [
                "Mouvement stock interne (Procedo / Maca)",
                "Clôturer / modifier une réparation",
                "Sortie de parc (détruit / retour garantie / perdu)",
            ],
            index=None, label_visibility="collapsed", key="cat_stock",
        )

    # Déterminer l'action sélectionnée
    action_choice = act_agences or act_scanners or act_stock

    if action_choice is None:
        st.info("Sélectionnez une action ci-dessus pour commencer.")
    else:
        st.divider()

    # ═══ SCANNER DÉFECTUEUX ═════════════════════════════════════════════════
    if action_choice == "Scanner défectueux (remplacement)":
        st.subheader("Scanner défectueux — Remplacement")
        st.info("Le scanner défectueux est retiré, un scanner de remplacement est installé.")

        agencies = get_open_agencies()
        filtered_ag_1 = filter_agencies(agencies, "act1")

        if not filtered_ag_1.empty:
            kid_list = ",".join(str(k) for k in filtered_ag_1["Kantoor_id"].tolist())
            scanners_in_filtered = run_query(
                f"""SELECT DISTINCT f.Serial_num, s.Mac_address, s.Localisation, s.Statut, f.Kantoor_id, k.Kantoor_Bureau, k.Localite
                    FROM FactMovementsHistory f
                    JOIN DimScanners s ON f.Serial_num = s.Serial_num
                    JOIN DimKantoren k ON f.Kantoor_id = k.Kantoor_id
                    WHERE f.Kantoor_id IN ({kid_list}) AND f.DateFin IS NULL AND f.Action = 'installé'
                    ORDER BY f.Serial_num"""
            )
        else:
            scanners_in_filtered = pd.DataFrame()

        with st.form("defect_scanner"):
            if scanners_in_filtered.empty:
                st.warning("Aucun scanner trouvé dans les agences filtrées.")
                sn_defect = None
                ag_dest = None
            else:
                sn_defect = st.selectbox(
                    "Scanner défectueux",
                    scanners_in_filtered["Serial_num"].tolist(),
                    format_func=lambda x: (
                        f"SN {x} — {scanners_in_filtered.loc[scanners_in_filtered['Serial_num']==x, 'Kantoor_Bureau'].values[0]} "
                        f"({scanners_in_filtered.loc[scanners_in_filtered['Serial_num']==x, 'Localite'].values[0]})"
                    ),
                    key="defect_sn"
                )
                ag_dest_val = scanners_in_filtered.loc[scanners_in_filtered["Serial_num"] == sn_defect, "Kantoor_id"].values[0]
                ag_dest = int(ag_dest_val)
                ag_name = scanners_in_filtered.loc[scanners_in_filtered["Serial_num"] == sn_defect, "Kantoor_Bureau"].values[0]
                st.caption(f"Agence : **{ag_name}** (ID {ag_dest}) — le scanner de remplacement sera installé dans cette agence.")

                nb_scanners_ag = len(scanners_in_filtered[scanners_in_filtered["Kantoor_id"] == ag_dest])
                if nb_scanners_ag >= 2:
                    st.warning(
                        f"⚠️ Cette agence possède **{nb_scanners_ag} scanners**. "
                        f"Attendez la confirmation de Maca Express pour identifier le scanner concerné "
                        f"avant d'effectuer cette action."
                    )

            dest_defect = st.selectbox(
                "Destination du scanner",
                ["Maca Express", "atelier Procedo"],
                key="defect_dest"
            )
            st.caption(f"Statut associé : **{get_statut_for_loc(dest_defect, transit_retour=True)}**")

            st.divider()
            st.markdown("**Fiche maintenance**")
            panne_defect = st.text_input("Description de la panne", value="Voir support@procedo.be", key="defect_panne")
            info_defect = st.text_input("Info maintenance", value="", key="defect_info", placeholder="Retour {localité} — rempli automatiquement si vide")

            st.divider()

            scanners_stock = get_stock_scanners()
            if scanners_stock.empty:
                st.warning("Aucun scanner de remplacement disponible en stock.")
                sn_replace = None
            else:
                sn_replace = st.selectbox(
                    "Scanner de remplacement",
                    scanners_stock["Serial_num"].tolist(),
                    format_func=lambda x: scanner_label(scanners_stock, x),
                    key="replace_sn"
                )

            action_date = st.date_input("Date", value=date.today(), key="defect_date")

            if st.form_submit_button("Exécuter le remplacement", type="primary"):
                if sn_defect is None or sn_replace is None or ag_dest is None:
                    st.error("Tous les champs sont obligatoires.")
                else:
                    # Récupérer la localité de l'agence pour Info_Maintenance
                    ag_loc_info = scanners_in_filtered.loc[scanners_in_filtered["Serial_num"] == sn_defect, "Localite"].values[0]
                    info_final = info_defect if info_defect.strip() else f"Retour {ag_loc_info}"

                    # Via_Maca_Fin récupération (défectueux) : 0 si atelier Procedo, 1 si Maca
                    via_maca_recup = 0 if dest_defect == "atelier Procedo" else 1
                    # Via_Maca livraison (remplacement) : 0 si le scanner vient de Procedo, 1 si de Maca
                    _repl_loc = scanners_stock.loc[scanners_stock["Serial_num"] == sn_replace, "Localisation"].values[0]
                    via_maca_livr = 0 if _repl_loc == "Procedo" else 1

                    run_execute(
                        "UPDATE FactMovementsHistory SET DateFin = ?, Action = 'panne détectée', Via_Maca_Fin = ? WHERE Serial_num = ? AND DateFin IS NULL AND Action = 'installé'",
                        [str(action_date), via_maca_recup, sn_defect],
                    )
                    update_scanner_loc(sn_defect, dest_defect, transit_retour=True)
                    run_execute(
                        """INSERT INTO FactScannersMaintenance
                           (Serial_num, Event_type, Panne_detected, Info_Maintenance, Copie, Return_date, End_Maintenance)
                           VALUES (?,'Failure',?,?,NULL,?,NULL)""",
                        [sn_defect, panne_defect or "Voir support@procedo.be", info_final, str(action_date)],
                    )
                    run_execute(
                        "UPDATE FactMovementsHistory SET DateFin = ? WHERE Serial_num = ? AND DateFin IS NULL AND Action IN ('stock', 'réparation/maintenance')",
                        [str(action_date), sn_replace],
                    )
                    run_execute(
                        "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action, Via_Maca) VALUES (?,?,?,NULL,'installé',?)",
                        [sn_replace, ag_dest, str(action_date), via_maca_livr],
                    )
                    update_scanner_loc(sn_replace, "agence DVV")
                    show_success(
                        f"✅ SN {sn_defect} retiré → {dest_defect}. "
                        f"SN {sn_replace} installé en remplacement.",
                        "act1"
                    )

        display_success("act1")

    # ═══ 2. CLÔTURER UNE AGENCE ════════════════════════════════════════════
    elif action_choice == "Clôturer une agence":
        st.subheader("Clôturer une agence")
        st.info("Tous les scanners seront rapatriés vers la destination choisie + ligne maintenance créée.")
        st.caption("💡 Si un scanner doit être repris par une autre agence, utilisez d'abord **Transfert scanner (agence → agence)** pour le transférer, puis revenez ici pour clôturer l'agence.")

        agencies = get_open_agencies()
        filtered_ag_2 = filter_agencies(agencies, "act2")

        with st.form("quick_close_agency"):
            if filtered_ag_2.empty:
                st.warning("Aucune agence trouvée.")
                ag_close = None
            else:
                ag_close = st.selectbox("Agence à fermer", filtered_ag_2["Kantoor_id"].tolist(),
                                        format_func=lambda x: agency_label(filtered_ag_2[filtered_ag_2["Kantoor_id"] == x].iloc[0]),
                                        key="quick_close_ag")
            close_date = st.date_input("Date de fermeture", value=date.today(), key="quick_close_date")
            close_dest_q = st.selectbox(
                "Destination du scanner",
                ["Maca Express", "atelier Procedo"],
                key="quick_close_dest"
            )

            if st.form_submit_button("Fermer l'agence", type="primary"):
                if ag_close is None:
                    st.error("Aucune agence sélectionnée.")
                else:
                    ag_info = run_query("SELECT Kantoor_Bureau, Localite FROM DimKantoren WHERE Kantoor_id = ?", [ag_close]).iloc[0]
                    localite_name = ag_info["Localite"] or ag_info["Kantoor_Bureau"]
                    _qclose_via_maca = 0 if close_dest_q == "atelier Procedo" else 1
                    run_execute(
                        "UPDATE DimKantoren SET Status = 'closed', Fermeture = ? WHERE Kantoor_id = ?",
                        [str(close_date), ag_close],
                    )
                    scanners_in = run_query(
                        "SELECT Serial_num FROM FactMovementsHistory WHERE Kantoor_id = ? AND DateFin IS NULL AND Action = 'installé'",
                        [ag_close],
                    )
                    for _, r in scanners_in.iterrows():
                        sn = int(r["Serial_num"])
                        run_execute(
                            "UPDATE FactMovementsHistory SET DateFin = ?, Action = 'agence fermée', Via_Maca_Fin = ? WHERE Serial_num = ? AND Kantoor_id = ? AND DateFin IS NULL AND Action = 'installé'",
                            [str(close_date), _qclose_via_maca, sn, ag_close],
                        )
                        run_execute(
                            "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action, Via_Maca) VALUES (?,NULL,?,NULL,'réparation/maintenance',?)",
                            [sn, str(close_date), _qclose_via_maca],
                        )
                        if close_dest_q == "atelier Procedo":
                            update_scanner_loc(sn, "atelier Procedo")
                        else:
                            update_scanner_loc(sn, "Maca Express", transit_retour=True)
                        run_execute(
                            """INSERT INTO FactScannersMaintenance
                               (Serial_num, Event_type, Panne_detected, Info_Maintenance, Copie, Return_date, End_Maintenance)
                               VALUES (?,'Maintenance','aucune',?,NULL,?,NULL)""",
                            [sn, f"Fermeture agence {localite_name}", str(close_date)],
                        )
                    _qdest_label = "atelier Procedo (à réparer)" if close_dest_q == "atelier Procedo" else "Maca Express (en transit retour)"
                    show_success(f"✅ Agence {localite_name} fermée. {len(scanners_in)} scanner(s) → {_qdest_label}.", "act2")

            display_success("act2")

    # ═══ 3. OUVRIR UNE AGENCE ══════════════════════════════════════════════
    elif action_choice == "Ouvrir une agence":
        st.subheader("Ouvrir une agence")
        st.info("→ Allez dans **Agences** > **Ouvrir une agence** pour créer l'agence avec toutes les infos + scanner associé.")

    # ═══ 4. AJOUTER UN SCANNER DANS UNE AGENCE EXISTANTE ═══════════════════
    elif action_choice == "Ajouter scanner dans agence existante":
        st.subheader("Ajouter un scanner dans une agence existante")
        st.info("Associe un scanner du stock à une agence ouverte.")

        agencies = get_open_agencies()
        filtered_ag_4 = filter_agencies(agencies, "act4")

        with st.form("add_scanner_to_agency"):
            scanners_stock = get_stock_scanners()

            if scanners_stock.empty:
                st.warning("Aucun scanner disponible en stock.")
                sn_add = None
            else:
                sn_add = st.selectbox(
                    "Scanner à installer",
                    scanners_stock["Serial_num"].tolist(),
                    format_func=lambda x: scanner_label(scanners_stock, x),
                    key="add_ag_sn"
                )

            if filtered_ag_4.empty:
                st.warning("Aucune agence trouvée.")
                ag_add = None
            else:
                ag_add = st.selectbox(
                    "Agence destination",
                    filtered_ag_4["Kantoor_id"].tolist(),
                    format_func=lambda x: agency_label(filtered_ag_4[filtered_ag_4["Kantoor_id"] == x].iloc[0]),
                    key="add_ag_dest"
                )

            add_date = st.date_input("Date d'installation", value=date.today(), key="add_ag_date")

            if st.form_submit_button("Installer le scanner", type="primary"):
                if sn_add is None or ag_add is None:
                    st.error("Tous les champs sont obligatoires.")
                else:
                    # Via_Maca = 0 si le scanner vient de Procedo (livré par Procedo, pas Maca)
                    _add_loc = scanners_stock.loc[scanners_stock["Serial_num"] == sn_add, "Localisation"].values[0]
                    _add_via_maca = 0 if _add_loc == "Procedo" else 1
                    run_execute(
                        "UPDATE FactMovementsHistory SET DateFin = ? WHERE Serial_num = ? AND DateFin IS NULL AND Action IN ('stock', 'réparation/maintenance')",
                        [str(add_date), sn_add],
                    )
                    run_execute(
                        "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action, Via_Maca) VALUES (?,?,?,NULL,'installé',?)",
                        [sn_add, ag_add, str(add_date), _add_via_maca],
                    )
                    update_scanner_loc(sn_add, "agence DVV")
                    show_success(f"✅ SN {sn_add} installé en agence !", "act4")

        display_success("act4")

    # ═══ 5. MOUVEMENT STOCK INTERNE ═════════════════════════════════════════
    elif action_choice == "Mouvement stock interne (Procedo / Maca)":
        st.subheader("Mouvement stock interne (Procedo / Maca)")
        st.info("Déplacement entre entrepôts : Procedo ↔ Maca Express ↔ Atelier Procedo")

        mvt_type = st.radio(
            "Type de mouvement",
            ["Procedo → Maca Express (prêt à livrer)", "Maca Express → Atelier Procedo (à réparer)"],
            key="stock_mvt_type"
        )

        if mvt_type.startswith("Procedo"):
            source_statut = "inactif"
            dest_loc = "Maca Express"
            scanners_source = run_query(
                f"SELECT Serial_num, Mac_address, Localisation, Statut FROM DimScanners WHERE Statut = '{source_statut}' ORDER BY Serial_num"
            )
        else:
            dest_loc = "atelier Procedo"
            scanners_source = run_query(
                "SELECT Serial_num, Mac_address, Localisation, Statut FROM DimScanners WHERE Localisation = 'Maca Express' AND Statut IN ('à livrer', 'en transit retour') ORDER BY Serial_num"
            )

        with st.form("stock_movement"):
            if scanners_source.empty:
                st.warning("Aucun scanner disponible pour ce mouvement.")
                sn_stock = None
            else:
                if mvt_type.startswith("Maca"):
                    sn_fmt = lambda x: f"SN {x} — {scanners_source.loc[scanners_source['Serial_num']==x, 'Statut'].values[0]}"
                else:
                    sn_fmt = lambda x: scanner_label(scanners_source, x)
                sn_stock = st.selectbox(
                    "Scanner",
                    scanners_source["Serial_num"].tolist(),
                    format_func=sn_fmt,
                    key="stock_sn"
                )
            st.caption(f"Destination : **{dest_loc}** → Statut : **{get_statut_for_loc(dest_loc)}**")
            stock_date = st.date_input("Date", value=date.today(), key="stock_date")

            if st.form_submit_button("Déplacer", type="primary"):
                if sn_stock is None:
                    st.error("Aucun scanner sélectionné.")
                else:
                    update_scanner_loc(sn_stock, dest_loc)
                    show_success(f"✅ SN {sn_stock} déplacé vers {dest_loc}.", "act5")

        display_success("act5")

    # ═══ 6. CLÔTURER / MODIFIER UNE RÉPARATION ═════════════════════════════
    elif action_choice == "Clôturer / modifier une réparation":
        st.subheader("Clôturer / Modifier une réparation")

        search_repair_sn = st.text_input("Rechercher par n° de série", key="act6_search_sn")

        open_repairs = run_query("""
            SELECT m.Maintenance_id, m.Serial_num, m.Panne_detected, m.Info_Maintenance,
                   m.Copie, m.Return_date, s.Localisation
            FROM FactScannersMaintenance m
            JOIN DimScanners s ON m.Serial_num = s.Serial_num
            WHERE m.End_Maintenance IS NULL
            ORDER BY m.Return_date DESC
        """)

        if search_repair_sn:
            open_repairs = open_repairs[open_repairs["Serial_num"].astype(str).str.contains(search_repair_sn)]

        if open_repairs.empty:
            st.info("Aucune réparation en cours" + (f" pour '{search_repair_sn}'." if search_repair_sn else "."))
        else:
            st.dataframe(open_repairs.rename(columns={"Copie": "Copies"}), use_container_width=True, hide_index=True, key="df_maintenance_modifier")

            st.markdown("**Clôturer une réparation**")
            st.caption("Le scanner passe de 'atelier Procedo' (à réparer) → 'Procedo' (inactif).")
            with st.form("close_repair"):
                repair_close_id = st.selectbox(
                    "Réparation à clôturer",
                    open_repairs["Maintenance_id"].tolist(),
                    format_func=lambda x: f"#{x} — SN {open_repairs.loc[open_repairs['Maintenance_id']==x, 'Serial_num'].values[0]}",
                    key="close_repair_id"
                )
                close_repair_date = st.date_input("Date fin réparation", value=date.today(), key="close_repair_date2")

                st.divider()
                st.caption("Compléter la fiche avant clôture (facultatif) :")
                close_panne = st.text_area("Description de la réparation", placeholder="Ecran remplacé, capteur recalibré...", key="close_repair_panne")
                close_info = st.text_input("Info maintenance", placeholder="Pièce commandée, renvoyé fournisseur...", key="close_repair_info")
                close_copie = st.text_input("Copies", value="", key="close_repair_copie")

                if st.form_submit_button("Clôturer la réparation", type="primary"):
                    sn_repair = int(open_repairs.loc[open_repairs["Maintenance_id"] == repair_close_id, "Serial_num"].values[0])
                    # Mettre à jour les champs modifiés + clôturer
                    _close_updates = ["End_Maintenance = ?"]
                    _close_params = [str(close_repair_date)]
                    if close_panne:
                        # Ajouter à la description existante (concaténer)
                        _existing_panne = open_repairs.loc[open_repairs["Maintenance_id"] == repair_close_id, "Panne_detected"].values[0]
                        if _existing_panne and str(_existing_panne).strip() and str(_existing_panne).strip() != "aucune":
                            _close_updates.append("Panne_detected = ?")
                            _close_params.append(f"{_existing_panne} | {close_panne}")
                        else:
                            _close_updates.append("Panne_detected = ?")
                            _close_params.append(close_panne)
                    if close_info:
                        # Ajouter à l'info existante (concaténer)
                        _existing_info = open_repairs.loc[open_repairs["Maintenance_id"] == repair_close_id, "Info_Maintenance"].values[0]
                        if _existing_info and str(_existing_info).strip() and str(_existing_info).strip() != "aucune":
                            _close_updates.append("Info_Maintenance = ?")
                            _close_params.append(f"{_existing_info} | {close_info}")
                        else:
                            _close_updates.append("Info_Maintenance = ?")
                            _close_params.append(close_info)
                    if close_copie.strip():
                        # Copie : remplacer la valeur
                        if close_copie.strip().isdigit():
                            _close_updates.append("Copie = ?")
                            _close_params.append(int(close_copie.strip()))
                        else:
                            st.error("Copies doit être un nombre entier.")
                            st.stop()
                    _close_params.append(repair_close_id)
                    run_execute(
                        f"UPDATE FactScannersMaintenance SET {', '.join(_close_updates)} WHERE Maintenance_id = ?",
                        _close_params,
                    )
                    run_execute(
                        "UPDATE FactMovementsHistory SET DateFin = ? WHERE Serial_num = ? AND DateFin IS NULL AND Action IN ('réparation/maintenance', 'stock')",
                        [str(close_repair_date), sn_repair],
                    )
                    run_execute(
                        "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action) VALUES (?,NULL,?,NULL,'stock')",
                        [sn_repair, str(close_repair_date)],
                    )
                    update_scanner_loc(sn_repair, "Procedo")
                    show_success(f"✅ Réparation #{repair_close_id} clôturée. SN {sn_repair} → Procedo (inactif).", "act6_close")

            display_success("act6_close")

            st.divider()

            st.markdown("**Modifier une réparation**")
            repair_id = st.selectbox(
                "Réparation à modifier",
                open_repairs["Maintenance_id"].tolist(),
                format_func=lambda x: (
                    f"#{x} — SN {open_repairs.loc[open_repairs['Maintenance_id']==x, 'Serial_num'].values[0]} "
                    f"— {open_repairs.loc[open_repairs['Maintenance_id']==x, 'Panne_detected'].values[0]}"
                ),
                key="edit_repair_id"
            )
            repair_row = open_repairs[open_repairs["Maintenance_id"] == repair_id].iloc[0]

            with st.form(f"edit_repair_{repair_id}"):
                new_panne = st.text_area(
                    "Description de la panne",
                    value=repair_row["Panne_detected"] if repair_row["Panne_detected"] else "",
                    key=f"edit_repair_panne_{repair_id}"
                )
                new_info = st.text_input(
                    "Info maintenance",
                    value=repair_row["Info_Maintenance"] if repair_row["Info_Maintenance"] else "",
                    key=f"edit_repair_info_{repair_id}"
                )
                has_copie = pd.notna(repair_row["Copie"])
                current_copie = int(repair_row["Copie"]) if has_copie else None
                new_copie = st.text_input(
                    "Copies",
                    value=str(current_copie) if current_copie is not None else "",
                    key=f"edit_repair_copie_{repair_id}"
                )

                if st.form_submit_button("Sauvegarder les modifications"):
                    copie_val = None
                    if new_copie.strip() != "":
                        if new_copie.strip().isdigit():
                            copie_val = int(new_copie.strip())
                        else:
                            st.error("Copies doit être un nombre entier.")
                            st.stop()
                    run_execute(
                        "UPDATE FactScannersMaintenance SET Panne_detected = ?, Info_Maintenance = ?, Copie = ? WHERE Maintenance_id = ?",
                        [new_panne or "aucune", new_info or "aucune", copie_val, repair_id],
                    )
                    show_success(f"✅ Réparation #{repair_id} mise à jour avec succès.", "act6_edit")

            display_success("act6_edit")

    # ═══ 7. SORTIE DE PARC (détruit / fournisseur / perdu) ═════════════════
    elif action_choice == "Sortie de parc (détruit / retour garantie / perdu)":
        st.subheader("Sortie de parc — Détruit / Retour garantie / Perdu")
        st.info("Change la localisation d'un scanner vers 'détruit', 'fournisseur' (retour garantie) ou 'perdu'. Le scanner ne doit pas être en agence.")
        st.warning("⚠️ Un scanner marqué **détruit** ou **retour garantie** ne pourra plus être remis en service. Cette action est irréversible.")

        search_sortie_sn = st.text_input("Rechercher par n° de série", key="sortie_search_sn")

        scanners_sortie = run_query(
            "SELECT Serial_num, Mac_address, Localisation, Statut FROM DimScanners "
            "WHERE Localisation NOT IN ('agence DVV', 'détruit', 'fournisseur') "
            "AND Statut NOT IN ('retour garantie') ORDER BY Serial_num"
        )
        if search_sortie_sn:
            scanners_sortie = scanners_sortie[scanners_sortie["Serial_num"].astype(str).str.contains(search_sortie_sn)]

        if scanners_sortie.empty:
            st.warning("Aucun scanner éligible trouvé.")
        else:
            with st.form("sortie_parc"):
                sn_sortie = st.selectbox(
                    "Scanner",
                    scanners_sortie["Serial_num"].tolist(),
                    format_func=lambda x: scanner_label(scanners_sortie, x),
                    key="sortie_sn"
                )

                DEST_SORTIE = {"détruit": "fin de vie", "retour garantie": "retour garantie", "perdu": "à rechercher"}
                dest_sortie = st.selectbox("Nouvelle localisation", list(DEST_SORTIE.keys()), key="sortie_dest")
                st.caption(f"Statut associé : **{DEST_SORTIE[dest_sortie]}**")
                sortie_date = st.date_input("Date", value=date.today(), key="sortie_date")

                if st.form_submit_button("Exécuter la sortie de parc", type="primary"):
                    current_info = run_query(
                        "SELECT Localisation, Statut FROM DimScanners WHERE Serial_num = ?", [sn_sortie]
                    )
                    current_loc_val = current_info["Localisation"].values[0] if not current_info.empty else ""
                    current_statut_val = current_info["Statut"].values[0] if not current_info.empty else ""

                    # Clôturer la maintenance ouverte si détruit ou retour garantie (pas perdu)
                    if dest_sortie != "perdu" and current_statut_val in ("en transit retour", "à réparer"):
                        if dest_sortie == "retour garantie":
                            info_maint = "Retour garantie"
                        else:
                            info_maint = "Destruction"
                        existing_info_df = run_query(
                            "SELECT Info_Maintenance FROM FactScannersMaintenance "
                            "WHERE Serial_num = ? AND End_Maintenance IS NULL",
                            [sn_sortie],
                        )
                        existing_info = ""
                        if not existing_info_df.empty:
                            val = existing_info_df["Info_Maintenance"].values[0]
                            existing_info = "" if val is None or str(val).strip() == "" else str(val).strip()
                        new_info = f"{existing_info} | {info_maint}" if existing_info and existing_info != "aucune" else info_maint
                        run_execute(
                            """UPDATE FactScannersMaintenance
                               SET Info_Maintenance = ?, End_Maintenance = ?
                               WHERE Serial_num = ? AND End_Maintenance IS NULL""",
                            [new_info, str(sortie_date), sn_sortie],
                        )

                    # Fermer le mouvement ouvert (sauf perdu → la ligne reste ouverte)
                    if dest_sortie != "perdu":
                        run_execute(
                            "UPDATE FactMovementsHistory SET DateFin = ? WHERE Serial_num = ? AND DateFin IS NULL",
                            [str(sortie_date), sn_sortie],
                        )

                    # Localisation DB : 'retour garantie' → stocké comme 'fournisseur'
                    loc_db = "fournisseur" if dest_sortie == "retour garantie" else dest_sortie
                    update_scanner_loc(sn_sortie, loc_db)
                    show_success(f"✅ SN {sn_sortie} → {dest_sortie} ({DEST_SORTIE[dest_sortie]}).", "act7")

        display_success("act7")

    # ═══ 8. DÉMÉNAGEMENT D'AGENCE ══════════════════════════════════════════
    elif action_choice == "Déménagement d'agence":
        st.subheader("Déménagement d'agence")
        st.info("L'ancienne agence est clôturée et une nouvelle est créée à la nouvelle adresse. Les scanners sont automatiquement transférés.")

        open_agencies = get_open_agencies()
        filtered_ag_8 = filter_agencies(open_agencies, "act8")

        if filtered_ag_8.empty:
            st.warning("Aucune agence trouvée.")
        else:
            ag_demenage = st.selectbox(
                "Agence qui déménage",
                filtered_ag_8["Kantoor_id"].tolist(),
                format_func=lambda x: agency_label(filtered_ag_8[filtered_ag_8["Kantoor_id"] == x].iloc[0]),
                key="demenage_ag"
            )

            old_ag = run_query("SELECT * FROM DimKantoren WHERE Kantoor_id = ?", [ag_demenage]).iloc[0]
            st.caption(f"Adresse actuelle : {old_ag['Adresse']}, {old_ag['C_Pos']} {old_ag['Localite']}")

            scanners_in = run_query(
                "SELECT f.Serial_num FROM FactMovementsHistory f WHERE f.Kantoor_id = ? AND f.DateFin IS NULL AND f.Action = 'installé'",
                [ag_demenage],
            )
            if not scanners_in.empty:
                st.caption(f"Scanner(s) à transférer : {', '.join(str(s) for s in scanners_in['Serial_num'].tolist())}")
            else:
                st.caption("Aucun scanner dans cette agence.")

            next_id_df = run_query("SELECT ISNULL(MAX(Kantoor_id), 0) + 1 AS next_id FROM DimKantoren")
            new_kantoor_id = int(next_id_df["next_id"].values[0])

            st.divider()
            st.markdown("**Nouvelle adresse**")

            with st.form("demenagement"):
                new_bureau = st.text_input("Nom bureau", value=old_ag["Kantoor_Bureau"] or "", key="dem_bureau")
                new_adresse = st.text_input("Nouvelle adresse", key="dem_adresse")
                new_cpos = st.number_input("Nouveau code postal", min_value=1000, max_value=9999, step=1, key="dem_cpos")
                new_localite = st.text_input("Nouvelle localité", key="dem_localite")
                new_taal = st.selectbox("Langue", ["F", "N", "D"],
                                        index=["F", "N", "D"].index(old_ag["Taal"]) if old_ag["Taal"] in ["F", "N", "D"] else 0,
                                        key="dem_taal")
                new_contact = st.text_input("Contact", value=old_ag["Contactnaam"] or "", key="dem_contact")
                new_tel = st.text_input("Tél", value=old_ag["Teln"] or "", key="dem_tel")
                new_gsm = st.text_input("GSM", value=old_ag["GSM"] or "", key="dem_gsm")
                new_email = st.text_input("Email", value=old_ag["Email"] or "", key="dem_email")
                dem_date = st.date_input("Date du déménagement", value=date.today(), key="dem_date")

                if st.form_submit_button("Exécuter le déménagement", type="primary"):
                    if not new_adresse or not new_localite:
                        st.error("La nouvelle adresse et la localité sont obligatoires.")
                    else:
                        old_localite = old_ag["Localite"] or old_ag["Kantoor_Bureau"]
                        run_execute(
                            "UPDATE DimKantoren SET Status = 'closed', Fermeture = ? WHERE Kantoor_id = ?",
                            [str(dem_date), ag_demenage],
                        )
                        run_execute(
                            """INSERT INTO DimKantoren
                               (Kantoor_id, Kantoor_Bureau, Adresse, C_Pos, Localite,
                                Apparition, Fermeture, Taal, Status, Contactnaam, Teln, GSM, Email)
                               VALUES (?,?,?,?,?,?,NULL,?,'open',?,?,?,?)""",
                            [new_kantoor_id, new_bureau, new_adresse, new_cpos, new_localite,
                             str(dem_date), new_taal, new_contact, new_tel, new_gsm, new_email],
                        )
                        for _, r in scanners_in.iterrows():
                            sn = int(r["Serial_num"])
                            run_execute(
                                "UPDATE FactMovementsHistory SET DateFin = ?, Action = 'déménagement (fermeture)', Via_Maca_Fin = 0 WHERE Serial_num = ? AND Kantoor_id = ? AND DateFin IS NULL AND Action = 'installé'",
                                [str(dem_date), sn, ag_demenage],
                            )
                            run_execute(
                                "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action, Via_Maca) VALUES (?,?,?,NULL,'déménagement (installation)', 0)",
                                [sn, new_kantoor_id, str(dem_date)],
                            )
                        nb = len(scanners_in)
                        show_success(
                            f"✅ Déménagement effectué ! Agence {old_localite} (ID {ag_demenage}) fermée. "
                            f"Nouvelle agence {new_localite} (ID {new_kantoor_id}) ouverte. "
                            f"{nb} scanner(s) transféré(s).",
                            "act8"
                        )

        display_success("act8")

    # ═══ 9. RETRAIT SCANNER SANS REMPLACEMENT ══════════════════════════════
    elif action_choice == "Retrait scanner sans remplacement":
        st.subheader("Retrait d’un scanner sans remplacement")
        st.info("Le scanner est retiré de l’agence et mis en stock. Une ligne maintenance est créée.")

        agencies = get_open_agencies()
        filtered_ag_9 = filter_agencies(agencies, "act9")

        if filtered_ag_9.empty:
            st.warning("Aucune agence trouvée.")
        else:
            ag_retrait = st.selectbox(
                "Agence concernée",
                filtered_ag_9["Kantoor_id"].tolist(),
                format_func=lambda x: agency_label(filtered_ag_9[filtered_ag_9["Kantoor_id"] == x].iloc[0]),
                key="retrait_ag"
            )

            ag_retrait_info = run_query("SELECT Localite, Kantoor_Bureau FROM DimKantoren WHERE Kantoor_id = ?", [ag_retrait]).iloc[0]
            ag_retrait_localite = ag_retrait_info["Localite"] or ag_retrait_info["Kantoor_Bureau"]

            if "retrait_prev_ag" not in st.session_state or st.session_state["retrait_prev_ag"] != ag_retrait:
                st.session_state["retrait_prev_ag"] = ag_retrait
                st.session_state["retrait_info"] = f"Retrait {ag_retrait_localite}"

            scanners_in_ag = run_query(
                """SELECT f.Serial_num, s.Mac_address
                   FROM FactMovementsHistory f
                   JOIN DimScanners s ON f.Serial_num = s.Serial_num
                   WHERE f.Kantoor_id = ? AND f.DateFin IS NULL AND f.Action = 'installé'""",
                [ag_retrait],
            )

            display_success("act9")

            if scanners_in_ag.empty:
                st.warning("Aucun scanner installé dans cette agence.")
            else:
                if len(scanners_in_ag) >= 2:
                    st.warning(
                        f"⚠️ Cette agence possède **{len(scanners_in_ag)} scanners**. "
                        f"Attendez la confirmation de Maca Express pour identifier le scanner concerné "
                        f"avant d’effectuer cette action."
                    )
                with st.form("retrait_scanner"):
                    sn_retrait = st.selectbox(
                        "Scanner à retirer",
                        scanners_in_ag["Serial_num"].tolist(),
                        format_func=lambda x: (
                            "SN " + str(x) + " — MAC " +
                            str(scanners_in_ag.loc[scanners_in_ag["Serial_num"]==x, "Mac_address"].values[0])
                        ),
                        key="retrait_sn"
                    )

                    dest_retrait = st.selectbox(
                        "Destination du scanner",
                        ["Maca Express", "atelier Procedo"],
                        key="retrait_dest"
                    )
                    st.caption(f"Statut associé : **{get_statut_for_loc(dest_retrait, transit_retour=True)}**")

                    retrait_date = st.date_input("Date du retrait", value=date.today(), key="retrait_date")

                    st.divider()
                    st.markdown("**Fiche maintenance**")
                    event_type = st.selectbox("Type d’événement", ["Maintenance", "Failure"], key="retrait_event")
                    panne_desc = st.text_area("Description de la panne", placeholder="Ecran cassé, capteur défaillant, Voir support@procedo.be...", key="retrait_panne")
                    info_maint = st.text_input(
                        "Info maintenance (complétez si besoin)",
                        key="retrait_info"
                    )

                    if st.form_submit_button("Retirer le scanner", type="primary"):
                        action_retrait = "panne détectée" if event_type == "Failure" else "retiré"
                        via_maca_retrait = 0 if dest_retrait == "atelier Procedo" else 1
                        run_execute(
                            "UPDATE FactMovementsHistory SET DateFin = ?, Action = ?, Via_Maca_Fin = ? WHERE Serial_num = ? AND Kantoor_id = ? AND DateFin IS NULL AND Action = 'installé'",
                            [str(retrait_date), action_retrait, via_maca_retrait, sn_retrait, ag_retrait],
                        )
                        run_execute(
                            "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action, Via_Maca) VALUES (?,NULL,?,NULL,'réparation/maintenance', ?)",
                            [sn_retrait, str(retrait_date), via_maca_retrait],
                        )
                        update_scanner_loc(sn_retrait, dest_retrait, transit_retour=True)
                        run_execute(
                            """INSERT INTO FactScannersMaintenance
                               (Serial_num, Event_type, Panne_detected, Info_Maintenance, Copie, Return_date, End_Maintenance)
                               VALUES (?,?,?,?,NULL,?,NULL)""",
                            [sn_retrait, event_type, panne_desc or "aucune", info_maint or "aucune", str(retrait_date)],
                        )
                        _mac_retrait = scanners_in_ag.loc[scanners_in_ag["Serial_num"] == sn_retrait, "Mac_address"].values[0]
                        show_success(
                            f"✅ SN {sn_retrait} (MAC {_mac_retrait}) retiré → {dest_retrait}. "
                            f"Fiche maintenance ({event_type}) enregistrée.",
                            "act9"
                        )

    # ═══ 10. TRANSFERT SCANNER (AGENCE → AGENCE) ═══════════════════════
    elif action_choice == "Transfert scanner (agence → agence)":
        st.subheader("Transfert d'un scanner (agence → agence)")
        st.info("Le scanner est transféré directement d'une agence à une autre, sans passer par le stock ni la maintenance.")

        agencies = get_open_agencies()
        filtered_ag_10 = filter_agencies(agencies, "act10")

        display_success("act10")

        if filtered_ag_10.empty:
            st.warning("Aucune agence trouvée.")
        else:
            ag_source = st.selectbox(
                "Agence source",
                filtered_ag_10["Kantoor_id"].tolist(),
                format_func=lambda x: agency_label(filtered_ag_10[filtered_ag_10["Kantoor_id"] == x].iloc[0]),
                key="transfert_ag_source"
            )

            scanners_in_source = run_query(
                """SELECT f.Serial_num, s.Mac_address
                   FROM FactMovementsHistory f
                   JOIN DimScanners s ON f.Serial_num = s.Serial_num
                   WHERE f.Kantoor_id = ? AND f.DateFin IS NULL AND f.Action = 'installé'""",
                [ag_source],
            )

            if scanners_in_source.empty:
                st.warning("Aucun scanner installé dans cette agence.")
            else:
                if len(scanners_in_source) >= 2:
                    st.warning(
                        f"⚠️ Cette agence possède **{len(scanners_in_source)} scanners**. "
                        f"Attendez la confirmation de Maca Express pour identifier le scanner concerné "
                        f"avant d'effectuer cette action."
                    )
                sn_transfert = st.selectbox(
                    "Scanner à transférer",
                    scanners_in_source["Serial_num"].tolist(),
                    format_func=lambda x: (
                        "SN " + str(x) + " — MAC " +
                        str(scanners_in_source.loc[scanners_in_source["Serial_num"]==x, "Mac_address"].values[0])
                    ),
                    key="transfert_sn"
                )

                ag_dest_list = agencies[agencies["Kantoor_id"] != ag_source]
                if ag_dest_list.empty:
                    st.warning("Aucune autre agence ouverte disponible comme destination.")
                else:
                    ag_dest = st.selectbox(
                        "Agence destination",
                        ag_dest_list["Kantoor_id"].tolist(),
                        format_func=lambda x: agency_label(ag_dest_list[ag_dest_list["Kantoor_id"] == x].iloc[0]),
                        key="transfert_ag_dest"
                    )

                    transfert_date = st.date_input("Date du transfert", value=date.today(), key="transfert_date")

                    _src = agencies[agencies["Kantoor_id"] == ag_source].iloc[0]
                    _dst = ag_dest_list[ag_dest_list["Kantoor_id"] == ag_dest].iloc[0]
                    st.caption(
                        f"SN {sn_transfert} : **{_src['Kantoor_Bureau']}** ({_src['Localite']}) "
                        f"→ **{_dst['Kantoor_Bureau']}** ({_dst['Localite']})"
                    )

                    if st.button("Transférer le scanner", type="primary", key="btn_transfert"):
                        run_execute(
                            "UPDATE FactMovementsHistory SET DateFin = ?, Action = 'transféré', Via_Maca_Fin = 0 WHERE Serial_num = ? AND Kantoor_id = ? AND DateFin IS NULL AND Action = 'installé'",
                            [str(transfert_date), sn_transfert, ag_source],
                        )
                        run_execute(
                            "INSERT INTO FactMovementsHistory (Serial_num, Kantoor_id, DateDebut, DateFin, Action, Via_Maca) VALUES (?,?,?,NULL,'installé', 0)",
                            [sn_transfert, ag_dest, str(transfert_date)],
                        )
                        _mac_tr = scanners_in_source.loc[scanners_in_source["Serial_num"] == sn_transfert, "Mac_address"].values[0]
                        show_success(
                            f"✅ SN {sn_transfert} (MAC {_mac_tr}) transféré de {_src['Localite']} → {_dst['Localite']}.",
                            "act10"
                        )

# ═════════════════════════════════════════════════════════════════════════════
#  FOOTER — affiché en bas de chaque page
# ═════════════════════════════════════════════════════════════════════════════
st.markdown(
    '<div style="text-align:center; font-size:0.8rem; color:#8BA3B8; padding-top:40px;">© 2026 Procedo SRL</div>',
    unsafe_allow_html=True
)
