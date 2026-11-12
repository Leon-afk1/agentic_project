# Phase 1 — Exploration technique de DataGrandEst

Date des tests : 12 novembre 2026 (soirée). Projet personnel indépendant, sans lien avec DataGrandEst ni ses partenaires.

Convention : **[testé]** = appel réel effectué ce jour, **[lu]** = lu dans une source publique, **[non vérifié]** = hypothèse ou point non testé.

## 0. Résumé

1. **L'API est riche et ouverte.** DataGrandEst est une plateforme geOrchestra. Son catalogue est un GeoNetwork 4 dont l'API de recherche (Elasticsearch) répond sans authentification. Le catalogue complet tient en 17 requêtes : **8 316 fiches, dont 6 867 jeux de données**. Pas besoin du plan B (data.gouv.fr).
2. **Un serveur MCP existe déjà, deux fois.** Camptocamp a présenté en juin 2026 un « MCP GeoNetwork », et `https://www.datagrandest.fr/mcp` redirige vers un serveur MCP déjà déployé, protégé par OAuth. Mon serveur MCP ne sera donc pas une nouveauté pour eux.
3. **Le chatbot n'est pas accessible publiquement** à cette date. Je n'ai pas pu le tester. La baseline « réelle » testée est la recherche du portail.
4. **Les métadonnées sont meilleures que supposé sur les critères simples** (4,9 % de descriptions de moins de 50 caractères, 1,4 % sans mots-clés) mais faibles sur d'autres : 17,7 % sans thématique, 47,5 % sans date de révision, 17,5 % de descriptions contenant du HTML, licences en texte libre. Les défauts sont concentrés sur quelques sources moissonnées.
5. **La recherche du portail utilise l'opérateur ET** entre tous les mots : une question en langage naturel renvoie souvent 0 résultat. C'est un terrain de comparaison net pour le benchmark.
6. **Recommandation de positionnement** : la valeur du projet n'est pas « un MCP de plus », c'est **l'évaluation** (jeu de référence, comparaison de quatre approches, juge calibré). C'est précisément ce que Camptocamp liste comme « reste à bâtir » dans sa présentation.

## 1. Comment DataGrandEst expose ses données

### 1.1 Architecture [testé + lu]

| Composant | URL | Constat |
|---|---|---|
| Site éditorial (WordPress) | `https://www.datagrandest.fr/site/` | 200 |
| Catalogue (interface) : datahub geonetwork-ui | `https://www.datagrandest.fr/datahub/` | 200 |
| Catalogue (moteur) : GeoNetwork 4 | `https://www.datagrandest.fr/geonetwork/srv/api/…` | recherche ouverte |
| CSW 2.0.2 | `https://www.datagrandest.fr/geonetwork/srv/fre/csw?SERVICE=CSW&VERSION=2.0.2&REQUEST=GetCapabilities` | 200, titre « Catalogue DataGrandEst » |
| GeoServer (WMS/WFS) | `https://www.datagrandest.fr/geoserver/ows` | 200, mais voir robots.txt (§4.2) |
| MapStore, mviewer, Superset | `/mapstore/`, `/mviewer/`, `/superset/` | 200 / 302 |
| Datafeeder (publication) | `/datafeeder/`, `/import/` | 302 vers `/login` |
| Serveur MCP | `/mcp` → `/mcp-server/mcp` | 401, OAuth (§2.2) |
| OGC API Records | `/ogc-api-records/` | 503 |
| Info de build GeoNetwork | `/geonetwork/srv/api/site/info/build` | 403 (version exacte inconnue) |

Les mentions légales [lu] indiquent : plateforme geOrchestra par **Camptocamp France SAS** (confirmé), site WordPress par ADIPSO, espace collaboratif par eXo Platform, hébergement SdV. La plateforme est portée en partenariat par l'État en région et la Région Grand Est.

Configuration publique du datahub (`/datahub/assets/configuration/default.toml`) [testé] :

```toml
[global]
geonetwork4_api_url = "/geonetwork/srv/api"
metadata_language = "fre"
[search]
advanced_filters = ['organization', 'format', 'topic', 'isSpatial']
[metadata-quality]
enabled = true
[editing]
new_record_standard = "iso19115-3"
```

