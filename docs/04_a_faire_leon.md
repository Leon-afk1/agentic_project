# Ce que tu as à faire, et comment

Mis à jour le 16/11/2026. Ce document liste tout ce qui dépend de toi, dans l'ordre conseillé, avec les commandes exactes. La grille de notation détaillée est dans `docs/03_guide_jugement.md` : lis-la une fois avant de commencer.

## Vue d'ensemble

| # | Tâche | Temps | Ce que ça débloque |
|---|---|---|---|
| 1 | Juger 100 candidats du jeu de développement, à l'aveugle | 30 min | La mesure d'accord entre toi et le modèle (kappa) |
| 2 | Relire le reste des notes du modèle sur le jeu de développement | 45 min | Un jeu de développement vérifié par un humain |
| 3 | Juger le jeu de test | 3 h à 3 h 30 | Les seuls chiffres publiables du projet |
| 4 | Relire le code des parties que tu n'as pas encore lues | 2 h | Le critère du MVP « je comprends chaque partie » |
| 5 | Quelques décisions en attente | 15 min | La suite du plan |

Total : 7 h environ. Les tâches 1 à 3 se font par sessions d'une heure au plus ; tout est sauvegardé après chaque note.

Avant de commencer, mets ton dépôt à jour et vérifie que tout marche :

```bash
git pull
uv sync --extra llm --extra embeddings
uv run pytest
```

## Pourquoi deux façons de juger

Il existe deux modes, et ils ne produisent pas la même chose.

- **À l'aveugle** (mode normal) : tu ne vois pas la note du modèle. C'est le seul mode qui permet de mesurer un accord, parce que ton jugement est indépendant.
- **Relecture** (option `--review`) : la note du modèle est affichée, tu appuies sur Entrée pour l'accepter ou tu tapes une autre note. C'est deux à trois fois plus rapide, mais tu es influencé par ce que tu vois. Le résultat est « vérifié par un humain », pas « jugé indépendamment ».

D'où l'ordre : d'abord un échantillon à l'aveugle pour mesurer l'accord, ensuite la relecture pour le reste. Faire l'inverse rendrait la mesure d'accord impossible.

## Tâche 1 : 100 jugements à l'aveugle sur le jeu de développement

```bash
uv run oabench golden judge --split dev
```

L'outil affiche la question, puis pour chaque candidat le titre, le producteur, les dates, les formats, 400 caractères de description et le lien vers la fiche. Tu tapes :

| Touche | Sens |
|---|---|
| `2` | pertinent : tu donnerais ce jeu à la personne, tel quel |
| `1` | partiellement pertinent : aide, mais mauvais territoire englobant, mauvaise période, ou simple complément |
| `0` | non pertinent |
| `m` | voir la description complète |
| `u` | annuler la note précédente |
| `s` | passer |
| `q` | enregistrer et quitter |

Le compteur affiche « candidate N/358 ». **Arrête-toi autour de 100** (tape `q`), en finissant la question en cours. Tes notes vont dans `data/golden/judgments.jsonl`.

Puis mesure ton accord avec le modèle :

```bash
uv run oabench golden agreement judgments.jsonl judgments_llm_claude.jsonl
```

Tu obtiens l'accord brut, le kappa de Cohen pondéré et la matrice de confusion (lignes : toi ; colonnes : le modèle).

**Comment lire le kappa**, selon la règle écrite dans le cadrage avant toute mesure :

| Kappa | Lecture | Suite |
|---|---|---|
| 0,8 et plus | accord très fort | les notes du modèle peuvent servir de référence, en le disant |
| 0,6 à 0,8 | accord correct | utilisables, avec l'accord publié à côté de chaque tableau |
| moins de 0,6 | accord insuffisant | les notes du modèle ne servent pas de référence : il faut tes jugements partout |

Regarde aussi **où** vous divergez dans la matrice. Un désaccord concentré entre 1 et 2 est moins grave qu'un désaccord entre 0 et 2. Si tu vois une règle que le modèle applique autrement que toi, note-la dans le journal de `docs/03_guide_jugement.md` : trois règles y sont déjà inscrites et attendent ton avis.

