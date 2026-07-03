"""
Backup script : Azure SQL Database → Local SQL Server
======================================================
Lit les données depuis Azure SQL (pymssql) et les synchronise
vers le SQL Server local (Windows Auth via pyodbc).
Crée aussi un backup horodaté dans le dossier BAK_BDD.

Usage : python backup_from_azure.py
"""

import pyodbc
import pymssql
import os
import json
import sys
from datetime import datetime

# ── Configuration locale (Windows Auth) ─────────────────────────────
LOCAL_SERVER = "localhost"
LOCAL_DB = "Parc_Scanners_Procedo"

# ── Configuration Azure ─────────────────────────────────────────────
AZURE_SERVER = "procedo-sql-srv.database.windows.net"
AZURE_DB = "Parc_Scanners_Procedo"
AZURE_USER = "procedo_admin"
AZURE_PASS = input("🔑 Mot de passe Azure SQL (procedo_admin) : ")

# ── Dossier de backup ──────────────────────────────────────────────
BACKUP_DIR = r"C:\Users\Pierre\Desktop\BDD_scanners_DVV\BAK_BDD"

# ── Tables (dans l'ordre pour respecter les FK) ────────────────────
TABLES = [
    "DimCodesPostaux",
    "DimScanners",
    "DimKantoren",
    "FactMovementsHistory",
    "FactScannersMaintenance",
]

# Tables avec IDENTITY (auto-increment)
IDENTITY_TABLES = {"FactMovementsHistory", "FactScannersMaintenance"}


def connect_azure():
    """Connexion à Azure SQL via pymssql."""
    return pymssql.connect(
        server=AZURE_SERVER,
        user=AZURE_USER,
        password=AZURE_PASS,
        database=AZURE_DB,
    )


def connect_local():
    """Connexion au SQL Server local via Windows Auth."""
    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={LOCAL_SERVER};"
        f"DATABASE={LOCAL_DB};"
        f"Trusted_Connection=yes;"
    )
    return pyodbc.connect(conn_str)


def get_columns(azure_cur, table_name):
    """Récupère la liste des colonnes d'une table Azure."""
    azure_cur.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
        (table_name,),
    )
    return [row[0] for row in azure_cur.fetchall()]


def read_azure_table(azure_conn, table_name):
    """Lit toutes les données d'une table Azure."""
    cur = azure_conn.cursor()
    columns = get_columns(cur, table_name)
    cur.execute(f"SELECT * FROM {table_name}")
    rows = cur.fetchall()
    return columns, rows


