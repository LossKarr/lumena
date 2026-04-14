---
name: email-config-troubleshooter
description: Skill email-config-troubleshooter
---

# Email Config Troubleshooter

Un skill pour diagnostiquer et résoudre automatiquement les problèmes de configuration email, en particulier les erreurs d'authentification Gmail (535, 534, etc.).

## Quand l'utiliser

1. **Quand un envoi d'email échoue avec une erreur d'authentification** (ex: "535 Username and Password not accepted", "534 Application-specific password required")
2. **Quand tu configures un nouveau compte email** et que la connexion IMAP/SMTP ne fonctionne pas

## Instructions

### Étape 1 : Diagnostiquer l'erreur
- Identifier le code d'erreur exact et le message
- Vérifier si c'est un problème Gmail spécifique ou générique
- Consulter les logs de connexion (si disponibles)

### Étape 2 : Solutions Gmail spécifiques
1. **Vérifier l'authentification à 2 facteurs** :
   - Si activée, générer un mot de passe d'application
   - Aller sur https://myaccount.google.com/apppasswords
   - Créer un mot de passe pour "Mail"
   - Utiliser ce mot de passe au lieu du mot de passe principal

2. **Autoriser les applications moins sécurisées** :
   - Aller sur https://myaccount.google.com/security
   - Activer "Accès moins sécurisé" (déconseillé mais fonctionnel)
   - Note : Google peut désactiver cette option automatiquement

3. **Vérifier les restrictions de compte** :
   - S'assurer que le compte n'est pas verrouillé pour sécurité
   - Vérifier les activités récentes du compte

### Étape 3 : Solutions génériques
1. **Vérifier les credentials** :
   - Confirmer l'email, le mot de passe, les serveurs (imap.gmail.com:993, smtp.gmail.com:587)
   - Vérifier les paramètres SSL/TLS

2. **Tester avec mail_quick_test** :
   ```python
   mail_quick_test(alias="nom_compte")
   ```

3. **Créer un compte de secours** :
   - Si le problème persiste, configurer un compte alternatif (Outlook, Yahoo, etc.)
   - Utiliser `mail_account_upsert` avec les nouveaux paramètres

### Étape 4 : Automatisation de la résolution
- Proposer à l'utilisateur les options disponibles
- Exécuter les corrections avec confirmation
- Tester la connexion après chaque correction

## Exemples

### Input : Erreur Gmail 535
```
Erreur SMTP: (535, b'5.7.8 Username and Password not accepted...')
```

### Output : Plan d'action
1. **Diagnostic** : Erreur 535 - Authentification refusée par Gmail
2. **Solution recommandée** :
   - Demander à l'utilisateur s'il a l'authentification à 2 facteurs
   - Si oui : guider vers la création d'un mot de passe d'application
   - Si non : proposer d'activer "Accès moins sécurisé" temporairement
3. **Commande de test** :
   ```python
   mail_quick_test(alias="gmail_account")
   ```
4. **Alternative** : Configurer un compte Outlook de secours

### Input : Nouvelle configuration échouée
```
Échec de connexion IMAP : Cannot connect to server
```

### Output : Checklist de vérification
1. Vérifier la connexion internet
2. Confirmer les paramètres serveur :
   - IMAP : imap.gmail.com:993 (SSL)
   - SMTP : smtp.gmail.com:587 (TLS)
3. Tester avec `mail_quick_test`
4. Si échec, essayer avec `mail_account_upsert` et nouveaux credentials

## Notes
- Toujours demander confirmation avant de modifier les paramètres de sécurité du compte
- Privilégier les mots de passe d'application plutôt que "Accès moins sécurisé"
- Garder un log des tentatives de résolution pour analyse future
- Ce skill peut être étendu pour supporter d'autres fournisseurs (Outlook, Yahoo, etc.)
