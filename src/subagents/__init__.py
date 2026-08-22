"""Sous-agents Lumena — exécution de missions par des workers « Lumena complète ».

Système autonome (zéro dépendance P2P) : Lumena lance elle-même des sous-agents
d'elle-même, en arrière-plan, sans bloquer le chat. Voir
`plans/plan-docs sous agent/` (DIAG / FINAL_PLAN / PHASES_STATUS / LOT0).

Lot 0 = raccords d'architecture (orchestrateur unique, factory de registre,
leases de ressources, protocole de reprise) AVANT toute mission.
"""
