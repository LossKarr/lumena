# MCP dans Lumena

Lumena peut rechercher, cataloguer, configurer, installer, activer, utiliser,
désactiver et supprimer des serveurs MCP sans demander à l'utilisateur de
lancer une commande dans un terminal. Le mode Agent accède ensuite aux outils
actifs sous la forme `mcp__<serveur>__<outil>`.

## Connexions prises en charge

- `npm:` : paquet installé dans le dossier MCP isolé de Lumena ;
- `pypi:` : paquet Python installé dans un environnement virtuel isolé ;
- `local:` : serveur MCP créé ou importé dans l'espace local contrôlé ;
- `exe:` : binaire Windows absolu, notamment fourni par une application hôte ;
- `remote:` : serveur Streamable HTTP ou serveur HTTP+SSE historique.

Les fichiers `.bat`, `.cmd` et `.ps1` ne sont jamais admis comme binaires MCP.
Lumena lance les exécutables avec une liste d'arguments, sans shell. Une
configuration d'application hôte peut être importée depuis un bloc JSON avec
`command`, `args` et des références de secrets `${NOM_DE_CLE}`.

Pour un serveur distant, une URL HTTPS est obligatoire. HTTP est réservé à
`localhost`, `127.0.0.1` ou `::1`. Les en-têtes d'authentification doivent être
déclarés dans le contrat de connexion et leurs valeurs proviennent du coffre
chiffré MCP. Une valeur secrète littérale dans un en-tête distant est refusée.
Le texte brut d'un bloc stdio importé est immédiatement remplacé par un
marqueur neutre ; les secrets nécessaires doivent être ressaisis dans le coffre
MCP et ne sont jamais recopiés dans le catalogue, les tickets ou les traces.

Lumena parle les révisions Streamable HTTP avec session et la révision sans
session `2026-07-28`. Cette dernière envoie les métadonnées du client dans
chaque requête, ainsi que les en-têtes `Mcp-Method` et `Mcp-Name`. La
compatibilité HTTP+SSE reste disponible pour les serveurs anciens et impose un
endpoint de messages de même origine.

## Recherche et admission

Le panneau **MCP → Registre officiel** interroge l'API officielle et conserve
un cache local validé. Les serveurs npm, PyPI et les serveurs uniquement
distants alimentent aussi la recherche autonome de Lumena. Le registre est une
source de provenance, pas une autorisation : un nouveau serveur passe toujours
par la file d'approbation avant l'écriture du catalogue.

Une URL exacte ou un bloc de configuration peut également être donné à Lumena
dans le chat. La résolution produit un contrat versionné séparant :

- la distribution ;
- le transport ;
- l'authentification ;
- les versions du protocole ;
- les noms de secrets, sans leurs valeurs.

Ce contrat suit le ticket d'approbation puis est signé avec l'entrée du
catalogue. Une substitution entre l'approbation et l'exécution est donc
refusée.

## Authentification OAuth

Les MCP distants peuvent déclarer un serveur d'autorisation OAuth. Lumena
prépare une autorisation avec `state` à usage unique et PKCE S256. Le navigateur
reste la frontière de consentement humain. Le callback échange le code, puis
stocke les jetons dans `MCPCredentialsService`, chiffrés au repos. Le code,
le vérificateur PKCE et les jetons ne sont pas écrits dans les journaux.

## Activation et sécurité

L'activation suit la chaîne existante : catalogue, approbation, installation,
client, découverte, attribution de politique, adaptateur, registre d'outils et
watcher. Les nouveaux transports utilisent cette même chaîne et ne possèdent
pas de raccourci vers le registre.

Avant l'enregistrement des handlers, `MCPSchemaGuard` :

- borne la profondeur, la taille et le nombre de nœuds des JSON Schemas ;
- refuse les références de schéma réseau ;
- calcule une empreinte canonique de tous les outils ;
- bloque toute dérive ultérieure ;
- exige l'acceptation de l'empreinte en attente exacte pour une mise à jour.

Les politiques existantes continuent de décider si un appel est en lecture,
écriture, réseau, authentification ou exécution sensible. Les appels restent
observables par le journal MCP, le watcher et la provenance enregistrée dans le
registre d'outils.

## Limites voulues

« Compatible avec les MCP » ne signifie pas exécuter une commande arbitraire.
Un serveur doit fournir un transport conforme et une distribution admise. Un
script shell, une URL HTTP publique, un secret en clair, un changement de
schéma non approuvé ou un endpoint SSE qui change d'origine est refusé. Les
permissions du service distant et le consentement OAuth restent sous le
contrôle de l'utilisateur et du fournisseur.
