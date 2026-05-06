# 💓 HEARTBEAT.md - Profil Production Continue (LUMENA)

# Le heartbeat est un WATCHDOG : il observe et alerte.
# Les actions (retrain, eval, curation) sont dans le SCHEDULER.
# Ne jamais mélanger les deux.

# schedule: 6,18

# ═══════════════════════════════════════════════════════════════
# 1) SANTÉ RUNTIME (chaque heartbeat)
# ═══════════════════════════════════════════════════════════════
- Vérifie l'espace disque C: et la RAM disponible. Si disque > 90% ou RAM < 500MB, note l'alerte dans le journal.
- Vérifie qu'aucun fichier lock n'est stale (> 10 min, PID mort). Si oui, note-le dans le journal (ne pas le supprimer).
- Vérifie la taille de la file d'attente scheduler. Si tâches en retard > 5, note un warning dans le journal.
- Vérifie que le daemon tourne depuis combien de temps (uptime) et note-le.

# ═══════════════════════════════════════════════════════════════
# 2) PROVIDER HEALTH (observation)
# ═══════════════════════════════════════════════════════════════
- Vérifie le statut health de tous les providers LLM. Si un provider est en cooldown depuis > 1h, note un warning.
- Note dans le journal le provider actif et sa latence récente.

# ═══════════════════════════════════════════════════════════════
# 3) PIPELINE DONNÉES (suivi passif)
# ═══════════════════════════════════════════════════════════════
- Vérifie le nombre d'entrées dans training_pool/ du jour. Note le total dans le journal.
- Vérifie le nombre d'exemples dans training_validated/. Si 0 depuis > 48h, note "pipeline juge inactif".
- Si le dernier retrain date de plus de 14 jours, note "retrain en retard" dans le journal.

# ═══════════════════════════════════════════════════════════════
# 4) QUALITÉ (tendance)
# ═══════════════════════════════════════════════════════════════
- Vérifie le dernier score micro_eval dans data/ops/micro_eval_log.jsonl. Si score en baisse sur 3 mesures, note l'alerte.
- Note dans le journal un résumé 1 ligne : nb conversations, nb validated, dernier score eval.

# ═══════════════════════════════════════════════════════════════
# 5) PRÉSENCE & RYTHME
# ═══════════════════════════════════════════════════════════════
- Si inactivité > 4h en journée (8h-23h), note dans le journal "gap d'activité - données non collectées".
- Si l'heure est tardive (> 23h), propose une recommandation repos concise.

# ═══════════════════════════════════════════════════════════════
# 6) VEILLE (signal faible, observation)
# ═════════════════════════════════════════════════════════════
- Vérifie dans ta mémoire les entrées de category "blind_spot". Note dans le journal le nombre d'entrées non résolues. Sinon, HEARTBEAT_OK.
