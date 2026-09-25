# Orienter un travail Lumena en cours

Lumena accepte de nouvelles consignes pendant un tour Agent ou une mission sans
oublier la demande initiale. Chaque orientation est enregistrée, ordonnée et
rattachée au travail concerné avant d'être remise au raisonnement.

## Les deux modes

- **Ajouter au travail** conserve l'exécution en cours. La consigne est intégrée
  au prochain point de reprise sûr.
- **Orienter maintenant** demande une reprise prioritaire. Une attente de modèle
  peut être relancée, mais une opération d'outil déjà commencée va toujours
  jusqu'à sa frontière sûre avant la remise de la consigne.

Le bouton **Stop** reste une action séparée. Orienter ne signifie ni annuler ni
remplacer tout le travail. Plusieurs orientations peuvent être envoyées ; elles
sont ordonnées par le serveur et réconciliées avec l'objectif initial, le plan
et les preuves déjà collectées.

## Chat, missions et canaux

Dans le chat Web, la zone de saisie reste disponible pendant le streaming. Le
travail actif est identifié par son identifiant serveur, conservé lors d'un
rafraîchissement, puis ses statuts sont relus depuis l'API.

Pour une mission, la consigne est d'abord attachée au lead. Elle est ensuite
transmise aux workers actifs concernés. Une contrainte sur un fichier est remise
au worker qui possède ce périmètre. Une demande qui élargit le contrat crée un
amendement à examiner ; elle n'étend jamais silencieusement les fichiers permis.

Le même contrat s'applique aux canaux liés à l'identité propriétaire. Lumena ne
choisit pas arbitrairement lorsqu'il existe plusieurs travaux possibles : elle
retourne alors les cibles candidates. Une conversation ordinaire continue de
suivre le chemin de chat normal.

## Annuler ou corriger une orientation

Chaque orientation encore en attente ou remise porte une croix. L'annuler la
retire du travail en cours et **ramène son texte dans la zone de saisie** : c'est
ainsi qu'on la corrige, en la réécrivant puis en la renvoyant. Si la zone
contient déjà autre chose, le texte n'est pas écrasé.

Une orientation déjà prise en compte ne peut plus être annulée : le travail l'a
reçue. Annuler n'interrompt jamais le travail lui-même — le bouton **Stop** reste
la seule action qui l'arrête.

## États et honnêteté

Une orientation peut être en attente, remise, partiellement appliquée, appliquée,
annulée, refusée ou arrivée trop tard. Le statut `applied` exige une preuve ; le
simple fait d'avoir placé une consigne dans le contexte ne suffit pas.

Les commandes en attente survivent au redémarrage. Une commande remise dont
l'incorporation n'a pas été confirmée est récupérée prudemment. Les traces
conservent les identifiants, la séquence, la politique et le résultat, sans
copier le texte de la consigne dans la télémétrie.

Lumena formule elle-même sa réponse à partir des faits structurés retournés par
le runtime. Si le modèle de formulation est indisponible après l'enregistrement,
les faits sont rendus directement et aucun second travail n'est créé.
