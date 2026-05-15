"""
UI/UX Pro Max Knowledge Base — Lumena
Extraite et adaptée du skill ui-ux-pro-max-skill-2.5.0

Contient:
- 79 palettes couleurs professionnelles (WCAG-compliant) par type de produit
- 57 font pairings par contexte (heading + body)
- 49 styles UI avec checklists
- 99 règles UX critics
- Mapping keywords → palette/font recommandés

Usage:
    from src.tools.ui_ux_knowledge import get_design_for_project
    design = get_design_for_project("site restaurant luxe")
"""

from __future__ import annotations
import random
from typing import Any

# ─────────────────────────────────────────────────────────────
# PALETTES PROFESSIONNELLES (WCAG-compliant, by product type)
# ─────────────────────────────────────────────────────────────

PRO_PALETTES: list[dict[str, Any]] = [
    # SaaS / Tech / IA
    {
        "name": "SaaS Trust Blue",
        "product_type": "SaaS (General)",
        "primary": "#2563EB", "secondary": "#3B82F6", "accent": "#EA580C",
        "background": "#F8FAFC", "foreground": "#1E293B",
        "card": "#FFFFFF", "muted": "#E9EFF8", "muted_fg": "#64748B",
        "border": "#E2E8F0", "theme": "light",
        "notes": "Trust blue + orange CTA",
        "keywords": ["saas", "logiciel", "software", "plateforme", "platform", "app", "service", "b2b"],
    },
    {
        "name": "Micro SaaS Indigo",
        "product_type": "Micro SaaS",
        "primary": "#6366F1", "secondary": "#818CF8", "accent": "#059669",
        "background": "#F5F3FF", "foreground": "#1E1B4B",
        "card": "#FFFFFF", "muted": "#EBEFF9", "muted_fg": "#64748B",
        "border": "#E0E7FF", "theme": "light",
        "notes": "Indigo primary + emerald CTA",
        "keywords": ["micro saas", "indie", "bootstrapped", "startup"],
    },
    {
        "name": "AI/Chatbot Purple",
        "product_type": "AI/Chatbot Platform",
        "primary": "#7C3AED", "secondary": "#A78BFA", "accent": "#0891B2",
        "background": "#FAF5FF", "foreground": "#1E1B4B",
        "card": "#FFFFFF", "muted": "#ECEEF9", "muted_fg": "#64748B",
        "border": "#DDD6FE", "theme": "light",
        "notes": "AI purple + cyan interactions",
        "keywords": ["ia", "ai", "chatbot", "gpt", "llm", "intelligence", "assistant", "bot"],
    },
    {
        "name": "Corporate Navy",
        "product_type": "B2B Service",
        "primary": "#0F172A", "secondary": "#334155", "accent": "#0369A1",
        "background": "#F8FAFC", "foreground": "#020617",
        "card": "#FFFFFF", "muted": "#E8ECF1", "muted_fg": "#64748B",
        "border": "#E2E8F0", "theme": "light",
        "notes": "Professional navy + blue CTA",
        "keywords": ["b2b", "entreprise", "corporate", "business", "professionnel", "conseil"],
    },
    # Finance / Crypto
    {
        "name": "Fintech Dark Gold",
        "product_type": "Fintech/Crypto",
        "primary": "#F59E0B", "secondary": "#FBBF24", "accent": "#8B5CF6",
        "background": "#0F172A", "foreground": "#F8FAFC",
        "card": "#222735", "muted": "#272F42", "muted_fg": "#94A3B8",
        "border": "#334155", "theme": "dark",
        "notes": "Gold trust + purple tech",
        "keywords": ["crypto", "blockchain", "web3", "nft", "defi", "fintech", "trading", "token"],
    },
    {
        "name": "NFT Web3 Neon",
        "product_type": "NFT/Web3 Platform",
        "primary": "#8B5CF6", "secondary": "#A78BFA", "accent": "#FBBF24",
        "background": "#0F0F23", "foreground": "#F8FAFC",
        "card": "#1E1D35", "muted": "#27273B", "muted_fg": "#94A3B8",
        "border": "#4C1D95", "theme": "dark",
        "notes": "Purple tech + gold value",
        "keywords": ["nft", "web3", "metaverse", "dao", "solana", "ethereum", "opensea"],
    },
    {
        "name": "Banking Navy Gold",
        "product_type": "Banking/Traditional Finance",
        "primary": "#0F172A", "secondary": "#1E3A8A", "accent": "#A16207",
        "background": "#F8FAFC", "foreground": "#020617",
        "card": "#FFFFFF", "muted": "#E8ECF1", "muted_fg": "#64748B",
        "border": "#E2E8F0", "theme": "light",
        "notes": "Trust navy + premium gold",
        "keywords": ["banque", "bank", "banking", "finance", "investissement", "épargne"],
    },
    {
        "name": "Analytics Dark Blue",
        "product_type": "Analytics Dashboard",
        "primary": "#1E40AF", "secondary": "#3B82F6", "accent": "#D97706",
        "background": "#F8FAFC", "foreground": "#1E3A8A",
        "card": "#FFFFFF", "muted": "#E9EEF6", "muted_fg": "#64748B",
        "border": "#DBEAFE", "theme": "light",
        "notes": "Blue data + amber highlights",
        "keywords": ["analytics", "dashboard", "data", "kpi", "statistiques", "rapport", "analyse"],
    },
    {
        "name": "Financial Dashboard Dark",
        "product_type": "Financial Dashboard",
        "primary": "#0F172A", "secondary": "#1E293B", "accent": "#22C55E",
        "background": "#020617", "foreground": "#F8FAFC",
        "card": "#0E1223", "muted": "#1A1E2F", "muted_fg": "#94A3B8",
        "border": "#334155", "theme": "dark",
        "notes": "Dark bg + green positive indicators",
        "keywords": ["trading", "bourse", "portfolio", "graphique", "chart", "finance", "comptabilité"],
    },
    # E-commerce
    {
        "name": "E-commerce Green",
        "product_type": "E-commerce",
        "primary": "#059669", "secondary": "#10B981", "accent": "#EA580C",
        "background": "#ECFDF5", "foreground": "#064E3B",
        "card": "#FFFFFF", "muted": "#E8F1F3", "muted_fg": "#64748B",
        "border": "#A7F3D0", "theme": "light",
        "notes": "Success green + urgency orange",
        "keywords": ["ecommerce", "boutique", "shop", "store", "vente", "produit", "catalogue", "panier"],
    },
    {
        "name": "Luxury Black Gold",
        "product_type": "E-commerce Luxury",
        "primary": "#1C1917", "secondary": "#44403C", "accent": "#A16207",
        "background": "#FAFAF9", "foreground": "#0C0A09",
        "card": "#FFFFFF", "muted": "#E8ECF0", "muted_fg": "#64748B",
        "border": "#D6D3D1", "theme": "light",
        "notes": "Premium dark + gold accent",
        "keywords": ["luxe", "luxury", "premium", "haut de gamme", "prestige", "bijoux", "joaillerie", "montre"],
    },
    {
        "name": "Subscription Purple",
        "product_type": "Subscription Box",
        "primary": "#D946EF", "secondary": "#E879F9", "accent": "#EA580C",
        "background": "#FDF4FF", "foreground": "#86198F",
        "card": "#FFFFFF", "muted": "#F0EEF9", "muted_fg": "#64748B",
        "border": "#F5D0FE", "theme": "light",
        "notes": "Excitement purple + urgency orange",
        "keywords": ["abonnement", "subscription", "box", "mensuel", "livraison"],
    },
    # Healthcare
    {
        "name": "Healthcare Teal",
        "product_type": "Healthcare App",
        "primary": "#0891B2", "secondary": "#22D3EE", "accent": "#059669",
        "background": "#ECFEFF", "foreground": "#164E63",
        "card": "#FFFFFF", "muted": "#E8F1F6", "muted_fg": "#64748B",
        "border": "#A5F3FC", "theme": "light",
        "notes": "Calm cyan + health green",
        "keywords": ["santé", "health", "médecin", "patient", "bien-être", "wellness"],
    },
    {
        "name": "Medical Clinic Teal",
        "product_type": "Medical Clinic",
        "primary": "#0891B2", "secondary": "#22D3EE", "accent": "#16A34A",
        "background": "#F0FDFA", "foreground": "#134E4A",
        "card": "#FFFFFF", "muted": "#E8F1F6", "muted_fg": "#64748B",
        "border": "#CCFBF1", "theme": "light",
        "notes": "Medical teal + health green",
        "keywords": ["clinique", "clinic", "médical", "médecin", "docteur", "hôpital", "cabinet médical"],
    },
    {
        "name": "Pharmacy Green",
        "product_type": "Pharmacy",
        "primary": "#15803D", "secondary": "#22C55E", "accent": "#0369A1",
        "background": "#F0FDF4", "foreground": "#14532D",
        "card": "#FFFFFF", "muted": "#E8F0F1", "muted_fg": "#64748B",
        "border": "#BBF7D0", "theme": "light",
        "notes": "Pharmacy green + trust blue",
        "keywords": ["pharmacie", "pharmacy", "médicament", "parapharmacie"],
    },
    {
        "name": "Mental Health Lavender",
        "product_type": "Mental Health App",
        "primary": "#8B5CF6", "secondary": "#C4B5FD", "accent": "#059669",
        "background": "#FAF5FF", "foreground": "#4C1D95",
        "card": "#FFFFFF", "muted": "#EDEFF9", "muted_fg": "#64748B",
        "border": "#EDE9FE", "theme": "light",
        "notes": "Calming lavender + wellness green",
        "keywords": ["mental", "psy", "thérapie", "méditation", "anxiété", "stress", "burnout"],
    },
    # Food & Restaurant
    {
        "name": "Restaurant Red Gold",
        "product_type": "Restaurant/Food Service",
        "primary": "#DC2626", "secondary": "#F87171", "accent": "#A16207",
        "background": "#FEF2F2", "foreground": "#450A0A",
        "card": "#FFFFFF", "muted": "#F0EDF1", "muted_fg": "#64748B",
        "border": "#FECACA", "theme": "light",
        "notes": "Appetizing red + warm gold",
        "keywords": ["restaurant", "cuisine", "chef", "menu", "plat", "gastronomie", "brasserie", "food"],
    },
    {
        "name": "Bakery Warm Brown",
        "product_type": "Bakery/Cafe",
        "primary": "#92400E", "secondary": "#B45309", "accent": "#92400E",
        "background": "#FEF3C7", "foreground": "#78350F",
        "card": "#FFFFFF", "muted": "#EDEEF0", "muted_fg": "#64748B",
        "border": "#FDE68A", "theme": "light",
        "notes": "Warm brown + cream white",
        "keywords": ["boulangerie", "pâtisserie", "café", "bakery", "coffee", "cake", "pain", "viennoiserie"],
    },
    {
        "name": "Brewery Burgundy",
        "product_type": "Brewery/Winery",
        "primary": "#7C2D12", "secondary": "#B91C1C", "accent": "#A16207",
        "background": "#FEF2F2", "foreground": "#450A0A",
        "card": "#FFFFFF", "muted": "#ECEDF0", "muted_fg": "#64748B",
        "border": "#FECACA", "theme": "light",
        "notes": "Deep burgundy + craft gold",
        "keywords": ["vin", "wine", "bière", "brasserie", "cave", "whisky", "spiritueux", "vignoble"],
    },
    # Creative / Media
    {
        "name": "Creative Agency Pink",
        "product_type": "Creative Agency",
        "primary": "#EC4899", "secondary": "#F472B6", "accent": "#0891B2",
        "background": "#FDF2F8", "foreground": "#831843",
        "card": "#FFFFFF", "muted": "#F1EEF5", "muted_fg": "#64748B",
        "border": "#FBCFE8", "theme": "light",
        "notes": "Bold pink + cyan accent",
        "keywords": ["agence", "agency", "créatif", "creative", "design", "branding", "marketing", "communication"],
    },
    {
        "name": "Portfolio Monochrome",
        "product_type": "Portfolio/Personal",
        "primary": "#18181B", "secondary": "#3F3F46", "accent": "#2563EB",
        "background": "#FAFAFA", "foreground": "#09090B",
        "card": "#FFFFFF", "muted": "#E8ECF0", "muted_fg": "#64748B",
        "border": "#E4E4E7", "theme": "light",
        "notes": "Monochrome + blue accent",
        "keywords": ["portfolio", "cv", "freelance", "développeur", "designer", "créateur", "artiste"],
    },
    {
        "name": "Photography Studio Black",
        "product_type": "Photography Studio",
        "primary": "#18181B", "secondary": "#27272A", "accent": "#F8FAFC",
        "background": "#000000", "foreground": "#FAFAFA",
        "card": "#0C0C0C", "muted": "#181818", "muted_fg": "#94A3B8",
        "border": "#3F3F46", "theme": "dark",
        "notes": "Pure black + white contrast",
        "keywords": ["photo", "photographe", "photography", "studio", "shooting", "portrait", "galerie"],
    },
    {
        "name": "Music Dark",
        "product_type": "Music Streaming",
        "primary": "#1E1B4B", "secondary": "#4338CA", "accent": "#22C55E",
        "background": "#0F0F23", "foreground": "#F8FAFC",
        "card": "#1B1B30", "muted": "#27273B", "muted_fg": "#94A3B8",
        "border": "#312E81", "theme": "dark",
        "notes": "Dark audio + play green",
        "keywords": ["musique", "music", "streaming", "playlist", "album", "artiste", "concert"],
    },
    {
        "name": "Gaming Neon Purple",
        "product_type": "Gaming",
        "primary": "#7C3AED", "secondary": "#A78BFA", "accent": "#F43F5E",
        "background": "#0F0F23", "foreground": "#E2E8F0",
        "card": "#1E1C35", "muted": "#27273B", "muted_fg": "#94A3B8",
        "border": "#4C1D95", "theme": "dark",
        "notes": "Neon purple + rose action",
        "keywords": ["gaming", "jeu", "esport", "fps", "rpg", "steam", "twitch", "console"],
    },
    {
        "name": "Video Streaming Dark",
        "product_type": "Video Streaming",
        "primary": "#0F0F23", "secondary": "#1E1B4B", "accent": "#E11D48",
        "background": "#000000", "foreground": "#F8FAFC",
        "card": "#0C0C0D", "muted": "#181818", "muted_fg": "#94A3B8",
        "border": "#312E81", "theme": "dark",
        "notes": "Cinema dark + play red",
        "keywords": ["video", "film", "cinéma", "série", "streaming", "VOD", "netflix"],
    },
    # Education
    {
        "name": "Education Indigo",
        "product_type": "Educational App",
        "primary": "#4F46E5", "secondary": "#818CF8", "accent": "#EA580C",
        "background": "#EEF2FF", "foreground": "#1E1B4B",
        "card": "#FFFFFF", "muted": "#EBEEF8", "muted_fg": "#64748B",
        "border": "#C7D2FE", "theme": "light",
        "notes": "Playful indigo + energetic orange",
        "keywords": ["éducation", "école", "cours", "formation", "apprendre", "étudiant", "université"],
    },
    {
        "name": "E-learning Teal",
        "product_type": "Online Course",
        "primary": "#0D9488", "secondary": "#2DD4BF", "accent": "#EA580C",
        "background": "#F0FDFA", "foreground": "#134E4A",
        "card": "#FFFFFF", "muted": "#E8F1F4", "muted_fg": "#64748B",
        "border": "#5EEAD4", "theme": "light",
        "notes": "Progress teal + achievement orange",
        "keywords": ["e-learning", "mooc", "cours en ligne", "certification", "formation en ligne"],
    },
    # Services
    {
        "name": "Real Estate Teal",
        "product_type": "Real Estate",
        "primary": "#0F766E", "secondary": "#14B8A6", "accent": "#0369A1",
        "background": "#F0FDFA", "foreground": "#134E4A",
        "card": "#FFFFFF", "muted": "#E8F0F3", "muted_fg": "#64748B",
        "border": "#99F6E4", "theme": "light",
        "notes": "Trust teal + professional blue",
        "keywords": ["immobilier", "real estate", "appartement", "maison", "location", "achat", "agence immobilière"],
    },
    {
        "name": "Legal Navy",
        "product_type": "Legal Services",
        "primary": "#1E3A8A", "secondary": "#1E40AF", "accent": "#B45309",
        "background": "#F8FAFC", "foreground": "#0F172A",
        "card": "#FFFFFF", "muted": "#E9EEF5", "muted_fg": "#64748B",
        "border": "#CBD5E1", "theme": "light",
        "notes": "Authority navy + trust gold",
        "keywords": ["avocat", "droit", "juridique", "legal", "notaire", "cabinet d'avocats", "juriste"],
    },
    {
        "name": "Travel Sky Blue",
        "product_type": "Travel/Tourism",
        "primary": "#0EA5E9", "secondary": "#38BDF8", "accent": "#EA580C",
        "background": "#F0F9FF", "foreground": "#0C4A6E",
        "card": "#FFFFFF", "muted": "#E8F2F8", "muted_fg": "#64748B",
        "border": "#BAE6FD", "theme": "light",
        "notes": "Sky blue + adventure orange",
        "keywords": ["voyage", "travel", "tourisme", "hôtel", "avion", "destination", "vacances", "séjour"],
    },
    {
        "name": "Hotel Luxury Navy",
        "product_type": "Hotel/Hospitality",
        "primary": "#1E3A8A", "secondary": "#3B82F6", "accent": "#A16207",
        "background": "#F8FAFC", "foreground": "#1E40AF",
        "card": "#FFFFFF", "muted": "#E9EEF5", "muted_fg": "#64748B",
        "border": "#BFDBFE", "theme": "light",
        "notes": "Luxury navy + gold service",
        "keywords": ["hôtel", "hotel", "hébergement", "chambre", "suite", "resort", "spa", "réservation"],
    },
    {
        "name": "Beauty Pink",
        "product_type": "Beauty/Spa/Wellness",
        "primary": "#EC4899", "secondary": "#F9A8D4", "accent": "#8B5CF6",
        "background": "#FDF2F8", "foreground": "#831843",
        "card": "#FFFFFF", "muted": "#F1EEF5", "muted_fg": "#64748B",
        "border": "#FBCFE8", "theme": "light",
        "notes": "Soft pink + lavender luxury",
        "keywords": ["beauté", "beauty", "spa", "esthétique", "coiffure", "cosmétique", "maquillage", "soin"],
    },
    {
        "name": "Wedding Pink Gold",
        "product_type": "Wedding/Events",
        "primary": "#DB2777", "secondary": "#F472B6", "accent": "#A16207",
        "background": "#FDF2F8", "foreground": "#831843",
        "card": "#FFFFFF", "muted": "#F0EDF4", "muted_fg": "#64748B",
        "border": "#FBCFE8", "theme": "light",
        "notes": "Romantic pink + elegant gold",
        "keywords": ["mariage", "wedding", "événement", "fête", "cérémonie", "réception", "noces"],
    },
    {
        "name": "Fitness Dark Orange",
        "product_type": "Fitness/Gym",
        "primary": "#F97316", "secondary": "#FB923C", "accent": "#22C55E",
        "background": "#1F2937", "foreground": "#F8FAFC",
        "card": "#313742", "muted": "#37414F", "muted_fg": "#94A3B8",
        "border": "#374151", "theme": "dark",
        "notes": "Energy orange + success green",
        "keywords": ["sport", "fitness", "gym", "musculation", "running", "coach", "entraînement"],
    },
    {
        "name": "Construction Grey",
        "product_type": "Construction/Architecture",
        "primary": "#64748B", "secondary": "#94A3B8", "accent": "#EA580C",
        "background": "#F8FAFC", "foreground": "#334155",
        "card": "#FFFFFF", "muted": "#EBF0F5", "muted_fg": "#64748B",
        "border": "#E2E8F0", "theme": "light",
        "notes": "Industrial grey + safety orange",
        "keywords": ["construction", "btp", "bâtiment", "architecte", "travaux", "ingénieur", "chantier"],
    },
    {
        "name": "Automotive Dark Red",
        "product_type": "Automotive/Car Dealership",
        "primary": "#1E293B", "secondary": "#334155", "accent": "#DC2626",
        "background": "#F8FAFC", "foreground": "#0F172A",
        "card": "#FFFFFF", "muted": "#E9EDF1", "muted_fg": "#64748B",
        "border": "#E2E8F0", "theme": "light",
        "notes": "Premium dark + action red",
        "keywords": ["voiture", "auto", "automobile", "garage", "concession", "mécanique", "car"],
    },
    {
        "name": "Productivity Teal",
        "product_type": "Productivity Tool",
        "primary": "#0D9488", "secondary": "#14B8A6", "accent": "#EA580C",
        "background": "#F0FDFA", "foreground": "#134E4A",
        "card": "#FFFFFF", "muted": "#E8F1F4", "muted_fg": "#64748B",
        "border": "#99F6E4", "theme": "light",
        "notes": "Teal focus + action orange",
        "keywords": ["productivité", "productivity", "gestion", "tâches", "organisation", "planning", "todo"],
    },
    {
        "name": "Social Rose",
        "product_type": "Social Media App",
        "primary": "#E11D48", "secondary": "#FB7185", "accent": "#2563EB",
        "background": "#FFF1F2", "foreground": "#881337",
        "card": "#FFFFFF", "muted": "#F0ECF2", "muted_fg": "#64748B",
        "border": "#FECDD3", "theme": "light",
        "notes": "Vibrant rose + engagement blue",
        "keywords": ["social", "réseau social", "communauté", "partage", "followers", "influenceur"],
    },
    {
        "name": "Coworking Amber",
        "product_type": "Coworking Space",
        "primary": "#F59E0B", "secondary": "#FBBF24", "accent": "#2563EB",
        "background": "#FFFBEB", "foreground": "#78350F",
        "card": "#FFFFFF", "muted": "#F1F2EF", "muted_fg": "#64748B",
        "border": "#FDE68A", "theme": "light",
        "notes": "Energetic amber + booking blue",
        "keywords": ["coworking", "bureau partagé", "espace de travail", "bureau", "open space"],
    },
    {
        "name": "Marketplace Purple Green",
        "product_type": "Marketplace",
        "primary": "#7C3AED", "secondary": "#A78BFA", "accent": "#16A34A",
        "background": "#FAF5FF", "foreground": "#4C1D95",
        "card": "#FFFFFF", "muted": "#ECEEF9", "muted_fg": "#64748B",
        "border": "#DDD6FE", "theme": "light",
        "notes": "Trust purple + transaction green",
        "keywords": ["marketplace", "marché", "place de marché", "vendeurs", "acheteurs", "annonces"],
    },
    {
        "name": "Startup Dark Indigo",
        "product_type": "Tech Startup",
        "primary": "#4F46E5", "secondary": "#6366F1", "accent": "#EA580C",
        "background": "#0F172A", "foreground": "#F8FAFC",
        "card": "#1E1B4B", "muted": "#312E81", "muted_fg": "#A5B4FC",
        "border": "#4338CA", "theme": "dark",
        "notes": "Dark tech startup + energetic orange",
        "keywords": ["startup", "tech startup", "innovation", "technologie", "disruption"],
    },
    {
        "name": "Insurance Blue Green",
        "product_type": "Insurance Platform",
        "primary": "#0369A1", "secondary": "#0EA5E9", "accent": "#16A34A",
        "background": "#F0F9FF", "foreground": "#0C4A6E",
        "card": "#FFFFFF", "muted": "#E7EFF5", "muted_fg": "#64748B",
        "border": "#BAE6FD", "theme": "light",
        "notes": "Security blue + protected green",
        "keywords": ["assurance", "insurance", "protection", "mutuelle", "garantie"],
    },
    {
        "name": "Logistics Blue Orange",
        "product_type": "Logistics/Delivery",
        "primary": "#2563EB", "secondary": "#3B82F6", "accent": "#EA580C",
        "background": "#EFF6FF", "foreground": "#1E40AF",
        "card": "#FFFFFF", "muted": "#E9EFF8", "muted_fg": "#64748B",
        "border": "#BFDBFE", "theme": "light",
        "notes": "Tracking blue + delivery orange",
        "keywords": ["livraison", "delivery", "logistique", "transport", "colis", "expédition", "suivi"],
    },
    {
        "name": "Smart Home Dark",
        "product_type": "Smart Home/IoT",
        "primary": "#1E293B", "secondary": "#334155", "accent": "#22C55E",
        "background": "#0F172A", "foreground": "#F8FAFC",
        "card": "#1B2336", "muted": "#272F42", "muted_fg": "#94A3B8",
        "border": "#475569", "theme": "dark",
        "notes": "Dark tech + status green",
        "keywords": ["iot", "smart home", "domotique", "connecté", "maison intelligente", "capteur"],
    },
    {
        "name": "Job Board Blue",
        "product_type": "Job Board/Recruitment",
        "primary": "#0369A1", "secondary": "#0EA5E9", "accent": "#16A34A",
        "background": "#F0F9FF", "foreground": "#0C4A6E",
        "card": "#FFFFFF", "muted": "#E7EFF5", "muted_fg": "#64748B",
        "border": "#BAE6FD", "theme": "light",
        "notes": "Professional blue + success green",
        "keywords": ["emploi", "job", "recrutement", "cv", "offre d'emploi", "rh", "ressources humaines"],
    },
    {
        "name": "News Red",
        "product_type": "News/Media",
        "primary": "#DC2626", "secondary": "#EF4444", "accent": "#1E40AF",
        "background": "#FEF2F2", "foreground": "#450A0A",
        "card": "#FFFFFF", "muted": "#F0EDF1", "muted_fg": "#64748B",
        "border": "#FECACA", "theme": "light",
        "notes": "Breaking red + link blue",
        "keywords": ["actualité", "news", "presse", "journal", "information", "média"],
    },
    {
        "name": "Magazine Editorial Black",
        "product_type": "Magazine/Blog",
        "primary": "#18181B", "secondary": "#3F3F46", "accent": "#EC4899",
        "background": "#FAFAFA", "foreground": "#09090B",
        "card": "#FFFFFF", "muted": "#E8ECF0", "muted_fg": "#64748B",
        "border": "#E4E4E7", "theme": "light",
        "notes": "Editorial black + accent pink",
        "keywords": ["blog", "magazine", "édito", "article", "rédaction", "contenu"],
    },
    {
        "name": "Non-profit Cyan",
        "product_type": "Non-profit/Charity",
        "primary": "#0891B2", "secondary": "#22D3EE", "accent": "#EA580C",
        "background": "#ECFEFF", "foreground": "#164E63",
        "card": "#FFFFFF", "muted": "#E8F1F6", "muted_fg": "#64748B",
        "border": "#A5F3FC", "theme": "light",
        "notes": "Compassion blue + action orange",
        "keywords": ["association", "ong", "charity", "don", "solidarité", "bénévolat", "humanitaire"],
    },
    {
        "name": "Florist Green Pink",
        "product_type": "Florist/Plant Shop",
        "primary": "#15803D", "secondary": "#22C55E", "accent": "#EC4899",
        "background": "#F0FDF4", "foreground": "#14532D",
        "card": "#FFFFFF", "muted": "#E8F0F1", "muted_fg": "#64748B",
        "border": "#BBF7D0", "theme": "light",
        "notes": "Natural green + floral pink",
        "keywords": ["fleuriste", "fleurs", "plantes", "jardin", "bouquet", "floral", "nature"],
    },
    {
        "name": "Dental Sky Blue",
        "product_type": "Dental Practice",
        "primary": "#0EA5E9", "secondary": "#38BDF8", "accent": "#0EA5E9",
        "background": "#F0F9FF", "foreground": "#0C4A6E",
        "card": "#FFFFFF", "muted": "#E8F2F8", "muted_fg": "#64748B",
        "border": "#BAE6FD", "theme": "light",
        "notes": "Fresh blue + clean white",
        "keywords": ["dentiste", "dental", "orthodontiste", "sourire", "dent", "blanchissement"],
    },
    {
        "name": "Agriculture Green Gold",
        "product_type": "Agriculture/Farm Tech",
        "primary": "#15803D", "secondary": "#22C55E", "accent": "#A16207",
        "background": "#F0FDF4", "foreground": "#14532D",
        "card": "#FFFFFF", "muted": "#E8F0F1", "muted_fg": "#64748B",
        "border": "#BBF7D0", "theme": "light",
        "notes": "Earth green + harvest gold",
        "keywords": ["agriculture", "ferme", "bio", "écologie", "nature", "alimentation", "récolte"],
    },
    {
        "name": "Podcast Dark",
        "product_type": "Podcast Platform",
        "primary": "#1E1B4B", "secondary": "#312E81", "accent": "#F97316",
        "background": "#0F0F23", "foreground": "#F8FAFC",
        "card": "#1B1B30", "muted": "#27273B", "muted_fg": "#94A3B8",
        "border": "#4338CA", "theme": "dark",
        "notes": "Dark audio + warm accent",
        "keywords": ["podcast", "radio", "audio", "épisode", "micro", "diffusion"],
    },
    {
        "name": "Dating Rose Warm",
        "product_type": "Dating App",
        "primary": "#E11D48", "secondary": "#FB7185", "accent": "#EA580C",
        "background": "#FFF1F2", "foreground": "#881337",
        "card": "#FFFFFF", "muted": "#F0ECF2", "muted_fg": "#64748B",
        "border": "#FECDD3", "theme": "light",
        "notes": "Romantic rose + warm orange",
        "keywords": ["rencontre", "dating", "amour", "couple", "célibataire", "rendez-vous"],
    },
    {
        "name": "Theater Dark Gold",
        "product_type": "Theater/Cinema",
        "primary": "#1E1B4B", "secondary": "#312E81", "accent": "#CA8A04",
        "background": "#0F0F23", "foreground": "#F8FAFC",
        "card": "#1B1B30", "muted": "#27273B", "muted_fg": "#94A3B8",
        "border": "#4338CA", "theme": "dark",
        "notes": "Dramatic dark + spotlight gold",
        "keywords": ["théâtre", "cinéma", "spectacle", "scène", "billet", "film", "événement culturel"],
    },
    {
        "name": "Developer Dark Terminal",
        "product_type": "Developer Tool",
        "primary": "#0F172A", "secondary": "#1E293B", "accent": "#22C55E",
        "background": "#020617", "foreground": "#F8FAFC",
        "card": "#0E1223", "muted": "#1A1E2F", "muted_fg": "#94A3B8",
        "border": "#334155", "theme": "dark",
        "notes": "Terminal dark + success green",
        "keywords": ["développeur", "developer", "dev", "code", "api", "cli", "outil", "github"],
    },
    {
        "name": "Remote Work Calm",
        "product_type": "Remote Work/Collaboration",
        "primary": "#6366F1", "secondary": "#818CF8", "accent": "#059669",
        "background": "#F5F3FF", "foreground": "#312E81",
        "card": "#FFFFFF", "muted": "#EBEFF9", "muted_fg": "#64748B",
        "border": "#E0E7FF", "theme": "light",
        "notes": "Calm indigo + success green",
        "keywords": ["télétravail", "remote", "collaboration", "équipe", "réunion", "slack", "teams"],
    },
    {
        "name": "Museum Gallery White",
        "product_type": "Museum/Gallery",
        "primary": "#18181B", "secondary": "#27272A", "accent": "#18181B",
        "background": "#FAFAFA", "foreground": "#09090B",
        "card": "#FFFFFF", "muted": "#E8ECF0", "muted_fg": "#64748B",
        "border": "#E4E4E7", "theme": "light",
        "notes": "Gallery black + white space",
        "keywords": ["musée", "galerie", "art", "exposition", "culture", "artiste", "œuvre"],
    },
    {
        "name": "Event Purple Orange",
        "product_type": "Event Management",
        "primary": "#7C3AED", "secondary": "#A78BFA", "accent": "#EA580C",
        "background": "#FAF5FF", "foreground": "#4C1D95",
        "card": "#FFFFFF", "muted": "#ECEEF9", "muted_fg": "#64748B",
        "border": "#DDD6FE", "theme": "light",
        "notes": "Excitement purple + action orange",
        "keywords": ["événement", "event", "conférence", "salon", "festival", "concert", "exposition"],
    },
    {
        "name": "Pet App Orange",
        "product_type": "Pet Tech App",
        "primary": "#F97316", "secondary": "#FB923C", "accent": "#2563EB",
        "background": "#FFF7ED", "foreground": "#9A3412",
        "card": "#FFFFFF", "muted": "#F1F0F0", "muted_fg": "#64748B",
        "border": "#FED7AA", "theme": "light",
        "notes": "Playful orange + trust blue",
        "keywords": ["animaux", "pet", "chien", "chat", "vétérinaire", "clinique vétérinaire", "animalerie"],
    },
    {
        "name": "EV Charging Cyan",
        "product_type": "EV/Charging Ecosystem",
        "primary": "#0891B2", "secondary": "#22D3EE", "accent": "#16A34A",
        "background": "#ECFEFF", "foreground": "#164E63",
        "card": "#FFFFFF", "muted": "#E8F1F6", "muted_fg": "#64748B",
        "border": "#A5F3FC", "theme": "light",
        "notes": "Electric cyan + eco green",
        "keywords": ["électrique", "ev", "bornes", "recharge", "véhicule électrique", "écologie"],
    },
    {
        "name": "Home Services Blue",
        "product_type": "Home Services",
        "primary": "#1E40AF", "secondary": "#3B82F6", "accent": "#EA580C",
        "background": "#EFF6FF", "foreground": "#1E3A8A",
        "card": "#FFFFFF", "muted": "#E9EEF6", "muted_fg": "#64748B",
        "border": "#BFDBFE", "theme": "light",
        "notes": "Professional blue + urgent orange",
        "keywords": ["plombier", "électricien", "serrurier", "rénovation", "bricolage", "artisan", "dépannage"],
    },

    # ── New product types from ui-ux-pro-max v2.5.0 ──
    {
        "name": "Government Public Service Palette",
        "product_type": "Government/Public Service",
        "primary": "#0F172A", "secondary": "#334155", "accent": "#0369A1",
        "background": "#F8FAFC", "foreground": "#020617",
        "card": "#FFFFFF", "muted": "#E8ECF1", "muted_fg": "#64748B",
        "border": "#E2E8F0", "theme": "light",
        "notes": "High contrast navy + blue",
        "keywords": ["gouvernement", "service public", "mairie", "administration", "civic"],
    },
    {
        "name": "Design System Component Librar Palette",
        "product_type": "Design System/Component Library",
        "primary": "#4F46E5", "secondary": "#6366F1", "accent": "#EA580C",
        "background": "#EEF2FF", "foreground": "#312E81",
        "card": "#FFFFFF", "muted": "#EBEEF8", "muted_fg": "#64748B",
        "border": "#C7D2FE", "theme": "light",
        "notes": "Indigo brand + doc hierarchy [Accent adjusted from #F97316 f",
        "keywords": ["design system", "composants", "ui kit", "storybook", "librairie"],
    },
    {
        "name": "Knowledge Base Documentation Palette",
        "product_type": "Knowledge Base/Documentation",
        "primary": "#475569", "secondary": "#64748B", "accent": "#2563EB",
        "background": "#F8FAFC", "foreground": "#1E293B",
        "card": "#FFFFFF", "muted": "#EAEFF3", "muted_fg": "#64748B",
        "border": "#E2E8F0", "theme": "light",
        "notes": "Neutral grey + link blue",
        "keywords": ["documentation", "wiki", "knowledge base", "aide", "faq", "docs"],
    },
    {
        "name": "Luxury Premium Brand Palette",
        "product_type": "Luxury/Premium Brand",
        "primary": "#1C1917", "secondary": "#44403C", "accent": "#A16207",
        "background": "#FAFAF9", "foreground": "#0C0A09",
        "card": "#FFFFFF", "muted": "#E8ECF0", "muted_fg": "#64748B",
        "border": "#D6D3D1", "theme": "light",
        "notes": "Premium black + gold accent [Accent adjusted from #CA8A04 fo",
        "keywords": ["luxe premium", "marque luxe", "haute couture", "maroquinerie"],
    },
    {
        "name": "Childcare Daycare Palette",
        "product_type": "Childcare/Daycare",
        "primary": "#F472B6", "secondary": "#FBCFE8", "accent": "#16A34A",
        "background": "#FDF2F8", "foreground": "#9D174D",
        "card": "#FFFFFF", "muted": "#F1F0F6", "muted_fg": "#64748B",
        "border": "#FCE7F3", "theme": "light",
        "notes": "Soft pink + safe green [Accent adjusted from #22C55E for WCA",
        "keywords": ["crèche", "garderie", "enfant", "bébé", "nursery", "nounou"],
    },
    {
        "name": "Senior Care Elderly Palette",
        "product_type": "Senior Care/Elderly",
        "primary": "#0369A1", "secondary": "#38BDF8", "accent": "#16A34A",
        "background": "#F0F9FF", "foreground": "#0C4A6E",
        "card": "#FFFFFF", "muted": "#E7EFF5", "muted_fg": "#64748B",
        "border": "#E0F2FE", "theme": "light",
        "notes": "Calm blue + reassuring green [Accent adjusted from #22C55E f",
        "keywords": ["senior", "personnes âgées", "ehpad", "aide à domicile", "retraite"],
    },
    {
        "name": "Veterinary Clinic Palette",
        "product_type": "Veterinary Clinic",
        "primary": "#0D9488", "secondary": "#14B8A6", "accent": "#EA580C",
        "background": "#F0FDFA", "foreground": "#134E4A",
        "card": "#FFFFFF", "muted": "#E8F1F4", "muted_fg": "#64748B",
        "border": "#99F6E4", "theme": "light",
        "notes": "Caring teal + warm orange [Accent adjusted from #F97316 for ",
        "keywords": ["vétérinaire", "vet", "clinique animale", "animaux"],
    },
    {
        "name": "Airline Palette",
        "product_type": "Airline",
        "primary": "#1E3A8A", "secondary": "#3B82F6", "accent": "#EA580C",
        "background": "#EFF6FF", "foreground": "#1E40AF",
        "card": "#FFFFFF", "muted": "#E9EEF5", "muted_fg": "#64748B",
        "border": "#BFDBFE", "theme": "light",
        "notes": "Sky blue + booking orange [Accent adjusted from #F97316 for ",
        "keywords": ["compagnie aérienne", "airline", "vol", "billet avion", "aéroport"],
    },
    {
        "name": "Freelancer Platform Palette",
        "product_type": "Freelancer Platform",
        "primary": "#6366F1", "secondary": "#818CF8", "accent": "#16A34A",
        "background": "#EEF2FF", "foreground": "#312E81",
        "card": "#FFFFFF", "muted": "#EBEFF9", "muted_fg": "#64748B",
        "border": "#C7D2FE", "theme": "light",
        "notes": "Creative indigo + hire green [Accent adjusted from #22C55E f",
        "keywords": ["freelance", "pigiste", "indépendant", "mission", "prestation"],
    },
    {
        "name": "Marketing Agency Palette",
        "product_type": "Marketing Agency",
        "primary": "#EC4899", "secondary": "#F472B6", "accent": "#0891B2",
        "background": "#FDF2F8", "foreground": "#831843",
        "card": "#FFFFFF", "muted": "#F1EEF5", "muted_fg": "#64748B",
        "border": "#FBCFE8", "theme": "light",
        "notes": "Bold pink + creative cyan [Accent adjusted from #06B6D4 for ",
        "keywords": ["marketing", "publicité", "ads", "campagne", "growth"],
    },
    {
        "name": "Membership Community Palette",
        "product_type": "Membership/Community",
        "primary": "#7C3AED", "secondary": "#A78BFA", "accent": "#16A34A",
        "background": "#FAF5FF", "foreground": "#4C1D95",
        "card": "#FFFFFF", "muted": "#ECEEF9", "muted_fg": "#64748B",
        "border": "#DDD6FE", "theme": "light",
        "notes": "Community purple + join green [Accent adjusted from #22C55E ",
        "keywords": ["communauté", "community", "membre", "forum", "club"],
    },
    {
        "name": "Newsletter Platform Palette",
        "product_type": "Newsletter Platform",
        "primary": "#0369A1", "secondary": "#0EA5E9", "accent": "#EA580C",
        "background": "#F0F9FF", "foreground": "#0C4A6E",
        "card": "#FFFFFF", "muted": "#E7EFF5", "muted_fg": "#64748B",
        "border": "#BAE6FD", "theme": "light",
        "notes": "Trust blue + subscribe orange [Accent adjusted from #F97316 ",
        "keywords": ["newsletter", "email marketing", "mailing", "abonné"],
    },
    {
        "name": "Digital Products Downloads Palette",
        "product_type": "Digital Products/Downloads",
        "primary": "#6366F1", "secondary": "#818CF8", "accent": "#16A34A",
        "background": "#EEF2FF", "foreground": "#312E81",
        "card": "#FFFFFF", "muted": "#EBEFF9", "muted_fg": "#64748B",
        "border": "#C7D2FE", "theme": "light",
        "notes": "Digital indigo + buy green [Accent adjusted from #22C55E for",
        "keywords": ["produit numérique", "ebook", "template", "digital", "téléchargement"],
    },
    {
        "name": "Church Religious Organization Palette",
        "product_type": "Church/Religious Organization",
        "primary": "#7C3AED", "secondary": "#A78BFA", "accent": "#A16207",
        "background": "#FAF5FF", "foreground": "#4C1D95",
        "card": "#FFFFFF", "muted": "#ECEEF9", "muted_fg": "#64748B",
        "border": "#DDD6FE", "theme": "light",
        "notes": "Spiritual purple + warm gold [Accent adjusted from #CA8A04 f",
        "keywords": ["église", "paroisse", "mosquée", "temple", "religieux", "culte"],
    },
    {
        "name": "Sports Team Club Palette",
        "product_type": "Sports Team/Club",
        "primary": "#DC2626", "secondary": "#EF4444", "accent": "#DC2626",
        "background": "#FEF2F2", "foreground": "#7F1D1D",
        "card": "#FFFFFF", "muted": "#F0EDF1", "muted_fg": "#64748B",
        "border": "#FECACA", "theme": "light",
        "notes": "Team red + championship gold [Accent adjusted from #FBBF24 f",
        "keywords": ["équipe sportive", "club", "football", "rugby", "basket", "match"],
    },
    {
        "name": "Language Learning App Palette",
        "product_type": "Language Learning App",
        "primary": "#4F46E5", "secondary": "#818CF8", "accent": "#16A34A",
        "background": "#EEF2FF", "foreground": "#312E81",
        "card": "#FFFFFF", "muted": "#EBEEF8", "muted_fg": "#64748B",
        "border": "#C7D2FE", "theme": "light",
        "notes": "Learning indigo + progress green [Accent adjusted from #22C5",
        "keywords": ["langue", "apprentissage langue", "anglais", "espagnol", "duolingo"],
    },
    {
        "name": "Coding Bootcamp Palette",
        "product_type": "Coding Bootcamp",
        "primary": "#0F172A", "secondary": "#1E293B", "accent": "#22C55E",
        "background": "#020617", "foreground": "#F8FAFC",
        "card": "#0E1223", "muted": "#1A1E2F", "muted_fg": "#94A3B8",
        "border": "#334155", "theme": "dark",
        "notes": "Terminal dark + success green",
        "keywords": ["bootcamp", "formation code", "coding school", "développeur junior"],
    },
    {
        "name": "Cybersecurity Platform Palette",
        "product_type": "Cybersecurity Platform",
        "primary": "#00FF41", "secondary": "#0D0D0D", "accent": "#FF3333",
        "background": "#000000", "foreground": "#E0E0E0",
        "card": "#0C130E", "muted": "#181818", "muted_fg": "#94A3B8",
        "border": "#1F1F1F", "theme": "dark",
        "notes": "Matrix green + alert red",
        "keywords": ["cybersécurité", "security", "firewall", "pentest", "audit sécurité"],
    },
    {
        "name": "Biotech   Life Sciences Palette",
        "product_type": "Biotech / Life Sciences",
        "primary": "#0EA5E9", "secondary": "#0284C7", "accent": "#059669",
        "background": "#F0F9FF", "foreground": "#0C4A6E",
        "card": "#FFFFFF", "muted": "#E8F2F8", "muted_fg": "#64748B",
        "border": "#BAE6FD", "theme": "light",
        "notes": "DNA blue + life green [Accent adjusted from #10B981 for WCAG",
        "keywords": ["biotech", "biotechnologie", "pharma", "recherche", "labo"],
    },
    {
        "name": "Space Tech   Aerospace Palette",
        "product_type": "Space Tech / Aerospace",
        "primary": "#F8FAFC", "secondary": "#94A3B8", "accent": "#3B82F6",
        "background": "#0B0B10", "foreground": "#F8FAFC",
        "card": "#1E1E23", "muted": "#232328", "muted_fg": "#94A3B8",
        "border": "#1E293B", "theme": "dark",
        "notes": "Star white + launch blue",
        "keywords": ["espace", "space", "aérospatial", "fusée", "satellite", "nasa"],
    },
    {
        "name": "Architecture   Interior Palette",
        "product_type": "Architecture / Interior",
        "primary": "#171717", "secondary": "#404040", "accent": "#A16207",
        "background": "#FFFFFF", "foreground": "#171717",
        "card": "#FFFFFF", "muted": "#E8ECF0", "muted_fg": "#64748B",
        "border": "#E5E5E5", "theme": "light",
        "notes": "Minimal black + accent gold [Accent adjusted from #D4AF37 fo",
        "keywords": ["architecte", "intérieur", "décoration", "aménagement", "design intérieur"],
    },
    {
        "name": "Generative Art Platform Palette",
        "product_type": "Generative Art Platform",
        "primary": "#18181B", "secondary": "#3F3F46", "accent": "#EC4899",
        "background": "#FAFAFA", "foreground": "#09090B",
        "card": "#FFFFFF", "muted": "#E8ECF0", "muted_fg": "#64748B",
        "border": "#E4E4E7", "theme": "light",
        "notes": "Canvas neutral + creative pink",
        "keywords": ["art génératif", "generative art", "nft art", "créatif numérique"],
    },
    {
        "name": "Sustainable Energy   Climate T Palette",
        "product_type": "Sustainable Energy / Climate Tech",
        "primary": "#059669", "secondary": "#10B981", "accent": "#059669",
        "background": "#ECFDF5", "foreground": "#064E3B",
        "card": "#FFFFFF", "muted": "#E8F1F3", "muted_fg": "#64748B",
        "border": "#A7F3D0", "theme": "light",
        "notes": "Nature green + solar gold [Accent adjusted from #FBBF24 for ",
        "keywords": ["énergie renouvelable", "solaire", "éolien", "climat", "green tech"],
    },
    {
        "name": "Personal Finance Tracker Palette",
        "product_type": "Personal Finance Tracker",
        "primary": "#1E40AF", "secondary": "#3B82F6", "accent": "#059669",
        "background": "#0F172A", "foreground": "#FFFFFF",
        "card": "#192134", "muted": "#101A34", "muted_fg": "#94A3B8",
        "border": "#334155", "theme": "dark",
        "notes": "Trust blue + profit green on dark",
        "keywords": ["finance personnelle", "budget", "dépenses", "épargne", "économies"],
    },
    {
        "name": "Chat and Messaging App Palette",
        "product_type": "Chat & Messaging App",
        "primary": "#2563EB", "secondary": "#6366F1", "accent": "#059669",
        "background": "#FFFFFF", "foreground": "#0F172A",
        "card": "#FFFFFF", "muted": "#F1F5FD", "muted_fg": "#64748B",
        "border": "#E4ECFC", "theme": "light",
        "notes": "Messenger blue + online green",
        "keywords": ["messagerie", "chat", "messager", "whatsapp", "telegram"],
    },
    {
        "name": "Notes and Writing App Palette",
        "product_type": "Notes & Writing App",
        "primary": "#78716C", "secondary": "#A8A29E", "accent": "#D97706",
        "background": "#FFFBEB", "foreground": "#0F172A",
        "card": "#FFFFFF", "muted": "#F6F6F6", "muted_fg": "#64748B",
        "border": "#EEEDED", "theme": "light",
        "notes": "Warm ink + amber accent on cream",
        "keywords": ["notes", "écriture", "carnet", "notion", "obsidian", "rédaction"],
    },
    {
        "name": "Habit Tracker Palette",
        "product_type": "Habit Tracker",
        "primary": "#D97706", "secondary": "#F59E0B", "accent": "#059669",
        "background": "#FFFBEB", "foreground": "#0F172A",
        "card": "#FFFFFF", "muted": "#FCF6F0", "muted_fg": "#64748B",
        "border": "#FAEEE1", "theme": "light",
        "notes": "Streak amber + habit green",
        "keywords": ["habitude", "habit", "routine", "streak", "objectif quotidien"],
    },
    {
        "name": "Food Delivery   On-Demand Palette",
        "product_type": "Food Delivery / On-Demand",
        "primary": "#EA580C", "secondary": "#F97316", "accent": "#2563EB",
        "background": "#FFF7ED", "foreground": "#0F172A",
        "card": "#FFFFFF", "muted": "#FDF4F0", "muted_fg": "#64748B",
        "border": "#FCEAE1", "theme": "light",
        "notes": "Appetizing orange + trust blue",
        "keywords": ["livraison repas", "food delivery", "uber eats", "commande", "plat livré"],
    },
    {
        "name": "Ride Hailing   Transportation Palette",
        "product_type": "Ride Hailing / Transportation",
        "primary": "#1E293B", "secondary": "#334155", "accent": "#2563EB",
        "background": "#0F172A", "foreground": "#FFFFFF",
        "card": "#192134", "muted": "#10182B", "muted_fg": "#94A3B8",
        "border": "#334155", "theme": "dark",
        "notes": "Map dark + route blue",
        "keywords": ["vtc", "taxi", "covoiturage", "transport", "trajet"],
    },
    {
        "name": "Recipe and Cooking App Palette",
        "product_type": "Recipe & Cooking App",
        "primary": "#9A3412", "secondary": "#C2410C", "accent": "#059669",
        "background": "#FFFBEB", "foreground": "#0F172A",
        "card": "#FFFFFF", "muted": "#F8F2F0", "muted_fg": "#64748B",
        "border": "#F2E6E2", "theme": "light",
        "notes": "Warm terracotta + fresh green",
        "keywords": ["recette", "cuisine maison", "cooking", "ingrédients", "plat"],
    },
    {
        "name": "Meditation and Mindfulness Palette",
        "product_type": "Meditation & Mindfulness",
        "primary": "#7C3AED", "secondary": "#8B5CF6", "accent": "#059669",
        "background": "#FAF5FF", "foreground": "#0F172A",
        "card": "#FFFFFF", "muted": "#F7F3FD", "muted_fg": "#64748B",
        "border": "#EFE7FC", "theme": "light",
        "notes": "Calm lavender + mindful green",
        "keywords": ["méditation", "pleine conscience", "mindfulness", "relaxation", "zen"],
    },
    {
        "name": "Weather App Palette",
        "product_type": "Weather App",
        "primary": "#0284C7", "secondary": "#0EA5E9", "accent": "#F59E0B",
        "background": "#F0F9FF", "foreground": "#0F172A",
        "card": "#FFFFFF", "muted": "#EFF7FB", "muted_fg": "#64748B",
        "border": "#E0F0F8", "theme": "light",
        "notes": "Sky blue + sun amber",
        "keywords": ["météo", "weather", "prévisions", "température", "climat"],
    },
    {
        "name": "CRM and Client Management Palette",
        "product_type": "CRM & Client Management",
        "primary": "#2563EB", "secondary": "#3B82F6", "accent": "#059669",
        "background": "#F8FAFC", "foreground": "#0F172A",
        "card": "#FFFFFF", "muted": "#F1F5FD", "muted_fg": "#64748B",
        "border": "#E4ECFC", "theme": "light",
        "notes": "Professional blue + deal green",
        "keywords": ["crm", "client", "pipeline", "prospect", "relation client"],
    },
    {
        "name": "Booking and Appointment App Palette",
        "product_type": "Booking & Appointment App",
        "primary": "#0284C7", "secondary": "#0EA5E9", "accent": "#059669",
        "background": "#F0F9FF", "foreground": "#0F172A",
        "card": "#FFFFFF", "muted": "#EFF7FB", "muted_fg": "#64748B",
        "border": "#E0F0F8", "theme": "light",
        "notes": "Calendar blue + available green",
        "keywords": ["réservation", "rendez-vous", "booking", "agenda", "créneau"],
    },
    {
        "name": "Photo Editor and Filters Palette",
        "product_type": "Photo Editor & Filters",
        "primary": "#7C3AED", "secondary": "#6366F1", "accent": "#0891B2",
        "background": "#0F172A", "foreground": "#FFFFFF",
        "card": "#192134", "muted": "#171939", "muted_fg": "#94A3B8",
        "border": "#334155", "theme": "dark",
        "notes": "Editor violet + filter cyan on dark",
        "keywords": ["photo", "éditeur photo", "filtre", "retouche", "image"],
    },
    {
        "name": "Music Creation and Beat Maker Palette",
        "product_type": "Music Creation & Beat Maker",
        "primary": "#7C3AED", "secondary": "#6366F1", "accent": "#22C55E",
        "background": "#0F172A", "foreground": "#FFFFFF",
        "card": "#192134", "muted": "#171939", "muted_fg": "#94A3B8",
        "border": "#334155", "theme": "dark",
        "notes": "Studio purple + waveform green on dark",
        "keywords": ["création musicale", "beat", "daw", "musique production", "studio"],
    },
    {
        "name": "Home Decoration and Interior D Palette",
        "product_type": "Home Decoration & Interior Design",
        "primary": "#78716C", "secondary": "#A8A29E", "accent": "#D97706",
        "background": "#FAF5F2", "foreground": "#0F172A",
        "card": "#FFFFFF", "muted": "#F6F6F6", "muted_fg": "#64748B",
        "border": "#EEEDED", "theme": "light",
        "notes": "Interior warm grey + gold accent",
        "keywords": ["déco intérieur", "home décor", "meuble", "intérieur maison"],
    },
]