### 1.2 L'endpoint principal : recherche Elasticsearch [testé]

`POST https://www.datagrandest.fr/geonetwork/srv/api/search/records/_search` avec un corps Elasticsearch standard (`query`, `aggs`, `from`, `size`, `sort`, `_source`). Pas de clé, pas d'authentification. Temps de réponse observés : 0,07 à 0,4 s pour une recherche, 0,8 à 3,6 s pour une page de 500 fiches.

Extrait d'une réponse (champs de contact retirés, ils contiennent des adresses e-mail nominatives) :

```json
{"hits":{"total":{"value":8316,"relation":"eq"},"hits":[{"_index":"gn-records",
 "_id":"epinal::surfaces-végétales-et-minérales","_source":{
  "resourceTitleObject":{"default":"Surfaces végétales et minérales"},
  "resourceAbstractObject":{"default":"Ce service recense les mobiliers de jeux pour enfants dans les communes de la C.A.E."},
  "tag":[{"default":"Aire de jeux"},{"default":"jeux"},{"default":"enfants"}],
  "format":["CSV","ZIP","GeoJSON","KML"],
  "resourceType":["dataset"], "isHarvested":"true", "qualityScore":62,
  "linkProtocol":["ESRI:REST","WWW:LINK:LANDING_PAGE"], "changeDate":"2015-05-07T09:46:53.502Z"}}]}}
```

(Cet exemple illustre au passage le problème : le titre parle de surfaces végétales, la description de jeux pour enfants.)

Autres endpoints utiles [testé] : `GET /geonetwork/srv/api/sources` (liste des moissonneurs et sous-portails), `GET /geonetwork/srv/api/groups`.

### 1.3 Contenu du catalogue [testé, agrégations ES sur les 8 316 fiches]

- **Types** : 6 842 `dataset`, 889 `staticMap`, 496 `map-static`, 26 `service`, 25 `interactiveMap`, 15 `series`, 10 `map`, 10 `nonGeographicDataset`, 2 autres.
- **Moissonnage** : 6 358 fiches moissonnées (76 %), 1 958 saisies localement. 33 sources ont au moins une fiche. Principales : Catalogue DataGrandEst (1 957), CEA (1 191), DDT 54 (753), DREAL Grand Est (589), DDT 10 (570), SIG Grande Région (404), data.strasbourg.eu (378), Observatoire des territoires ANCT (369).
- **Organisations** : 446 valeurs distinctes du champ `OrgForResourceObject` (estimation `cardinality` d'Elasticsearch, donc approximative).
- **Standards** : 7 009 fiches ISO 19139, 1 307 ISO 19115-3.
- **Formats déclarés** : Shapefile 2 711, PDF 1 382, CSV 981, GeoJSON 435, Excel 400, JSON 320, ZIP 296, KML 262, GeoPackage 60.
- **Thématiques ISO** (`cl_topic`, 19 valeurs) : planningCadastre 2 484, society 1 163, geoscientificInformation 1 042, intelligenceMilitary 1 021, environment 728, location 558, biota 534, transportation 497, boundaries 406, health 276…
- **Licences** : champ en texte libre, plus de 150 formulations. « Pas de restriction d'accès public selon INSPIRE » (3 124, ce n'est pas une licence), Licence Ouverte sous au moins six orthographes (≈ 650 au total), ODbL sous forme d'URL (217), une valeur HTML « CC-BY-SA » (57), « Conditions unknown » (24).
- **Langue** : 8 262 fiches en français.

À noter : environ 1 000 fiches sont des cartes historiques de la Première Guerre mondiale (thématique `intelligenceMilitary` : 1 021 fiches ; organisation « Archeologie Alsace » : 1 016), avec des titres quasi identiques. Elles pèsent dans les statistiques et peuvent polluer une recherche.

### 1.4 Où sont réellement les données [testé, calcul local]

