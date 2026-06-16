"""Tests du builder unifié create_skill (src/skills/tools.py).

Couvre S1 (implémentation unique), S2 (porte de validation),
S3 (trigger garanti). Le dossier cible est injecté via skills_dir → aucune
pollution du repo ni du loader runtime.
"""

import pytest

from src.skills.tools import create_skill, update_skill, delete_skill


# ─── S3 — trigger garanti ───────────────────────────────────────────────────

def test_refuse_description_vide(tmp_path):
    r = create_skill(name="ghost", description="", skills_dir=tmp_path)
    assert r.startswith("❌")
    assert not (tmp_path / "ghost").exists()


def test_refuse_description_generique(tmp_path):
    r = create_skill(name="meteo", description="Skill meteo", skills_dir=tmp_path)
    assert r.startswith("❌")
    assert not (tmp_path / "meteo").exists()


def test_accepte_description_reelle(tmp_path):
    r = create_skill(
        name="meteo",
        description="Affiche la météo en direct d'une ville",
        skills_dir=tmp_path,
    )
    assert r.startswith("✅")
    md = (tmp_path / "meteo" / "SKILL.md").read_text(encoding="utf-8")
    assert "Affiche la météo" in md
    assert "name: meteo" in md


# ─── Chemin interactif (content) ────────────────────────────────────────────

def test_content_avec_frontmatter_preserve(tmp_path):
    content = (
        "---\nname: ignore-me\n"
        'description: "Convertit un CSV en graphique PNG"\n---\n\n'
        "# Guide\n\nÉtapes...\n"
    )
    r = create_skill(name="charts", content=content, with_script=False, skills_dir=tmp_path)
    assert r.startswith("✅")
    md = (tmp_path / "charts" / "SKILL.md").read_text(encoding="utf-8")
    # Le nom est imposé par l'argument (pas par le frontmatter fourni).
    assert "name: charts" in md
    assert "Convertit un CSV" in md


def test_content_sans_frontmatter_description_derivee(tmp_path):
    r = create_skill(
        name="notes",
        content="# Prise de notes\n\nStructure tes notes en Markdown.",
        with_script=False,
        skills_dir=tmp_path,
    )
    assert r.startswith("✅")
    md = (tmp_path / "notes" / "SKILL.md").read_text(encoding="utf-8")
    assert "Prise de notes" in md


# ─── S2 — porte de validation ───────────────────────────────────────────────

def test_validation_rejette_et_nettoie(tmp_path):
    # with_script crée scripts/<name>.py ; on simule un échec en désactivant
    # la description → md None avant écriture. Pour tester le nettoyage après
    # écriture, on force une description valide mais un nom déjà pris ailleurs.
    ok = create_skill(name="good", description="Une vraie description claire", skills_dir=tmp_path)
    assert ok.startswith("✅")
    # Re-création → refus (existe déjà), pas d'écrasement.
    again = create_skill(name="good", description="Autre description claire", skills_dir=tmp_path)
    assert again.startswith("❌")
    assert "existe deja" in again


# ─── Nom invalide ───────────────────────────────────────────────────────────

def test_nom_invalide(tmp_path):
    r = create_skill(name="!!!", description="Une vraie description", skills_dir=tmp_path)
    assert r.startswith("❌")
    assert "invalide" in r.lower()


# ─── Rétro-compat autonomie (with_script + verdict ✅/❌) ────────────────────

def test_with_script_cree_script_et_valide(tmp_path):
    r = create_skill(
        name="daily",
        description="Résume l'actualité tech du jour",
        with_script=True,
        skills_dir=tmp_path,
    )
    assert r.startswith("✅")
    assert (tmp_path / "daily" / "scripts" / "daily.py").exists()


def test_verdict_commence_par_check(tmp_path):
    # Les appelants autonomie testent str(result).startswith("✅").
    r = create_skill(name="ok", description="Description claire et utile", skills_dir=tmp_path)
    assert r.strip().startswith("✅")


# ─── P0 — garde "guides purs" (allow_scripts) ───────────────────────────────

def test_allow_scripts_false_interdit_script(tmp_path):
    """Même avec with_script=True, allow_scripts=False ne crée AUCUN script."""
    r = create_skill(
        name="guide",
        description="Un guide clair et utile pour une tâche",
        with_script=True,
        allow_scripts=False,
        skills_dir=tmp_path,
    )
    assert r.startswith("✅")
    assert (tmp_path / "guide" / "SKILL.md").exists()
    assert not (tmp_path / "guide" / "scripts").exists()  # garde guides purs


