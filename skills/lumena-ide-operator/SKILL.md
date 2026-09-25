---
name: lumena-ide-operator
description: "Pilote Lumena IDE depuis le chat ou une mission : connexion, instance et workspace, lecture de buffers, navigation, modifications bornées, reprise et preuves. À utiliser quand l'utilisateur parle explicitement de Lumena IDE, de son éditeur, d'un buffer ou onglet, ou d'une instance IDE ; pas pour une simple question de code sans action dans l'IDE."
keywords: [lumena ide, ide, éditeur de code, editor, workspace ide, buffer non sauvegardé, onglet, pont ide, reconnecter ide, nouvelle instance ide]
applyTo: [ide, éditeur, editor, lumena_ide, ide_launch]
license: Lumena - usage interne
---

# Lumena IDE — doctrine d'utilisation

Les outils et le catalogue négocié donnent la capacité réelle. Ce skill choisit la bonne
voie pour l'utiliser ; il n'ajoute aucun droit et ne remplace aucune politique.

## Source de vérité

- Utilise uniquement les outils IDE réellement présents dans le tour courant : la façade
  `lumena_ide`, `ide_launch`, les outils natifs `ide_*` et les commandes négociées `ide__*`
  lorsqu'elles sont exposées.
- Le catalogue vivant, la session authentifiée, le workspace annoncé et la sémantique
  retournée par l'outil font foi. N'invente jamais un identifiant de commande.
- « ton IDE », « ton éditeur » ou « l'IDE de Lumena » désigne Lumena IDE. N'ouvre VS Code
  que si l'utilisateur le nomme explicitement.

## Choisir la voie

### Depuis le chat

Utilise l'instance de l'utilisateur pour observer et naviguer : état du pont, workspace,
fichiers, problèmes, tests, terminal, onglets, sélection et position du curseur. Pour lire
un changement non sauvegardé, préfère la commande négociée de contenu d'éditeur au fichier
sur disque.

Une demande de modification de code passe par le rail projet, CodeAgent ou mission prévu
par Lumena. Ce rail ouvre et cible une instance dédiée lorsque la politique l'exige. Ne
transforme jamais une lecture autorisée en permission d'écrire.

### Dans une mission

Travaille uniquement dans l'instance dédiée à la mission et dans son workspace canonique.
Vérifie l'identité de mission et la connexion ciblée avant la première mutation. Ne route
jamais une commande vers la fenêtre personnelle de l'utilisateur comme solution de repli.

## Connexion, instance et reprise

1. Observe l'état réel avant d'annoncer que l'IDE est connecté ou prêt.
2. Réutilise une instance qui correspond au workspace demandé. Lance une nouvelle instance
   seulement si aucune instance compatible n'existe ou si l'utilisateur en demande une.
3. Après une coupure, reconnecte ou reprends par les outils prévus, puis relis l'état.
4. Reprends la même opération depuis sa dernière preuve connue. Avant une nouvelle mutation,
   vérifie si la précédente a déjà produit son effet pour éviter un doublon.

Un accusé d'envoi, un lancement de processus ou une commande acceptée ne prouve pas que
l'effet demandé est terminé.

## Refus et erreurs

- Respecte le refus, la confirmation, le périmètre et la classification d'effet retournés.
- Ne contourne jamais un refus IDE avec PowerShell, VS Code, un outil fichier natif, un MCP
  ou une autre session.
- Utilise la cause et le geste de réparation fournis dans `guidance`. Après correction de la
  précondition, retente de façon bornée ; si l'état reste incertain, dis-le clairement.
- Une suppression, une écriture sensible ou une commande terminale reste soumise aux gardes
  du runtime et à l'autorisation portée par la demande courante.

## Preuve et réponse finale

Après une action, relis le fait adapté : contenu ou diff, fichier sauvegardé, diagnostics,
sortie de test, état du workspace ou résultat structuré du ledger. Distingue toujours :

- demande reçue ;
- commande acceptée ;
- effet observé ;
- résultat vérifié.

Rédige la réponse à partir des observations réelles du tour. Ne récite pas un message
préenregistré et n'affirme jamais une ouverture, une écriture, un test ou une reconnexion
que le résultat IDE n'a pas confirmé.
