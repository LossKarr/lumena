"""
Prompts centralises - src/core_services/agent_service.py

Constantes de prompts pour AgentService.
Importe depuis: from src.prompts.services.agent_service_prompts import <NOM>
"""

_LLM_FACT_EXTRACT_PROMPT = (
    "Analyse cet échange et extrais les informations personnelles sur l'UTILISATEUR "
    "(PAS sur l'assistant). Retourne UNIQUEMENT un JSON valide.\n\n"
    "Clés possibles (ne retourne que celles trouvées dans l'échange) :\n"
    "- prénom_utilisateur: prénom de l'utilisateur\n"
    "- profession: métier, travail, domaine d'activité\n"
    "- ville: où il habite/vit\n"
    "- language: langue préférée\n"
    "- age: son âge\n"
    "- email: adresse email\n"
    "- centres_interet: passions, hobbies (liste séparée par virgules)\n"
    "- formality: tutoiement ou vouvoiement (d'après le ton de l'utilisateur)\n"
    "- registre_langue: familier, courant, soutenu (d'après le style de l'utilisateur)\n"
    "- relationship: comment l'utilisateur voit sa relation avec l'assistant\n"
    "- portfolio: URL de site/portfolio\n\n"
    "Règles STRICTES :\n"
    "- Si AUCUNE info perso n'est détectée, retourne exactement: {{}}\n"
    "- N'invente RIEN — ne retourne que ce qui est EXPLICITEMENT dit par l'utilisateur\n"
    "- Ignore les questions techniques, demandes d'aide, commandes — seules les infos personnelles comptent\n"
    "- Le JSON doit être parsable directement, sans markdown, sans explication\n\n"
    "Échange :\nUtilisateur : {user_msg}\nAssistant : {assistant_msg}\n\nJSON :"
)