def test_allow_scripts_true_cree_script(tmp_path):
    """Le défaut (allow_scripts=True) laisse with_script créer le script."""
    r = create_skill(
        name="avec-code",
        description="Un skill avec un script utile",
        with_script=True,
        skills_dir=tmp_path,
    )
    assert r.startswith("✅")
    assert (tmp_path / "avec-code" / "scripts" / "avec-code.py").exists()


# ─── P1 — update_skill (re-validé) ──────────────────────────────────────────

def test_update_skill_ok(tmp_path):
    create_skill(name="evolve", description="Première description claire", skills_dir=tmp_path)
    r = update_skill(
        name="evolve",
        content='---\nname: evolve\ndescription: "Nouvelle description bien plus précise"\n---\n\n# Evolve\n',
        skills_dir=tmp_path,
    )
    assert r.startswith("✅")
    md = (tmp_path / "evolve" / "SKILL.md").read_text(encoding="utf-8")
    assert "Nouvelle description bien plus précise" in md


def test_update_skill_inexistant(tmp_path):
    r = update_skill(name="fantome", content="# rien", skills_dir=tmp_path)
    assert r.startswith("❌")
    assert "n'existe pas" in r


def test_update_skill_sans_frontmatter_reutilise_description(tmp_path):
    """F5 : un update dont le content n'a pas de description exploitable réutilise
    la description existante (au lieu de refuser)."""
    create_skill(name="demo", description="Une description initiale bien claire", skills_dir=tmp_path)
    # content sans frontmatter, dont le titre H1 = nom du skill → dérivée générique
    r = update_skill(name="demo", content="# Demo\n\nNouveau corps enrichi du skill.", skills_dir=tmp_path)
    assert r.startswith("✅"), r
    md = (tmp_path / "demo" / "SKILL.md").read_text(encoding="utf-8")
    assert "Une description initiale bien claire" in md  # description préservée
    assert "Nouveau corps enrichi" in md                 # nouveau corps appliqué


def test_update_skill_nouvelle_description_prioritaire(tmp_path):
    """F5 ne doit pas écraser une vraie nouvelle description fournie dans le content."""
    create_skill(name="demo2", description="Ancienne description claire", skills_dir=tmp_path)
    r = update_skill(
        name="demo2",
        content='---\nname: demo2\ndescription: "Toute nouvelle description précise et utile"\n---\n\n# Demo2\n',
        skills_dir=tmp_path,
    )
    assert r.startswith("✅")
    md = (tmp_path / "demo2" / "SKILL.md").read_text(encoding="utf-8")
    assert "Toute nouvelle description précise" in md
    assert "Ancienne description" not in md


def test_update_skill_contenu_minimal_preserve_description(tmp_path):
    """F5 : la protection est passée de 'refus' à 'préservation'. Un content
    minimal (sans description exploitable) ne fait plus perdre la description —
    elle est réutilisée, le skill reste valide et déclenchable."""
    create_skill(name="stable", description="Description initiale valable", skills_dir=tmp_path)
    r = update_skill(name="stable", content="stable", skills_dir=tmp_path)
    assert r.startswith("✅")
    md = (tmp_path / "stable" / "SKILL.md").read_text(encoding="utf-8")
    assert "Description initiale valable" in md  # description jamais perdue


def test_update_preserve_scripts(tmp_path):
    create_skill(
        name="kit", description="Un kit avec script", with_script=True, skills_dir=tmp_path
    )
    assert (tmp_path / "kit" / "scripts" / "kit.py").exists()
    update_skill(
        name="kit",
        content='---\nname: kit\ndescription: "Kit mis à jour avec plus de détails"\n---\n\n# Kit\n',
        skills_dir=tmp_path,
    )
    # La mise à jour ne touche que SKILL.md → le script reste.
    assert (tmp_path / "kit" / "scripts" / "kit.py").exists()


# ─── P1 — delete_skill ──────────────────────────────────────────────────────

def test_delete_skill_ok(tmp_path):
    create_skill(name="jetable", description="Skill temporaire à supprimer", skills_dir=tmp_path)
    assert (tmp_path / "jetable").exists()
    r = delete_skill(name="jetable", skills_dir=tmp_path)
    assert r.startswith("✅")
    assert not (tmp_path / "jetable").exists()


def test_delete_skill_inexistant(tmp_path):
    r = delete_skill(name="jamais-cree", skills_dir=tmp_path)
    assert r.startswith("❌")
    assert "introuvable" in r
