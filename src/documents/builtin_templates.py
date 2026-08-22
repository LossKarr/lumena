"""Metadata and representative preview data for legacy builtin templates."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


_PERSON = {"name": "Camille Martin", "address": "12 rue des Lilas", "city": "75011 Paris", "email": "camille@example.fr", "phone": "01 23 45 67 89"}
_COMPANY = {**_PERSON, "name": "Atelier Lumena", "siret": "123 456 789 00012"}

BUILTIN_LABELS = {
    "attestation": ("Attestation", "legal"),
    "bon_commande": ("Bon de commande", "commercial"),
    "bulletin_paie": ("Bulletin de paie", "hr"),
    "contrat_prestation": ("Contrat de prestation", "legal"),
    "devis": ("Devis", "commercial"),
    "facture": ("Facture", "commercial"),
    "fiche_poste": ("Fiche de poste", "hr"),
    "lettre_officielle": ("Lettre officielle", "correspondence"),
    "nda": ("Accord de confidentialité", "legal"),
    "note_interne": ("Note interne", "internal"),
    "proces_verbal": ("Procès-verbal", "legal"),
    "rapport_activite": ("Rapport d'activité", "report"),
    "relance_impaye": ("Relance impayée", "commercial"),
    "avoir": ("Avoir", "finance"),
    "facture_proforma": ("Facture proforma", "finance"),
    "bon_livraison": ("Bon de livraison", "operations"),
    "recu_paiement": ("Reçu de paiement", "finance"),
    "note_frais": ("Note de frais", "finance"),
    "releve_client": ("Relevé client", "finance"),
    "ordre_jour": ("Ordre du jour", "governance"),
    "demande_conge": ("Demande de congé", "hr"),
    "feuille_temps": ("Feuille de temps", "hr"),
    "entretien_annuel": ("Compte rendu d'entretien annuel", "hr"),
    "contrat_travail": ("Contrat de travail", "legal"),
    "ordre_mission": ("Ordre de mission", "operations"),
    "cahier_charges": ("Cahier des charges", "project"),
    "rapport_intervention": ("Rapport d'intervention", "operations"),
    "rapport_incident": ("Rapport d'incident", "operations"),
    "procedure_operationnelle": ("Procédure opérationnelle", "quality"),
    "plan_action": ("Plan d'action", "project"),
}

# Natural-language names used by the deterministic document router. Keep this
# vocabulary next to the catalog labels so adding a builtin cannot silently
# leave routing behind. Values are deliberately specific: generic words such
# as "commande", "note" or "rapport" alone would create false positives.
BUILTIN_ALIASES: dict[str, tuple[str, ...]] = {
    "attestation": (
        "attestation", "attestation de travail", "attestation employeur",
        "certificat", "certificate",
    ),
    "bon_commande": (
        "bon de commande", "bon commande", "commande fournisseur",
        "purchase order", "order form",
    ),
    "bulletin_paie": (
        "bulletin de paie", "bulletin de paye", "fiche de paie",
        "fiche de paye", "bulletin de salaire", "fiche de salaire", "payslip",
    ),
    "contrat_prestation": (
        "contrat", "contrat de prestation", "contrat de service", "convention de prestation",
        "contrat freelance", "service contract",
    ),
    "devis": (
        "devis", "estimation commerciale", "chiffrage", "offre de prix",
        "proposition commerciale", "quotation", "quote",
    ),
    "facture": (
        "facture", "facturation", "note d honoraires", "invoice",
    ),
    "fiche_poste": (
        "fiche de poste", "description de poste", "descriptif de poste",
        "profil de poste", "job description",
    ),
    "lettre_officielle": (
        "lettre", "lettre officielle", "courrier officiel", "lettre administrative",
        "courrier administratif", "official letter",
    ),
    "nda": (
        "nda", "accord de confidentialite", "contrat de confidentialite",
        "accord de non divulgation", "non disclosure agreement",
    ),
    "note_interne": (
        "note interne", "note de service", "memo interne", "internal memo",
    ),
    "proces_verbal": (
        "pv", "proces verbal", "proces-verbal", "pv de reunion", "pv d assemblee",
        "minutes de reunion", "compte rendu", "compte rendu reunion", "compte rendu de reunion",
        "compte-rendu de reunion",
        "meeting minutes",
    ),
    "rapport_activite": (
        "rapport d activite", "rapport de activite", "bilan d activite",
        "rapport annuel", "rapport mensuel", "activity report",
    ),
    "relance_impaye": (
        "relance impayee", "relance impaye", "lettre de relance",
        "rappel de paiement", "relance de facture", "relance pour une facture impayee",
        "payment reminder",
    ),
    "avoir": ("avoir", "note de credit", "credit note", "credit memo"),
    "facture_proforma": ("facture proforma", "pro forma invoice", "proforma invoice"),
    "bon_livraison": ("bon de livraison", "bordereau de livraison", "delivery note", "packing slip"),
    "recu_paiement": ("recu de paiement", "recu de reglement", "payment receipt", "receipt of payment"),
    "note_frais": ("note de frais", "rapport de depenses", "expense report", "expense claim"),
    "releve_client": ("releve client", "releve de compte client", "customer statement", "account statement"),
    "ordre_jour": ("ordre du jour", "agenda de reunion", "meeting agenda"),
    "demande_conge": ("demande de conge", "demande de vacances", "leave request", "vacation request"),
    "feuille_temps": ("feuille de temps", "releve d heures", "timesheet", "time sheet"),
    "entretien_annuel": (
        "entretien annuel", "compte rendu d entretien annuel",
        "evaluation annuelle", "performance review", "annual review",
    ),
    "contrat_travail": ("contrat de travail", "contrat employe", "employment contract", "work contract"),
    "ordre_mission": ("ordre de mission", "fiche de mission", "mission order", "travel order"),
    "cahier_charges": ("cahier des charges", "specification fonctionnelle", "statement of work", "project brief"),
    "rapport_intervention": ("rapport d intervention", "compte rendu intervention", "service report", "field service report"),
    "rapport_incident": ("rapport d incident", "fiche incident", "incident report", "accident report"),
    "procedure_operationnelle": (
        "procedure operationnelle", "procedure d accueil", "procedure accueil",
        "mode operatoire", "sop", "standard operating procedure",
    ),
    "plan_action": ("plan d action", "actions correctives", "action plan", "corrective action plan"),
}

_CATEGORY_ACCENTS = {
    "commercial": "#B45309",
    "legal": "#1D4ED8",
    "hr": "#0F766E",
    "correspondence": "#7C3AED",
    "internal": "#475467",
    "report": "#0369A1",
    "finance": "#0F766E",
    "operations": "#B45309",
    "governance": "#4338CA",
    "project": "#0369A1",
    "quality": "#047857",
}

_LOCALIZED_REFERENCE_MODELS = frozenset(
    {
        "bulletin_paie", "contrat_prestation", "facture", "nda",
        "relance_impaye", "avoir", "facture_proforma", "contrat_travail",
    }
)


def builtin_compliance(name: str) -> dict[str, Any]:
    """Return additive compliance metadata without changing rendering behavior."""
    if name in _LOCALIZED_REFERENCE_MODELS:
        return {
            "locale": "fr-FR",
            "scope": "localized",
            "compliance_level": "reference",
            "jurisdictions": ("FR", "EU"),
            "legal_notice": (
                "Modele de reference a adapter aux lois, conventions et obligations "
                "applicables avant utilisation officielle."
            ),
        }
    return {
        "locale": "fr-FR",
        "scope": "universal",
        "compliance_level": "structure",
        "jurisdictions": (),
        "legal_notice": "",
    }


def builtin_design(name: str) -> dict[str, Any]:
    """Professional, editable design defaults for every builtin model."""
    _, category = BUILTIN_LABELS.get(name, (name, "general"))
    return {
        "accent": _CATEGORY_ACCENTS.get(category, "#B45309"),
        "text": "#1C2430",
        "muted": "#667085",
        "surface": "#F5F7FA",
        "font": "classic" if category == "legal" else "modern",
        "density": "standard",
        "page_margin_mm": 18 if category in {"commercial", "hr", "report"} else 22,
        "logo_enabled": True,
        "logo_position": "left",
        "logo_width_px": 128,
    }


def builtin_sample_data(name: str) -> dict[str, Any]:
    common = {"accent": "#e8892f", "date": "14 juillet 2026", "lieu": "Paris", "currency": "€"}
    samples: dict[str, dict[str, Any]] = {
        "attestation": {"societe": _COMPANY, "attestant": {"name": "Alex Dupont", "titre": "Direction"}, "beneficiaire": {"name": "Morgan Leroy", "titre": "Consultant"}, "objet": "la réalisation de la mission confiée"},
        "bon_commande": {"numero": "BC-2026-014", "acheteur": _COMPANY, "fournisseur": {**_PERSON, "name": "Studio Horizon"}, "items": [{"ref": "SRV-01", "description": "Conception documentaire", "qty": 2, "unit_price": 450}], "conditions_livraison": "Livraison sous 10 jours"},
        "bulletin_paie": {"periode": "Juillet 2026", "employeur": _COMPANY, "salarie": {"name": "Morgan Leroy", "matricule": "EMP-042", "poste": "Designer", "qualification": "Cadre", "date_entree": "03/01/2024", "convention": "Syntec", "temps_travail": "151,67 h"}, "brut": 3200, "net_imposable": 2550, "net_paye": 2420, "cotisations": [{"libelle": "Assurance maladie", "base": 3200, "taux_salarial": 0.75, "part_salariale": 24, "taux_patronal": 7, "part_patronale": 224}], "date_paiement": "31/07/2026", "mode_paiement": "Virement"},
        "contrat_prestation": {"prestataire": _COMPANY, "client": {**_PERSON, "name": "Société Nova"}, "objet": "Création d'un système documentaire", "date_debut": "1 août 2026", "duree": "3 mois", "montant": "4 500 € HT", "clauses": [{"titre": "Confidentialité", "contenu": "Les parties protègent les informations échangées."}]},
        "devis": {"numero": "DEV-2026-031", "issuer": _COMPANY, "client": {**_PERSON, "name": "Maison Atlas"}, "items": [{"description": "Audit et conception", "qty": 3, "unit": "jour", "unit_price": 600, "vat_rate": 20}], "validity_days": 30, "conditions": "Acompte de 30 % à la commande"},
        "facture": {"numero": "FAC-2026-118", "issuer": _COMPANY, "client": {**_PERSON, "name": "Maison Atlas"}, "items": [{"description": "Conception du modèle", "qty": 2, "unit": "jour", "unit_price": 650, "vat_rate": 20}, {"description": "Intégration", "qty": 1, "unit": "forfait", "unit_price": 900, "vat_rate": 20}], "due_date": "14 août 2026", "payment_terms": "Paiement à 30 jours", "notes": "Merci pour votre confiance."},
        "fiche_poste": {"titre_poste": "Responsable documentaire", "departement": "Produit", "description": "Piloter la qualité et l'automatisation documentaire.", "missions": ["Définir les modèles", "Contrôler les livrables"], "competences": ["Rigueur", "Design d'information"], "qualifications": ["3 ans d'expérience"], "conditions": {"Contrat": "CDI", "Lieu": "Paris / hybride"}},
        "lettre_officielle": {"expediteur": _COMPANY, "destinataire": {**_PERSON, "name": "Madame Leroy"}, "objet": "Confirmation de notre collaboration", "formule_appel": "Madame,", "corps": ["Nous vous confirmons le démarrage de la mission.", "Nous restons disponibles pour toute précision."], "formule_politesse": "Veuillez agréer, Madame, nos salutations distinguées."},
        "nda": {"parties": [{"name": "Atelier Lumena", "address": "12 rue des Lilas, Paris"}, {"name": "Maison Atlas", "address": "8 avenue Victor-Hugo, Lyon"}], "objet": "un projet documentaire confidentiel", "duree_confidentialite": "3 ans", "juridiction": "Paris"},
        "note_interne": {"expediteur": "Direction Produit", "destinataires": ["Équipe Design", "Équipe Technique"], "objet": "Nouveau processus documentaire", "priorite": "Normale", "contenu": ["Le nouveau catalogue entre en service.", "Chaque modèle devra être validé avant usage."]},
        "proces_verbal": {"societe": "Atelier Lumena", "assemblee_type": "Réunion de pilotage", "participants": [{"name": "Alex Dupont", "role": "Président"}, {"name": "Morgan Leroy", "role": "Secrétaire"}], "ordre_du_jour": ["Validation du catalogue", "Plan de déploiement"], "resolutions": [{"titre": "Catalogue", "details": "Le catalogue est adopté.", "vote": "Adoptée", "pour": 2, "contre": 0, "abstention": 0}], "president": "Alex Dupont", "secretaire": "Morgan Leroy", "heure_fin": "11h30"},
        "rapport_activite": {"societe": "Atelier Lumena", "periode": "Premier semestre 2026", "kpis": [{"label": "Documents produits", "value": "1 284"}, {"label": "Temps gagné", "value": "312 h"}], "sections": [{"title": "Synthèse", "content": "La production documentaire progresse avec une qualité stable."}, {"title": "Prochaines étapes", "content": "Déployer le studio auprès des équipes."}], "graphiques_paths": []},
        "relance_impaye": {"creancier": _COMPANY, "debiteur": {**_PERSON, "name": "Maison Atlas"}, "facture_ref": "FAC-2026-042", "montant": "1 560,00", "date_echeance": "30 juin 2026", "niveau": 1, "formule_appel": "Madame, Monsieur,"},
        "avoir": {"numero": "AV-2026-018", "facture_origine": "FAC-2026-118", "issuer": _COMPANY, "client": {**_PERSON, "name": "Maison Atlas"}, "motif": "Ajustement commercial", "items": [{"description": "Remise exceptionnelle", "qty": 1, "unit_price": 180, "vat_rate": 20}]},
        "facture_proforma": {"numero": "PRO-2026-021", "issuer": _COMPANY, "client": {**_PERSON, "name": "Maison Atlas"}, "items": [{"description": "Accompagnement documentaire", "qty": 5, "unit": "jour", "unit_price": 620}], "validity_days": 15, "incoterm": "DAP Paris"},
        "bon_livraison": {"numero": "BL-2026-044", "expediteur": _COMPANY, "destinataire": {**_PERSON, "name": "Maison Atlas"}, "commande_ref": "BC-2026-014", "items": [{"ref": "DOC-01", "description": "Kit documentaire", "qty_commandee": 12, "qty_livree": 12}], "transporteur": "Transport Horizon", "reservations": "Aucune réserve à la réception."},
        "recu_paiement": {"numero": "REC-2026-077", "emetteur": _COMPANY, "payeur": {**_PERSON, "name": "Maison Atlas"}, "montant": 1560, "mode": "Virement bancaire", "reference": "FAC-2026-042", "date_paiement": "14 juillet 2026"},
        "note_frais": {"numero": "NF-2026-032", "collaborateur": {"name": "Morgan Leroy", "matricule": "EMP-042", "service": "Produit"}, "periode": "Juillet 2026", "expenses": [{"date": "08/07/2026", "category": "Transport", "description": "Train client", "amount": 128.40, "vat": 11.67}, {"date": "08/07/2026", "category": "Repas", "description": "Déjeuner mission", "amount": 24.90, "vat": 2.26}], "avance": 0},
        "releve_client": {"numero": "REL-2026-007", "issuer": _COMPANY, "client": {**_PERSON, "name": "Maison Atlas"}, "periode": "1-31 juillet 2026", "opening_balance": 900, "transactions": [{"date": "02/07/2026", "reference": "FAC-118", "description": "Facture", "debit": 1560, "credit": 0}, {"date": "14/07/2026", "reference": "VIR-077", "description": "Paiement", "debit": 0, "credit": 1560}]},
        "ordre_jour": {"reunion": "Comité de pilotage", "organisateur": "Direction Produit", "date_reunion": "21 juillet 2026", "heure": "09:30-11:00", "lieu_reunion": "Salle Atlas / visioconférence", "participants": ["Alex Dupont", "Morgan Leroy", "Camille Martin"], "items": [{"time": "09:30", "topic": "Avancement", "owner": "Morgan", "duration": "20 min"}, {"time": "09:50", "topic": "Décisions", "owner": "Alex", "duration": "30 min"}], "preparation": ["Lire le rapport mensuel", "Ajouter les risques avant lundi"]},
        "demande_conge": {"collaborateur": {"name": "Morgan Leroy", "matricule": "EMP-042", "service": "Produit"}, "type_conge": "Congés payés", "date_debut": "3 août 2026", "date_fin": "14 août 2026", "jours": 10, "relais": "Camille Martin", "commentaire": "Passation jointe au dossier équipe."},
        "feuille_temps": {"collaborateur": {"name": "Morgan Leroy", "matricule": "EMP-042", "service": "Produit"}, "periode": "Semaine 29 - 2026", "entries": [{"date": "13/07/2026", "project": "Atlas", "activity": "Conception", "hours": 7.5}, {"date": "14/07/2026", "project": "Nova", "activity": "Revue", "hours": 6.0}], "expected_hours": 35},
        "entretien_annuel": {"collaborateur": {"name": "Morgan Leroy", "poste": "Designer documentaire", "service": "Produit"}, "manager": "Alex Dupont", "periode": "2025-2026", "bilan": "Objectifs atteints avec une forte progression sur la qualité.", "objectives": [{"label": "Industrialiser les modèles", "status": "Atteint", "comment": "30 modèles qualifiés"}], "skills": [{"label": "Rigueur", "rating": 4}, {"label": "Collaboration", "rating": 5}], "next_objectives": ["Piloter la gouvernance documentaire"], "training": ["Accessibilité des documents"]},
        "contrat_travail": {"employeur": _COMPANY, "salarie": {**_PERSON, "name": "Morgan Leroy", "birth_date": "12/03/1992"}, "poste": "Designer documentaire", "date_debut": "1 septembre 2026", "type_contrat": "CDI", "periode_essai": "3 mois renouvelable selon la loi applicable", "lieu_travail": "Paris / hybride", "remuneration": "48 000 EUR brut annuel", "temps_travail": "35 heures par semaine", "convention": "À renseigner selon la juridiction", "clauses": [{"title": "Confidentialité", "content": "Le salarié protège les informations confidentielles de l'employeur."}]},
        "ordre_mission": {"numero": "OM-2026-016", "employeur": _COMPANY, "collaborateur": {"name": "Morgan Leroy", "matricule": "EMP-042"}, "objet": "Atelier de cadrage client", "destination": "Lyon", "date_debut": "22 juillet 2026", "date_fin": "23 juillet 2026", "transport": "Train", "hebergement": "Hôtel partenaire", "budget": "480 EUR", "contact": "Camille Martin"},
        "cahier_charges": {"project": "Portail documentaire Atlas", "sponsor": "Direction Produit", "owner": "Morgan Leroy", "version": "1.0", "context": "Centraliser la création et la validation des documents.", "objectives": ["Réduire le temps de production", "Tracer chaque version"], "scope_in": ["Modèles", "Workflow de validation", "Exports PDF"], "scope_out": ["Signature qualifiée"], "requirements": [{"id": "REQ-001", "priority": "Must", "title": "Catalogue", "description": "Afficher les modèles avec aperçu", "acceptance": "La galerie charge 30 aperçus"}], "deliverables": ["Application", "Documentation", "Rapport de tests"], "milestones": [{"date": "31/07/2026", "label": "Prototype"}], "risks": [{"risk": "Formats hétérogènes", "response": "Conversion et validation"}]},
        "rapport_intervention": {"numero": "INT-2026-061", "prestataire": _COMPANY, "client": {**_PERSON, "name": "Maison Atlas"}, "site": "Paris", "technicien": "Morgan Leroy", "date_intervention": "14 juillet 2026", "start_time": "09:00", "end_time": "11:30", "request": "Vérifier la chaîne de génération", "diagnosis": "Configuration de modèle incomplète", "actions": ["Correction du manifeste", "Régénération des aperçus"], "parts": [{"ref": "CFG-01", "label": "Configuration", "qty": 1}], "result": "Service opérationnel", "follow_up": "Contrôle à J+7"},
        "rapport_incident": {"numero": "INC-2026-009", "reported_by": "Camille Martin", "incident_date": "14 juillet 2026 10:20", "location": "Plateforme documentaire", "severity": "Majeure", "summary": "Aperçus indisponibles pendant huit minutes.", "impact": "Génération conservée, galerie dégradée.", "timeline": [{"time": "10:20", "event": "Alerte reçue"}, {"time": "10:28", "event": "Service restauré"}], "root_cause": "Cache de rendu saturé", "corrective_actions": [{"owner": "Équipe plateforme", "action": "Borner le cache", "due": "18/07/2026", "status": "En cours"}]},
        "procedure_operationnelle": {"code": "SOP-DOC-004", "title": "Publier un modèle documentaire", "version": "2.1", "owner": "Qualité documentaire", "effective_date": "1 août 2026", "purpose": "Garantir une publication reproductible et contrôlée.", "scope": "Tous les modèles internes.", "roles": [{"role": "Auteur", "responsibility": "Prépare le modèle"}, {"role": "Validateur", "responsibility": "Contrôle le rendu"}], "prerequisites": ["Source approuvée", "Données d'exemple"], "steps": [{"number": 1, "title": "Importer", "instruction": "Déposer la source dans Document Studio", "evidence": "Brouillon créé"}, {"number": 2, "title": "Vérifier", "instruction": "Comparer la source et le rendu", "evidence": "Validation enregistrée"}], "controls": ["Aucune macro", "Aucun lien externe actif"], "records": ["Manifeste", "Aperçu", "Journal de validation"]},
        "plan_action": {"title": "Plan d'action qualité documentaire", "sponsor": "Direction Produit", "owner": "Morgan Leroy", "period": "T3 2026", "objective": "Atteindre 100 % de rendus vérifiés.", "actions": [{"id": "ACT-01", "action": "Compléter les modèles prioritaires", "owner": "Équipe Documents", "due": "31/07/2026", "priority": "Haute", "status": "En cours", "indicator": "30/30 modèles"}], "governance": "Revue hebdomadaire chaque lundi", "success_metrics": ["Aucun rendu blanc", "Tous les alias testés"]},
    }
    result = dict(common)
    result.update(deepcopy(samples.get(name, {})))
    return result