Envoie-moi le résultat de la commande : c'est lui qui décide de la suite.

## Tâche 2 : relire le reste du jeu de développement

Seulement après la tâche 1.

```bash
uv run oabench golden judge --split dev --review judgments_llm_claude.jsonl
```

La ligne `MODEL graded 1 (partially relevant). Enter = accept.` apparaît sous chaque fiche. Entrée accepte ; `0`, `1` ou `2` corrige. Les 100 candidats déjà jugés ne reviennent pas.

À la fin, le jeu de développement entier est dans `judgments.jsonl`. Pour relancer le tableau sur tes notes :

```bash
uv run oabench eval --split dev --agent
```

Sans l'option `--judgments`, c'est ton fichier qui est utilisé. Compare avec `results/dev_table_llm_judged.txt` : si les conclusions changent, c'est un résultat à écrire.

## Tâche 3 : juger le jeu de test

C'est la tâche la plus importante. Le jeu de test (44 questions, 790 candidats) n'a été regardé par personne, et il doit le rester jusqu'à ce que l'agent soit figé.

**À ne pas faire avant** : attends que je te dise que l'agent est figé (plus aucun réglage des consignes ni des garde-fous). Je te donnerai alors l'identifiant du commit. Sinon tu jugerais des candidats auxquels s'ajouteraient ensuite ceux d'un agent modifié.

**Déroulé** :

1. Je fige l'agent et je lance toutes les configurations sur le jeu de test, sans regarder les résultats question par question.
2. J'ajoute à la liste des candidats les fiches citées par l'agent ou l'hybride qui n'y sont pas encore.
3. Tu juges, à l'aveugle, sans notes du modèle :

```bash
uv run oabench golden judge --split test
```

4. Tu lances l'évaluation finale, une seule fois :

```bash
uv run oabench eval --split test --agent
```

Deux choix possibles pour l'étape 3, à trancher avec le kappa de la tâche 1 :

- **Tout juger toi-même** (3 h à 3 h 30) : c'est le mieux, et ce que demande ton cahier des charges.
- **Si le kappa est d'au moins 0,6** : je juge le jeu de test une fois l'agent figé, et tu relis en mode `--review` (1 h 15 environ). Les chiffres sont alors « jugés par un modèle, vérifiés par un humain, accord mesuré de X ».

**Ne relance pas l'évaluation sur le test après avoir vu le résultat.** Si quelque chose te paraît faux, on corrige sur le jeu de développement et on l'écrit ; le chiffre du test reste celui de la première exécution.

## Tâche 4 : relire le code

Le critère du MVP dit que tu as relu et que tu comprends chaque partie. Voici l'ordre que je conseille, avec une question par fichier. Si tu sais y répondre, tu peux en parler en entretien.

| Fichier | Question à te poser |
|---|---|
| `src/oabench/portal/http.py` | Pourquoi un long temps d'inactivité ne donne-t-il pas droit à une rafale d'appels ? |
| `src/oabench/snapshot/records.py` | Que deviennent une adresse e-mail et une balise HTML présentes dans une description ? |
| `src/oabench/retrieval/bm25.py` | Que font `k1` et `b` ? Pourquoi l'idf ne devient-il jamais négatif ? |
| `src/oabench/retrieval/engines.py` | Pourquoi la fusion de rangs n'a-t-elle pas besoin de calibrer les scores ? |
| `src/oabench/mcp_server/server.py` | Qu'est-ce qui empêche un outil de renvoyer 50 000 caractères ? |
| `src/oabench/eval/metrics.py` | Pourquoi le rappel@5 est-il plafonné ? Que resample le bootstrap ? |
| `src/oabench/eval/abstention.py` | Pourquoi publier deux taux et pas un seul ? |
| `src/oabench/eval/agreement.py` | En quoi le kappa pondéré diffère-t-il de l'accord brut ? |
| `src/oabench/agent/loop.py` | Pour chacune des dix raisons d'arrêt, quelle ligne la déclenche ? |
| `src/oabench/agent/answer.py` | Pourquoi finir par un appel d'outil plutôt que par du texte ? |
| `src/oabench/llm/wrappers.py` | Qu'est-ce qui entre dans la clé du cache, et pourquoi pas l'objet `raw` ? |

