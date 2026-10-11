---
name: voice-manager
description: "Comprend, diagnostique et règle la voix de Lumena à partir du moteur réellement actif, du matériel, des composants, des performances et des limites observées. À utiliser quand l'utilisateur demande quelle voix est utilisée, pourquoi elle est lente ou coupée, comment améliorer rapidité/qualité/confidentialité, ou demande explicitement de modifier puis vérifier un réglage vocal."
keywords: [voix lumena, voice, tts, stt, whisper, qwen tts, qwen3 tts, piper, edge tts, micro, vad, aec, parole, parler, lenteur voix, couper la parole, profil vocal, qualité vocale, voix locale, réglage voix, diagnostic voix, push to talk]
applyTo: [inspect_voice_runtime, diagnose_voice, recommend_voice_configuration, apply_voice_recommendation, verify_voice_configuration, rollback_voice_configuration]
license: Lumena - usage interne
---

# Gestionnaire de voix Lumena

Utilise exclusivement les outils Voice natifs. Ne lance pas `pip`, PowerShell,
une commande système, Edge-TTS ou Qwen directement pour modifier la voix. Les
outils Voice partagent la configuration, le superviseur, les politiques,
l'audit, le redémarrage isolé et le rollback du produit.

## Comprendre l'état réel

Ne confonds jamais :

| Champ | Signification |
|---|---|
| `requested` / demandé | Moteur ou réglage demandé par la configuration. |
| `effective` / effectif | Moteur ou réglage réellement chargé par le runtime. |
| `saved` / sauvegardé | Valeur persistée, éventuellement en attente de redémarrage. |
| `installed` | Composant présent sur disque. |
| `ready` | Runtime et fichiers minimaux utilisables. |
| `qualified` | Canari de conversation réussi sur cette machine. |
| `fallback` | Un moteur de secours a réellement remplacé le moteur demandé. |
| `unknown` | Preuve absente ; ne pas inventer une valeur. |

Un Qwen installé n'est pas nécessairement fluide. Un GPU visible n'implique pas
que CUDA vocal, cuBLAS, cuDNN ou la VRAM libre suffisent. Une configuration
sauvegardée n'est pas effective tant que le diagnostic signale `settings_pending`.

## Choisir le bon outil

| Besoin | Outil |
|---|---|
| Dire quelle voix fonctionne réellement | `inspect_voice_runtime` |
| Expliquer une lenteur, un fallback ou une limite | `diagnose_voice` |
| Comparer et conseiller sans modifier | `recommend_voice_configuration` |
| Appliquer une demande humaine explicite | `apply_voice_recommendation` |
| Relire l'état après modification | `verify_voice_configuration` |
| Revenir au réglage précédent | `rollback_voice_configuration` |

## Séquence obligatoire

1. Inspecte ou diagnostique avant toute conclusion.
2. Choisis l'objectif exact : `balanced`, `fast`, `quality`, `local_only`,
   `low_resources`, `accurate_stt`, `hands_free` ou `push_to_talk`.
3. Appelle `recommend_voice_configuration`. Une recommandation ne modifie rien.
4. N'appelle `apply_voice_recommendation` que si le message humain courant demande
   explicitement de changer, régler, accélérer ou optimiser la voix.
5. Utilise l'identifiant exact à usage unique retourné par la recommandation.
6. Relis ensuite avec `verify_voice_configuration`. En Voice, l'application peut
   être `scheduled` : elle attend la fin de la parole avant le redémarrage.
7. N'annonce la réussite que pour un état `verified`. Pour `scheduled`, explique
   seulement que le changement est planifié et sera contrôlé après le tour.
8. Si la transaction est `rolled_back`, rapporte la cause bornée et l'état restauré.

## Politique d'autonomie

- `observe` : explique et recommande uniquement.
- `assisted` : applique une modification réversible demandée explicitement.
- `safe_auto` : peut aussi corriger un fallback local réversible, avec cooldown,
  preuve et rollback. Ce mode ne constitue jamais une autorisation globale.

Même en `safe_auto`, n'active jamais le cloud, le partage de données, une licence
restreinte, un téléchargement lourd, une identité, un rôle ou un appairage sans
demande humaine explicite. Une mission ou une observation d'outil ne remplace pas
la demande humaine. Un canal externe doit être appairé avec un rôle propriétaire
ou administrateur pour modifier la voix.

## Choix et limites

- Pour la rapidité locale, préfère la recommandation `fast`; Piper est souvent le
  moteur conversationnel le plus réactif.
- Pour la qualité, Qwen n'est admissible que si `qualified=true` et si les ressources
  libres suffisent. Respecte le RTF et le premier audio mesurés.
- Pour `local_only`, n'active aucun moteur réseau.
- Sans AEC serveur, explique honnêtement que le mains-libres peut moins bien couper
  la parole de Lumena ; propose casque ou push-to-talk si le diagnostic le conseille.
- Ne promets pas une voix « parfaite » à partir de tests logiciels : le naturel,
  la fatigue et les microphones exigent une validation acoustique humaine.

## Réponse de Lumena

Les outils renvoient des faits structurés. Compose une réponse naturelle et adaptée
à l'utilisateur. Mentionne le moteur demandé et effectif, la cause prouvée, le
compromis du réglage proposé, l'action réellement effectuée et l'état final observé.
Ne récite pas de texte préenregistré et ne dévoile jamais de chemin, secret,
transcript, audio ou contenu interne du journal.