# ─────────────────────────────────────────────────────────────
# FONT PAIRINGS (heading + body, contextualisés)
# ─────────────────────────────────────────────────────────────

PRO_FONT_PAIRINGS: list[dict[str, Any]] = [
    {
        "name": "Tech Startup",
        "heading": "Space Grotesk", "body": "DM Sans",
        "heading_weights": "400;500;600;700",
        "body_weights": "400;500;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=DM+Sans:wght@400;500;700&display=swap');",
        "css_vars": "--font-heading: 'Space Grotesk'; --font-body: 'DM Sans';",
        "keywords": ["tech", "startup", "saas", "ia", "ai", "dev", "logiciel", "software"],
    },
    {
        "name": "Modern Professional",
        "heading": "Poppins", "body": "Open Sans",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&family=Open+Sans:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Poppins'; --font-body: 'Open Sans';",
        "keywords": ["corporate", "business", "professionnel", "entreprise", "b2b", "conseil"],
    },
    {
        "name": "Classic Elegant",
        "heading": "Playfair Display", "body": "Inter",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Playfair Display'; --font-body: 'Inter';",
        "keywords": ["luxe", "luxury", "premium", "élégant", "mode", "fashion", "haut de gamme", "bijoux"],
    },
    {
        "name": "Minimal Swiss",
        "heading": "Inter", "body": "Inter",
        "heading_weights": "300;400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Inter'; --font-body: 'Inter';",
        "keywords": ["dashboard", "admin", "data", "analytics", "outil", "minimal", "simple"],
    },
    {
        "name": "Wellness Calm",
        "heading": "Lora", "body": "Raleway",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Lora:wght@400;500;600;700&family=Raleway:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Lora'; --font-body: 'Raleway';",
        "keywords": ["wellness", "bien-être", "yoga", "méditation", "santé", "nature", "bio", "organic"],
    },
    {
        "name": "Bold Statement",
        "heading": "Bebas Neue", "body": "Source Sans 3",
        "heading_weights": "400",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Source+Sans+3:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Bebas Neue'; --font-body: 'Source Sans 3';",
        "keywords": ["sport", "fitness", "gym", "musculation", "agence", "marketing", "événement", "concert"],
    },
    {
        "name": "Developer Mono",
        "heading": "JetBrains Mono", "body": "IBM Plex Sans",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'JetBrains Mono'; --font-body: 'IBM Plex Sans';",
        "keywords": ["développeur", "developer", "code", "cli", "terminal", "devops", "api", "github", "hack"],
    },
    {
        "name": "Crypto Web3",
        "heading": "Orbitron", "body": "Exo 2",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;600;700&family=Exo+2:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Orbitron'; --font-body: 'Exo 2';",
        "keywords": ["crypto", "blockchain", "web3", "nft", "defi", "futuriste", "metaverse"],
    },
    {
        "name": "Gaming Bold",
        "heading": "Russo One", "body": "Chakra Petch",
        "heading_weights": "400",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Russo+One&family=Chakra+Petch:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Russo One'; --font-body: 'Chakra Petch';",
        "keywords": ["gaming", "jeu", "esport", "fps", "rpg", "action", "compétition"],
    },
    {
        "name": "Restaurant Menu",
        "heading": "Playfair Display SC", "body": "Karla",
        "heading_weights": "400;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Playfair+Display+SC:wght@400;700&family=Karla:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Playfair Display SC'; --font-body: 'Karla';",
        "keywords": ["restaurant", "menu", "cuisine", "gastronomie", "brasserie", "traiteur"],
    },
    {
        "name": "Financial Trust",
        "heading": "IBM Plex Sans", "body": "IBM Plex Sans",
        "heading_weights": "300;400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'IBM Plex Sans'; --font-body: 'IBM Plex Sans';",
        "keywords": ["banque", "finance", "assurance", "investissement", "comptabilité", "fintech"],
    },
    {
        "name": "Medical Clean",
        "heading": "Figtree", "body": "Noto Sans",
        "heading_weights": "300;400;500;600;700",
        "body_weights": "300;400;500;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Figtree:wght@300;400;500;600;700&family=Noto+Sans:wght@300;400;500;700&display=swap');",
        "css_vars": "--font-heading: 'Figtree'; --font-body: 'Noto Sans';",
        "keywords": ["médical", "clinique", "hôpital", "docteur", "pharmacie", "santé"],
    },
    {
        "name": "Legal Professional",
        "heading": "EB Garamond", "body": "Lato",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;500;600;700&family=Lato:wght@300;400;700&display=swap');",
        "css_vars": "--font-heading: 'EB Garamond'; --font-body: 'Lato';",
        "keywords": ["avocat", "droit", "juridique", "legal", "notaire"],
    },
    {
        "name": "Real Estate Luxury",
        "heading": "Cinzel", "body": "Josefin Sans",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;500;600;700&family=Josefin+Sans:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Cinzel'; --font-body: 'Josefin Sans';",
        "keywords": ["immobilier", "luxe immobilier", "appartement", "architecture", "intérieur"],
    },
    {
        "name": "Friendly SaaS",
        "heading": "Plus Jakarta Sans", "body": "Plus Jakarta Sans",
        "heading_weights": "300;400;500;600;700;800",
        "body_weights": "300;400;500;600;700;800",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap');",
        "css_vars": "--font-heading: 'Plus Jakarta Sans'; --font-body: 'Plus Jakarta Sans';",
        "keywords": ["saas", "app", "application", "productivité", "web", "outil", "dashboard"],
    },
    {
        "name": "Fashion Forward",
        "heading": "Syne", "body": "Manrope",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;500;600;700;800",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700&family=Manrope:wght@300;400;500;600;700;800&display=swap');",
        "css_vars": "--font-heading: 'Syne'; --font-body: 'Manrope';",
        "keywords": ["mode", "fashion", "beauté", "cosmétique", "design", "créatif", "artiste", "galerie"],
    },
    {
        "name": "Educational Geometric",
        "heading": "Outfit", "body": "Work Sans",
        "heading_weights": "300;400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Work+Sans:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Outfit'; --font-body: 'Work Sans';",
        "keywords": ["éducation", "formation", "cours", "école", "université", "apprentissage"],
    },
    {
        "name": "Sport Condensed",
        "heading": "Barlow Condensed", "body": "Barlow",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600;700&family=Barlow:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Barlow Condensed'; --font-body: 'Barlow';",
        "keywords": ["sport", "fitness", "athlète", "compétition", "marathon", "running"],
    },
    {
        "name": "Wedding Romance",
        "heading": "Great Vibes", "body": "Cormorant Infant",
        "heading_weights": "400",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Great+Vibes&family=Cormorant+Infant:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Great Vibes'; --font-body: 'Cormorant Infant';",
        "keywords": ["mariage", "wedding", "romantique", "cérémonie", "fleuriste", "événement"],
    },
    {
        "name": "Dashboard Data",
        "heading": "Fira Code", "body": "Fira Sans",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Fira Code'; --font-body: 'Fira Sans';",
        "keywords": ["données", "analytics", "finance data", "graphique", "tableau", "rapport"],
    },

    # ── New pairings from ui-ux-pro-max v2.5.0 ──
    {
        "name": "Retro Vintage",
        "heading": "Abril Fatface", "body": "Merriweather",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Abril+Fatface&family=Merriweather:wght@300;400;700&display=swap');",
        "css_vars": "--font-heading: 'Abril Fatface'; --font-body: 'Merriweather';",
        "keywords": ["retro", "vintage", "nostalgic", "dramatic", "decorative", "bold"],
    },
    {
        "name": "Luxury Serif",
        "heading": "Cormorant", "body": "Montserrat",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Cormorant:wght@400;500;600;700&family=Montserrat:wght@300;400;500;600;700&display=swap');",
        "css_vars": "--font-heading: 'Cormorant'; --font-body: 'Montserrat';",
        "keywords": ["luxury", "high-end", "fashion", "elegant", "refined", "premium"],
    },
    {
        "name": "Korean Modern",
        "heading": "Noto Sans KR", "body": "Noto Sans KR",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700&display=swap');",
        "css_vars": "--font-heading: 'Noto Sans KR'; --font-body: 'Noto Sans KR';",
        "keywords": ["korean", "modern", "clean", "professional", "multilingual", "readable"],
    },
    {
        "name": "Accessibility First",
        "heading": "Atkinson Hyperlegible", "body": "Atkinson Hyperlegible",
        "heading_weights": "400;500;600;700",
        "body_weights": "300;400;500;600;700",
        "css_import": "@import url('https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap');",
        "css_vars": "--font-heading: 'Atkinson Hyperlegible'; --font-body: 'Atkinson Hyperlegible';",
        "keywords": ["accessible", "readable", "inclusive", "wcag", "dyslexia-friendly", "clear"],
    },
]