Le catalogue décrit surtout des données hébergées **ailleurs**. Sur les 6 867 jeux, nombre de jeux ayant au moins un lien vers : geo-ide (État) 2 928, `www.datagrandest.fr` 1 727, data.strasbourg.eu 378, data.education.gouv.fr 313, ArcGIS ATMO Grand Est 312, data.mulhouse-alsace.fr 146.

- 3 172 jeux ont un lien WFS (dont 331 sur le GeoServer de DataGrandEst) ; 1 367 ont un lien CSV ou JSON.
- **65 % des jeux (4 463) sont « prévisualisables en principe »** (lien WFS, CSV ou JSON). Je n'ai testé aucun de ces liens : leur taux de disponibilité réel est **[non vérifié]**.

## 2. Ce qui existe déjà

### 2.1 Serveur MCP de data.gouv.fr [lu]

- Dépôt : `https://github.com/datagouv/datagouv-mcp`, licence **MIT**, Python, `uv`, ≈ 1 600 étoiles, actif (dernier push le 12/11/2026).
- SDK officiel (`mcp.server.fastmcp.FastMCP`), transport **Streamable HTTP uniquement**, `stateless_http=True`, protection DNS rebinding, endpoint `/health`. Serveur hébergé : `https://mcp.data.gouv.fr/mcp`.
- 10 outils : `search_datasets`, `get_dataset_info`, `list_dataset_resources`, `get_resource_info`, `query_resource_data`, `search_organizations`, `search_dataservices`, `get_dataservice_info`, `get_dataservice_openapi_spec`, `get_metrics`.
- **Réutilisable ?** Non comme base de code : chaque outil est calqué sur les API de data.gouv.fr (Main, Tabular, Metrics), qui n'existent pas ici. Oui comme **modèle de structure** (organisation `tools/`, `helpers/`, sécurité du transport, tests). Je n'ai lu que le README et `main.py`, pas le détail des outils.

### 2.2 MCP côté GeoNetwork et geOrchestra

| Projet | Source | Constat |
|---|---|---|
| Serveur MCP sur DataGrandEst | `https://www.datagrandest.fr/mcp-server/mcp` [testé] | 401 avec `WWW-Authenticate: Bearer`. Métadonnées OAuth publiques : serveur d'autorisation `/auth-server`, scopes `mcp.tools.read` et `mcp.tools.write`. Je n'ai pas cherché à m'authentifier. Contenu et outils inconnus. |
| « MCP GeoNetwork » de Camptocamp | présentation geOcom 2026 [lu] ; dépôt probable `github.com/fgravin/geonetwork_mcp` | TypeScript, stdio, 3 outils (`search_records`, `get_record`, `get_record_resources`), 2 commits, **aucun fichier de licence** donc non réutilisable en l'état. Le lien avec le serveur déployé sur DataGrandEst est **[non vérifié]**. |
| `eea/eea-geonetwork-mcp` | GitHub [lu] | MIT, TypeScript, ≈ 20 outils dont écriture, pour le catalogue de l'Agence européenne pour l'environnement. |
| `georoen/gdi-geonetwork-mcp` | GitHub [lu] | MIT, lecture seule, catalogue allemand GDI-DE. |
| `camptocamp/mcp-geoserver` | GitHub [lu] | GPL-2.0, actif. |
| `adriengout/mcp-mviewer-python` | GitHub [lu] | 10 outils, expérience GéoBretagne, sans licence. |
| Serveurs MCP CKAN (ondata, openascot…) | annuaires tiers [non vérifié] | Hors sujet : DataGrandEst n'est pas un CKAN. |

Aucun de ces serveurs n'est en Python avec le SDK officiel pour un GeoNetwork. Écrire le mien reste justifié pour **apprendre**, à condition de ne pas le présenter comme une première.

### 2.3 Outils publics de Camptocamp utilisés ici [lu]

