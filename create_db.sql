-- ============================================================
-- Script de creation BDD Parc_Scanners_Procedo (SQL Server)
-- Inclut la colonne Via_Maca dans FactMovementsHistory
-- ============================================================

-- Creer la base si elle n'existe pas
IF NOT EXISTS (SELECT name FROM sys.databases WHERE name = 'Parc_Scanners_Procedo')
BEGIN
    CREATE DATABASE Parc_Scanners_Procedo;
END
GO

USE Parc_Scanners_Procedo;
GO

-- ── DimScanners ──────────────────────────────────────────────
IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'DimScanners')
BEGIN
    CREATE TABLE DimScanners (
        Serial_num   INT          PRIMARY KEY,       -- 8 chiffres
        Mac_address  NVARCHAR(50),
        Produit      VARCHAR(25)  DEFAULT '730ex plus',
        Localisation VARCHAR(25),
        Statut       VARCHAR(25)
    );
END
GO

-- ── DimKantoren ──────────────────────────────────────────────
IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'DimKantoren')
BEGIN
    CREATE TABLE DimKantoren (
        Kantoor_id    INT          PRIMARY KEY,      -- MAX+1, jamais saisi manuellement
        Kantoor_Bureau NVARCHAR(100),
        Adresse       NVARCHAR(200),
        C_Pos         INT,
        Localite      NVARCHAR(100),
        Apparition    DATE,
        Fermeture     DATE,
        Taal          CHAR(1),                       -- F / N / D
        Status        VARCHAR(10)  DEFAULT 'open',   -- open / closed
        Contactnaam   NVARCHAR(100),
        Teln          NVARCHAR(20),
        GSM           NVARCHAR(20),
        Email         NVARCHAR(100)
    );
END
GO

-- ── FactMovementsHistory ─────────────────────────────────────
IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'FactMovementsHistory')
BEGIN
    CREATE TABLE FactMovementsHistory (
        Movement_id  INT IDENTITY(1,1) PRIMARY KEY,
        Serial_num   INT          NOT NULL,
        Kantoor_id   INT          NULL,              -- NULL si hors agence
        DateDebut    DATE         NOT NULL,
        DateFin      DATE         NULL,              -- NULL = mouvement en cours
        Action       VARCHAR(50)  NOT NULL,
        Via_Maca     BIT          DEFAULT 1,         -- 1 = via Maca Express, 0 = pas via Maca
        FOREIGN KEY (Serial_num)  REFERENCES DimScanners(Serial_num),
        FOREIGN KEY (Kantoor_id)  REFERENCES DimKantoren(Kantoor_id)
    );
END
GO

-- ── FactScannersMaintenance ──────────────────────────────────
IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'FactScannersMaintenance')
BEGIN
    CREATE TABLE FactScannersMaintenance (
        Maintenance_id   INT IDENTITY(1,1) PRIMARY KEY,
        Serial_num       INT          NOT NULL,
        Event_type       VARCHAR(25)  NOT NULL,      -- Failure / Maintenance
        Panne_detected   NVARCHAR(200),
        Info_Maintenance NVARCHAR(200),
        Copie            INT          NULL,
        Return_date      DATE         NOT NULL,
        End_Maintenance  DATE         NULL,
        FOREIGN KEY (Serial_num) REFERENCES DimScanners(Serial_num)
    );
END
GO

-- ── DimCodesPostaux ──────────────────────────────────────────
-- (Cette table est creee automatiquement par l'app au demarrage,
--  mais voici la structure si vous voulez la creer manuellement)
IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'DimCodesPostaux')
BEGIN
    CREATE TABLE DimCodesPostaux (
        Code_Postal  INT          PRIMARY KEY,       -- 1000-9999
        Province     NVARCHAR(50)
    );
END
GO

PRINT 'Toutes les tables ont ete creees avec succes.';
PRINT 'La colonne Via_Maca (BIT DEFAULT 1) est incluse dans FactMovementsHistory.';
PRINT 'DimCodesPostaux sera peuplee automatiquement par l''app Streamlit au premier demarrage.';
GO