# ─────────────────────────────────────────────────────────────
# UI STYLES (from ui-ux-pro-max v2.5.0, expanded)
# ─────────────────────────────────────────────────────────────

PRO_UI_STYLES: list[dict[str, Any]] = [
    {
        "name": "Glassmorphism",
        "css_rules": "backdrop-filter:blur(20px); background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.12); border-radius:16px; box-shadow:0 4px 30px rgba(0,0,0,0.1);",
        "best_for": ["SaaS", "AI", "startup", "tech", "fintech", "crypto"],
        "keywords": ["glass", "glassmorphism", "transparent", "blur", "modern", "tech"],
    },
    {
        "name": "Neumorphism",
        "css_rules": "background:#e0e5ec; border-radius:14px; box-shadow:8px 8px 16px #b8bec7,-8px -8px 16px #ffffff;",
        "best_for": ["wellness", "health", "meditation", "soft UI"],
        "keywords": ["neumorphism", "soft UI", "3d soft", "relief", "wellness"],
    },
    {
        "name": "Minimalist Premium",
        "css_rules": "padding:8rem 0; background:#fff; font-size:clamp(2.5rem,5vw,5rem); font-weight:800; letter-spacing:-0.02em;",
        "best_for": ["portfolio", "luxury", "agency", "editorial"],
        "keywords": ["minimal", "clean", "white space", "simple", "luxury"],
    },
    {
        "name": "Dark Luxury",
        "css_rules": "background:#050505; color:#fff; border:1px solid rgba(255,255,255,0.06); letter-spacing:0.1em;",
        "best_for": ["luxury", "premium", "fashion", "photography"],
        "keywords": ["dark luxury", "premium dark", "black", "gold", "élégant"],
    },
    {
        "name": "Gradient Heavy",
        "css_rules": "background:var(--gradient); -webkit-background-clip:text; -webkit-text-fill-color:transparent; filter:drop-shadow(0 0 20px rgba(var(--primary-rgb),0.4));",
        "best_for": ["creative", "agency", "startup", "entertainment"],
        "keywords": ["gradient", "colorful", "vibrant", "saturated"],
    },
    {
        "name": "Editorial Magazine",
        "css_rules": "column-gap:4rem; grid-template-columns:1fr 1.5fr; border-bottom:1px solid #eee; padding-bottom:4rem;",
        "best_for": ["news", "blog", "magazine", "publishing"],
        "keywords": ["editorial", "magazine", "grid", "journalism", "publication"],
    },
    {
        "name": "Brutalist Bold",
        "css_rules": "border:3px solid #000; border-radius:0; font-weight:900; text-transform:uppercase; padding:1rem 2rem;",
        "best_for": ["portfolio", "creative agency", "art", "experimental"],
        "keywords": ["brutal", "brutalism", "raw", "bold", "stark"],
    },
    {
        "name": "Bento Grid",
        "css_rules": "display:grid; grid-template-columns:repeat(4,1fr); grid-template-rows:auto; gap:1rem; border-radius:16px; overflow:hidden;",
        "best_for": ["SaaS features", "portfolio", "product showcase"],
        "keywords": ["bento", "grid", "cards", "feature grid"],
    },
    {
        "name": "Neubrutalism",
        "css_rules": "border:2px solid #000; box-shadow:4px 4px 0 #000; border-radius:4px; background:var(--accent);",
        "best_for": ["startup", "fintech", "creative", "bold brand"],
        "keywords": ["neubrutalism", "neubrutalist", "offset shadow", "outline"],
    },
    {
        "name": "Aurora UI",
        "css_rules": "background:radial-gradient(ellipse at top,rgba(var(--primary-rgb),0.2),transparent 70%),radial-gradient(ellipse at bottom,rgba(var(--accent),0.15),transparent 70%); backdrop-filter:blur(40px);",
        "best_for": ["AI tools", "creative", "futuristic dashboard"],
        "keywords": ["aurora", "iridescent", "halo", "glow", "futuriste"],
    },
    {
        "name": "Organic Soft",
        "css_rules": "border-radius:32px; background:var(--card); box-shadow:0 8px 32px rgba(0,0,0,0.08); padding:2.5rem;",
        "best_for": ["wellness", "children", "food", "lifestyle"],
        "keywords": ["organic", "blob", "soft", "rounded", "friendly", "warm"],
    },
    {
        "name": "Cyberpunk HUD",
        "css_rules": "border:1px solid rgba(0,217,255,0.3); background:rgba(0,217,255,0.05); box-shadow:0 0 20px rgba(0,217,255,0.2),inset 0 0 20px rgba(0,217,255,0.05); font-family:monospace;",
        "best_for": ["gaming", "crypto", "sci-fi", "cybersecurity"],
        "keywords": ["cyberpunk", "hud", "neon", "sci-fi", "futuriste", "gaming"],
    },

    # ── Enrichment from ui-ux-pro-max v2.5.0 ──
    {
        "name": "3D Hyperrealism",
        "css_rules": "transform:translate3d(0,0,0); perspective:1000px; box-shadow:0 25px 50px rgba(0,0,0,0.25); backface-visibility:hidden;",
        "best_for": ["gaming", "product showcase", "e-commerce luxury", "architecture"],
        "keywords": ["3d", "hyperrealism", "realistic", "depth", "spatial", "immersive"],
    },
    {
        "name": "Vibrant Block",
        "css_rules": "display:grid; gap:48px; font-size:32px; background:var(--accent); color:#000; border-radius:0; font-weight:800;",
        "best_for": ["startup", "creative agency", "gaming", "entertainment", "social media"],
        "keywords": ["vibrant", "block", "bold", "energetic", "playful", "colorful", "bloc"],
    },
    {
        "name": "Dark Mode OLED",
        "css_rules": "background:#000000; color:#E0E0E0; border:1px solid rgba(255,255,255,0.08); text-shadow:none;",
        "best_for": ["coding", "music", "entertainment", "dashboard", "night mode"],
        "keywords": ["oled", "dark mode", "dark theme", "midnight", "deep black", "sombre"],
    },
    {
        "name": "Claymorphism",
        "css_rules": "border-radius:20px; border:3px solid rgba(0,0,0,0.08); box-shadow:inset -2px -2px 8px rgba(0,0,0,0.1),4px 4px 8px rgba(0,0,0,0.15); background:linear-gradient(135deg,var(--card),var(--muted));",
        "best_for": ["education", "children", "SaaS", "creative tools", "fun apps"],
        "keywords": ["clay", "claymorphism", "3d soft", "bubbly", "playful", "toy", "enfant"],
    },
    {
        "name": "Retro Futurism",
        "css_rules": "color:#00FFFF; text-shadow:0 0 10px currentColor; background:#1A1A2E; font-family:monospace; border:1px solid rgba(0,255,255,0.3);",
        "best_for": ["gaming", "entertainment", "music", "tech brand", "nostalgia"],
        "keywords": ["retro", "futurism", "80s", "neon", "synthwave", "crt", "scanlines", "rétro"],
    },
    {
        "name": "Flat Design",
        "css_rules": "box-shadow:none; background:var(--primary); border-radius:4px; color:#fff; fill:currentColor; stroke-width:2px;",
        "best_for": ["web apps", "mobile", "startup MVP", "SaaS", "dashboard"],
        "keywords": ["flat", "2d", "simple", "clean", "material", "mvp"],
    },
    {
        "name": "Liquid Glass",
        "css_rules": "backdrop-filter:blur(40px) saturate(180%); background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1); border-radius:24px; animation:morph 8s ease-in-out infinite;",
        "best_for": ["premium SaaS", "luxury e-commerce", "creative platforms", "branding"],
        "keywords": ["liquid", "glass", "morphing", "fluid", "translucent", "liquide"],
    },
    {
        "name": "Motion Driven",
        "css_rules": "animation:fadeInUp 0.6s ease-out; transform:translateY(0); will-change:transform; scroll-behavior:smooth;",
        "best_for": ["portfolio", "storytelling", "interactive", "entertainment"],
        "keywords": ["motion", "animation", "scroll", "parallax", "cinematic", "transition"],
    },
    {
        "name": "Accessible Ethical",
        "css_rules": "color-scheme:light dark; font-size:1rem; outline:3px solid var(--accent); outline-offset:2px; border-radius:8px;",
        "best_for": ["government", "healthcare", "education", "legal", "public service"],
        "keywords": ["accessible", "wcag", "a11y", "inclusive", "éthique", "handicap"],
    },
    {
        "name": "AI Native UI",
        "css_rules": "background:radial-gradient(ellipse at 50% 0%,rgba(99,102,241,0.15),transparent 60%); border:1px solid rgba(99,102,241,0.2); border-radius:20px; font-family:'Inter',sans-serif;",
        "best_for": ["AI tools", "chatbot", "LLM", "generative", "tech startup"],
        "keywords": ["ai native", "chatbot ui", "llm", "generative", "conversational"],
    },
    {
        "name": "Y2K Aesthetic",
        "css_rules": "background:linear-gradient(180deg,#FFE4F3,#E4F0FF); border:2px solid #FF69B4; border-radius:20px; font-family:'Comic Sans MS',cursive; color:#9B59B6;",
        "best_for": ["fashion", "beauty", "social", "nostalgia", "pop culture"],
        "keywords": ["y2k", "2000s", "bubblegum", "pink", "nostalgic", "pop"],
    },
    {
        "name": "Dimensional Layering",
        "css_rules": "box-shadow:0 1px 1px rgba(0,0,0,0.08),0 4px 8px rgba(0,0,0,0.05),0 16px 32px rgba(0,0,0,0.04); border-radius:16px; transform:translateZ(0);",
        "best_for": ["SaaS", "dashboard", "fintech", "enterprise"],
        "keywords": ["layered", "depth", "elevation", "stacked", "z-index", "couches"],
    },
    {
        "name": "Kinetic Typography",
        "css_rules": "font-size:clamp(3rem,8vw,8rem); font-weight:900; letter-spacing:-0.04em; line-height:0.95; mix-blend-mode:difference;",
        "best_for": ["portfolio", "agency", "art", "event", "festival"],
        "keywords": ["kinetic", "typography", "big text", "hero text", "typographie"],
    },
    {
        "name": "Parallax Storytelling",
        "css_rules": "background-attachment:fixed; background-size:cover; min-height:100vh; display:flex; align-items:center; scroll-snap-type:y mandatory;",
        "best_for": ["storytelling", "portfolio narrative", "product launch", "NGO"],
        "keywords": ["parallax", "storytelling", "scroll", "narrative", "histoire"],
    },
    {
        "name": "Spatial UI VisionOS",
        "css_rules": "backdrop-filter:blur(60px) saturate(200%); background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.06); border-radius:32px; padding:2rem; box-shadow:0 8px 40px rgba(0,0,0,0.3);",
        "best_for": ["VR/AR", "spatial computing", "futuristic", "premium tech"],
        "keywords": ["spatial", "visionos", "apple vision", "vr", "ar", "immersif"],
    },
    {
        "name": "E-Ink Paper",
        "css_rules": "background:#F5F1EB; color:#2C2C2C; font-family:'Literata','Georgia',serif; border:none; box-shadow:none; line-height:1.8;",
        "best_for": ["blog", "reading", "documentation", "notes", "writing"],
        "keywords": ["paper", "e-ink", "journal", "livre", "papier", "lecture", "reading"],
    },
    {
        "name": "Gen Z Maximalism",
        "css_rules": "background:linear-gradient(135deg,#FF006E,#8338EC,#3A86FF); border:3px solid #000; border-radius:0; font-weight:900; mix-blend-mode:screen;",
        "best_for": ["social media", "entertainment", "fashion", "meme", "pop culture"],
        "keywords": ["maximalism", "gen z", "chaos", "loud", "colorful", "gen-z"],
    },
    {
        "name": "Swiss Modernism 2.0",
        "css_rules": "display:grid; grid-template-columns:repeat(12,1fr); gap:1.5rem; font-family:'Helvetica Neue','Arial',sans-serif; letter-spacing:0.05em; text-transform:uppercase;",
        "best_for": ["corporate", "architecture", "museum", "editorial", "brand"],
        "keywords": ["swiss", "modernism", "grid", "helvetica", "structured", "suisse"],
    },
    {
        "name": "Gradient Mesh Aurora",
        "css_rules": "background:conic-gradient(from 180deg at 50% 50%,var(--primary),var(--accent),var(--secondary),var(--primary)); filter:blur(80px); opacity:0.4; position:absolute; inset:-20%;",
        "best_for": ["creative", "AI", "branding", "landing page", "startup"],
        "keywords": ["mesh", "gradient mesh", "aurora evolved", "conic", "blob gradient"],
    },
    {
        "name": "Interactive Cursor",
        "css_rules": "cursor:none; mix-blend-mode:difference; transition:transform 0.15s ease-out; pointer-events:all;",
        "best_for": ["portfolio", "creative agency", "interactive art", "experimental"],
        "keywords": ["cursor", "interactive", "mouse", "hover effect", "custom cursor"],
    },
]