- `geonetwork/geonetwork-ui` (GPL-2.0, actif) : c'est le datahub. La requête de recherche réelle est en §2.5.
- `georchestra/datafeeder` (GPL-3.0, actif) : module de publication. Une capture de la présentation geOcom montre un « Metadata Feeder » avec un bouton **« Générer avec l'IA »** (titre, description, généalogie, mots-clés par type, catalogue d'attributs). Le générateur de métadonnées est donc déjà prototypé côté prestataire.
- **ontrack** : R&D Camptocamp, deux agents LangGraph (assistant SIG conversationnel et « Discovery Agent » de catalogage automatique).

### 2.4 Le chatbot de DataGrandEst

Sources [lu] : webinaire DataGrandEst du 9 avril 2026 (« Chatbot IA » entre le site et la plateforme de données) et deux présentations de Florent Gravin (Camptocamp) à geOcom, Strasbourg, 10–12 juin 2026 :
`https://www.georchestra.org/public/geocom2026/presentations/02_02_gravin_ia_agentique.pdf` et `…/02_03_gravin_mcp_georchestra.pdf`.

Ce qu'elles disent du chatbot geOrchestra « (DataGrandEst & co.) » :
- il se présente comme « l'assistant d'aide à la recherche de données géographiques du DataHub de DataGrandEst » ;
- retours utilisateurs : « trop axé recherche » (retrouve des fiches, ne répond pas à la question), « pas de conversation », « pas de raisonnement » ;
- améliorations identifiées : graphe de raisonnement, approche agentique ;
- « reste à bâtir » : serveurs MCP standardisés, métadonnées pour lecteurs machines, **jeux d'évaluation métier**, modèles open-weight auto-hébergés.

**Test du chatbot : non réalisé.** Le 12/11/2026, aucun widget de chat n'apparaît sur `/site/` ni sur `/datahub/` (recherche du mot « chat » dans le HTML et dans l'arbre d'accessibilité), `/chat` renvoie 404 et `/chatbot` ne répond pas. Soit il n'est pas encore en ligne, soit il est réservé aux utilisateurs connectés. À demander aux contacts.

### 2.5 Baseline réelle : la recherche du portail [testé]

Requête exacte envoyée par le datahub (capturée dans le navigateur) :

```json
{"query_string":{"query":"pistes cyclables Strasbourg","default_operator":"AND",
 "fields":["resourceTitleObject.langfre^5","tag.langfre^4","resourceAbstractObject.langfre^3",
           "lineageObject.langfre^2","any.langfre","uuid"]}}
```

Rejouée telle quelle sur 13 questions (script `scripts/exploration/portal_search_baseline.py`) :

| Question | Résultats | Observation |
|---|---|---|
| pistes cyclables Strasbourg | 3 | Le bon jeu (« Aménagements cyclables au format BNAC ») est 2e, derrière « Itinéraires de viabilité hivernale » |
| Quelles données sur les pistes cyclables à Strasbourg | 2 | Fonctionne par chance (les mots vides sont filtrés) |
| aménagements cyclables Eurométropole | 4 | 1er résultat : Metz, pas Strasbourg (territoire ambigu) |
| vélo Strasbourg | 14 | Pertinent mais ne remonte pas le jeu BNAC dans les 5 premiers |
| mobilité douce | 8 | Ne trouve que les fiches contenant littéralement ces mots (Épinal, Thionville) ; rien sur Strasbourg, Metz, Nancy |
| zones inondables Bas-Rhin | 23 | 2e résultat hors sujet (propriétés foncières, prairies) |
| établissements scolaires Strasbourg | 12 | Bon |
| où sont les écoles à Strasbourg | **0** | Échec : « écoles » ≠ « établissements scolaires » et opérateur ET |
| qualité de l'air Nancy | 9 | Bon |
| bornes de recharge véhicules électriques | 17 | Bon, mais 5 territoires différents sans tri |
| occupation du sol Grand Est | 151 | Bon |
| ligne Maginot | 3 | 1 bon, 2 hors sujet (chiroptères, Ardennes) |
| prix de l'immobilier à Reims | 0 | Peut être un « rien n'existe » correct, non vérifié |

Limite : 13 questions choisies par moi, sans vérité terrain. C'est une illustration, pas une mesure. La mesure viendra du jeu de référence.

## 3. Qualité des métadonnées

### 3.1 Méthode