def save_backup_json(table_name, columns, rows, backup_folder):
    """Sauvegarde une table en JSON dans le dossier de backup."""
    data = []
    for row in rows:
        record = {}
        for i, col in enumerate(columns):
            val = row[i]
            # Convertir les types non-sérialisables
            if val is None:
                record[col] = None
            elif hasattr(val, "isoformat"):
                record[col] = val.isoformat()
            elif isinstance(val, (bytes, bytearray)):
                record[col] = val.decode("utf-8", errors="replace")
            else:
                record[col] = val
        data.append(record)

    filepath = os.path.join(backup_folder, f"{table_name}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return filepath


def sync_to_local(local_conn, table_name, columns, rows):
    """Synchronise une table vers le SQL Server local (vide puis re-remplit)."""
    cur = local_conn.cursor()

    # Désactiver les contraintes FK temporairement
    cur.execute(f"ALTER TABLE {table_name} NOCHECK CONSTRAINT ALL")
    local_conn.commit()

    # Vider la table locale
    cur.execute(f"DELETE FROM {table_name}")
    local_conn.commit()

    if not rows:
        cur.execute(f"ALTER TABLE {table_name} CHECK CONSTRAINT ALL")
        local_conn.commit()
        return

    # Activer IDENTITY_INSERT si nécessaire
    has_identity = table_name in IDENTITY_TABLES
    if has_identity:
        cur.execute(f"SET IDENTITY_INSERT {table_name} ON")

    # Insérer les données
    col_list = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    insert_sql = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})"

    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i: i + batch_size]
        for row in batch:
            values = []
            for val in row:
                if val is None:
                    values.append(None)
                elif hasattr(val, "item"):
                    values.append(val.item())
                else:
                    values.append(val)
            cur.execute(insert_sql, values)
        local_conn.commit()
        print(f"    → {min(i + batch_size, len(rows))}/{len(rows)} lignes")

    if has_identity:
        cur.execute(f"SET IDENTITY_INSERT {table_name} OFF")

    # Réactiver les contraintes FK
    cur.execute(f"ALTER TABLE {table_name} CHECK CONSTRAINT ALL")
    local_conn.commit()


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_folder = os.path.join(BACKUP_DIR, f"backup_{timestamp}")

    print("=" * 60)
    print("  Backup Azure SQL → Local + fichiers JSON")
    print(f"  {timestamp}")
    print("=" * 60)

    # Créer le dossier de backup
    os.makedirs(backup_folder, exist_ok=True)
    print(f"\n Dossier backup : {backup_folder}")

    # Connexion Azure
    print("\n  Connexion à Azure SQL...")
    try:
        azure_conn = connect_azure()
        print(" Connecté à Azure")
    except Exception as e:
        print(f" Erreur connexion Azure : {e}")
        sys.exit(1)

    # Connexion locale
    print("\n🔌 Connexion au SQL Server local...")
    try:
        local_conn = connect_local()
        print(" Connecté à localhost")
        sync_local = True
    except Exception as e:
        print(f"   Pas de connexion locale : {e}")
        print("  → Les fichiers JSON seront quand même créés.")
        sync_local = False

    # Lire toutes les tables depuis Azure + sauvegarder en JSON
    print("\n Lecture des données Azure...\n")
    table_data = {}
    for table in TABLES:
        print(f"── {table} ──")
        try:
            columns, rows = read_azure_table(azure_conn, table)
            table_data[table] = (columns, rows)
            print(f"  {len(rows)} lignes lues depuis Azure")

            filepath = save_backup_json(table, columns, rows, backup_folder)
            print(f"  Sauvegardé → {os.path.basename(filepath)}")
        except Exception as e:
            print(f"  Erreur : {e}")
        print()

    # Synchroniser vers local (ordre inversé pour les DELETE, puis ordre normal pour les INSERT)
    if sync_local:
        print(" Synchronisation locale...\n")
        local_cur = local_conn.cursor()

        # 1. Vider les tables enfants d'abord (ordre inversé)
        print("  Vidage des tables locales (enfants d'abord)...")
        for table in reversed(TABLES):
            try:
                local_cur.execute(f"DELETE FROM {table}")
                local_conn.commit()
                print(f"    {table} vidée")
            except Exception as e:
                print(f"    Erreur vidage {table} : {e}")
                local_conn.commit()

        # 2. Réinsérer dans l'ordre normal (parents d'abord)
        print("\n  Insertion des données (parents d'abord)...")
        for table in TABLES:
            if table not in table_data:
                continue
            columns, rows = table_data[table]
            if not rows:
                print(f"  {table} — aucune donnée")
                continue
            print(f"  ── {table} ({len(rows)} lignes) ──")
            try:
                has_identity = table in IDENTITY_TABLES
                if has_identity:
                    local_cur.execute(f"SET IDENTITY_INSERT {table} ON")

                col_list = ", ".join(columns)
                placeholders = ", ".join(["?"] * len(columns))
                insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

                batch_size = 500
                for i in range(0, len(rows), batch_size):
                    batch = rows[i: i + batch_size]
                    for row in batch:
                        values = []
                        for val in row:
                            if val is None:
                                values.append(None)
                            elif hasattr(val, "item"):
                                values.append(val.item())
                            else:
                                values.append(val)
                        local_cur.execute(insert_sql, values)
                    local_conn.commit()
                    print(f"    → {min(i + batch_size, len(rows))}/{len(rows)} lignes")

                if has_identity:
                    local_cur.execute(f"SET IDENTITY_INSERT {table} OFF")
                    local_conn.commit()

                print(f"    {len(rows)} lignes synchronisées")
            except Exception as e:
                print(f"    Erreur : {e}")
                local_conn.commit()
            print()

    azure_conn.close()
    if sync_local:
        local_conn.close()

    print("=" * 60)
    print(f"  Backup terminé !")
    print(f"  Fichiers JSON : {backup_folder}")
    if sync_local:
        print(f"  Base locale synchronisée")
    print("=" * 60)


if __name__ == "__main__":
    main()
