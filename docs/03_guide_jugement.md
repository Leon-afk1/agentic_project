# Guide de jugement des candidats

Ce guide décrit comment noter les fiches candidates du jeu de référence. Il sert deux fois : pour le faire, et pour expliquer plus tard comment les chiffres ont été obtenus.

## Où on en est

- 66 questions validées, découpage figé le 15/11/2026 : 22 de développement, 44 de test.
- 1 146 candidats à juger : 356 pour les questions de développement, 790 pour celles de test.
- Les candidats viennent des 10 premiers résultats de quatre moteurs (recherche du portail, portail en mots-clés, BM25, embeddings), fusionnés par question.

## Lancer une session

```bash
uv run oabench golden judge
```

L'outil reprend où tu t'es arrêté. Chaque note est enregistrée immédiatement dans `data/golden/judgments.jsonl` : tu peux quitter à tout moment.

Pour voir ce qu'il reste :

```bash
uv run oabench golden stats
```

## Ce que l'outil affiche

Pour chaque candidat : la question, puis le titre de la fiche, son producteur, ses dates, ses formats, les 400 premiers caractères de sa description et le lien vers sa page sur le portail. Un compteur indique la progression.

## Les touches

| Touche | Sens |
|---|---|
| `2` | pertinent |
| `1` | partiellement pertinent |
| `0` | non pertinent |
| `m` | afficher la description complète |
| `u` | annuler la note précédente |
| `s` | passer ce candidat (il reviendra à la prochaine session) |
| `q` | enregistrer et quitter |

## La grille

Pose-toi une seule question : **donnerais-je ce jeu de données à la personne qui pose la question ?**

**2, pertinent.** Oui, tel quel. Le sujet, le territoire et la période correspondent. Exemple : pour « pistes cyclables à Strasbourg », les aménagements cyclables de l'Eurométropole de Strasbourg.

**1, partiellement pertinent.** Le jeu aide, mais ne suffit pas ou ne correspond pas exactement. Cas typiques :
- bon sujet, territoire voisin ou plus large sans détail local (les véloroutes du Rhin supérieur pour une question sur Strasbourg) ;
- bon sujet, mauvaise période (un millésime 2008 quand on demande l'évolution récente) ;
- donnée de contexte utile seulement en complément (le filaire des voies pour une question sur les pistes cyclables).

**0, non pertinent.** Tout le reste, y compris un jeu qui partage des mots avec la question sans y répondre (« eau douce » pour « mobilité douce »).

## Règles pour rester cohérent

1. **Juge la fiche par rapport à la question, pas par rapport aux autres candidats.** Plusieurs fiches peuvent valoir 2 pour une même question, et aucune peut ne le valoir.
2. **Le territoire compte.** Un jeu sur Mulhouse ne vaut pas 2 pour une question sur Strasbourg, même si le sujet est exact.
3. **Juge ce que la fiche dit, pas ce que tu supposes.** Si la description ne permet pas de savoir, ouvre la description complète (`m`) ou la page ; si le doute persiste, mets 1.
4. **Un jeu national ou régional qui contient le territoire demandé vaut 2** s'il descend au niveau de détail demandé (par commune, par exemple), sinon 1.
5. **Les doublons se notent pareil.** Deux fiches décrivant la même donnée reçoivent la même note.
6. **En cas d'hésitation entre 1 et 2, mets 1.** Le chiffre principal ne compte que les 2 : mieux vaut un 2 sûr.
7. **Ne cherche pas à deviner quel moteur a trouvé la fiche.** L'ordre est mélangé exprès et l'outil ne l'affiche pas.

## Questions « sans réponse »

Douze questions sont supposées sans réponse dans le catalogue (par exemple « prix de l'immobilier à Reims » ou « pistes cyclables à Bordeaux »). Tu n'as rien de spécial à faire : si aucun candidat ne reçoit 2, la question sera comptée comme sans réponse. L'inverse est vrai aussi : si tu trouves un 2 pour une question supposée sans réponse, elle redevient une question ordinaire, et c'est très bien.

## Organisation conseillée

- **Les questions de développement passent en premier** (356 candidats, environ 1 h 30 à 15 secondes par candidat). Dès qu'elles sont jugées, le premier tableau sort :

```bash
uv run oabench eval --split dev
```

- **Ne lance pas `oabench eval --split test` avant la fin.** Le jeu de test ne doit servir qu'une fois les réglages terminés ; le regarder plus tôt fausserait la mesure.
- Fais des sessions d'une heure au plus. La fatigue rend les notes moins stables.
- Si tu changes d'avis sur la grille en cours de route, note-le ici, dans la section ci-dessous, plutôt que de corriger en silence.

## Jugements déjà faits par un modèle

Les 22 questions de développement ont été jugées par Claude le 16/11/2026, dans `data/golden/judgments_llm_claude.jsonl`. L'outil ne te montre pas ces notes : tes jugements restent indépendants. Pour mesurer ton accord avec le modèle après avoir jugé une partie des mêmes candidats :

```bash
uv run oabench golden agreement judgments.jsonl judgments_llm_claude.jsonl
```

Une centaine de jugements suffit pour une première mesure. Sous un kappa de 0,6, les jugements du modèle ne doivent pas servir de référence.

## Limites connues, à écrire dans le README

- **Un seul annotateur.** Parade prévue : réannoter 15 questions deux semaines plus tard et mesurer l'accord avec toi-même.
- **Fiches jamais jugées.** Un jeu pertinent qu'aucun des quatre moteurs n'a remonté n'est pas dans la liste. Quand un nouveau système (hybride, agent) sera évalué, ses 5 premiers résultats non jugés seront ajoutés, et tu les jugeras avant le calcul.
- **Jugement sur la fiche, pas sur la donnée.** Aucun fichier n'est téléchargé : une fiche bien écrite sur une donnée médiocre obtient 2.

## Journal des décisions de jugement

À remplir au fil de l'eau (date, cas rencontré, règle retenue).

| Date | Cas | Règle retenue |
|---|---|---|
| 16/11/2026 | Bon sujet sur un territoire sans rapport avec celui demandé (les arbres de Mulhouse pour une question sur Strasbourg). Rencontré par Claude en jugeant le jeu de développement. | 0. La note 1 est réservée au territoire qui recouvre ou englobe celui demandé. À confirmer ou corriger par Léon. |
| 16/11/2026 | Donnée régionale découpée en fiches départementales (réseau Fluo, occupation du sol) pour une question régionale. | Chaque fiche départementale reçoit 2 : c'est la bonne donnée, publiée en plusieurs morceaux. |
| 16/11/2026 | Question en deux parties (lignes et gares ; surface et essences) et fiche qui ne répond qu'à une partie. | 2 si la fiche est la référence pour sa partie, sinon 1. |
