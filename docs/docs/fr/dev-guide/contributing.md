# Contribuer

- Les PR doivent inclure les tests unitaires nécessaires.
- Les nouveaux scripts suivent les deux étapes de [« Ajouter un script »](./add-script.md), maintenant la séparation entre entrée légère et logique métier.
- Les capacités partagées sont placées dans `lib/{domaine}.py`, réutilisables entre les commandes, évitant de dupliquer le code dans les entrées légères ou les commandes individuelles.
- Les opérations Git utilisent le `git` standard, évitant les enveloppes interactives ; avant l'automatisation, les vérifications de propreté de l'arbre de travail et les retours sont obligatoires.