- **Population** : tout le catalogue (8 316 fiches non-modèles, `isTemplate:n`), téléchargé le 12/11/2026 en 17 pages de 500 triées par `uuid`. Les chiffres ci-dessous portent sur les **6 867 jeux de données** (`resourceType` ∈ `dataset`, `series`, `nonGeographicDataset`). Pas d'échantillonnage, donc pas d'intervalle de confiance à calculer.
- **Source** : les champs de l'index Elasticsearch de GeoNetwork, pas le XML ISO d'origine. Un champ absent de l'index est compté comme absent.
- **Texte** : balises HTML retirées et espaces normalisés avant de mesurer les longueurs.
- Scripts : `scripts/exploration/fetch_catalog.py` puis `metadata_quality.py`.

### 3.2 Résultats (jeux de données, n = 6 867)

| Critère | Définition | Nombre | Part |
|---|---|---|---|
| Sans description | `resourceAbstractObject` vide | 1 | 0,0 % |
| Description < 50 caractères | après nettoyage | 337 | 4,9 % |
| Description < 100 caractères | | 655 | 9,5 % |
| Description identique au titre | | 84 | 1,2 % |
| Description contenant du HTML | balises `p`, `div`, `span`, `br`, `a` | 1 205 | 17,5 % |
| Sans mots-clés | `tag` vide | 96 | 1,4 % |
| 2 mots-clés ou moins | | 588 | 8,6 % |
| **Sans thématique** | `cl_topic` absent | 1 218 | **17,7 %** |
| Titre technique ou ambigu | heuristique ci-dessous | 425 | 6,2 % |
| Sans emprise géographique | `geom` absent | 984 | 14,3 % |
| Sans aucune date de ressource | `resourceDate` absent | 1 210 | 17,6 % |
| Sans date de révision | pas de date de type `revision` | 3 264 | 47,5 % |
| Sans fréquence de mise à jour | | 3 845 | 56,0 % |
| Sans généalogie | `lineageObject` vide | 1 489 | 21,7 % |
| Sans licence ni contrainte | | 1 203 | 17,5 % |
| Sans aucun lien | `linkUrl` vide | 108 | 1,6 % |
| Au moins un défaut parmi : description < 50, sans mots-clés, sans thématique | | 1 551 | 22,6 % |

Médianes : description 406 caractères, 7 mots-clés. 412 jeux partagent leur titre exact avec au moins un autre jeu (153 titres en double).

**Titre technique** : heuristique par expressions régulières, donc approximative. Motifs : tiret bas entre deux caractères (392), titre sans espace (87), moins de 10 caractères (16), mot `v2`/`final`/`copie`/`test`/`tmp` (11), extension de fichier (9). Faux positifs constatés à la lecture d'un échantillon : noms latins d'espèces (`Phengaris_arion`), « Base SIRENE V3 », « TCF (Test de connaissance du français) ». Vrais positifs typiques : `N_RPG_ANONYME_S_067_2015`, `CSV_Routes_57_2024_secteurs`, `futurpro_familles_metiers`, `dataviz2`, `X_PMV_P_067.shp`. Le chiffre de 6,2 % est un ordre de grandeur, à affiner par annotation manuelle.

**Date de mise à jour** : toutes les fiches ont une date de modification de la *fiche* (`changeDate`), mais pour une fiche moissonnée c'est la date de moissonnage. La date de mise à jour de la *donnée* est celle de type `revision`, absente dans 47,5 % des cas.

### 3.3 Les défauts sont concentrés par source

| Source (jeux) | Desc. < 50 | Sans thématique | Titre technique | Sans emprise |
|---|---|---|---|---|
| Catalogue DataGrandEst (1 846) | 3 % | 5 % | 5 % | 6 % |
| DREAL Grand Est (589) | 1 % | 0 % | **36 %** | 0 % |
| DDT 67 (203) | **51 %** | 0 % | 23 % | 0 % |
| DDT 51 (209) | 19 % | 0 % | 11 % | 0 % |
| data.strasbourg.eu (378) | 2 % | **61 %** | 1 % | 33 % |
| data.education.gouv.fr (313) | 7 % | **100 %** | 4 % | 100 % |
| ATMO Grand Est ArcGIS (277) | 0 % | **100 %** | 0 % | 100 % |
| data.mulhouse-alsace.fr (146) | 1 % | **88 %** | 1 % | 32 % |
| Épinal ArcGIS (52) | 19 % | 100 % | 6 % | 100 % |
| Euro-Métropole de Metz (29) | 34 % | 59 % | 0 % | 0 % |

