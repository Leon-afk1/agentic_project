# Questions à poser aux équipes DataGrandEst

Liste tenue à jour pour un futur appel. Rien n'a été envoyé. Le projet avance sans ces réponses ; la colonne de droite dit ce que chaque réponse changerait.

| # | Question | Hypothèse retenue en attendant | Ce que la réponse change |
|---|---|---|---|
| 1 | Le chatbot du datahub est-il accessible quelque part pour un test (public, compte, préproduction) ? | Non accessible. La baseline « réelle » est la recherche du portail. | Ajouter le chatbot comme cinquième système du comparatif. |
| 2 | Le serveur `https://www.datagrandest.fr/mcp-server/mcp` (OAuth) est-il le « MCP GeoNetwork » de Camptocamp ? Sera-t-il ouvert au public, et avec quels outils ? | Inconnu. J'écris mon propre serveur en lecture seule. | Brancher mon agent sur leur serveur pour comparer, éviter les doublons. |
| 3 | Un usage en lecture de l'API de recherche GeoNetwork (cache local, une extraction complète par mois environ, User-Agent identifiable) vous convient-il ? Y a-t-il une limite de débit à respecter ? | Oui, avec 1 requête par seconde au plus et un cache. | Fréquence de rafraîchissement, possibilité d'une démo en ligne. |
| 4 | Quelle liste de thématiques fait référence : les 19 catégories ISO, le thésaurus « DataGrandEst – Thèmes opendata v1.0 », ou les thématiques du site ? | Catégories ISO (décision du 12/11/2026). | Ajouter une seconde sortie de classification. |
| 5 | Puis-je publier dans un dépôt public un jeu de questions avec les identifiants et titres des fiches attendues ? Et une extraction du catalogue ? | Identifiants et titres : oui. Extraction complète : non versionnée. | Reproductibilité du banc d'essai par des tiers. |
| 6 | Avez-vous des questions réelles d'utilisateurs (journaux de recherche anonymisés, demandes reçues par le support) ? | Non. Questions construites à partir de sources publiques. | Un jeu de référence plus réaliste. C'est ce qui aurait le plus de valeur. |
| 7 | Pour le générateur de métadonnées, quels champs vous posent le plus de problèmes en pratique ? | Thématique, titre, description (d'après mes mesures). | Priorités de l'assistant de métadonnées. |
| 8 | Qu'est-ce qui vous serait utile dans un retour d'évaluation : un tableau de résultats, la liste des fiches à corriger par source, autre chose ? | Note de 2 pages avec les deux. | Format du livrable de la Phase 4. |
