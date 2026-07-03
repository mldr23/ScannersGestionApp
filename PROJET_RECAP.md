# Projet ScannerApp — Recapitulatif complet
# Derniere mise a jour : 27 mai 2026

## Contexte
PME Procedo (5-10 personnes) qui gere un parc de scanners 730ex plus deployes dans des agences clients (DVV).
Pierre a cree une BDD SQL Server (DWH) avec historisation des mouvements, repartitions, stocks et liste d'agences.
L'objectif est une interface Streamlit pour modifier la BDD automatiquement via des actions metier.

## Stack technique
- **BDD** : SQL Server (localhost), base ProcedoV47
- **Interface** : Streamlit (Python)
- **Connexion** : pyodbc, Windows Authentication
- **Graphiques** : Plotly (carte interactive, barres)
- **Typographie** : Poppins (Google Fonts)
- **Theme** : couleurs ProceDo (bleu marine #1B2A4A + cyan #00B4D8)
- **Sidebar** : fond bleu clair (#F0F7FA), bordure cyan, logo ProceDo, largeur 300px

## Structure BDD (DWH - 5 tables)

### DimScanners
- Serial_num (INT, PK, 8 chiffres)
- Mac_address (NVARCHAR 50)
- Produit (VARCHAR 25) — toujours "730ex plus"
- Localisation (VARCHAR 25)
- Statut (VARCHAR 25)

**Mapping Localisation → Statut (lies automatiquement, JAMAIS modifies separement) :**
- agence DVV → actif
- perdu → a rechercher
- atelier Procedo → a reparer
- detruit → fin de vie
- Maca Express → a livrer (par defaut) OU en transit retour (scanners rapatries du terrain)
- Procedo → inactif
- fournisseur → retour garantie

**Statut special "en transit retour"** : scanners chez Maca Express qui reviennent du terrain (pas encore prets a livrer). Utilise via parametre transit_retour=True dans get_statut_for_loc() et update_scanner_loc(). Concerne : scanner defectueux, cloture agence, retrait scanner, declaration maintenance.

### DimKantoren
- Kantoor_id (INT, PK, auto-increment MAX+1, jamais saisi manuellement)
- Kantoor_Bureau, Adresse, C_Pos, Localite
- Apparition, Fermeture (DATE)
- Taal (F/N/D), Status (open/closed)
- Contactnaam, Teln, GSM, Email

### FactMovementsHistory
- Movement_id (INT, IDENTITY, PK)
- Serial_num (FK → DimScanners)
- Kantoor_id (FK → DimKantoren) — NULL si hors agence
- DateDebut, DateFin (DATE) — DateFin NULL = mouvement en cours
- Action : installe, stock, transfere, reparation/maintenance, agence fermee, panne detectee, retire, demenagement (fermeture), demenagement (installation)
- Via_Maca (BIT, DEFAULT 1) — transport de la LIVRAISON (DateDebut). 1 = livre par Maca Express, 0 = livre par Procedo/demenagement/transfert
- Via_Maca_Fin (BIT, DEFAULT 1) — transport de la RECUPERATION (DateFin). 1 = recupere par Maca Express, 0 = pas via Maca
- Une meme ligne represente a la fois une livraison (DateDebut) et une recuperation (DateFin) → les 2 colonnes permettent de cocher/decocher independamment
- Colonnes creees automatiquement au demarrage si elles n'existent pas (init_via_maca, session key via_maca_init_v4)
- Backfill historique : Via_Maca NULL → 1, Via_Maca_Fin NULL → copie de Via_Maca (si DateFin existe) puis 1 pour le reste, transfere/demenagement → les deux a 0

### FactScannersMaintenance
- Maintenance_id (INT, IDENTITY, PK)
- Serial_num (FK → DimScanners)
- Event_type : Failure / Maintenance
- Panne_detected, Info_Maintenance
- Copie (INT), Return_date, End_Maintenance (DATE)

### DimCodesPostaux
- Code_Postal (INT, PK) — codes postaux belges 1000-9999
- Province (NVARCHAR 50) — 11 provinces/regions belges
- Table creee automatiquement au demarrage de l'app si elle n'existe pas
- Mapping par plages : 1000-1299 Bruxelles-Capitale, 1300-1499 Brabant wallon, 1500-1999+3000-3499 Brabant flamand, 2000-2999 Anvers, 3500-3999 Limbourg, 4000-4999 Liege, 5000-5999 Namur, 6000-6599+7000-7999 Hainaut, 6600-6999 Luxembourg, 8000-8999 Flandre occidentale, 9000-9999 Flandre orientale

## Navigation (ordre dans la sidebar)
1. Dashboard
2. Actions frequentes
3. Scanners
4. Agences
5. Maintenance
6. Logistique & Transport
7. Mouvements

### Pied de page sidebar
Coordonnees de l'entreprise affichees sous le menu de navigation, au-dessus de l'indicateur de connexion :
Procedo SRL — Chaussee de Louvain 775, 1140 Evere — BE 0461.065.843 — support@procedo.be

### CSS personnalise
- Boutons de collapse sidebar masques (display: none)
- KPIs : hauteur reduite (padding 8px 18px), label 0.82rem, valeur 1.4rem
- Icones KPIs differenciees : ouvertures/fermetures distinctes des moyennes

## Dashboard — KPIs
Ligne 1 : Total scanners, Actifs en agence, Agences ouvertes
Ligne 2 : Atelier Procedo (a reparer), Maca Express (a livrer / en transit retour separes), Procedo (inactif)
+ Carte interactive Belgique : scanners actifs par province (choropleth Plotly + GeoJSON belgium_provinces.geojson)
+ Graphique horizontal combine : repartition par localisation (statut), ordre fixe : agence DVV, atelier Procedo, Maca Express, Procedo, detruit, fournisseur, perdu
+ 15 derniers mouvements

## Logistique & Transport
Page dediee au suivi des livraisons, recuperations et trajets Maca Express.
Titre de l'onglet : "Logistique & Transport". Positionne avant Mouvements dans la sidebar.

### Filtres
- Annee (selectbox, de l'annee courante jusqu'a la premiere donnee en BDD)
- Mois (selectbox, "Toute l'annee" par defaut + chaque mois)
- Option "Dates personnalisees" (checkbox) pour override libre
- Periode par defaut = annee en cours
- Recherche par n° de mouvement, n° de serie, localite/adresse (recherche sur toute la base, ignore le filtre de dates)

### Ajustement des plages de dates (historique import annuel)
Beaucoup de DateFin historiques sont au 31/12 (script d'import par comparaison de snapshots annuels).
Pour eviter les doublons et capter correctement ces dates :
- 2021 : 01/01 → 30/12 (1ere annee, pas de donnees avant)
- 2022-2025 : 31/12/(n-1) → 30/12 (capter les 31/12 historiques sans doublon)
- 2026 : 31/12/2025 → 31/12/2026 (annee de transition)
- 2027+ : 01/01 → 31/12 (tout gere par l'appli, dates exactes)

### KPIs (2 lignes de 3 colonnes)
Ligne 1 (Maca uniquement, sur la periode filtree) :
- Livraisons Maca : Kantoor_id IS NOT NULL + Via_Maca = 1, comptee sur DateDebut
- Recuperations Maca : Kantoor_id IS NOT NULL + DateFin IS NOT NULL + Via_Maca_Fin = 1 + Action IN ('panne detectee', 'agence fermee', 'retire'), comptee sur DateFin
- Trajets Maca : nombre de trajets sur la periode (algorithme de groupage, voir ci-dessous)

Ligne 2 (totaux) :
- Livraisons (total) : Kantoor_id IS NOT NULL, comptee sur DateDebut (exclut transferts et demenagements)
- Recuperations (total) : idem sur DateFin
- Total trajets Maca : nombre total de trajets depuis le debut (toutes dates confondues)

Exclusions dans les requetes livraisons : Action NOT IN ('transfere', 'demenagement (fermeture)', 'demenagement (installation)')
Filtre requetes recuperations : Action IN ('panne detectee', 'agence fermee', 'retire')
Note : les transports Procedo (Via_Maca = 0 / Via_Maca_Fin = 0) sont inclus dans les totaux mais PAS dans les comptes Maca.

### Calcul des trajets Maca
- Un "trajet" = un passage physique de Maca Express a une agence un jour donne
- Formule (periode ET global) : MAX(livraisons_dedup, panne_dedup) + standalone_recup_dedup
- livraisons_dedup : unique (Kantoor_id, DateDebut), Via_Maca = 1, Action NOT IN exclusions — inclut remplacements + ouvertures + ajouts
- panne_dedup : unique (Kantoor_id, DateFin), Via_Maca_Fin = 1, Action = 'panne detectee' — partie recuperation des remplacements uniquement
- standalone_recup_dedup : unique (Kantoor_id, DateFin), Via_Maca_Fin = 1, Action IN ('agence fermee', 'retire') — trajets independants (pas lies a un remplacement)
- MAX couvre l'ecart de dates des remplacements (DateDebut livraison ≠ DateFin recuperation) ; les ouvertures/ajouts sont deja dans les livraisons ; fermetures/retraits sont ajoutes separement
- Kantoor_id caste en int pour eviter les problemes de type float/int dans pandas
- Exemples : remplacement defectueux = 1 trajet (MAX gere les dates differentes). Ouverture avec 2 scanners = 1 trajet. Cloture agence avec 3 scanners = 1 trajet.
- Le undo restaure correctement les trajets : suppression du mouvement + DateFin remise a NULL sur le mouvement restaure

### Pied de page
Copyright "© 2026 Procedo SRL" affiche en bas de chaque page principale (centre, gris clair #8BA3B8, padding-top 40px)

### Tableaux editables (pas d'onglets)
- Tableau Livraisons : st.data_editor avec colonne checkbox "Par Maca Express" → edite Via_Maca
- Tableau Recuperations : st.data_editor avec colonne checkbox "Par Maca Express" → edite Via_Maca_Fin
- Bouton "Valider" (type primary) sous chaque tableau, aligne a droite (ratio colonnes 5.5:1)
- Sauvegarde des changements via UPDATE FactMovementsHistory (Via_Maca pour livraisons, Via_Maca_Fin pour recuperations)

### Colonnes affichees
- Tableau livraisons : Movement_id, Serial_num, Kantoor_Bureau, Localite, DateDebut, Action, Par Maca Express
- Tableau recuperations : Movement_id, Serial_num, Kantoor_Bureau, Localite, Action, DateFin, Par Maca Express

### Logique Via_Maca / Via_Maca_Fin (automatique)
**Via_Maca** = transport de la LIVRAISON (DateDebut). Positionne sur les INSERT de nouveaux mouvements.
**Via_Maca_Fin** = transport de la RECUPERATION (DateFin). Positionne sur les UPDATE qui closent un mouvement (DateFin + Action recup).

- Via_Maca = 0 (livraison) automatiquement sur :
  - Demenagement d'agence : 'demenagement (installation)' avec Via_Maca = 0
  - Transfert scanner agence → agence : nouveau 'installe' avec Via_Maca = 0
  - Ouverture agence / Ajouter scanner quand le scanner vient de Procedo (Localisation = "Procedo")
- Via_Maca_Fin = 0 (recuperation) automatiquement sur :
  - Demenagement d'agence : 'demenagement (fermeture)' avec Via_Maca_Fin = 0
  - Transfert scanner : cloture 'transfere' avec Via_Maca_Fin = 0
  - Scanner defectueux / Retrait / Maintenance > Declarer quand destination = atelier Procedo
- Via_Maca = 1 et Via_Maca_Fin = 1 (default) pour tous les autres mouvements (transport par Maca Express)
- Les Undo ne modifient PAS Via_Maca/Via_Maca_Fin — Via_Maca (livraison) est preserve, Via_Maca_Fin devient sans objet quand DateFin est remise a NULL
- Detection historique des demenagements (init_via_maca) : ligne 'agence fermee' suivie d'un 'installe' pour le meme scanner SANS ligne intermediaire 'reparation/maintenance' ou 'stock' entre les deux → renomme en 'demenagement (fermeture)' / 'demenagement (installation)'

## Scanners
### Liste
- Recherche par : SN, Kantoor ID (exact), Localite
- Filtres : statut, localisation
- Hauteur dataframe : 600px

### Ajouter
- SN obligatoire 8 chiffres + verification existence en BDD (erreur si deja existant)
- Warning : "le numero de serie ne pourra plus etre modifie apres creation"
- Localisation limitee a : Procedo, Maca Express, agence DVV (dans cet ordre)
- Si Procedo ou Maca Express → mouvement 'stock' cree (DateDebut=today, DateFin=NULL)
- Si agence DVV → obligation associer une agence + mouvement 'stock' cree et ferme + mouvement 'installe' cree

### Modifier
- Seuls Mac_address et Produit modifiables
- Localisation/Statut bloques → message renvoyant vers Actions frequentes
- Localisation actuelle affichee en lecture seule

## Agences

### KPIs (filtres par annee/mois, meme pattern que Logistique)
Filtres : annee, mois, dates personnalisees (optionnel).

KPIs (2x2) :
- Ouvertures : COUNT DISTINCT Kantoor_id WHERE Apparition BETWEEN dates, excluant demenagements (NOT EXISTS mouvement avec Action = 'demenagement (installation)')
- Fermetures : COUNT DISTINCT Kantoor_id WHERE Fermeture BETWEEN dates, excluant demenagements (NOT EXISTS mouvement avec Action = 'demenagement (fermeture)')
- Moy. ouvertures/an : moyenne annuelle excluant 2021-2022 (annees d'installation) et annee en cours
- Moy. fermetures/an : moyenne annuelle excluant uniquement annee en cours (pas d'exclusion 2021-2022)

### Liste
- Recherche par localite/adresse + Kantoor ID (exact)
- Filtre par statut : selectbox Toutes / open / closed (remplace l'ancien checkbox)
- Colonne Province affichee (JOIN DimCodesPostaux sur C_Pos)
- Hauteur dataframe : 600px

### Ouvrir une agence
- Kantoor ID automatique (MAX+1)
- Scanner obligatoire (depuis stock : a livrer ou inactif)
- Code Postal valide contre DimCodesPostaux (doit exister dans la table)
- Clot le mouvement 'stock' du scanner
- Cree : agence + mouvement 'installe' + scanner → agence DVV/actif

### Modifier
- Adresse/CP/Localite BLOQUES
- Message : "En cas de changement d'adresse, veuillez utiliser l'option Demenagement d'agence dans la section Actions frequentes."
- Modifiable : nom bureau, langue, contact, tel, GSM, email
- Recherche par localite/adresse + Kantoor ID

### Cloturer une agence
- Recherche par localite/adresse + Kantoor ID
- Message d'info (st.info) : "La cloture rapatrie tous le(s) scanner(s) vers Maca Express..."
- Message conseil : transferer les scanners d'abord via Transfert scanner si une autre agence les reprend
- Affiche scanners dans l'agence
- Pour chaque scanner :
  - Mouvement 'installe' clos avec Action = 'agence fermee' + DateFin
  - Nouveau mouvement 'reparation/maintenance' (DateFin=NULL, Kantoor_id=NULL)
  - Scanner → Maca Express / en transit retour
  - Ligne FactScannersMaintenance : Maintenance, "Fermeture agence {localite}"

## Mouvements
### Historique
- Recherche par : SN, n° de mouvement (exact), localite, Kantoor ID (exact)
- Filtre par action
- Plage de dates : "Periode (Date debut) — du" / "au" (DateDebut between)
- Compteur total de lignes affiche sous le tableau

### Annuler la derniere action (Undo)
Systeme d'annulation de la derniere action effectuee, avec restauration complete de l'etat precedent.
Message d'avertissement jaune (st.warning) : "L'annulation est prevue pour corriger une erreur ponctuelle. Enchainer plusieurs annulations peut entrainer des incoherences dans les donnees."

**Choix du type d'annulation** (radio button, dans cet ordre) :
- **Scanner defectueux (remplacement)** : recherche par "N° de serie du scanner" (defectueux ou remplacement). Detecte automatiquement la paire defectueux/remplacement. Restaure le defectueux en agence, remet le remplacement en stock.
- **Ajout, transfert ou retrait sans remplacement scanner** : recherche par "N° de serie du scanner". Validation verte affichee (SN, derniere action, agence, date).
- **Cloture d'agence** : recherche par Kantoor ID. Annule TOUS les scanners de l'agence d'un coup. Restaure agence → Status='open', Fermeture=NULL. Supprime mouvements reparation/maintenance + fiches maintenance crees par la cloture.
- **Ouverture d'agence** : recherche par Kantoor ID. Supprime l'agence de DimKantoren + restaure scanners vers stock. Uniquement possible si l'agence n'a pas eu d'autres mouvements depuis l'ouverture.
- **Demenagement d'agence** : recherche par Kantoor ID de la nouvelle agence. Supprime la nouvelle agence, rouvre l'ancienne (Status='open', Fermeture=NULL), restaure les scanners dans l'ancienne agence. Uniquement possible si la nouvelle agence n'a pas eu d'autres mouvements.

Logique d'annulation scanner (actions individuelles) :
- **Undo installe** : supprime l'installe, rouvre le mouvement precedent (stock → DateFin NULL), scanner → Maca Express/a livrer
- **Undo reparation/maintenance** : supprime la reparation, rouvre l'installe precedent (Action restauree de panne detectee/retire/agence fermee → installe, DateFin → NULL, Via_Maca/Via_Maca_Fin inchanges), scanner → agence DVV/actif, supprime la fiche maintenance associee
- **Undo stock** : supprime le stock, rouvre la reparation precedente (DateFin → NULL), scanner → Maca Express/en transit retour, rouvre la fiche maintenance (End_Maintenance → NULL)
- **Undo installe apres transfert** : supprime l'installe en agence B, rouvre l'installe en agence A

Garde-fous : si un mouvement est lie a une action agence (cloture, ouverture, demenagement), l'undo scanner redirige vers le type d'annulation agence.

### Cloture d'agence — destination
Dans Agences > Cloturer et Actions frequentes > Cloturer une agence : choix de la **destination du scanner** (selectbox) :
- **Maca Express** (par defaut) : Via_Maca_Fin = 1, scanner → Maca Express (en transit retour)
- **atelier Procedo** : Via_Maca_Fin = 0, scanner → atelier Procedo (a reparer)

## Maintenance

### KPIs (filtres par annee/mois, meme pattern que Logistique/Agences)
Filtres : annee, mois, dates personnalisees (optionnel).

KPIs (3 colonnes) :
- Nouvelles pannes : COUNT maintenances WHERE Return_date BETWEEN dates
- Reparations cloturees : COUNT maintenances WHERE End_Maintenance BETWEEN dates
- Duree moy. reparation : AVG(DATEDIFF(DAY, Return_date, End_Maintenance)) sur TOUTES les maintenances cloturees (global, pas filtre par periode)

### Onglets : Historique, Modifier, Declarer (pas de Supprimer — le Undo couvre ce cas)

### Historique
- Recherche par SN, Maintenance_id (exact), plage de dates (Return_date between)
- Checkbox "Maintenances ouvertes uniquement" pour filtrer
- Compteur total de lignes affiche sous le tableau
- Colonne "Copies" (renommee depuis "Copie" en BDD) dans le dataframe affiche
- Cloture reparation en bas de l'historique (meme logique que Actions frequentes) :
  - Formulaire avec date + champs facultatifs (panne, info, copies)
  - Caption : "Completer la fiche avant cloture (facultatif) :"
  - Panne et Info : AJOUTENT au texte existant (concatenation avec " | "), ne remplacent pas
  - Copies : REMPLACE la valeur existante
  - Ferme mouvement 'reparation/maintenance' + cree mouvement 'stock' + scanner → Procedo/inactif

### Modifier
- Recherche par SN
- Mettre a jour Panne_detected, Info_Maintenance et Copies (INT nullable) sur maintenance ouverte
- Champs pre-remplis avec les valeurs existantes
- Cle de formulaire dynamique pour eviter les conflits de state Streamlit
- Label "Copies" (sans mention "entier, laisser vide si non applicable")

### Declarer
- Recherche par SN (selectbox HORS formulaire pour permettre le pre-remplissage dynamique)
- Scanners exclus : fin de vie, retour garantie, a reparer (deja en maintenance)
- Choix destination : Maca Express (defaut) ou atelier Procedo
- Copies (INT nullable), label "Copies"
- Info_Maintenance pre-rempli "Retour {localite}" si scanner actif (en agence), vide si scanner en stock
- Logique dependante du statut actuel du scanner :
  - actif (agence DVV) : cloture mouvement 'installe' + cree mouvement 'reparation/maintenance'
  - inactif (Procedo) : cloture mouvement 'stock' + cree mouvement 'reparation/maintenance'
  - a livrer (Maca Express) : cloture mouvement 'stock' + cree mouvement 'reparation/maintenance'
  - a rechercher (perdu) : cloture mouvement existant + cree mouvement 'reparation/maintenance'
- Scanner → localisation choisie (Maca Express ou atelier Procedo) + statut correspondant
- Cree ligne FactScannersMaintenance (Failure)

## Actions frequentes (10 actions en 3 categories)

Interface avec 3 colonnes de categories (Agences, Scanners en agence, Stock & Maintenance).
Titres avec badges colores (bleu marine #1B2A4A et cyan #00B4D8).
Selection unique : choisir dans une categorie deselectionne les autres.

### Categorie : Agences

#### Ouvrir une agence
- Renvoie vers Agences > Ouvrir une agence

#### Cloturer une agence
- Recherche agence par localite/adresse + Kantoor ID
- Message conseil : transferer les scanners d'abord via Transfert scanner si une autre agence les reprend
- Mouvement 'installe' clos avec Action = 'agence fermee'
- Scanners → Maca Express + mouvements 'reparation/maintenance' + maintenance "Fermeture agence {localite}"

#### Demenagement d'agence
- Recherche agence par localite/adresse + Kantoor ID
- Ancienne agence → closed
- Nouvelle agence creee (ID auto, TOUS les champs vides a remplir)
- Scanners : mouvement ancien clos avec Action = 'demenagement (fermeture)' (Via_Maca_Fin = 0) + nouveau 'demenagement (installation)' (Via_Maca = 0)
- Scanners restent en agence DVV/actif

### Categorie : Scanners en agence

#### Scanner defectueux (remplacement)
- Recherche agence par localite/adresse + Kantoor ID
- Liste scanners filtree par agences selectionnees (avec nom agence affiche)
- Scanner defectueux → Maca Express (defaut) OU atelier Procedo
- Mouvement ancien clos avec action 'panne detectee' (Via_Maca_Fin selon destination)
- Panne_detected : champ modifiable, defaut "Voir support@procedo.be"
- Info_Maintenance : champ modifiable, auto-rempli "Retour {localite}" si vide
- Scanner remplacement (depuis stock) : mouvement 'stock' CLOS + nouveau mouvement 'installe'

#### Ajouter scanner dans agence existante
- Recherche agence par localite/adresse + Kantoor ID
- Scanner du stock : mouvement 'stock' CLOS + nouveau mouvement 'installe'
- Scanner → agence DVV/actif

#### Retrait scanner sans remplacement
- Recherche agence par localite/adresse + Kantoor ID
- Choisir scanner parmi ceux installes dans l'agence (SN + MAC affiches)
- Destination : atelier Procedo OU Maca Express
- Mouvement 'installe' clos → action 'panne detectee' (si Failure) ou 'retire' (si Maintenance), Via_Maca_Fin selon destination
- Nouveau mouvement 'reparation/maintenance' (DateFin=NULL)
- Fiche maintenance avec Info_Maintenance pre-rempli "Retour {localite}" (modifiable)

#### Transfert scanner (agence → agence)
- Recherche agence source par localite/adresse + Kantoor ID
- Choisir scanner parmi ceux installes dans l'agence source (SN + MAC affiches)
- Choisir agence destination (toutes agences ouvertes sauf la source)
- Mouvement 'installe' source clos avec Action = 'transfere' + DateFin + Via_Maca_Fin = 0
- Nouveau mouvement 'installe' cree dans l'agence destination (Via_Maca = 0)
- Scanner reste agence DVV/actif (aucun changement de statut)
- Aucune maintenance creee, aucun passage par le stock

### Categorie : Stock & Maintenance

#### Mouvement stock interne (Procedo / Maca)
- Procedo → Maca Express (pret a livrer)
- Maca Express → Atelier Procedo (a reparer) — affichage Statut (a livrer / en transit retour)

#### Cloturer / Modifier une reparation
- Recherche par SN
- Tableau des reparations ouvertes affiche (avec colonne Copies)
- **Cloturer** : formulaire avec date + champs facultatifs (panne, info, copies)
  - Caption : "Completer la fiche avant cloture (facultatif) :"
  - Panne et Info : AJOUTENT au texte existant (concatenation avec " | "), ne remplacent pas
  - Copies : REMPLACE la valeur existante
  - End_Maintenance = date + mouvement 'reparation/maintenance' CLOS + nouveau mouvement 'stock' (DateFin=NULL) + scanner → Procedo/inactif
- **Modifier** : selectbox hors formulaire (pre-remplissage dynamique), champs pre-remplis avec valeurs existantes (panne, info, copies), ecrasement complet a la sauvegarde (meme logique que Maintenance > Modifier)

#### Sortie de parc (detruit / retour garantie / perdu)
- Recherche par n° de serie
- Scanner ne doit PAS etre en agence
- Scanners exclus : deja 'detruit', 'fournisseur' (retour garantie)
- Message d'avertissement jaune : "Un scanner marque detruit ou retour garantie ne pourra plus etre remis en service. Cette action est irreversible."
- Options : detruit (fin de vie), retour garantie (retour garantie), perdu (a rechercher)
- Affichage "retour garantie" dans l'interface, stocke comme "fournisseur" en BDD (compatibilite existante)
- Si 'detruit' ou 'retour garantie' : mouvement ouvert CLOS + cloture maintenance si statut "en transit retour" ou "a reparer"
  - retour garantie → Info_Maintenance concatene " | Retour garantie"
  - detruit → Info_Maintenance concatene " | Destruction"
- Si 'perdu' : mouvement reste OUVERT, maintenance reste OUVERTE (espoir de retrouver)
- Aucun trajet Logistique lie (scanner deja hors agence, Kantoor_id = NULL)

## Regles metier importantes
- Serial_num = exactement 8 chiffres
- Localisation et Statut sont TOUJOURS lies (mapping fixe, jamais modifies separement)
- Kantoor_id auto-incremente (MAX+1, jamais saisi manuellement)
- Changement de localisation uniquement via Actions frequentes (pas dans Scanners > Modifier)
- Changement d'adresse agence uniquement via Cloturer + Ouvrir (pas dans Agences > Modifier)
- Tout scanner du stock a un mouvement 'stock' ouvert (DateFin=NULL)
- Avant d'installer un scanner en agence → TOUJOURS clore le mouvement 'stock' ou 'reparation' ouvert
- Retour scanner = toujours mouvement 'reparation/maintenance' (pas 'stock') car passage par check atelier
- Cloture reparation = ferme 'reparation' + ouvre 'stock'
- Recherche Kantoor ID = correspondance EXACTE (pas partielle)
- Messages de succes affiches sous chaque formulaire (systeme show_success avec localisation)
- Conversion numpy → Python natif pour pyodbc (_convert_params)
- Compatibilite SQLite/SQL Server (sql_top, sql_cast_text)

## Flux type d'un scanner
1. Ajout en BDD → Procedo (inactif) + mouvement 'stock' ouvert
2. Installation en agence → mouvement 'stock' ferme + mouvement 'installe' ouvert + agence DVV (actif)
3. Retrait/panne → mouvement 'installe' ferme (action 'panne detectee') + mouvement 'reparation/maintenance' ouvert + Maca Express ou atelier
4. Check atelier → (mouvement stock interne si besoin)
5. Cloture reparation → mouvement 'reparation/maintenance' ferme + mouvement 'stock' ouvert + Procedo (inactif)
6. Reinstallation → retour a etape 2
7. OU transfert direct vers autre agence → mouvement 'installe' ferme (action 'transfere') + nouveau 'installe'
8. OU sortie de parc → detruit/fournisseur/perdu

## Fichiers
- app.py — application principale
- requirements.txt — streamlit, pandas, pyodbc, plotly
- .streamlit/config.toml — theme ProceDo (couleurs, fond)
- belgium_provinces.geojson — frontieres des 11 provinces belges (converti depuis FR_Provinces.json TopoJSON)
- FR_Provinces.json — source TopoJSON des provinces (arneh61/Belgium-Map)
- logo.png — logo ProceDo (a placer manuellement dans le dossier)
- fix_missing_stock.py — script one-shot de correction historique : stock manquant, maintenances ouvertes (preview/--execute)
- fix_historical_dates.py — script one-shot de correction des dates artificielles 31/12 sur les DEPLACE (match Return_date maintenance, preview/--execute)
- create_db.sql — script de creation des 5 tables SQL Server (avec Via_Maca et Via_Maca_Fin)
- LISEZMOI.txt — guide de demarrage
- check_demenagements.py — script d'analyse lecture seule : detecte les demenagements (meme Localite + ecart ≤ 30j + meme scanner)
- fix_demenagements.py — script de correction des 2 demenagements historiques (Diksmuide + Cuesmes), preview/--execute
- PROJET_RECAP.md — ce fichier