Lecture : les portails open data moissonnés (Opendatasoft, ArcGIS Hub) arrivent sans thématique ISO ni emprise ; les catalogues de l'État arrivent avec des titres codés (nomenclature COVADIS) et parfois le nom de table en guise de description (`N_DIGUE_L_R42`).

### 3.4 Conséquences pour le projet

- L'affirmation « beaucoup de jeux sont mal décrits » est **vraie mais à nuancer** : presque tout a une description et des mots-clés. Les vrais manques sont la thématique, l'emprise, la fraîcheur, les licences non normalisées et les titres codés.
- **L'attribution de thématique est la tâche la mieux posée** pour l'assistant de métadonnées : 1 218 jeux à traiter et 5 649 jeux déjà étiquetés pour mesurer l'accord (dont 1 405 avec plusieurs thématiques : l'évaluation devra être multi-étiquettes).
- **Quelle « liste officielle » de thématiques ?** Trois candidats : les 19 catégories ISO (`cl_topic`, filtre « topic » du datahub, sous-portails `topic-*`) ; le thésaurus « DataGrandEst – Thèmes opendata version 1.0 » (présent sur 333 jeux seulement) ; les thématiques du site éditorial (au moins 14 : mobilité et transports, biodiversité, eau et hydrographie…). Recommandation : ISO pour la mesure (vérité terrain abondante), et demander aux contacts laquelle ils veulent.
- Le portail calcule déjà un score de qualité (`qualityScore`, médiane 87). Il faudra dire ce que mes indicateurs ajoutent.

## 4. Faisabilité

### 4.1 Faisable d'ici mi-décembre (5 semaines, ≈ 45 h à confirmer)

| Brique | Avis | Raison |
|---|---|---|
| Client API avec cache | Facile | API stable, rapide, sans clé |
| Serveur MCP Python, 4–5 outils | Faisable | `search_datasets`, `get_dataset_metadata`, `list_themes`, `list_organizations` reposent sur un seul endpoint |
| BM25 et embeddings sur 6 867 jeux | Faisable | Corpus petit, tient en mémoire sur 8 Go |
| Graphe jeux / thématiques / organisations / territoires | Faisable après le MVP | Le nœud « territoire » demande un travail d'extraction : l'emprise manque sur 14 % des jeux et les lieux sont en texte libre |
| Jeu de référence de 60 questions | Faisable mais c'est le chemin critique | Dépend de ton temps de relecture, pas du code |
| Agent de recherche | Faisable | À condition que l'évaluation existe avant |

### 4.2 Points durs et limites

- **`preview_dataset` est la brique la plus risquée.** Les données sont chez des tiers (geo-ide surtout), dans des formats hétérogènes, et aucun lien n'a été testé. De plus le `robots.txt` du portail contient `User-agent: * / Disallow: /geoserver/`. Un appel WFS déclenché par un utilisateur n'est pas du crawl, mais c'est une zone grise. **J'ai effectué un appel `GetCapabilities` WFS (1 Mo) avant d'avoir lu cette ligne ; je n'en ai pas fait d'autre.** Recommandation : sortir `preview_dataset` du MVP, ou le limiter aux liens CSV/JSON des portails open data tiers, après lecture de leurs conditions.
- **Conditions d'utilisation de l'API : il n'y en a pas de publiées.** Les mentions légales ne parlent ni de l'API, ni de quotas, ni de collecte automatisée. Elles réservent les droits sur les contenus du *site*. Aucun en-tête de limitation de débit observé. Charge de cette exploration : environ 60 requêtes, espacées d'une à deux secondes. À faire : demander aux contacts si un usage de ce type leur convient et s'il existe une limite à respecter.
- **Redistribution du catalogue** : les fiches ont des licences variées et souvent floues. Recommandation : ne pas versionner l'extraction (58 Mo), seulement les scripts et les statistiques agrégées ; le jeu de référence ne contient que des identifiants et des titres.
- **Données personnelles** : les fiches contiennent des noms et adresses e-mail de contacts. Je les ai exclus de l'extraction ; ils doivent rester hors des index, des traces et des prompts.
- **Injection de prompt** : 17,5 % des descriptions contiennent du HTML brut venant de portails tiers. Le texte non fiable est donc bien réel, pas théorique.
- **Vérité terrain mouvante** : le catalogue est moissonné en continu (2 324 fiches modifiées en 2026). Il faut figer une extraction datée pour l'évaluation.
- **LLM local sur 8 Go de RAM sans GPU** : un modèle 7B quantifié sera lent et à la limite de la mémoire. À trancher en Phase 2 avec des mesures, pas maintenant.

