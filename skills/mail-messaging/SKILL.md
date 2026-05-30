---
name: mail-messaging
description: "À utiliser pour l'email et la messagerie : lire/envoyer/répondre des mails (IMAP/SMTP), pièces jointes, et envois WhatsApp / Telegram / SMS critique. RÈGLE LUMENA PRIORITAIRE : utilise TOUJOURS les outils natifs `mail_*` et les outils messaging. Ne jamais envoyer sans confirmation si destinataire/contenu/action est sensible."
keywords: [mail, email, courriel, envoyer mail, lire mail, imap, smtp, boite mail, piece jointe, repondre mail, whatsapp, telegram, sms, message, notification critique, messagerie]
license: Lumena - usage interne
---

# Mail & Messaging — Outils natifs

⛔ **Confirmer avant d'envoyer** dès que le destinataire, le contenu ou l'action est
sensible. **Tester le compte** avant un premier envoi. **Ne jamais exposer** de secrets
ou de variables d'environnement (identifiants, tokens) dans une réponse ou un message.

## Table de routage : besoin → outil natif

### Email
| Tu veux… | Outil |
|---|---|
| Lister / configurer les comptes | `mail_list_accounts` · `mail_account_upsert` |
| **Tester** un compte (avant envoi) | `mail_quick_test` |
| Lister / lire des messages | `mail_list_messages` · `mail_read_message` |
| **Envoyer** un mail | `mail_send` *(confirmation si sensible)* |
| **Répondre** | `mail_reply_message` *(confirmation si sensible)* |
| Télécharger les pièces jointes | `mail_download_attachments` |
| Déplacer / supprimer un message | `mail_move_message` · `mail_delete_message` *(confirmation)* |

### Messagerie & alertes
| Tu veux… | Outil |
|---|---|
| WhatsApp : message / photo / doc / audio | `send_whatsapp_message` · `send_whatsapp_photo` · `send_whatsapp_document` · `send_whatsapp_audio` |
| Telegram : envoyer un document | `telegram_send_document` |
| Alerte critique (SMS / appel) | `send_critical_sms` · `place_critical_call` · `notify_critical` *(réservé urgences)* |

## Règles de sécurité
1. **Confirmation obligatoire** avant tout envoi à un destinataire externe ou avec contenu sensible.
2. **`mail_quick_test`** avant le premier envoi sur un compte (vérifier SMTP).
3. **Ne jamais exposer** mots de passe, tokens, env vars — ni dans le mail, ni dans la réponse.
4. **Confirmer** avant modification de compte (`mail_account_upsert`) ou suppression de message.
5. WhatsApp / Telegram / SMS peuvent être **indisponibles** selon la config — vérifier et le signaler plutôt qu'inventer un succès.
6. Les alertes critiques (SMS/appel) sont **réservées aux urgences réelles**.