# ─────────────────────────────────────────────────────────────
# UX RULES (pre-delivery quality checklist)
# ─────────────────────────────────────────────────────────────

UX_CRITICAL_RULES = """
═══ RÈGLES UX CRITIQUES (ui-ux-pro-max v2.5.0) ═══

♿ ACCESSIBILITÉ (WCAG 2.1 AA — OBLIGATOIRE):
- Contraste couleur minimum 4.5:1 pour le texte normal (3:1 pour le gros texte et les composants UI)
- Focus visible et stylisé sur TOUS les éléments cliquables — :focus {{ outline:2px solid var(--accent); outline-offset:2px; }}
- Alt text descriptif sur TOUTES les images — pas alt="" sur les images informatives
- aria-label sur les boutons icônes (icône seule sans texte visible)
- Navigation au clavier COMPLÈTE — pas de focus-trap, ordre logique
- Labels associés sur chaque champ de formulaire — pas de placeholder seul comme label
- Hiérarchie des titres respectée — h1→h2→h3 (jamais sauter un niveau)
- Couleur NON utilisée seule pour transmettre l'info (ex: erreur = rouge + icône + texte)
- prefers-reduced-motion: @media (prefers-reduced-motion: reduce) {{ * {{ animation-duration:0.01ms !important; transition-duration:0.01ms !important; }} }}

👆 INTERACTIONS TOUCH (Mobile-first):
- Zone tactile minimum 44×44px (boutons, liens, icônes) — Touch target 48dp recommandé
- Espacement minimum 8px entre zones tactiles
- Pas de hover-only interactions sur mobile (hover:* doit avoir un équivalent :active)
- Tap delay: touch-action:manipulation sur les éléments cliquables
- Pas de double-tap zoom sur les boutons
- Retour visuel immédiat (<16ms) sur tap

⚡ PERFORMANCE PERÇUE:
- Skeleton loading sur toutes les sections chargées dynamiquement
- Images: lazy loading (loading="lazy"), srcset pour responsive, aspect-ratio défini
- CSS: variables CSS pour cohérence, no !important excessif
- Font display: swap — @font-face {{ font-display:swap; }}
- CLS (Cumulative Layout Shift) < 0.1 — réserver l'espace des images
- LCP (Largest Contentful Paint) image: priorité de chargement

📐 LAYOUT & RESPONSIVE:
- Mobile-first: media queries min-width (pas max-width)
- Container max-width 1280px, padding horizontal 1rem-2rem
- Pas de débordement horizontal — overflow-x:hidden sur body + html
- Grid fluide: auto-fit/auto-fill + minmax() au lieu de breakpoints fixes
- Images: max-width:100%; height:auto sur TOUTES les images
- Viewport meta: <meta name="viewport" content="width=device-width,initial-scale=1">
- Line-height 1.5-1.7 pour le corps du texte
- Font-size minimum 16px pour le corps (pas de 12px sur mobile)

📝 FORMULAIRES & FEEDBACK:
- Validation en temps réel avec messages d'erreur clairs et spécifiques
- Champs requis signalés VISUELLEMENT (astérisque + aria-required="true")
- Message d'erreur: rouge + icône ⚠ + texte explicatif (jamais juste rouge)
- État de chargement sur les boutons de submit (disabled + spinner)
- Toast/notification success après action réussie (disparaît après 4-5s)
- Confirmation destructive (modal de confirmation avant suppression)
- Autofill compatible: autocomplete="email/name/tel/address" sur les champs appropriés
- type="email/tel/url/number" pour la validation native et le bon clavier mobile

🧭 NAVIGATION:
- Active state visible dans la navigation principale
- Breadcrumbs pour les pages à plus de 2 niveaux de profondeur
- Back button fonctionnel — pas besoin du bouton retour du navigateur
- Skip navigation link: <a href="#main-content" class="skip-link">Aller au contenu principal</a>
- Header sticky avec z-index élevé et padding-top compensé dans les sections

✨ MICRO-ANIMATIONS (principes):
- Durée: 150-300ms pour UI feedback, 300-600ms pour transitions de page
- Easing: cubic-bezier(0.4, 0, 0.2, 1) pour les animations matérielles
- Hover buttons: translateY(-2px) + box-shadow (pas scale > 1.05)
- Cards: translateY(-4px) à -8px au hover + shadow augmenté
- Loading: spinner (rotate 1s linear infinite) ou skeleton pulsant
- Pas d'animations en boucle qui distraient (autoplay seulement si préférence réduite OFF)
"""



# ─────────────────────────────────────────────────────────────
# LANDING PAGE PATTERNS (from ui-ux-pro-max v2.5.0)
# ─────────────────────────────────────────────────────────────