### 4.3 Recommandations pour la Phase 2

1. **Garder DataGrandEst comme terrain.** Alternative : data.gouv.fr filtré sur le Grand Est, inutile ici.
2. **Recentrer le récit sur l'évaluation.** Le MCP et l'agent sont les moyens, le banc d'essai est le résultat. Alternative : viser un MCP plus riche que celui de Camptocamp, ce qui te mettrait en concurrence frontale avec le prestataire.
3. **Écrire le serveur MCP en Python avec le SDK officiel, sans reprendre de code existant**, en s'inspirant de la structure de `datagouv-mcp` (MIT, à citer). Alternative : utiliser le MCP GeoNetwork de Camptocamp, impossible sans licence.
4. **Assistant de métadonnées centré sur thématique, titre et description**, évalué sur les sources où le défaut est mesuré (DDT 67, DREAL, portails open data).
5. **Construire le jeu de référence à partir de besoins, pas de titres** : les exemples du chatbot (« ligne Maginot », « établissements scolaires à Strasbourg », « risques d'inondation dans le Bas-Rhin »), les 14 thématiques du site, les cas d'usage publiés, puis recherche des jeux attendus par plusieurs méthodes.

### 4.4 Questions à poser aux contacts (à toi de décider si et quand)

- Le chatbot est-il accessible quelque part pour un test ?
- Le serveur `/mcp-server/mcp` est-il celui de Camptocamp, et sera-t-il ouvert au public ?
- Un usage en lecture de l'API de recherche avec cache local leur pose-t-il un problème ?
- Quelle liste de thématiques fait référence pour eux ?

## 5. Reproduire

```bash
cd scripts/exploration
python fetch_catalog.py            # 17 requêtes, 2 s d'intervalle, écrit pages/*.json
python metadata_quality.py         # tableaux du §3
python links_and_themes.py         # §1.4
python portal_search_baseline.py   # §2.5
```

Les scripts ont besoin de `sources.json` (`GET /geonetwork/srv/api/sources`) dans le même dossier pour les noms de sources. L'extraction du 12/11/2026 est sur ma machine, hors dépôt.

## 6. Sources

- DataGrandEst : https://www.datagrandest.fr/site/ , mentions légales, datahub, webinaire du 9 avril 2026 (`/public/webinar/webinar_20260409/01_DGE_Webinaire_20260409_Portail-SiteInternet.pdf`).
- F. Gravin (Camptocamp), geOcom 2026 : « L'IA agentique au service des données (géospatiales) » et « Un MCP dans votre plateforme geOrchestra », https://www.georchestra.org/public/geocom2026/presentations/
- https://github.com/datagouv/datagouv-mcp (MIT)
- https://github.com/fgravin/geonetwork_mcp , https://github.com/eea/eea-geonetwork-mcp , https://github.com/georoen/gdi-geonetwork-mcp , https://github.com/camptocamp/mcp-geoserver , https://github.com/adriengout/mcp-mviewer-python
- https://github.com/geonetwork/geonetwork-ui , https://github.com/georchestra/datafeeder
