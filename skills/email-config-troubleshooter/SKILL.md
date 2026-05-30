---
name: email-config-troubleshooter
description: "[Fusionné dans mail-messaging] Diagnostic des problèmes de configuration email SMTP/Gmail : échec d'envoi, erreurs d'auth 535/534, mot de passe d'application, SMTP incorrect. RÈGLE LUMENA : utiliser les outils natifs mail_* — voir le skill `mail-messaging` pour l'ensemble email + messagerie."
keywords: [email config, smtp, gmail, 535, 534, authentification email, mot de passe app, configuration email, email error, sendmail, python email, nodemailer, email setup]
---

# Email Config Troubleshooter

> ℹ️ **Ce skill est désormais une sous-partie de `mail-messaging`.** Pour tout l'email +
> WhatsApp/Telegram/SMS, référez-vous au skill **`mail-messaging`**. Ce fichier reste
> uniquement comme aide-mémoire de **diagnostic SMTP** et sera retiré ultérieurement.

## Diagnostic d'un problème d'envoi (via outils natifs)

| Étape | Outil natif |
|---|---|
| Lister les comptes configurés | `mail_list_accounts` |
| **Tester** la connexion SMTP/IMAP | `mail_quick_test` |
| Corriger / (re)configurer un compte | `mail_account_upsert` *(confirmation)* |
| Réessayer un envoi | `mail_send` |

## Codes d'erreur fréquents
- **535 / 534** : authentification refusée → souvent un **mot de passe d'application**
  requis (Gmail/Outlook avec 2FA), pas le mot de passe du compte. Vérifier via
  `mail_quick_test` après `mail_account_upsert`.
- **Timeout / connexion refusée** : hôte/port SMTP incorrect → corriger via `mail_account_upsert`.

## Règles
1. **Toujours `mail_quick_test`** avant de conclure qu'un compte fonctionne.
2. **Ne jamais exposer** le mot de passe / token dans une réponse.
3. Ne pas coder de client SMTP Python/Node — utiliser les outils `mail_*`.
4. Pour l'envoi réel et la messagerie complète → skill **`mail-messaging`**.