LANDING_PATTERNS: list[dict[str, Any]] = [
    {
        "name": "Hero + Features + CTA",
        "keywords": ["hero", "hero-centric", "hero-centric design", "features", "feature-rich", "feature-rich showcase", "cta", "call-to-action"],
        "sections": "1. Hero with headline/image, 2. Value prop, 3. Key features (3-5), 4. CTA section, 5. Footer",
        "cta_placement": "Hero (sticky) + Bottom",
        "color_strategy": "Hero: Brand primary or vibrant. Features: Card bg #FAFAFA. CTA: Contrasting accent color",
        "conversion_tip": "Deep CTA placement. Use contrasting color (at least 7:1 contrast ratio). Sticky navbar CTA.",
    },
    {
        "name": "Hero + Testimonials + CTA",
        "keywords": ["hero", "testimonials", "social-proof", "social-proof-focused", "social proof focused", "trust", "reviews", "cta"],
        "sections": "1. Hero, 2. Problem statement, 3. Solution overview, 4. Testimonials carousel, 5. CTA",
        "cta_placement": "Hero (sticky) + Post-testimonials",
        "color_strategy": "Hero: Brand color. Testimonials: Light bg #F5F5F5. Quotes: Italic, muted color #666. CTA: Vibrant",
        "conversion_tip": "Social proof before CTA. Use 3-5 testimonials. Include photo + name + role. CTA after social proof.",
    },
    {
        "name": "Product Demo + Features",
        "keywords": ["demo", "product-demo", "features", "showcase", "interactive", "interactive-product-demo", "interactive product demo"],
        "sections": "1. Hero, 2. Product video/mockup (center), 3. Feature breakdown per section, 4. Comparison (optional), 5. CTA",
        "cta_placement": "Video center + CTA right/bottom",
        "color_strategy": "Video surround: Brand color overlay. Features: Icon color #0080FF. Text: Dark #222",
        "conversion_tip": "Embedded product demo increases engagement. Use interactive mockup if possible. Auto-play video muted.",
    },
    {
        "name": "Minimal Single Column",
        "keywords": ["minimal", "simple", "direct", "minimal & direct", "minimal-direct", "single-column", "clean"],
        "sections": "1. Hero headline, 2. Short description, 3. Benefit bullets (3 max), 4. CTA, 5. Footer",
        "cta_placement": "Center, large CTA button",
        "color_strategy": "Minimalist: Brand + white #FFFFFF + accent. Buttons: High contrast 7:1+. Text: Black/Dark grey",
        "conversion_tip": "Single CTA focus. Large typography. Lots of whitespace. No nav clutter. Mobile-first.",
    },
    {
        "name": "Funnel (3-Step Conversion)",
        "keywords": ["funnel", "conversion", "conversion-optimized", "conversion optimized", "steps", "wizard", "onboarding"],
        "sections": "1. Hero, 2. Step 1 (problem), 3. Step 2 (solution), 4. Step 3 (action), 5. CTA progression",
        "cta_placement": "Each step: mini-CTA. Final: main CTA",
        "color_strategy": "Step colors: 1 (Red/Problem), 2 (Orange/Process), 3 (Green/Solution). CTA: Brand color",
        "conversion_tip": "Progressive disclosure. Show only essential info per step. Use progress indicators. Multiple CTAs.",
    },
    {
        "name": "Comparison Table + CTA",
        "keywords": ["comparison", "table", "compare", "versus", "cta"],
        "sections": "1. Hero, 2. Problem intro, 3. Comparison table (product vs competitors), 4. Pricing (optional), 5. CTA",
        "cta_placement": "Table: Right column. CTA: Below table",
        "color_strategy": "Table: Alternating rows (white/light grey). Your product: Highlight #FFFACD (light yellow) or green. Text: Dark",
        "conversion_tip": "Use comparison to show unique value. Highlight your product row. Include 'free trial' in pricing row.",
    },
    {
        "name": "Lead Magnet + Form",
        "keywords": ["lead", "form", "signup", "capture", "email", "magnet"],
        "sections": "1. Hero (benefit headline), 2. Lead magnet preview (ebook cover, checklist, etc), 3. Form (minimal fields), 4. CTA submit",
        "cta_placement": "Form CTA: Submit button",
        "color_strategy": "Lead magnet: Professional design. Form: Clean white bg. Inputs: Light border #CCCCCC. CTA: Brand color",
        "conversion_tip": "Form fields ≤ 3 for best conversion. Offer valuable lead magnet preview. Show form submission progress.",
    },
    {
        "name": "Pricing Page + CTA",
        "keywords": ["pricing", "plans", "tiers", "comparison", "cta"],
        "sections": "1. Hero (pricing headline), 2. Price comparison cards, 3. Feature comparison table, 4. FAQ section, 5. Final CTA",
        "cta_placement": "Each card: CTA button. Sticky CTA in nav",
        "color_strategy": "Free: Grey, Starter: Blue, Pro: Green/Gold, Enterprise: Dark. Cards: 1px border, shadow",
        "conversion_tip": "Recommend starter plan (pre-select/highlight). Show annual discount (20-30%). Use FAQs to address concerns.",
    },
    {
        "name": "Video-First Hero",
        "keywords": ["video", "hero", "media", "visual", "engaging"],
        "sections": "1. Hero with video background, 2. Key features overlay, 3. Benefits section, 4. CTA",
        "cta_placement": "Overlay on video (center/bottom) + Bottom section",
        "color_strategy": "Dark overlay 60% on video. Brand accent for CTA. White text on dark.",
        "conversion_tip": "86% higher engagement with video. Add captions for accessibility. Compress video for performance.",
    },
    {
        "name": "Scroll-Triggered Storytelling",
        "keywords": ["storytelling", "scroll", "narrative", "story", "immersive"],
        "sections": "1. Intro hook, 2. Chapter 1 (problem), 3. Chapter 2 (journey), 4. Chapter 3 (solution), 5. Climax CTA",
        "cta_placement": "End of each chapter (mini) + Final climax CTA",
        "color_strategy": "Progressive reveal. Each chapter has distinct color. Building intensity.",
        "conversion_tip": "Narrative increases time-on-page 3x. Use progress indicator. Mobile: simplify animations.",
    },
    {
        "name": "AI Personalization Landing",
        "keywords": ["ai", "personalization", "smart", "recommendation", "dynamic"],
        "sections": "1. Dynamic hero (personalized), 2. Relevant features, 3. Tailored testimonials, 4. Smart CTA",
        "cta_placement": "Context-aware placement based on user segment",
        "color_strategy": "Adaptive based on user data. A/B test color variations per segment.",
        "conversion_tip": "20%+ conversion with personalization. Requires analytics integration. Fallback for new users.",
    },
    {
        "name": "Waitlist/Coming Soon",
        "keywords": ["waitlist", "coming-soon", "launch", "early-access", "notify"],
        "sections": "1. Hero with countdown, 2. Product teaser/preview, 3. Email capture form, 4. Social proof (waitlist count)",
        "cta_placement": "Email form prominent (above fold) + Sticky form on scroll",
        "color_strategy": "Anticipation: Dark + accent highlights. Countdown in brand color. Urgency indicators.",
        "conversion_tip": "Scarcity + exclusivity. Show waitlist count. Early access benefits. Referral program.",
    },
    {
        "name": "Comparison Table Focus",
        "keywords": ["comparison", "table", "versus", "compare", "features"],
        "sections": "1. Hero (problem statement), 2. Comparison matrix (you vs competitors), 3. Feature deep-dive, 4. Winner CTA",
        "cta_placement": "After comparison table (highlighted row) + Bottom",
        "color_strategy": "Your product column highlighted (accent bg or green). Competitors neutral. Checkmarks green.",
        "conversion_tip": "Show value vs competitors. 35% higher conversion. Be factual. Include pricing if favorable.",
    },
    {
        "name": "Pricing-Focused Landing",
        "keywords": ["pricing", "price", "cost", "plans", "subscription"],
        "sections": "1. Hero (value proposition), 2. Pricing cards (3 tiers), 3. Feature comparison, 4. FAQ, 5. Final CTA",
        "cta_placement": "Each pricing card + Sticky CTA in nav + Bottom",
        "color_strategy": "Popular plan highlighted (brand color border/bg). Free: grey. Enterprise: dark/premium.",
        "conversion_tip": "Annual discount 20-30%. Recommend mid-tier (most popular badge). Address objections in FAQ.",
    },
    {
        "name": "App Store Style Landing",
        "keywords": ["app", "mobile", "download", "store", "install"],
        "sections": "1. Hero with device mockup, 2. Screenshots carousel, 3. Features with icons, 4. Reviews/ratings, 5. Download CTAs",
        "cta_placement": "Download buttons prominent (App Store + Play Store) throughout",
        "color_strategy": "Dark/light matching app store feel. Star ratings in gold. Screenshots with device frames.",
        "conversion_tip": "Show real screenshots. Include ratings (4.5+ stars). QR code for mobile. Platform-specific CTAs.",
    },
    {
        "name": "FAQ/Documentation Landing",
        "keywords": ["faq", "documentation", "help", "support", "questions", "faq/documentation", "knowledge base"],
        "sections": "1. Hero with search bar, 2. Popular categories, 3. FAQ accordion, 4. Contact/support CTA",
        "cta_placement": "Search bar prominent + Contact CTA for unresolved questions",
        "color_strategy": "Clean, high readability. Minimal color. Category icons in brand color. Success green for resolved.",
        "conversion_tip": "Reduce support tickets. Track search analytics. Show related articles. Contact escalation path.",
    },
    {
        "name": "Immersive/Interactive Experience",
        "keywords": ["immersive", "interactive", "experience", "3d", "animation", "immersive/interactive experience"],
        "sections": "1. Full-screen interactive element, 2. Guided product tour, 3. Key benefits revealed, 4. CTA after completion",
        "cta_placement": "After interaction complete + Skip option for impatient users",
        "color_strategy": "Immersive experience colors. Dark background for focus. Highlight interactive elements.",
        "conversion_tip": "40% higher engagement. Performance trade-off. Provide skip option. Mobile fallback essential.",
    },
    {
        "name": "Event/Conference Landing",
        "keywords": ["event", "conference", "meetup", "registration", "schedule", "hero-centric design", "hero-centric"],
        "sections": "1. Hero (date/location/countdown), 2. Speakers grid, 3. Agenda/schedule, 4. Sponsors, 5. Register CTA",
        "cta_placement": "Register CTA sticky + After speakers + Bottom",
        "color_strategy": "Urgency colors (countdown). Event branding. Speaker cards professional. Sponsor logos neutral.",
        "conversion_tip": "Early bird pricing with deadline. Social proof (past attendees). Speaker credibility. Multi-ticket discounts.",
    },
    {
        "name": "Product Review/Ratings Focused",
        "keywords": ["reviews", "ratings", "testimonials", "social-proof", "social-proof-focused", "stars"],
        "sections": "1. Hero (product + aggregate rating), 2. Rating breakdown, 3. Individual reviews, 4. Buy/CTA",
        "cta_placement": "After reviews summary + Buy button alongside reviews",
        "color_strategy": "Trust colors. Star ratings gold. Verified badge green. Review sentiment colors.",
        "conversion_tip": "User-generated content builds trust. Show verified purchases. Filter by rating. Respond to negative reviews.",
    },
    {
        "name": "Community/Forum Landing",
        "keywords": ["community", "forum", "social", "members", "discussion"],
        "sections": "1. Hero (community value prop), 2. Popular topics/categories, 3. Active members showcase, 4. Join CTA",
        "cta_placement": "Join button prominent + After member showcase",
        "color_strategy": "Warm, welcoming. Member photos add humanity. Topic badges in brand colors. Activity indicators green.",
        "conversion_tip": "Show active community (member count, posts today). Highlight benefits. Preview content. Easy onboarding.",
    },
    {
        "name": "Before-After Transformation",
        "keywords": ["before-after", "transformation", "results", "comparison"],
        "sections": "1. Hero (problem state), 2. Transformation slider/comparison, 3. How it works, 4. Results CTA",
        "cta_placement": "After transformation reveal + Bottom",
        "color_strategy": "Contrast: muted/grey (before) vs vibrant/colorful (after). Success green for results.",
        "conversion_tip": "Visual proof of value. 45% higher conversion. Real results. Specific metrics. Guarantee offer.",
    },
    {
        "name": "Marketplace / Directory",
        "keywords": ["marketplace", "directory", "search", "listing"],
        "sections": "1. Hero (Search focused), 2. Categories, 3. Featured Listings, 4. Trust/Safety, 5. CTA (Become a host/seller)",
        "cta_placement": "Hero Search Bar + Navbar 'List your item'",
        "color_strategy": "Search: High contrast. Categories: Visual icons. Trust: Blue/Green.",
        "conversion_tip": "Search bar is the CTA. Reduce friction to search. Popular searches suggestions.",
    },
    {
        "name": "Newsletter / Content First",
        "keywords": ["newsletter", "content", "writer", "blog", "subscribe", "minimal & direct", "minimal-direct"],
        "sections": "1. Hero (Value Prop + Form), 2. Recent Issues/Archives, 3. Social Proof (Subscriber count), 4. About Author",
        "cta_placement": "Hero inline form + Sticky header form",
        "color_strategy": "Minimalist. Paper-like background. Text focus. Accent color for Subscribe.",
        "conversion_tip": "Single field form (Email only). Show 'Join X, 000 readers'. Read sample link.",
    },
    {
        "name": "Webinar Registration",
        "keywords": ["webinar", "registration", "event", "training", "live"],
        "sections": "1. Hero (Topic + Timer + Form), 2. What you'll learn, 3. Speaker Bio, 4. Urgency/Bonuses, 5. Form (again)",
        "cta_placement": "Hero (Right side form) + Bottom anchor",
        "color_strategy": "Urgency: Red/Orange. Professional: Blue/Navy. Form: High contrast white.",
        "conversion_tip": "Limited seats logic. 'Live' indicator. Auto-fill timezone.",
    },
    {
        "name": "Enterprise Gateway",
        "keywords": ["enterprise", "corporate", "gateway", "solutions", "portal", "trust", "authority", "trust & authority"],
        "sections": "1. Hero (Video/Mission), 2. Solutions by Industry, 3. Solutions by Role, 4. Client Logos, 5. Contact Sales",
        "cta_placement": "Contact Sales (Primary) + Login (Secondary)",
        "color_strategy": "Corporate: Navy/Grey. High integrity. Conservative accents.",
        "conversion_tip": "Path selection (I am a...). Mega menu navigation. Trust signals prominent.",
    },
    {
        "name": "Portfolio Grid",
        "keywords": ["portfolio", "grid", "showcase", "gallery", "masonry", "portfolio grid + visuals"],
        "sections": "1. Hero (Name/Role), 2. Project Grid (Masonry), 3. About/Philosophy, 4. Contact",
        "cta_placement": "Project Card Hover + Footer Contact",
        "color_strategy": "Neutral background (let work shine). Text: Black/White. Accent: Minimal.",
        "conversion_tip": "Visuals first. Filter by category. Fast loading essential.",
    },
    {
        "name": "Horizontal Scroll Journey",
        "keywords": ["horizontal", "scroll", "journey", "gallery", "storytelling", "panoramic", "storytelling-driven"],
        "sections": "1. Intro (Vertical), 2. The Journey (Horizontal Track), 3. Detail Reveal, 4. Vertical Footer",
        "cta_placement": "Floating Sticky CTA or End of Horizontal Track",
        "color_strategy": "Continuous palette transition. Chapter colors. Progress bar #000000.",
        "conversion_tip": "Immersive product discovery. High engagement. Keep navigation visible.",
    },
    {
        "name": "Bento Grid Showcase",
        "keywords": ["bento", "grid", "features", "modular", "apple-style", "showcase", "feature-rich showcase"],
        "sections": "1. Hero, 2. Bento Grid (Key Features), 3. Detail Cards, 4. Tech Specs, 5. CTA",
        "cta_placement": "Floating Action Button or Bottom of Grid",
        "color_strategy": "Card backgrounds: #F5F5F7 or Glass. Icons: Vibrant brand colors. Text: Dark.",
        "conversion_tip": "Scannable value props. High information density without clutter. Mobile stack.",
    },
    {
        "name": "Interactive 3D Configurator",
        "keywords": ["3d", "configurator", "customizer", "interactive", "product", "interactive product demo"],
        "sections": "1. Hero (Configurator), 2. Feature Highlight (synced), 3. Price/Specs, 4. Purchase",
        "cta_placement": "Inside Configurator UI + Sticky Bottom Bar",
        "color_strategy": "Neutral studio background. Product: Realistic materials. UI: Minimal overlay.",
        "conversion_tip": "Increases ownership feeling. 360 view reduces return rates. Direct add-to-cart.",
    },
    {
        "name": "AI-Driven Dynamic Landing",
        "keywords": ["ai", "dynamic", "personalized", "adaptive", "generative"],
        "sections": "1. Prompt/Input Hero, 2. Generated Result Preview, 3. How it Works, 4. Value Prop",
        "cta_placement": "Input Field (Hero) + 'Try it' Buttons",
        "color_strategy": "Adaptive to user input. Dark mode for compute feel. Neon accents.",
        "conversion_tip": "Immediate value demonstration. 'Show, don't tell'. Low friction start.",
    },
    {
        "name": "Feature-Rich Showcase",
        "keywords": ["feature-rich", "feature-rich showcase", "features", "showcase", "product showcase"],
        "sections": "1. Hero (value prop), 2. Feature grid/cards (4-6), 3. Use cases or benefits, 4. Social proof or logos, 5. CTA",
        "cta_placement": "Hero (sticky) + After features + Bottom",
        "color_strategy": "Brand primary + card bg #FAFAFA. Feature icons accent. CTA contrasting.",
        "conversion_tip": "Clear feature hierarchy. One key message per card. Strong CTA repetition.",
    },
    {
        "name": "Hero-Centric Design",
        "keywords": ["hero-centric", "hero-centric design", "hero-first", "hero above fold"],
        "sections": "1. Full-bleed Hero (headline + visual), 2. Single value prop strip, 3. Key benefit or proof, 4. Primary CTA",
        "cta_placement": "Hero dominant (center/bottom) + Sticky nav CTA",
        "color_strategy": "Hero: High-impact visual. Minimal text. CTA 7:1 contrast.",
        "conversion_tip": "One primary CTA. Hero is 60-80% above fold. Mobile: same hierarchy.",
    },
    {
        "name": "Trust & Authority + Conversion",
        "keywords": ["trust & authority", "trust", "authority", "conversion", "credibility", "enterprise"],
        "sections": "1. Hero (mission/credibility), 2. Proof (logos, certs, stats), 3. Solution overview, 4. Clear CTA path",
        "cta_placement": "Contact Sales / Get Quote (primary) + Nav",
        "color_strategy": "Navy/Grey corporate. Trust blue. Accent for CTA only.",
        "conversion_tip": "Security badges. Case studies. Transparent pricing. Low-friction form.",
    },
    {
        "name": "Real-Time / Operations Landing",
        "keywords": ["real-time", "real-time monitor", "operations", "dashboard", "telemetry", "live data"],
        "sections": "1. Hero (product + live preview or status), 2. Key metrics/indicators, 3. How it works, 4. CTA (Start trial / Contact)",
        "cta_placement": "Primary CTA in nav + After metrics",
        "color_strategy": "Dark or neutral. Status colors (green/amber/red). Data-dense but scannable.",
        "conversion_tip": "For ops/security/iot products. Demo or sandbox link. Trust signals.",
    },
]


# ─────────────────────────────────────────────────────────────
# PRODUCT → DESIGN ROUTING TABLE (from ui-ux-pro-max v2.5.0)
# ─────────────────────────────────────────────────────────────