Si une réponse ne te vient pas, demande-moi : c'est le but. Je peux aussi te proposer de réécrire toi-même une partie (le BM25 ou le calcul du kappa s'y prêtent bien) et relire ton code.

## Tâche 5 : décisions en attente

| Décision | Recommandation | Pourquoi |
|---|---|---|
| Qui juge le jeu de test | Toi entièrement, sinon modèle puis relecture si kappa ≥ 0,6 | Voir la tâche 3 |
| Modèle juge pour la justesse des réponses (version complète) | Réessayer une clé Mistral ; à défaut un autre fournisseur gratuit de modèles ouverts | Le juge doit venir d'une autre famille que Gemini |
| Extrait de 200 fiches versionné pour la CI | Oui, limité aux fiches sous Licence Ouverte ou ODbL, avec un fichier d'attribution | La non-régression en CI en a besoin ; c'est une publication, donc ta décision |
| Nom du dépôt GitHub | À décider avant la mise en valeur | `agentic_project` est neutre mais peu parlant |
| Démo en ligne | Rien pour l'instant | Coût et charge sur le portail ; une vidéo de 2 minutes suffit au MVP |

## État de l'agent au 16/11/2026 : pas encore figé

Les quatre améliorations sont appliquées (agent v2) et l'évaluation du jeu de développement est refaite. Deux choses restent à faire avant de figer l'agent, et elles ne dépendent pas de toi mais du quota Gemini :

1. Relancer les séries de robustesse en v2 (elles reprennent où elles se sont arrêtées) :

```bash
uv run oabench robustness ambiguous
uv run oabench robustness offtopic
uv run oabench robustness outage
uv run oabench robustness injection
```

2. Décider d'une v3 : les deux cas d'ambiguïté passés en v2 échouent encore. La piste est de faire le tri (recherche, trop vague, hors sujet) dans l'étape de plan. Je te proposerai le changement avec sa mesure sur le jeu de développement.

**Tant que je ne t'ai pas annoncé « agent figé » avec un identifiant de commit, ne juge pas le jeu de test.** Les tâches 1, 2 et 4 de ce document peuvent se faire dès maintenant.

## Ce qui ne dépend pas de toi

Je continue pendant ce temps : comparaison de l'agent selon le moteur de recherche, second modèle, tests de robustesse (hors sujet, ambiguïté, injection de prompt, panne), le tout sur le jeu de développement uniquement.

## En cas de problème

| Symptôme | Cause probable | Quoi faire |
|---|---|---|
| `no snapshot in data/snapshot` | L'extraction n'existe pas sur cette machine | `uv run oabench snapshot` (17 requêtes, une par seconde) |
| `GEMINI_API_KEY is not set` | Fichier `.env` absent | Copier `.env.example` en `.env` et y coller la clé |
| L'outil de jugement n'affiche rien à juger | Tout est déjà jugé pour ce jeu | `uv run oabench golden stats` pour vérifier |
| Erreurs 503 pendant une évaluation de l'agent | Service Google surchargé | Relancer plus tard : le lot reprend où il s'est arrêté |
| Erreur 429 | Quota journalier atteint (500 requêtes) | Relancer le lendemain |
| Tu t'es trompé de note | | `u` juste après ; sinon édite `data/golden/judgments.jsonl` à la main |

## Ce qu'il ne faut jamais faire

- Regarder ou essayer une question de test avant l'évaluation finale. `oabench ask` les refuse pour cette raison.
- Mélanger tes notes et celles du modèle dans un même fichier.
- Présenter un chiffre jugé par le modèle comme une vérité terrain.
- Commiter `.env`.
