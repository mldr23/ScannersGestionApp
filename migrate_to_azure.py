"""
Migration script : Local SQL Server → Azure SQL Database
=========================================================
Lit les données depuis le SQL Server local (Windows Auth)
et les insère dans Azure SQL Database (pymssql).

Usage : python migrate_to_azure.py
"""

import pyodbc
import pymssql
import sys

# ── Configuration locale (Windows Auth) ─────────────────────────────
LOCAL_SERVER = "localhost"
LOCAL_DB = "Parc_Scanners_Procedo"

# ── Configuration Azure ─────────────────────────────────────────────
AZURE_SERVER = "procedo-sql-srv.database.windows.net"
AZURE_DB = "Parc_Scanners_Procedo"
AZURE_USER = "procedo_admin"
AZURE_PASS = input("🔑 Mot de passe Azure SQL (procedo_admin) : ")

# ── Tables à migrer (dans l'ordre pour respecter les FK) ────────────
TABLES = [
    "DimCodesPostaux",
    "DimScanners",
    "DimKantoren",
    "FactMovementsHistory",
    "FactScannersMaintenance",
]

# ── CREATE TABLE statements pour Azure SQL ──────────────────────────
CREATE_STATEMENTS = {
    "DimCodesPostaux": """
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'DimCodesPostaux')
        CREATE TABLE DimCodesPostaux (
            Code_Postal INT PRIMARY KEY,
            Province NVARCHAR(50) NOT NULL
        )
    """,
    "DimScanners": """
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'DimScanners')
        CREATE TABLE DimScanners (
            Serial_num INT PRIMARY KEY,
            Mac_address VARCHAR(50) NOT NULL,
            Produit VARCHAR(50) NOT NULL DEFAULT '730ex plus',
            Localisation VARCHAR(50) NOT NULL,
            Statut VARCHAR(50) NOT NULL
        )
    """,
    "DimKantoren": """
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'DimKantoren')
        CREATE TABLE DimKantoren (
            Kantoor_id INT PRIMARY KEY,
            Kantoor_Bureau NVARCHAR(100),
            Adresse NVARCHAR(200),
            C_Pos INT,
            Localite NVARCHAR(100),
            Apparition DATE,
            Fermeture DATE,
            Taal VARCHAR(10),
            Status VARCHAR(20) NOT NULL DEFAULT 'open',
            Contactnaam NVARCHAR(100),
            Teln VARCHAR(30),
            GSM VARCHAR(30),
            Email VARCHAR(100)
        )
    """,
    "FactMovementsHistory": """
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'FactMovementsHistory')
        CREATE TABLE FactMovementsHistory (
            Movement_id INT IDENTITY(1,1) PRIMARY KEY,
            Serial_num INT,
            Kantoor_id INT,
            DateDebut DATE,
            DateFin DATE,
            Action VARCHAR(50) NOT NULL,
            Via_Maca BIT DEFAULT 1,
            Via_Maca_Fin BIT DEFAULT 1,
            FOREIGN KEY (Serial_num) REFERENCES DimScanners(Serial_num),
            FOREIGN KEY (Kantoor_id) REFERENCES DimKantoren(Kantoor_id)
        )
    """,
    "FactScannersMaintenance": """
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'FactScannersMaintenance')
        CREATE TABLE FactScannersMaintenance (
            Maintenance_id INT IDENTITY(1,1) PRIMARY KEY,
            Serial_num INT NOT NULL,
            Event_type VARCHAR(50) NOT NULL,
            Panne_detected NVARCHAR(200),
            Info_Maintenance NVARCHAR(500),
            Copie INT,
            Return_date DATE NOT NULL,
            End_Maintenance DATE,
            FOREIGN KEY (Serial_num) REFERENCES DimScanners(Serial_num)
        )
    """,
}


def connect_local():
    """Connexion au SQL Server local via Windows Auth."""
    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={LOCAL_SERVER};"
        f"DATABASE={LOCAL_DB};"
        f"Trusted_Connection=yes;"
    )
    return pyodbc.connect(conn_str)


def connect_azure():
    """Connexion à Azure SQL via pymssql."""
    return pymssql.connect(
        server=AZURE_SERVER,
        user=AZURE_USER,
        password=AZURE_PASS,
        database=AZURE_DB,
    )


def get_columns(local_cur, table_name):
    """Récupère la liste des colonnes d'une table locale."""
    local_cur.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
        table_name,
    )
    return [row[0] for row in local_cur.fetchall()]


def migrate_table(local_conn, azure_conn, table_name):
    """Migre une table du local vers Azure."""
    local_cur = local_conn.cursor()
    azure_cur = azure_conn.cursor()

    # 1. Créer la table sur Azure
    print(f"  📋 Création de la table {table_name}...")
    azure_cur.execute(CREATE_STATEMENTS[table_name])
    azure_conn.commit()

    # 2. Vérifier si la table Azure a déjà des données
    azure_cur.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = azure_cur.fetchone()[0]
    if count > 0:
        print(f"  ⏭️  {table_name} contient déjà {count} lignes, skip.")
        return

    # 3. Lire les données locales
    columns = get_columns(local_cur, table_name)
    local_cur.execute(f"SELECT * FROM {table_name}")
    rows = local_cur.fetchall()
    print(f"  📦 {len(rows)} lignes à migrer...")

    if not rows:
        print(f"  ✅ {table_name} — aucune donnée à migrer.")
        return

    # 4. Pour les tables IDENTITY, on doit activer IDENTITY_INSERT
    has_identity = table_name in ("FactMovementsHistory", "FactScannersMaintenance")

    if has_identity:
        azure_cur.execute(f"SET IDENTITY_INSERT {table_name} ON")

    # 5. Insérer par batch
    col_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})"

    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        for row in batch:
            # Convertir les valeurs pour pymssql
            values = []
            for val in row:
                if val is None:
                    values.append(None)
                elif hasattr(val, "item"):
                    values.append(val.item())
                else:
                    values.append(val)
            azure_cur.execute(insert_sql, tuple(values))
        azure_conn.commit()
        print(f"    → {min(i + batch_size, len(rows))}/{len(rows)} lignes insérées")

    if has_identity:
        azure_cur.execute(f"SET IDENTITY_INSERT {table_name} OFF")
        azure_conn.commit()

    print(f"  ✅ {table_name} — {len(rows)} lignes migrées !")


def main():
    print("=" * 60)
    print("  Migration Local SQL Server → Azure SQL Database")
    print("=" * 60)

    # Connexion locale
    print("\n🔌 Connexion au SQL Server local...")
    try:
        local_conn = connect_local()
        print("  ✅ Connecté à localhost")
    except Exception as e:
        print(f"  ❌ Erreur connexion locale : {e}")
        sys.exit(1)

    # Connexion Azure
    print("\n☁️  Connexion à Azure SQL...")
    try:
        azure_conn = connect_azure()
        print("  ✅ Connecté à Azure")
    except Exception as e:
        print(f"  ❌ Erreur connexion Azure : {e}")
        print("  💡 Vérifie le mot de passe et les règles firewall.")
        sys.exit(1)

    # Migration table par table
    print("\n🚀 Début de la migration...\n")
    for table in TABLES:
        print(f"── {table} ──")
        try:
            migrate_table(local_conn, azure_conn, table)
        except Exception as e:
            print(f"  ❌ Erreur sur {table} : {e}")
            azure_conn.rollback()
        print()

    local_conn.close()
    azure_conn.close()

    print("=" * 60)
    print("  ✅ Migration terminée !")
    print("=" * 60)


if __name__ == "__main__":
    main()