PRODUCT_DESIGN_MAP: dict[str, dict[str, str]] = {
    "AI Photo & Avatar Generator": {
        "style": "AI-Native UI + Aurora UI", "secondary": "Glassmorphism, Minimalism",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "AI purple + aurora gradients + before/after neutral",
        "considerations": "Style selection. Multiple output variations. Privacy policy prominent. Fast generation. Credits/subscription system.",
    },
    "AI/Chatbot Platform": {
        "style": "AI-Native UI + Minimalism", "secondary": "Zero Interface, Glassmorphism",
        "landing": "Interactive Product Demo", "palette_focus": "Neutral + AI Purple (#6366F1)",
        "considerations": "Conversational UI. Streaming text. Context awareness. Minimal chrome.",
    },
    "Agriculture/Farm Tech": {
        "style": "Organic Biophilic + Flat Design", "secondary": "Minimalism, Accessible & Ethical",
        "landing": "Feature-Rich Showcase + Trust", "palette_focus": "Earth Green (#4A7C23) + Brown + Sky Blue",
        "considerations": "Crop monitoring. Weather data. IoT sensors. Yield tracking. Market prices. Sustainable imagery.",
    },
    "Airline": {
        "style": "Minimalism + Glassmorphism", "secondary": "Motion-Driven, Accessible & Ethical",
        "landing": "Conversion-Optimized + Feature-Rich", "palette_focus": "Sky Blue + Brand colors + Trust accents",
        "considerations": "Flight search. Booking. Check-in. Boarding pass. Loyalty program. Route maps. Mobile-first.",
    },
    "Alarm & World Clock": {
        "style": "Dark Mode (OLED) + Minimalism", "secondary": "Neumorphism, Flat Design",
        "landing": "Minimal & Direct", "palette_focus": "Deep dark + ambient glow accent + timezone gradient",
        "considerations": "Gentle wake (gradual volume). Timezone visualizer. Sleep tracking integration. Smart alarm skip. Bedtime mode.",
    },
    "Analytics Dashboard": {
        "style": "Data-Dense + Heat Map & Heatmap", "secondary": "Minimalism, Dark Mode (OLED)",
        "landing": "N/A - Analytics focused", "palette_focus": "Cool→Hot gradients + neutral grey",
        "considerations": "Clarity > aesthetics. Color-coded data priority.",
    },
    "Anonymous Community / Confession": {
        "style": "Dark Mode (OLED) + Minimalism", "secondary": "Glassmorphism, Soft UI Evolution",
        "landing": "Social Proof-Focused + Feature-Rich", "palette_focus": "Dark protective + subtle gradient + upvote green + empathy warm accent",
        "considerations": "Anonymous posting with moderation. Safety reporting. Reaction system. Trending topics. Mental health resources link.",
    },
    "Arcade & Retro Game": {
        "style": "Pixel Art + Retro-Futurism", "secondary": "Vibrant & Block-based, Motion-Driven",
        "landing": "Feature-Rich Showcase + Hero-Centric", "palette_focus": "Neon on black + pixel palette + score gold + danger red",
        "considerations": "Instant play with no login. Game Center leaderboards. Haptic feedback on collision. Offline. Controller support.",
    },
    "Architecture / Interior": {
        "style": "Exaggerated Minimalism + High Imagery", "secondary": "Swiss Modernism 2.0, Parallax",
        "landing": "Portfolio Grid + Visuals", "palette_focus": "Monochrome + Gold Accent + High Imagery",
        "considerations": "High-res images. Typography. Space.",
    },
    "Automotive/Car Dealership": {
        "style": "Motion-Driven + 3D & Hyperrealism", "secondary": "Dark Mode (OLED), Glassmorphism",
        "landing": "Hero-Centric Design + Feature-Rich", "palette_focus": "Brand colors + Metallic accents + Dark/Light",
        "considerations": "Vehicle showcase. 360° views. Comparison tools. Financing calculator. Test drive booking. High-quality imagery.",
    },
    "Autonomous Drone Fleet Manager": {
        "style": "HUD / Sci-Fi FUI", "secondary": "Real-Time Monitor, Spatial UI",
        "landing": "Real-Time Monitor", "palette_focus": "Tactical Green #00FF00 + Alert Red + Map Dark",
        "considerations": "Real-time telemetry. 3D spatial awareness. Latency indicators. Safety alerts.",
    },
    "B2B Service": {
        "style": "Trust & Authority + Minimal", "secondary": "Feature-Rich, Conversion-Optimized",
        "landing": "Feature-Rich Showcase", "palette_focus": "Professional blue + neutral grey",
        "considerations": "Credibility essential. Clear ROI messaging.",
    },
    "Bakery/Cafe": {
        "style": "Vibrant & Block-based + Soft UI Evolution", "secondary": "Claymorphism, Motion-Driven",
        "landing": "Hero-Centric Design + Conversion", "palette_focus": "Warm Brown + Cream + Appetizing accents",
        "considerations": "Menu display. Online ordering. Location/hours. Catering. Seasonal specials. Appetizing photography.",
    },
    "Banking/Traditional Finance": {
        "style": "Minimalism + Accessible & Ethical", "secondary": "Trust & Authority, Dark Mode (OLED)",
        "landing": "Trust & Authority + Feature-Rich", "palette_focus": "Navy (#0A1628) + Trust Blue + Gold accents",
        "considerations": "Security-first. Account overview. Transaction history. Mobile banking. Accessibility critical. Trust paramount.",
    },
    "Beauty/Spa/Wellness Service": {
        "style": "Soft UI Evolution + Neumorphism", "secondary": "Glassmorphism, Minimalism",
        "landing": "Hero-Centric Design + Social Proof", "palette_focus": "Soft pastels (Pink #FFB6C1 Sage #90EE90) + Cream + Gold accents",
        "considerations": "Calming aesthetic. Booking system. Service menu. Before/after gallery. Testimonials. Relaxing imagery.",
    },
    "Biohacking / Longevity App": {
        "style": "Biomimetic / Organic 2.0", "secondary": "Minimalism, Dark Mode (OLED)",
        "landing": "Data-Dense + Storytelling", "palette_focus": "Cellular Pink/Red + DNA Blue + Clean White",
        "considerations": "Personal data privacy. Scientific credibility. Biological visualizations.",
    },
    "Biotech / Life Sciences": {
        "style": "Glassmorphism + Clean Science", "secondary": "Minimalism, Organic Biophilic",
        "landing": "Storytelling-Driven + Research", "palette_focus": "Sterile White + DNA Blue + Life Green",
        "considerations": "Data accuracy. Cleanliness. Complex data viz.",
    },
    "Book & Reading Tracker": {
        "style": "Swiss Modernism 2.0 + Minimalism", "secondary": "E-Ink Paper, Soft UI Evolution",
        "landing": "Social Proof-Focused + Feature-Rich", "palette_focus": "Warm paper white + ink brown + reading progress green + book cover colors",
        "considerations": "Barcode scan to add. Progress percentage. Annual reading goal. Notes and quotes. Friends activity. Genre stats.",
    },
    "Booking & Appointment App": {
        "style": "Soft UI Evolution + Flat Design", "secondary": "Minimalism, Micro-interactions",
        "landing": "Conversion-Optimized", "palette_focus": "Trust blue + available green + booked grey + confirm accent",
        "considerations": "Calendar strip or month picker. Available time-slot grid. Service + staff selector. Confirmation summary. Reminder push.",
    },
    "Bookmark & Read-Later": {
        "style": "Minimalism + Flat Design", "secondary": "Editorial Grid, Swiss Modernism 2.0",
        "landing": "Minimal & Direct + Demo", "palette_focus": "Paper warm white + ink neutral + minimal accent + tag colors",
        "considerations": "Fast save via share sheet. Article distraction-free view. Tags and collections. Offline sync. Reading progress.",
    },
    "Brewery/Winery": {
        "style": "Motion-Driven + Storytelling-Driven", "secondary": "Dark Mode (OLED), Organic Biophilic",
        "landing": "Storytelling-Driven + Hero-Centric", "palette_focus": "Deep amber/burgundy + Gold + Craft aesthetic",
        "considerations": "Product showcase. Story/heritage. Tasting notes. Events. Club membership. Artisanal imagery.",
    },
    "CRM & Client Management": {
        "style": "Flat Design + Minimalism", "secondary": "Soft UI Evolution, Micro-interactions",
        "landing": "Feature-Rich Showcase + Demo", "palette_focus": "Professional blue + pipeline stage colors + closed-won green",
        "considerations": "Contact card list with avatar. Pipeline kanban board. Activity timeline. Quick-log (call/email/meeting). Deal amount + p",
    },
    "Calculator & Unit Converter": {
        "style": "Neumorphism + Minimalism", "secondary": "Flat Design, Dark Mode (OLED)",
        "landing": "Minimal & Direct", "palette_focus": "Dark functional + orange operation keys + clear button hierarchy",
        "considerations": "Scientific mode toggle. Live currency rates. Calculation history. Widget support. Gesture input.",
    },
    "Calendar & Scheduling App": {
        "style": "Flat Design + Micro-interactions", "secondary": "Minimalism, Soft UI Evolution",
        "landing": "Feature-Rich Showcase + Demo", "palette_focus": "Clean blue + event category accent colors + success green",
        "considerations": "Event color coding. Week/month/day views. Recurring events. Conflict detection. Multi-calendar sync.",
    },
    "Calorie & Nutrition Counter": {
        "style": "Flat Design + Vibrant & Block-based", "secondary": "Minimalism, Claymorphism",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Healthy green + macro colors (protein blue, carb orange, fat yellow) + progress ",
        "considerations": "Barcode scanner food log. Large database. Macro goals. Restaurant lookup. Recipe builder. AI photo food logging.",
    },
    "Card & Board Game": {
        "style": "3D & Hyperrealism + Flat Design", "secondary": "Motion-Driven, Dark Mode (OLED)",
        "landing": "Feature-Rich Showcase", "palette_focus": "Game-theme felt green + dark wood + card back patterns",
        "considerations": "Real-time or async multiplayer. Game state sync. Tutorial mode. Match history. ELO rating system.",
    },
    "Casual Puzzle Game": {
        "style": "Claymorphism + Vibrant & Block-based", "secondary": "Micro-interactions, Motion-Driven",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Cheerful pastels + progression gradient + reward gold + bright accent",
        "considerations": "Satisfying match/clear animations. Progressive difficulty. Daily challenges. No-skip tutorials. Offline play.",
    },
    "Chat & Messaging App": {
        "style": "Minimalism + Micro-interactions", "secondary": "Glassmorphism, Flat Design",
        "landing": "Feature-Rich Showcase + Demo", "palette_focus": "Brand primary + bubble contrast (sender/receiver) + typing grey",
        "considerations": "Bubble UI (left/right alignment). Typing indicators. Read receipts (✓✓). Image/file preview. Emoji reactions. Group avat",
    },
    "Childcare/Daycare": {
        "style": "Claymorphism + Vibrant & Block-based", "secondary": "Soft UI Evolution, Accessible & Ethical",
        "landing": "Social Proof-Focused + Trust", "palette_focus": "Playful pastels + Safe colors + Warm accents",
        "considerations": "Programs. Staff profiles. Safety certifications. Parent portal. Activity updates. Cheerful imagery.",
    },
    "Church/Religious Organization": {
        "style": "Accessible & Ethical + Soft UI Evolution", "secondary": "Minimalism, Trust & Authority",
        "landing": "Hero-Centric Design + Social Proof", "palette_focus": "Warm Gold + Deep Purple/Blue + White",
        "considerations": "Service times. Events. Sermons. Community. Giving. Location. Welcoming imagery.",
    },
    "Coding Bootcamp": {
        "style": "Dark Mode (OLED) + Minimalism", "secondary": "Cyberpunk UI, Flat Design",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Code editor colors + Brand + Success green",
        "considerations": "Curriculum. Projects. Career outcomes. Alumni. Pricing. Application. Terminal aesthetic.",
    },
    "Coding Challenge & Practice": {
        "style": "Dark Mode (OLED) + Cyberpunk UI", "secondary": "Minimalism, Flat Design",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Code editor dark + success green + difficulty gradient (easy green / medium ambe",
        "considerations": "Code editor with syntax highlight. Multiple languages. Hint system. Solution explanation. Company tags. Contest mode.",
    },
    "Construction/Architecture": {
        "style": "Minimalism + 3D & Hyperrealism", "secondary": "Brutalism, Swiss Modernism 2.0",
        "landing": "Hero-Centric Design + Feature-Rich", "palette_focus": "Grey (#4A4A4A) + Orange (safety) + Blueprint Blue",
        "considerations": "Project portfolio. 3D renders. Timeline. Material specs. Team collaboration. Blueprint aesthetic.",
    },
    "Couple & Relationship App": {
        "style": "Aurora UI + Soft UI Evolution", "secondary": "Claymorphism, Glassmorphism",
        "landing": "Storytelling-Driven + Social Proof", "palette_focus": "Warm romantic pink/rose + soft gradient + memory photo tones",
        "considerations": "Shared timeline. Anniversary countdowns. Secret chat. Photo albums. Love language quiz. Date night ideas.",
    },
    "Coworking Space": {
        "style": "Vibrant & Block-based + Glassmorphism", "secondary": "Minimalism, Motion-Driven",
        "landing": "Hero-Centric Design + Feature-Rich", "palette_focus": "Energetic colors + Wood tones + Brand accent",
        "considerations": "Space tour. Membership plans. Booking system. Amenities. Community events. Virtual tour.",
    },
    "Creative Agency": {
        "style": "Brutalism + Motion-Driven", "secondary": "Retro-Futurism, Storytelling-Driven",
        "landing": "Storytelling-Driven", "palette_focus": "Bold primaries + artistic freedom",
        "considerations": "Differentiation key. Wow-factor necessary.",
    },
    "Creator Economy Platform": {
        "style": "Vibrant & Block-based + Bento Box Grid", "secondary": "Motion-Driven, Aurora UI",
        "landing": "Social Proof-Focused", "palette_focus": "Vibrant + Brand colors",
        "considerations": "Creator profiles. Monetization display. Engagement metrics. Social proof.",
    },
    "Cybersecurity Platform": {
        "style": "Cyberpunk UI + Dark Mode (OLED)", "secondary": "Neubrutalism, Minimal & Direct",
        "landing": "Trust & Authority + Real-Time", "palette_focus": "Matrix Green + Deep Black + Terminal feel",
        "considerations": "Data density. Threat visualization. Dark mode default.",
    },
    "Dating App": {
        "style": "Vibrant & Block-based + Motion-Driven", "secondary": "Aurora UI, Glassmorphism",
        "landing": "Social Proof-Focused", "palette_focus": "Warm + Romantic (Pink/Red gradients)",
        "considerations": "Profile cards. Swipe interactions. Match animations. Safety features. Video chat.",
    },
    "Dental Practice": {
        "style": "Soft UI Evolution + Minimalism", "secondary": "Accessible & Ethical, Trust & Authority",
        "landing": "Social Proof-Focused + Conversion", "palette_focus": "Fresh Blue + White + Smile Yellow accent",
        "considerations": "Services. Dentist profiles. Before/after. Online booking. Insurance. Patient testimonials. Friendly imagery.",
    },
    "Design System/Component Library": {
        "style": "Minimalism + Accessible & Ethical", "secondary": "Flat Design, Zero Interface",
        "landing": "Feature-Rich Showcase", "palette_focus": "Clear hierarchy + code-like structure",
        "considerations": "Consistency. Developer-first approach.",
    },
    "Developer Tool / IDE": {
        "style": "Dark Mode (OLED) + Minimalism", "secondary": "Flat Design, Bento Box Grid",
        "landing": "Minimal & Direct + Documentation", "palette_focus": "Dark syntax theme colors + Blue focus",
        "considerations": "Keyboard shortcuts. Syntax highlighting. Fast performance.",
    },
    "Diary & Journal App": {
        "style": "Soft UI Evolution + Minimalism", "secondary": "Neumorphism, Sketch Hand-Drawn",
        "landing": "Storytelling-Driven", "palette_focus": "Warm paper tones (cream/linen) + muted ink + mood-coded accents",
        "considerations": "Calendar month-view entry. Mood tag selector (emoji/color). Photo/voice attachment. Writing prompts. Privacy lock (FaceI",
    },
    "Digital Products/Downloads": {
        "style": "Vibrant & Block-based + Motion-Driven", "secondary": "Glassmorphism, Bento Box Grid",
        "landing": "Feature-Rich Showcase + Conversion", "palette_focus": "Product category colors + Brand + Success green",
        "considerations": "Product showcase. Preview. Pricing. Instant delivery. License management. Customer reviews.",
    },
    "Drawing & Sketching Canvas": {
        "style": "Minimalism + Dark Mode (OLED)", "secondary": "Anti-Polish Raw, Motion-Driven",
        "landing": "Interactive Product Demo + Storytelling", "palette_focus": "Neutral canvas + full-spectrum color picker + tool panel dark",
        "considerations": "Pressure sensitivity. Infinite canvas (pan/zoom). Layer management. Undo history. Export PNG/PSD/SVG.",
    },
    "E-commerce": {
        "style": "Vibrant & Block-based", "secondary": "Aurora UI, Motion-Driven",
        "landing": "Feature-Rich Showcase", "palette_focus": "Brand primary + success green",
        "considerations": "Engagement & conversions. High visual hierarchy.",
    },
    "E-commerce Luxury": {
        "style": "Liquid Glass + Glassmorphism", "secondary": "3D & Hyperrealism, Aurora UI",
        "landing": "Feature-Rich Showcase", "palette_focus": "Premium colors + minimal accent",
        "considerations": "Elegance & sophistication. Premium materials.",
    },
    "EV/Charging Ecosystem": {
        "style": "Minimalism + Aurora UI", "secondary": "Glassmorphism, Organic Biophilic",
        "landing": "Hero-Centric Design", "palette_focus": "Electric Blue (#009CD1) + Green",
        "considerations": "Charging station maps. Range estimation. Cost calculation. Environmental impact.",
    },
    "Educational App": {
        "style": "Claymorphism + Micro-interactions", "secondary": "Vibrant & Block-based, Flat Design",
        "landing": "Storytelling-Driven", "palette_focus": "Playful colors + clear hierarchy",
        "considerations": "Engagement & ease of use. Age-appropriate design.",
    },
    "Email Client": {
        "style": "Flat Design + Minimalism", "secondary": "Micro-interactions, Soft UI Evolution",
        "landing": "Feature-Rich Showcase + Demo", "palette_focus": "Clean white + brand primary + priority red + snooze amber",
        "considerations": "Unified inbox. Swipe actions (archive/delete/snooze). Priority sorting. Smart reply. Unsubscribe tool.",
    },
    "Emergency SOS & Safety": {
        "style": "Accessible & Ethical + Flat Design", "secondary": "Dark Mode (OLED), Minimalism",
        "landing": "Trust & Authority + Social Proof", "palette_focus": "Alert red + safety blue + location green + high contrast critical",
        "considerations": "One-tap SOS. Emergency contacts auto-notify. Live location sharing. Fake call feature. Safe walk mode. Local emergency n",
    },
    "Event Management": {
        "style": "Vibrant & Block-based + Motion-Driven", "secondary": "Glassmorphism, Aurora UI",
        "landing": "Hero-Centric Design + Feature-Rich", "palette_focus": "Event theme colors + Excitement accents",
        "considerations": "Event showcase. Registration. Agenda. Speakers. Sponsors. Ticket sales. Countdown timer.",
    },
    "Expense Splitter / Bill Split": {
        "style": "Flat Design + Vibrant & Block-based", "secondary": "Minimalism, Micro-interactions",
        "landing": "Minimal & Direct + Demo", "palette_focus": "Success green + alert red + neutral grey + avatar accent colors",
        "considerations": "Group expense tracking. Debt simplification algorithm. Payment reminders. Multi-currency. Receipt photo import.",
    },
    "Family Calendar & Chores": {
        "style": "Flat Design + Claymorphism", "secondary": "Accessible & Ethical, Vibrant & Block-based",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Warm playful + member color coding + chore completion green",
        "considerations": "Member color coding. Chore assignment rotation. Recurring events. Shared shopping list. Allowance tracking.",
    },
    "Fasting & Intermittent Timer": {
        "style": "Minimalism + Dark Mode (OLED)", "secondary": "Neumorphism, Flat Design",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Fasting deep blue/purple + eating window green + timeline neutral",
        "considerations": "Protocol selector (16:8, 18:6, OMAD). Circular countdown timer. Fasting history log. Tips during fast. Electrolytes.",
    },
    "File Manager & Transfer": {
        "style": "Flat Design + Minimalism", "secondary": "Accessible & Ethical, Dark Mode (OLED)",
        "landing": "Feature-Rich Showcase + Demo", "palette_focus": "Functional neutral + file type color coding (PDF orange, doc blue, image purple)",
        "considerations": "Folder tree navigation. File type preview. Wireless P2P transfer. Cloud integration. Compress and extract.",
    },
    "Financial Dashboard": {
        "style": "Dark Mode (OLED) + Data-Dense", "secondary": "Minimalism, Accessible & Ethical",
        "landing": "N/A - Dashboard focused", "palette_focus": "Dark bg + red/green alerts + trust blue",
        "considerations": "High contrast, real-time updates, accuracy paramount.",
    },
    "Fintech/Crypto": {
        "style": "Glassmorphism + Dark Mode (OLED)", "secondary": "Retro-Futurism, Motion-Driven",
        "landing": "Conversion-Optimized", "palette_focus": "Dark tech colors + trust + vibrant accents",
        "considerations": "Security perception. Real-time data critical.",
    },
    "Fitness/Gym App": {
        "style": "Vibrant & Block-based + Dark Mode (OLED)", "secondary": "Motion-Driven, Neumorphism",
        "landing": "Feature-Rich Showcase", "palette_focus": "Energetic (Orange #FF6B35 Electric Blue) + Dark bg",
        "considerations": "Progress tracking. Workout plans. Community features. Achievements. Motivational design.",
    },
    "Flashcard & Study Tool": {
        "style": "Claymorphism + Micro-interactions", "secondary": "Vibrant & Block-based, Flat Design",
        "landing": "Feature-Rich Showcase + Demo", "palette_focus": "Playful primary + correct green + incorrect red + progress blue",
        "considerations": "3D card flip animation. Spaced repetition algorithm. Deck browser. Session progress bar. Streak tracking. Timed quiz mod",
    },
    "Florist/Plant Shop": {
        "style": "Organic Biophilic + Vibrant & Block-based", "secondary": "Aurora UI, Motion-Driven",
        "landing": "Hero-Centric Design + Conversion", "palette_focus": "Natural Green + Floral pinks/purples + Earth tones",
        "considerations": "Product catalog. Occasion categories. Delivery scheduling. Care guides. Seasonal collections. Beautiful imagery.",
    },
    "Food Delivery / On-Demand": {
        "style": "Vibrant & Block-based + Motion-Driven", "secondary": "Glassmorphism, Flat Design",
        "landing": "Hero-Centric Design + Feature-Rich", "palette_focus": "Appetizing warm (orange/red) + trust blue + map accent",
        "considerations": "Restaurant cards with ratings. Menu category horizontal scroll. Cart bottom sheet. Real-time map tracking + driver ETA. ",
    },
    "Freelancer Platform": {
        "style": "Flat Design + Minimalism", "secondary": "Vibrant & Block-based, Micro-interactions",
        "landing": "Feature-Rich Showcase + Conversion", "palette_focus": "Professional Blue + Success Green + Neutral",
        "considerations": "Profile creation. Portfolio. Skill matching. Messaging. Payment. Reviews. Project management.",
    },
    "Gaming": {
        "style": "3D & Hyperrealism + Retro-Futurism", "secondary": "Motion-Driven, Vibrant & Block",
        "landing": "Feature-Rich Showcase", "palette_focus": "Vibrant + neon + immersive colors",
        "considerations": "Immersion priority. Performance critical.",
    },
    "Generative Art Platform": {
        "style": "Minimalism (Frame) + Gen Z Chaos", "secondary": "Masonry Grid, Dark Mode",
        "landing": "Bento Grid Showcase", "palette_focus": "Neutral #F5F5F5 (Canvas) + User Content",
        "considerations": "Content is king. Fast loading. Creator attribution. Minting flow.",
    },
    "Gift & Wishlist": {
        "style": "Vibrant & Block-based + Soft UI Evolution", "secondary": "Claymorphism, Flat Design",
        "landing": "Minimal & Direct + Conversion", "palette_focus": "Celebration warm pink/gold/red + category colors + surprise accent",
        "considerations": "Add from any URL. Price range filter. Reserved-by-others system. Occasion calendar. Collaborative list. Surprise mode.",
    },
    "Government/Public Service": {
        "style": "Accessible & Ethical + Minimalism", "secondary": "Flat Design, Inclusive Design",
        "landing": "Minimal & Direct", "palette_focus": "Professional blue + high contrast",
        "considerations": "WCAG AAA mandatory. Trust paramount.",
    },
    "Grocery & Shopping List": {
        "style": "Flat Design + Vibrant & Block-based", "secondary": "Claymorphism, Micro-interactions",
        "landing": "Minimal & Direct + Demo", "palette_focus": "Fresh green + food-category colors + checkmark accent",
        "considerations": "Category-grouped list. Tap-to-check interaction (with strikethrough). Quantity stepper. Share list with family. Store ai",
    },
    "Habit Tracker": {
        "style": "Claymorphism + Vibrant & Block-based", "secondary": "Micro-interactions, Flat Design",
        "landing": "Social Proof-Focused + Demo", "palette_focus": "Streak warm (amber/orange) + progress green + motivational accents",
        "considerations": "Streak calendar heatmap. Daily check-in interaction. Gamification (badges/levels/fire). Reminder push. Progress ring cha",
    },
    "Healthcare App": {
        "style": "Neumorphism + Accessible & Ethical", "secondary": "Soft UI Evolution, Claymorphism (for patients)",
        "landing": "Social Proof-Focused", "palette_focus": "Calm blue + health green + trust",
        "considerations": "Accessibility mandatory. Calming aesthetic.",
    },
    "Home Decoration & Interior Design": {
        "style": "Minimalism + 3D Product Preview", "secondary": "Organic Biophilic, Aurora UI",
        "landing": "Storytelling-Driven + Feature-Rich", "palette_focus": "Neutral interior palette + material texture accent + AR blue",
        "considerations": "AR room visualization. Style quiz. Product catalog with purchase links. 3D room planner. Mood board. Before/after.",
    },
    "Home Services (Plumber/Electrician)": {
        "style": "Flat Design + Trust & Authority", "secondary": "Minimalism, Accessible & Ethical",
        "landing": "Conversion-Optimized + Trust", "palette_focus": "Trust Blue + Safety Orange + Professional grey",
        "considerations": "Service list. Emergency contact. Booking. Price transparency. Certifications. Local trust signals.",
    },
    "Hotel/Hospitality": {
        "style": "Liquid Glass + Minimalism", "secondary": "Glassmorphism, Soft UI Evolution",
        "landing": "Hero-Centric Design + Social Proof", "palette_focus": "Warm neutrals + Gold (#D4AF37) + Brand accent",
        "considerations": "Room booking. Amenities showcase. Location maps. Guest reviews. Seasonal pricing. Luxury imagery.",
    },
    "Hyperlocal Services": {
        "style": "Minimalism + Vibrant & Block-based", "secondary": "Micro-interactions, Flat Design",
        "landing": "Conversion-Optimized", "palette_focus": "Location markers + Trust colors",
        "considerations": "Map integration. Service categories. Provider profiles. Booking system. Reviews.",
    },
    "Idle & Clicker Game": {
        "style": "Vibrant & Block-based + Motion-Driven", "secondary": "Claymorphism, 3D & Hyperrealism",
        "landing": "Feature-Rich Showcase", "palette_focus": "Coin gold + upgrade blue + prestige purple + progress green",
        "considerations": "Offline progress calculation. Satisfying number animations. Upgrade tree clarity. Prestige system. Optional ads.",
    },
    "Insurance Platform": {
        "style": "Trust & Authority + Flat Design", "secondary": "Accessible & Ethical, Minimalism",
        "landing": "Conversion-Optimized + Trust", "palette_focus": "Trust Blue (#0066CC) + Green (security) + Neutral",
        "considerations": "Quote calculator. Policy comparison. Claims process. Trust signals. Clear pricing. Security badges.",
    },
    "Inventory & Stock Management": {
        "style": "Flat Design + Minimalism", "secondary": "Dark Mode (OLED), Accessible & Ethical",
        "landing": "Feature-Rich Showcase", "palette_focus": "Functional neutral + status traffic-light (green/amber/red) + scanner accent",
        "considerations": "Product list/grid with thumbnails. Barcode/QR scanner. Stock level badges. Low-stock alert banner. Category/location fil",
    },
    "Invoice & Billing Tool": {
        "style": "Minimalism + Flat Design", "secondary": "Swiss Modernism 2.0, Accessible & Ethical",
        "landing": "Conversion-Optimized + Trust", "palette_focus": "Professional navy + paid green + overdue red + neutral grey",
        "considerations": "Invoice template with line items. Tax/discount calculation. Status badges (Draft/Sent/Paid/Overdue). PDF export + share.",
    },
    "Job Board/Recruitment": {
        "style": "Flat Design + Minimalism", "secondary": "Vibrant & Block-based, Accessible & Ethical",
        "landing": "Conversion-Optimized + Feature-Rich", "palette_focus": "Professional Blue + Success Green + Neutral",
        "considerations": "Job listings. Search/filter. Company profiles. Application tracking. Resume upload. Salary insights.",
    },
    "Kids Learning (ABC & Math)": {
        "style": "Claymorphism + Vibrant & Block-based", "secondary": "Micro-interactions, Flat Design",
        "landing": "Social Proof-Focused + Trust", "palette_focus": "Bright primary + child-safe pastels + reward gold + interactive accent",
        "considerations": "Age-appropriate UI for 2-8. No ads. No dark patterns. Curriculum aligned. Parent progress reports. Reward system.",
    },
    "Knowledge Base/Documentation": {
        "style": "Minimalism + Accessible & Ethical", "secondary": "Swiss Modernism 2.0, Flat Design",
        "landing": "FAQ/Documentation", "palette_focus": "Clean hierarchy + minimal color",
        "considerations": "Search-first. Clear navigation. Code highlighting. Version switching. Feedback system.",
    },
    "Language Learning App": {
        "style": "Claymorphism + Vibrant & Block-based", "secondary": "Micro-interactions, Flat Design",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Playful colors + Progress indicators + Country flags",
        "considerations": "Lesson structure. Progress tracking. Gamification. Speaking practice. Community. Achievement badges.",
    },
    "Legal Services": {
        "style": "Trust & Authority + Minimalism", "secondary": "Accessible & Ethical, Swiss Modernism 2.0",
        "landing": "Trust & Authority + Minimal", "palette_focus": "Navy Blue (#1E3A5F) + Gold + White",
        "considerations": "Credibility paramount. Practice areas. Attorney profiles. Case results. Contact forms. Professional imagery.",
    },
    "Link-in-Bio Page Builder": {
        "style": "Vibrant & Block-based + Bento Box Grid", "secondary": "Minimalism, Glassmorphism",
        "landing": "Conversion-Optimized + Social Proof", "palette_focus": "Brand-customizable + accent link color + clean white canvas",
        "considerations": "Drag-drop builder. Theme templates. Click analytics. Custom domain. Social icon integration. QR code export.",
    },
    "Local Events & Discovery": {
        "style": "Vibrant & Block-based + Motion-Driven", "secondary": "Glassmorphism, Flat Design",
        "landing": "Hero-Centric Design + Feature-Rich", "palette_focus": "City vibrant + event category colors + map accent + date highlight",
        "considerations": "Location-based discovery. Category filters. RSVP flow. Map view. Friend attendance. Organizer tools. Reminders.",
    },
    "Logistics/Delivery": {
        "style": "Minimalism + Flat Design", "secondary": "Dark Mode (OLED), Micro-interactions",
        "landing": "Feature-Rich Showcase + Conversion", "palette_focus": "Blue (#2563EB) + Orange (tracking) + Green (delivered)",
        "considerations": "Real-time tracking. Delivery scheduling. Route optimization. Driver management. Status updates. Map integration.",
    },
    "Luxury/Premium Brand": {
        "style": "Liquid Glass + Glassmorphism", "secondary": "Minimalism, 3D & Hyperrealism",
        "landing": "Storytelling-Driven + Feature-Rich", "palette_focus": "Black + Gold (#FFD700) + White + Minimal accent",
        "considerations": "Elegance paramount. Premium imagery. Storytelling. High-quality visuals. Exclusive feel.",
    },
    "Magazine/Blog": {
        "style": "Swiss Modernism 2.0 + Motion-Driven", "secondary": "Minimalism, Aurora UI",
        "landing": "Storytelling-Driven + Hero-Centric", "palette_focus": "Editorial colors + Brand primary + Clean white",
        "considerations": "Article showcase. Category navigation. Author profiles. Newsletter signup. Related content. Typography-focused.",
    },
    "Marketing Agency": {
        "style": "Brutalism + Motion-Driven", "secondary": "Vibrant & Block-based, Aurora UI",
        "landing": "Storytelling-Driven + Feature-Rich", "palette_focus": "Bold brand colors + Creative freedom",
        "considerations": "Portfolio. Case studies. Services. Team. Creative showcase. Results-focused. Bold aesthetic.",
    },
    "Marketplace (P2P)": {
        "style": "Vibrant & Block-based + Flat Design", "secondary": "Micro-interactions, Trust & Authority",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Trust colors + Category colors + Success green",
        "considerations": "Seller/buyer profiles. Listings. Reviews/ratings. Secure payment. Messaging. Search/filter. Trust badges.",
    },
    "Medical Clinic": {
        "style": "Accessible & Ethical + Minimalism", "secondary": "Neumorphism, Trust & Authority",
        "landing": "Trust & Authority + Conversion", "palette_focus": "Medical Blue (#0077B6) + Trust White + Calm Green",
        "considerations": "Services. Doctor profiles. Online booking. Patient portal. Insurance info. HIPAA compliant. Trust signals.",
    },
    "Medication & Pill Reminder": {
        "style": "Accessible & Ethical + Flat Design", "secondary": "Minimalism, Trust & Authority",
        "landing": "Trust & Authority + Feature-Rich", "palette_focus": "Medical trust blue + missed alert red + taken green + clean white",
        "considerations": "Multi-medication schedule. Caregiver sharing. Refill reminders. Drug interaction warnings. Large touch targets.",
    },
    "Meditation & Mindfulness": {
        "style": "Neumorphism + Soft UI Evolution", "secondary": "Aurora UI, Glassmorphism",
        "landing": "Storytelling-Driven + Social Proof", "palette_focus": "Ultra-calm pastels (lavender/sage/sky) + breathing animation gradient",
        "considerations": "Breathing circle animation. Session duration picker. Ambient sound mixer. Streak/consistency tracking. Guided audio play",
    },
    "Membership/Community": {
        "style": "Vibrant & Block-based + Soft UI Evolution", "secondary": "Bento Box Grid, Micro-interactions",
        "landing": "Social Proof-Focused + Conversion", "palette_focus": "Community brand colors + Engagement accents",
        "considerations": "Member benefits. Pricing tiers. Community showcase. Events. Member directory. Exclusive content.",
    },
    "Meme & Sticker Maker": {
        "style": "Vibrant & Block-based + Flat Design", "secondary": "Gen Z Chaos, Claymorphism",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Bold primary + comedic yellow + viral red + high saturation accent",
        "considerations": "Template library. Caption text overlay. Font variety. Reaction sticker packs. Share to all platforms. Fast creation.",
    },
    "Mental Health App": {
        "style": "Neumorphism + Accessible & Ethical", "secondary": "Claymorphism, Soft UI Evolution",
        "landing": "Social Proof-Focused", "palette_focus": "Calm Pastels + Trust colors",
        "considerations": "Calming aesthetics. Privacy-first. Crisis resources. Progress tracking. Accessibility mandatory.",
    },
    "Micro SaaS": {
        "style": "Flat Design + Vibrant & Block", "secondary": "Motion-Driven, Micro-interactions",
        "landing": "Minimal & Direct + Demo", "palette_focus": "Vibrant primary + white space",
        "considerations": "Keep simple, show product quickly. Speed is key.",
    },
    "Micro-Credentials/Badges Platform": {
        "style": "Minimalism + Flat Design", "secondary": "Accessible & Ethical, Swiss Modernism 2.0",
        "landing": "Trust & Authority", "palette_focus": "Trust Blue + Gold (#FFD700)",
        "considerations": "Credential verification. Badge display. Progress tracking. Issuer trust. LinkedIn integration.",
    },
    "Mood Tracker": {
        "style": "Soft UI Evolution + Minimalism", "secondary": "Aurora UI, Neumorphism",
        "landing": "Storytelling-Driven + Social Proof", "palette_focus": "Emotion gradient (blue sad to yellow happy) + pastel per mood + insight accent",
        "considerations": "One-tap daily check-in. Emotion wheel selector. Mood calendar heatmap. Pattern insights. Export and share.",
    },
    "Museum/Gallery": {
        "style": "Minimalism + Motion-Driven", "secondary": "Swiss Modernism 2.0, 3D & Hyperrealism",
        "landing": "Storytelling-Driven + Feature-Rich", "palette_focus": "Art-appropriate neutrals + Exhibition accents",
        "considerations": "Exhibitions. Collections. Tickets. Events. Virtual tours. Educational content. Art-focused design.",
    },
    "Music Creation & Beat Maker": {
        "style": "Dark Mode (OLED) + Motion-Driven", "secondary": "Cyberpunk UI, Glassmorphism",
        "landing": "Interactive Product Demo + Storytelling", "palette_focus": "Dark studio background + track colors rainbow + waveform accent + BPM pulse",
        "considerations": "Touch piano and drum pad. Loop browser. MIDI support. Export MP3/WAV. Low-latency audio engine.",
    },
    "Music Instrument Learning": {
        "style": "Vibrant & Block-based + Motion-Driven", "secondary": "Dark Mode (OLED), Soft UI Evolution",
        "landing": "Interactive Product Demo + Social Proof", "palette_focus": "Musical warm deep red/brown + note color system + skill progress bar",
        "considerations": "Interactive instrument on-screen. Sheet music display. Song library. Slow-tempo practice. Recording and playback. Teache",
    },
    "Music Streaming": {
        "style": "Dark Mode (OLED) + Vibrant & Block-based", "secondary": "Motion-Driven, Aurora UI",
        "landing": "Feature-Rich Showcase", "palette_focus": "Dark (#121212) + Vibrant accents + Album art colors",
        "considerations": "Audio player. Playlist management. Artist pages. Personalization. Social features. Waveform visualizations.",
    },
    "NFT/Web3 Platform": {
        "style": "Cyberpunk UI + Glassmorphism", "secondary": "Aurora UI, 3D & Hyperrealism",
        "landing": "Feature-Rich Showcase", "palette_focus": "Dark + Neon + Gold (#FFD700)",
        "considerations": "Wallet integration. Transaction feedback. Gas fees display. Dark mode essential.",
    },
    "News/Media Platform": {
        "style": "Minimalism + Flat Design", "secondary": "Dark Mode (OLED), Accessible & Ethical",
        "landing": "Hero-Centric Design + Feature-Rich", "palette_focus": "Brand colors + High contrast + Category colors",
        "considerations": "Article layout. Breaking news. Categories. Search. Subscription. Mobile reading. Fast loading.",
    },
    "Newsletter Platform": {
        "style": "Minimalism + Flat Design", "secondary": "Swiss Modernism 2.0, Accessible & Ethical",
        "landing": "Minimal & Direct + Conversion", "palette_focus": "Brand primary + Clean white + CTA accent",
        "considerations": "Subscribe form. Archive. About. Social proof. Sample content. Simple conversion.",
    },
    "Non-profit/Charity": {
        "style": "Accessible & Ethical + Organic Biophilic", "secondary": "Minimalism, Storytelling-Driven",
        "landing": "Storytelling-Driven + Trust", "palette_focus": "Cause-related colors + Trust + Warm",
        "considerations": "Impact stories. Donation flow. Transparency reports. Volunteer signup. Event calendar. Emotional connection.",
    },
    "Notes & Writing App": {
        "style": "Minimalism + Flat Design", "secondary": "Swiss Modernism 2.0, Soft UI Evolution",
        "landing": "Minimal & Direct", "palette_focus": "Clean white/cream + minimal accent + editor syntax colors",
        "considerations": "WYSIWYG or Markdown toggle. Folder/tag organization. Full-text search. Cloud sync. Typography-first. Distraction-free ze",
    },
    "Online Course/E-learning": {
        "style": "Claymorphism + Vibrant & Block-based", "secondary": "Motion-Driven, Flat Design",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Vibrant learning colors + Progress green",
        "considerations": "Course catalog. Progress tracking. Video player. Quizzes. Certificates. Community forums. Gamification.",
    },
    "Parenting & Baby Tracker": {
        "style": "Claymorphism + Soft UI Evolution", "secondary": "Vibrant & Block-based, Accessible & Ethical",
        "landing": "Social Proof-Focused + Trust", "palette_focus": "Soft pastels (baby pink/sky blue/mint/peach) + warm accents",
        "considerations": "Feed/sleep/diaper quick-log buttons. Growth percentile chart. Milestone timeline with photos. Multiple child profiles. P",
    },
    "Parking Finder": {
        "style": "Minimalism + Glassmorphism", "secondary": "Flat Design, Micro-interactions",
        "landing": "Conversion-Optimized + Feature-Rich", "palette_focus": "Trust blue + available green + occupied red + map neutral",
        "considerations": "Real-time availability. In-app navigation. Payment integration. Parking timer alert. Favorite spots. Street vs garage.",
    },
    "Password Manager": {
        "style": "Minimalism + Accessible & Ethical", "secondary": "Dark Mode (OLED), Trust & Authority",
        "landing": "Trust & Authority + Feature-Rich", "palette_focus": "Trust blue + security green + dark neutral",
        "considerations": "Security-first. Zero-knowledge architecture. Biometric unlock. Breach alert dashboard. Password generator.",
    },
    "Period & Cycle Tracker": {
        "style": "Soft UI Evolution + Aurora UI", "secondary": "Accessible & Ethical, Claymorphism",
        "landing": "Social Proof-Focused + Trust", "palette_focus": "Rose/blush + lavender + fertility green + soft calendar tones",
        "considerations": "Cycle prediction. Symptom logging. Fertility window. Personalized insights. Privacy-first. Partner sharing option.",
    },
    "Personal Finance Tracker": {
        "style": "Glassmorphism + Dark Mode (OLED)", "secondary": "Minimalism, Flat Design",
        "landing": "Interactive Product Demo", "palette_focus": "Calm blue + success green + alert red + chart accents",
        "considerations": "Category pie/donut charts. Monthly trend lines. Budget progress bars. Transaction list with swipe actions. Receipt camer",
    },
    "Pet Tech App": {
        "style": "Claymorphism + Vibrant & Block-based", "secondary": "Micro-interactions, Flat Design",
        "landing": "Storytelling-Driven", "palette_focus": "Playful + Warm colors",
        "considerations": "Pet profiles. Health tracking. Playful UI. Photo galleries. Vet integration.",
    },
    "Pharmacy/Drug Store": {
        "style": "Flat Design + Accessible & Ethical", "secondary": "Minimalism, Trust & Authority",
        "landing": "Conversion-Optimized + Trust", "palette_focus": "Pharmacy Green + Trust Blue + Clean White",
        "considerations": "Product catalog. Prescription upload. Refill reminders. Health info. Store locator. Safety certifications.",
    },
    "Photo Editor & Filters": {
        "style": "Minimalism + Dark Mode (OLED)", "secondary": "Motion-Driven, Flat Design",
        "landing": "Feature-Rich Showcase + Interactive Demo", "palette_focus": "Dark editor background + vibrant filter preview strip + tool icon accent",
        "considerations": "Non-destructive editing. Filter preview carousel. Histogram. RAW support. Batch export. Social share direct.",
    },
    "Photography Studio": {
        "style": "Motion-Driven + Minimalism", "secondary": "Aurora UI, Glassmorphism",
        "landing": "Storytelling-Driven + Hero-Centric", "palette_focus": "Black + White + Minimal accent",
        "considerations": "Portfolio gallery. Before/after. Service packages. Booking system. Client galleries. Full-bleed imagery.",
    },
    "Plant Care Tracker": {
        "style": "Organic Biophilic + Soft UI Evolution", "secondary": "Claymorphism, Flat Design",
        "landing": "Storytelling-Driven + Social Proof", "palette_focus": "Nature greens + earth brown + sunny yellow reminder + water blue",
        "considerations": "Plant database with care guides. Watering reminders. Growth photo timeline. AI health diagnosis. Collection sharing.",
    },
    "Podcast Platform": {
        "style": "Dark Mode (OLED) + Minimalism", "secondary": "Motion-Driven, Vibrant & Block-based",
        "landing": "Storytelling-Driven", "palette_focus": "Dark + Audio waveform accents",
        "considerations": "Audio player UX. Episode discovery. Creator tools. Analytics for podcasters.",
    },
    "Portfolio/Personal": {
        "style": "Motion-Driven + Minimalism", "secondary": "Brutalism, Aurora UI",
        "landing": "Storytelling-Driven", "palette_focus": "Brand primary + artistic interpretation",
        "considerations": "Showcase work. Personality shine through.",
    },
    "Productivity Tool": {
        "style": "Flat Design + Micro-interactions", "secondary": "Minimalism, Soft UI Evolution",
        "landing": "Interactive Product Demo", "palette_focus": "Clear hierarchy + functional colors",
        "considerations": "Ease of use. Speed & efficiency focus.",
    },
    "Public Transit Guide": {
        "style": "Flat Design + Accessible & Ethical", "secondary": "Minimalism, Motion-Driven",
        "landing": "Feature-Rich Showcase + Interactive Demo", "palette_focus": "Transit brand line colors + real-time indicator green/red + map neutral",
        "considerations": "Real-time arrivals. Offline maps. Disruption alerts. Multi-modal routing. Fare calculation. Accessibility features.",
    },
    "Quantum Computing Interface": {
        "style": "Holographic / HUD + Dark Mode", "secondary": "Glassmorphism, Spatial UI",
        "landing": "Immersive/Interactive Experience", "palette_focus": "Quantum Blue #00FFFF + Deep Black + Interference patterns",
        "considerations": "Visualize complexity. Qubit states. Probability clouds. High-tech trust.",
    },
    "Real Estate/Property": {
        "style": "Glassmorphism + Minimalism", "secondary": "Motion-Driven, 3D & Hyperrealism",
        "landing": "Hero-Centric Design + Feature-Rich", "palette_focus": "Trust Blue (#0077B6) + Gold accents + White",
        "considerations": "Property listings. Virtual tours. Map integration. Agent profiles. Mortgage calculator. High-quality imagery.",
    },
    "Recipe & Cooking App": {
        "style": "Claymorphism + Vibrant & Block-based", "secondary": "Soft UI Evolution, Organic Biophilic",
        "landing": "Hero-Centric Design + Feature-Rich", "palette_focus": "Warm food tones (terracotta/sage/cream) + appetizing imagery",
        "considerations": "Step-by-step with checkable instructions. Ingredient list with serving adjuster. Built-in timer per step. Cooking mode (",
    },
    "Remote Work/Collaboration Tool": {
        "style": "Soft UI Evolution + Minimalism", "secondary": "Glassmorphism, Micro-interactions",
        "landing": "Feature-Rich Showcase", "palette_focus": "Calm Blue + Neutral grey",
        "considerations": "Real-time collaboration. Status indicators. Video integration. Notification management.",
    },
    "Restaurant/Food Service": {
        "style": "Vibrant & Block-based + Motion-Driven", "secondary": "Claymorphism, Flat Design",
        "landing": "Hero-Centric Design + Conversion", "palette_focus": "Warm colors (Orange Red Brown) + appetizing imagery",
        "considerations": "Menu display. Online ordering. Reservation system. Food photography. Location/hours prominent.",
    },
    "Ride Hailing / Transportation": {
        "style": "Minimalism + Glassmorphism", "secondary": "Dark Mode (OLED), Motion-Driven",
        "landing": "Conversion-Optimized + Demo", "palette_focus": "Brand primary + map neutral + status indicator colors",
        "considerations": "Map-centric full-screen UI. Pickup/dropoff pins + route polyline. Driver card (photo/rating/vehicle). Fare estimate. Tri",
    },
    "Road Trip Planner": {
        "style": "Aurora UI + Organic Biophilic", "secondary": "Motion-Driven, Vibrant & Block-based",
        "landing": "Storytelling-Driven + Hero-Centric", "palette_focus": "Adventure warm sunset orange + map teal + stop markers + road neutral",
        "considerations": "Route planning with stops. Point-of-interest discovery. Gas/food/hotel along route. Offline maps. Trip sharing.",
    },
    "Running & Cycling GPS": {
        "style": "Dark Mode (OLED) + Vibrant & Block-based", "secondary": "Motion-Driven, Glassmorphism",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Energetic orange + map accent + pace zones (green/yellow/red)",
        "considerations": "Live GPS tracking. Route map. Auto-pause detection. Segment leaderboards. Training zones. Social feed. Garmin sync.",
    },
    "SaaS (General)": {
        "style": "Glassmorphism + Flat Design", "secondary": "Soft UI Evolution, Minimalism",
        "landing": "Hero + Features + CTA", "palette_focus": "Trust blue + accent contrast",
        "considerations": "Balance modern feel with clarity. Focus on CTAs.",
    },
    "Scanner & Document Manager": {
        "style": "Minimalism + Flat Design", "secondary": "Dark Mode (OLED), Accessible & Ethical",
        "landing": "Feature-Rich Showcase + Demo", "palette_focus": "Clean white + camera viewfinder accent + file-type color coding",
        "considerations": "Camera capture with auto-edge detection. Crop/rotate/enhance. OCR text extraction overlay. PDF multi-page creation. Fold",
    },
    "Senior Care/Elderly": {
        "style": "Accessible & Ethical + Soft UI Evolution", "secondary": "Minimalism, Neumorphism",
        "landing": "Trust & Authority + Social Proof", "palette_focus": "Calm Blue + Warm neutrals + Large text",
        "considerations": "Care services. Staff qualifications. Facility tour. Family portal. Large touch targets. High contrast. Accessibility-fir",
    },
    "Short Video Editor": {
        "style": "Dark Mode (OLED) + Motion-Driven", "secondary": "Vibrant & Block-based, Glassmorphism",
        "landing": "Feature-Rich Showcase + Hero-Centric", "palette_focus": "Dark background + timeline track accent colors + effect preview vivid",
        "considerations": "Multi-track timeline. Licensed music library. Text overlays. Auto-captions. Export 9:16 / 16:9 / 1:1.",
    },
    "Sleep Tracker": {
        "style": "Dark Mode (OLED) + Neumorphism", "secondary": "Glassmorphism, Minimalism",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Deep midnight blue + stars/moon accent + sleep quality gradient (poor red to gre",
        "considerations": "Sleep cycle detection. Smart alarm wakes at light sleep. Snore detection. Weekly trends. Apple Health integration.",
    },
    "Smart Home/IoT Dashboard": {
        "style": "Glassmorphism + Dark Mode (OLED)", "secondary": "Minimalism, AI-Native UI",
        "landing": "Interactive Product Demo", "palette_focus": "Dark + Status indicator colors",
        "considerations": "Device status. Real-time controls. Energy monitoring. Automation rules. Quick actions.",
    },
    "Social Media App": {
        "style": "Vibrant & Block-based + Motion-Driven", "secondary": "Aurora UI, Micro-interactions",
        "landing": "Feature-Rich Showcase", "palette_focus": "Vibrant + engagement colors",
        "considerations": "Engagement & retention. Addictive design ethics.",
    },
    "Space Tech / Aerospace": {
        "style": "Holographic / HUD + Dark Mode", "secondary": "Glassmorphism, 3D & Hyperrealism",
        "landing": "Immersive Experience + Hero", "palette_focus": "Deep Space Black + Star White + Metallic",
        "considerations": "High-tech feel. Precision. Telemetry data.",
    },
    "Spatial Computing OS / App": {
        "style": "Spatial UI (VisionOS)", "secondary": "Glassmorphism, 3D & Hyperrealism",
        "landing": "Immersive/Interactive Experience", "palette_focus": "Frosted Glass + System Colors + Depth",
        "considerations": "Gaze/Pinch interaction. Depth hierarchy. Environment awareness.",
    },
    "Sports Team/Club": {
        "style": "Vibrant & Block-based + Motion-Driven", "secondary": "Dark Mode (OLED), 3D & Hyperrealism",
        "landing": "Hero-Centric Design + Feature-Rich", "palette_focus": "Team colors + Energetic accents",
        "considerations": "Schedule. Roster. News. Tickets. Merchandise. Fan engagement. Action imagery.",
    },
    "Study Together / Virtual Coworking": {
        "style": "Minimalism + Soft UI Evolution", "secondary": "Flat Design, Dark Mode (OLED)",
        "landing": "Social Proof-Focused + Feature-Rich", "palette_focus": "Calm focus blue + session progress indicator + ambient warm neutrals",
        "considerations": "Live study rooms with video/avatar presence. Shared focus timer. Ambient music. Goals sharing. Streak accountability.",
    },
    "Subscription Box Service": {
        "style": "Vibrant & Block-based + Motion-Driven", "secondary": "Claymorphism, Aurora UI",
        "landing": "Feature-Rich Showcase", "palette_focus": "Brand + Excitement colors",
        "considerations": "Unboxing experience. Personalization quiz. Subscription management. Product reveals.",
    },
    "Sustainable Energy / Climate Tech": {
        "style": "Organic Biophilic + E-Ink / Paper", "secondary": "Data-Dense, Swiss Modernism",
        "landing": "Interactive Demo + Data", "palette_focus": "Earth Green + Sky Blue + Solar Yellow",
        "considerations": "Data transparency. Impact visualization. Low-carbon web design.",
    },
    "Theater/Cinema": {
        "style": "Dark Mode (OLED) + Motion-Driven", "secondary": "Vibrant & Block-based, Glassmorphism",
        "landing": "Hero-Centric Design + Conversion", "palette_focus": "Dark + Spotlight accents + Gold",
        "considerations": "Showtimes. Seat selection. Trailers. Coming soon. Membership. Dramatic imagery.",
    },
    "Timer & Pomodoro": {
        "style": "Minimalism + Neumorphism", "secondary": "Dark Mode (OLED), Micro-interactions",
        "landing": "Minimal & Direct", "palette_focus": "High-contrast on dark + focus red/amber + break green",
        "considerations": "Large centered countdown digits. Circular progress ring. Session/break auto-switch. Session history log. Custom interval",
    },
    "Translator App": {
        "style": "Flat Design + AI-Native UI", "secondary": "Minimalism, Micro-interactions",
        "landing": "Feature-Rich Showcase + Interactive Demo", "palette_focus": "Global blue + neutral grey + language flag accent",
        "considerations": "Real-time camera translation (OCR). Voice input and output. Offline mode. Conversation mode. Phrasebook.",
    },
    "Travel/Tourism Agency": {
        "style": "Aurora UI + Motion-Driven", "secondary": "Vibrant & Block-based, Glassmorphism",
        "landing": "Storytelling-Driven + Hero-Centric", "palette_focus": "Vibrant destination colors + Sky Blue + Warm accents",
        "considerations": "Destination showcase. Booking system. Itinerary builder. Reviews. Inspiration galleries. Mobile-first.",
    },
    "Trivia & Quiz Game": {
        "style": "Vibrant & Block-based + Micro-interactions", "secondary": "Claymorphism, Flat Design",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Energetic blue + correct green + incorrect red + leaderboard gold",
        "considerations": "Timer pressure UX. Category selection. Streak system. Real-time multiplayer. Daily quiz mode.",
    },
    "VPN & Privacy Tool": {
        "style": "Minimalism + Dark Mode (OLED)", "secondary": "Cyberpunk UI, Trust & Authority",
        "landing": "Trust & Authority + Conversion-Optimized", "palette_focus": "Dark shield blue + connected green + disconnected red + trust accent",
        "considerations": "One-tap connect. Server selection by country. No-log policy prominent. Speed indicator. Kill switch. Protocol choice.",
    },
    "Veterinary Clinic": {
        "style": "Claymorphism + Accessible & Ethical", "secondary": "Soft UI Evolution, Flat Design",
        "landing": "Social Proof-Focused + Trust", "palette_focus": "Caring Blue + Pet-friendly colors + Warm accents",
        "considerations": "Pet services. Vet profiles. Online booking. Pet portal. Emergency info. Friendly animal imagery.",
    },
    "Video Streaming/OTT": {
        "style": "Dark Mode (OLED) + Motion-Driven", "secondary": "Glassmorphism, Vibrant & Block-based",
        "landing": "Hero-Centric Design + Feature-Rich", "palette_focus": "Dark bg + Content poster colors + Brand accent",
        "considerations": "Video player. Content discovery. Watchlist. Continue watching. Personalized recommendations. Thumbnail-heavy.",
    },
    "Voice Recorder & Memo": {
        "style": "Minimalism + AI-Native UI", "secondary": "Flat Design, Dark Mode (OLED)",
        "landing": "Interactive Product Demo + Minimal", "palette_focus": "Clean white + recording red + waveform accent",
        "considerations": "Waveform display. Background recording. Auto-transcription (AI). Tag/organize. Cloud sync.",
    },
    "Wallpaper & Theme App": {
        "style": "Vibrant & Block-based + Aurora UI", "secondary": "Glassmorphism, Motion-Driven",
        "landing": "Feature-Rich Showcase + Social Proof", "palette_focus": "Content-driven + trending aesthetic palettes + download accent",
        "considerations": "Category browsing. Preview on device. Daily wallpaper auto-set. Widget matching. Creator uploads. Resolution auto-fit.",
    },
    "Wardrobe & Outfit Planner": {
        "style": "Minimalism + Motion-Driven", "secondary": "Aurora UI, Soft UI Evolution",
        "landing": "Storytelling-Driven + Feature-Rich", "palette_focus": "Clean fashion neutral + full clothes color palette + accent",
        "considerations": "Photo catalog of clothes. AI outfit suggestions. Calendar integration. Capsule wardrobe. Season filtering.",
    },
    "Water & Hydration Reminder": {
        "style": "Claymorphism + Vibrant & Block-based", "secondary": "Flat Design, Micro-interactions",
        "landing": "Minimal & Direct + Demo", "palette_focus": "Refreshing blue + water wave animation + goal progress accent",
        "considerations": "Tap to log quickly. Animated fill visualization. Custom reminders. Goal by weight/weather. Streak system. Widget.",
    },
    "Weather App": {
        "style": "Glassmorphism + Aurora UI", "secondary": "Motion-Driven, Minimalism",
        "landing": "Hero-Centric Design", "palette_focus": "Atmospheric gradients (sky blue → sunset → storm grey) + temp scale",
        "considerations": "Location auto-detect. Hourly horizontal scroll + daily/weekly list. Animated weather icons. Air quality index. UV/wind/h",
    },
    "Wedding/Event Planning": {
        "style": "Soft UI Evolution + Aurora UI", "secondary": "Glassmorphism, Motion-Driven",
        "landing": "Storytelling-Driven + Social Proof", "palette_focus": "Soft Pink (#FFD6E0) + Gold + Cream + Sage",
        "considerations": "Portfolio gallery. Vendor directory. Planning tools. Timeline. Budget tracker. Romantic aesthetic.",
    },
    "White Noise & Ambient Sound": {
        "style": "Minimalism + Dark Mode (OLED)", "secondary": "Neumorphism, Organic Biophilic",
        "landing": "Minimal & Direct + Social Proof", "palette_focus": "Calming dark + ambient texture visual + subtle sound wave + sleep blue",
        "considerations": "Sound mixer with multiple simultaneous layers. Sleep timer with fade. Custom soundscapes. Offline. Background audio.",
    },
    "Word & Crossword Game": {
        "style": "Minimalism + Flat Design", "secondary": "Swiss Modernism 2.0, Micro-interactions",
        "landing": "Minimal & Direct + Demo", "palette_focus": "Clean white + warm letter tiles + success green + shake red",
        "considerations": "Daily challenge with shareable results. Physical keyboard feel. Difficulty levels. Dictionary hints. Streak stats.",
    },
    "Yoga & Stretching Guide": {
        "style": "Organic Biophilic + Soft UI Evolution", "secondary": "Neumorphism, Minimalism",
        "landing": "Storytelling-Driven + Social Proof", "palette_focus": "Earth calming sage/terracotta/cream + breathing gradient + warm accent",
        "considerations": "Pose library with illustrations. Guided sessions with audio. Breathing exercises. Progress calendar. Beginner to advance",
    },
}


# ─────────────────────────────────────────────────────────────
# UX RULES STRUCTURED (Critical/High only, from ui-ux-pro-max)
# ─────────────────────────────────────────────────────────────

UX_RULES_STRUCTURED: list[dict[str, str]] = [
    {"category": "Navigation", "issue": "Smooth Scroll", "severity": "High", "do": "Use scroll-behavior: smooth on html element", "dont": "Jump directly without transition"},
    {"category": "Navigation", "issue": "Back Button", "severity": "High", "do": "Preserve navigation history properly", "dont": "Break browser/app back button behavior"},
    {"category": "Animation", "issue": "Excessive Motion", "severity": "High", "do": "Animate 1-2 key elements per view maximum", "dont": "Animate everything that moves"},
    {"category": "Animation", "issue": "Reduced Motion", "severity": "High", "do": "Check prefers-reduced-motion media query", "dont": "Ignore accessibility motion settings"},
    {"category": "Animation", "issue": "Loading States", "severity": "High", "do": "Use skeleton screens or spinners", "dont": "Leave UI frozen with no feedback"},
    {"category": "Animation", "issue": "Hover vs Tap", "severity": "High", "do": "Use click/tap for primary interactions", "dont": "Rely only on hover for important actions"},
    {"category": "Layout", "issue": "Z-Index Management", "severity": "High", "do": "Define z-index scale system (10 20 30 50)", "dont": "Use arbitrary large z-index values"},
    {"category": "Layout", "issue": "Content Jumping", "severity": "High", "do": "Reserve space for async content", "dont": "Let images/content push layout around"},
    {"category": "Touch", "issue": "Touch Target Size", "severity": "High", "do": "Minimum 44x44px touch targets", "dont": "Tiny clickable areas"},
    {"category": "Interaction", "issue": "Focus States", "severity": "High", "do": "Use visible focus rings on interactive elements", "dont": "Remove focus outline without replacement"},
    {"category": "Interaction", "issue": "Loading Buttons", "severity": "High", "do": "Disable button and show loading state", "dont": "Allow multiple clicks during processing"},
    {"category": "Interaction", "issue": "Error Feedback", "severity": "High", "do": "Show clear error messages near problem", "dont": "Silent failures with no feedback"},
    {"category": "Interaction", "issue": "Confirmation Dialogs", "severity": "High", "do": "Confirm before delete/irreversible actions", "dont": "Delete without confirmation"},
    {"category": "Accessibility", "issue": "Color Contrast", "severity": "High", "do": "Minimum 4.5:1 ratio for normal text", "dont": "Low contrast text"},
    {"category": "Accessibility", "issue": "Color Only", "severity": "High", "do": "Use icons/text in addition to color", "dont": "Red/green only for error/success"},
    {"category": "Accessibility", "issue": "Alt Text", "severity": "High", "do": "Descriptive alt text for meaningful images", "dont": "Empty or missing alt attributes"},
    {"category": "Accessibility", "issue": "ARIA Labels", "severity": "High", "do": "Add aria-label for icon-only buttons", "dont": "Icon buttons without labels"},
    {"category": "Accessibility", "issue": "Keyboard Navigation", "severity": "High", "do": "Tab order matches visual order", "dont": "Keyboard traps or illogical tab order"},
    {"category": "Accessibility", "issue": "Form Labels", "severity": "High", "do": "Use label with for attribute or wrap input", "dont": "Placeholder-only inputs"},
    {"category": "Accessibility", "issue": "Error Messages", "severity": "High", "do": "Use aria-live or role=alert for errors", "dont": "Visual-only error indication"},
    {"category": "Performance", "issue": "Image Optimization", "severity": "High", "do": "Use appropriate size and format (WebP)", "dont": "Unoptimized full-size images"},
    {"category": "Forms", "issue": "Input Labels", "severity": "High", "do": "Always show label above or beside input", "dont": "Placeholder as only label"},
    {"category": "Forms", "issue": "Submit Feedback", "severity": "High", "do": "Show loading then success/error state", "dont": "No feedback after submit"},
    {"category": "Responsive", "issue": "Touch Friendly", "severity": "High", "do": "Increase touch targets on mobile", "dont": "Same tiny buttons on mobile"},
    {"category": "Responsive", "issue": "Readable Font Size", "severity": "High", "do": "Minimum 16px body text on mobile", "dont": "Tiny text on mobile"},
    {"category": "Responsive", "issue": "Viewport Meta", "severity": "High", "do": "Use width=device-width initial-scale=1", "dont": "Missing or incorrect viewport"},
    {"category": "Responsive", "issue": "Horizontal Scroll", "severity": "High", "do": "Ensure content fits viewport width", "dont": "Content wider than viewport"},
    {"category": "Typography", "issue": "Contrast Readability", "severity": "High", "do": "Use darker text on light backgrounds", "dont": "Gray text on gray background"},
    {"category": "Feedback", "issue": "Loading Indicators", "severity": "High", "do": "Show spinner/skeleton for operations > 300ms", "dont": "No feedback during loading"},
    {"category": "AI Interaction", "issue": "Disclaimer", "severity": "High", "do": "Clearly label AI generated content", "dont": "Present AI as human"},
    {"category": "Spatial UI", "issue": "Gaze Hover", "severity": "High", "do": "Scale/highlight element on look", "dont": "Static element until pinch"},
    {"category": "Accessibility", "issue": "Motion Sensitivity", "severity": "High", "do": "Respect prefers-reduced-motion", "dont": "Force scroll effects"},
]


# ─────────────────────────────────────────────────────────────
# SELECTOR ENGINE
# ─────────────────────────────────────────────────────────────

def _score_palette(palette: dict, msg_lower: str) -> int:
    """Score de pertinence d'une palette pour un message."""
    return sum(1 for kw in palette["keywords"] if kw in msg_lower)


def _score_font(font: dict, msg_lower: str) -> int:
    """Score de pertinence d'un font pairing pour un message."""
    return sum(1 for kw in font["keywords"] if kw in msg_lower)


def select_pro_palette(user_message: str) -> dict:
    """Sélectionne la palette professionnelle la plus adaptée (WCAG-compliant)."""
    msg_lower = user_message.lower()
    best_score = 0
    candidates: list[dict] = []
    for p in PRO_PALETTES:
        score = _score_palette(p, msg_lower)
        if score > best_score:
            best_score = score
            candidates = [p]
        elif score == best_score and score > 0:
            candidates.append(p)
    return random.choice(candidates) if candidates else random.choice(PRO_PALETTES)


def select_pro_font(user_message: str) -> dict:
    """Sélectionne le font pairing professionnel le plus adapté."""
    msg_lower = user_message.lower()
    best_score = 0
    candidates: list[dict] = []
    for f in PRO_FONT_PAIRINGS:
        score = _score_font(f, msg_lower)
        if score > best_score:
            best_score = score
            candidates = [f]
        elif score == best_score and score > 0:
            candidates.append(f)
    return random.choice(candidates) if candidates else random.choice(PRO_FONT_PAIRINGS)


def select_pro_style(user_message: str) -> dict:
    """Sélectionne le style UI adapté."""
    msg_lower = user_message.lower()
    for style in PRO_UI_STYLES:
        if any(kw in msg_lower for kw in style["keywords"]):
            return style
    return random.choice(PRO_UI_STYLES)


def get_design_for_project(user_message: str) -> dict[str, Any]:
    """
    Retourne un système de design complet adapté au projet.
    
    Returns:
        {
            "palette": {...},    # couleurs WCAG-compliant
            "font": {...},       # heading + body fonts
            "style": {...},      # style UI
            "ux_rules": str,     # règles UX critiques à injecter
            "css_root": str,     # bloc :root CSS complet
            "font_import": str,  # balise <link> Google Fonts
        }
    """
    palette = select_pro_palette(user_message)
    font = select_pro_font(user_message)
    style = select_pro_style(user_message)
    is_dark = palette["theme"] == "dark"

    # Calcul RGB pour les rgba()
    def hex_to_rgb(h: str) -> str:
        h = h.lstrip("#")
        if len(h) == 6:
            return f"{int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}"
        return "99, 102, 241"

    primary_rgb = hex_to_rgb(palette["primary"])

    css_root = f""":root {{
    /* === Palette: {palette["name"]} ({palette["product_type"]}) === */
    --primary: {palette["primary"]};
    --secondary: {palette["secondary"]};
    --accent: {palette["accent"]};
    --background: {palette["background"]};
    --foreground: {palette["foreground"]};
    --card: {palette["card"]};
    --muted: {palette["muted"]};
    --muted-foreground: {palette["muted_fg"]};
    --border: {palette["border"]};
    --primary-rgb: {primary_rgb};
    --gradient: linear-gradient(135deg, {palette["primary"]}, {palette["secondary"]});

    /* === Computed === */
    --bg-dark: {palette["background"]};
    --bg-card: {palette["card"]};
    --text-primary: {palette["foreground"]};
    --text-secondary: {palette["muted_fg"]};
    --glass-bg: rgba({primary_rgb}, {"0.08" if is_dark else "0.06"});
    --glass-border: rgba({primary_rgb}, {"0.15" if is_dark else "0.12"});
    --glow: rgba({primary_rgb}, 0.35);
    --shadow-sm: 0 1px 2px rgba(0,0,0,{"0.2" if is_dark else "0.05"});
    --shadow: 0 4px 6px -1px rgba(0,0,0,{"0.3" if is_dark else "0.1"});
    --shadow-lg: 0 10px 15px -3px rgba(0,0,0,{"0.4" if is_dark else "0.1"});
    --shadow-xl: 0 20px 25px -5px rgba(0,0,0,{"0.5" if is_dark else "0.1"});
    --radius: 16px;
    --radius-sm: 8px;
    --radius-lg: 24px;
    --radius-full: 9999px;
    --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);

    /* === Typography === */
    {font["css_vars"]}
    --font-size-base: 1rem;
    --line-height-base: 1.7;
}}"""

    font_import = font["css_import"]

    landing = select_landing_pattern(user_message)
    product_match = match_product_type(user_message)

    return {
        "palette": palette,
        "font": font,
        "style": style,
        "ux_rules": UX_CRITICAL_RULES,
        "ux_rules_structured": UX_RULES_STRUCTURED,
        "css_root": css_root,
        "font_import": font_import,
        "is_dark": is_dark,
        "primary_rgb": primary_rgb,
        "landing_pattern": landing,
        "product_routing": product_match,
    }

def select_landing_pattern(user_message: str) -> dict:
    """Sélectionne le landing pattern adapté au projet."""
    msg_lower = user_message.lower()
    best_score = 0
    candidates: list[dict] = []
    for lp in LANDING_PATTERNS:
        score = sum(1 for kw in lp["keywords"] if kw in msg_lower)
        if score > best_score:
            best_score = score
            candidates = [lp]
        elif score == best_score and score > 0:
            candidates.append(lp)
    return random.choice(candidates) if candidates else LANDING_PATTERNS[0]


def match_product_type(user_message: str) -> dict | None:
    """Trouve le type de produit le plus proche dans PRODUCT_DESIGN_MAP."""
    msg_lower = user_message.lower()
    best_score = 0
    best_match = None
    for pt, info in PRODUCT_DESIGN_MAP.items():
        pt_lower = pt.lower()
        # Score: count words from product type found in message
        words = [w for w in pt_lower.replace("/", " ").replace("(", " ").replace(")", " ").split() if len(w) > 2]
        score = sum(2 for w in words if w in msg_lower)
        if score > best_score:
            best_score = score
            best_match = {"product_type": pt, **info}
    return best_match if best_score >= 2 else None


# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
