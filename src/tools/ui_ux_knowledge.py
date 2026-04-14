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

    return {
        "palette": palette,
        "font": font,
        "style": style,
        "ux_rules": UX_CRITICAL_RULES,
        "css_root": css_root,
        "font_import": font_import,
        "is_dark": is_dark,
        "primary_rgb": primary_rgb,
    }
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
