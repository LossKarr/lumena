"""Lot REG-1 - l'ordre des gardes du registre est un CONTRAT, pas un hasard.

--- Ce que la regression complete du 24/09 a mesure ---

Trois tests de `test_dynamic_registry_extension.py` tombaient en regression
complete et passaient isolement. Ce n'etait NI un flake NI une pollution d'etat :

    Expected regex: 'native'
    Actual message: 'IDE namespace is reserved for its authenticated provider'

`test_register_dynamic_handler_rejects_native_collision` choisissait son nom par
`next(iter(registry._native_handler_names))` - un **frozenset**, dont l'ordre
d'iteration depend du hash seed du processus. Mesure du 24/09 : **33 des 612
natifs appartiennent au namespace IDE, soit 5,4 %** - une chance sur dix-huit,
a chaque processus, de tirer un `ide_*`.

Et pour ces 33, le garde de namespace tirait AVANT le controle de collision
native : le refus « native » que le docstring promet etait **inatteignable**.

--- Ce que ce lot fige ---

1. L'invariant le plus fort passe d'abord : **un natif n'est jamais
   reenregistrable**, et c'est cette cause qui est nommee - namespace IDE compris.
2. Le garde de namespace garde son objet propre : les `ide_*` qui ne sont PAS
   natifs, reserves au fournisseur authentifie. Ce garde n'etait couvert par
   AUCUN test avant ce lot (celui de CONN-3c porte sur un autre registre,
   `ExternalToolProviderRegistry`).
3. **On n'exerce plus un tirage, on exerce l'ensemble.** Un test qui pioche dans
   un frozenset ne prouve rien de reproductible : il fallait un test qui passe
   sous n'importe quel hash seed, donc qui parcourt TOUS les natifs.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from src.mcp.policy import MCPPolicy
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.registry_v2 import HandlerDef
from src.reasoning.tool_registry import DynamicRegistryError, ToolRegistry
from src.utils.external_tool_names import is_ide_tool_name


@pytest.fixture(scope="module")
def registry() -> ToolRegistry:
    """Registre reel, tous les handlers V2 charges (couteux -> portee module)."""
    return ToolRegistry()


def _hdef(name: str) -> HandlerDef:
    async def _handler(ctx, **kwargs):
        return HandlerResult.ok(output="echo")

    parametres: Dict[str, Any] = {"type": "object", "properties": {}}
    return HandlerDef(
        name=name,
        description="Echo for test",
        parameters=parametres,
        handler=_handler,
        category="mcp",
        source_module="mcp.test",
    )


def _refus(registry: ToolRegistry, name: str) -> str:
    """Enregistre et rend le message de refus. Un enregistrement REUSSI est une faute."""
    try:
        registry.register_dynamic_handler(_hdef(name), policy=MCPPolicy.READ_ONLY)
    except DynamicRegistryError as erreur:
        return str(erreur)
    registry.unregister_dynamic_handler(name)  # ne pas polluer les tests suivants
    pytest.fail(f"{name!r} a ete enregistre alors qu'il est natif")


# ── 1. AUCUN natif n'est reenregistrable, et tous nomment la meme cause ─────

def test_aucun_natif_nest_reenregistrable_et_tous_disent_native(registry):
    """Le test qui remplace le tirage : il parcourt les 612 natifs.

    C'est le coeur du lot. Un `next(iter(frozenset))` ne prouve qu'un cas sur
    612, tire au hasard du hash seed ; ici le contrat est verifie en entier,
    donc le resultat est le meme sous n'importe quel seed.
    """
    natifs = sorted(registry._native_handler_names)
    assert len(natifs) > 500, f"registre anormalement petit : {len(natifs)} natifs"

    manquants = [n for n in natifs if "native" not in _refus(registry, n).lower()]
    assert not manquants, (
        f"{len(manquants)} natifs sur {len(natifs)} ne nomment pas la collision "
        f"native ; exemples : {manquants[:5]}"
    )


def test_les_natifs_du_namespace_ide_nomment_aussi_la_collision(registry):
    """Les 33 facades IDE dépréciées : natives d'abord, IDE ensuite.

    C'est le cas exact qui faisait tomber la regression. Il est desormais
    exerce explicitement, par son nom, et non par chance.
    """
    ide_natifs = sorted(n for n in registry._native_handler_names if is_ide_tool_name(n))
    assert ide_natifs, "aucun natif IDE : le registre n'a pas charge .handlers.ide"

    for nom in ide_natifs:
        message = _refus(registry, nom)
        assert "native" in message.lower(), (
            f"{nom!r} est natif mais son refus ne le dit pas : {message!r}"
        )


# ── 2. Le garde de namespace garde son objet propre ─────────────────────────

def test_un_nom_ide_non_natif_reste_refuse_pour_le_namespace(registry):
    """Reordonner ne doit pas desarmer le garde qu'on deplace.

    Un `ide_*` qui n'existe pas comme natif ne tombe sur aucune collision : seul
    le garde de namespace l'arrete. Avant ce lot, ce garde n'etait couvert par
    aucun test - le deplacer sans le couvrir l'aurait laisse sans filet.
    """
    nom = "ide_ce_nom_nexiste_pas_comme_natif"
    assert nom not in registry._native_handler_names
    assert is_ide_tool_name(nom)

    message = _refus(registry, nom)
    assert "namespace" in message.lower() and "reserved" in message.lower(), message


def test_un_nom_mcp_ordinaire_senregistre_toujours(registry):
    """Le garde ne doit pas devenir un mur : le cas nominal passe encore."""
    nom = "mcp__reg1__echo"
    registry.register_dynamic_handler(_hdef(nom), policy=MCPPolicy.READ_ONLY)
    try:
        assert nom in registry._dynamic_handlers
    finally:
        registry.unregister_dynamic_handler(nom)


# ── 3. Le desarmement ne passe pas non plus par unregister ──────────────────

def test_aucun_natif_ne_peut_etre_desenregistre(registry):
    """Symetrie : proteger l'enregistrement sans proteger le retrait ne protege rien."""
    natifs = sorted(registry._native_handler_names)
    for nom in natifs:
        assert registry.unregister_dynamic_handler(nom) is False, (
            f"{nom!r} natif a pu etre desenregistre"
        )


# ── 4. L'ecart entre « natif » et « visible » est un FAIT, on le fige ───────

def test_les_natifs_ide_sont_proteges_mais_absents_de_tools(registry):
    """Deux sources de verite divergent par conception - alors on la mesure.

    `_native_handler_names` est un snapshot pris a l'init : il contient les 33
    facades IDE. `tools` est un `ExternalToolView` dont l'instantane vaut
    « natifs non-IDE + ce que le PROJECTEUR expose » : les facades sortent donc
    de la vue (CONN-8a) et n'y reviennent que par le projecteur, quand une IDE
    est reellement connectee.

    L'ecart est voulu : protege contre le reenregistrement, invisible a l'usage.
    Mais il n'etait ecrit NULLE PART, et c'est lui qui a fait tomber trois tests
    en regression complete - `assert natif in registry.tools` est faux pour les
    facades. Un ecart qu'on mesure ne surprend plus personne ; un ecart qu'on
    laisse implicite revient sous forme de faux flake.

    Ce test s'est d'ailleurs corrige lui-meme a l'ecriture : sa premiere version
    exigeait « tous les IDE invisibles » et a immediatement trouve `ide_launch`.
    """
    natifs = registry._native_handler_names
    ide = {n for n in natifs if is_ide_tool_name(n)}
    exposes = set(registry.tools._project())
    invisibles = {n for n in natifs if n not in registry.tools}

    assert ide, "aucun natif IDE : .handlers.ide n'a pas ete charge"
    assert invisibles == ide - exposes, (
        "l'ecart natif/visible ne s'explique plus par le projecteur ; "
        f"invisibles a tort : {sorted(invisibles - (ide - exposes))[:5]} ; "
        f"visibles a tort : {sorted((ide - exposes) - invisibles)[:5]}"
    )
    assert invisibles, "aucune facade projetee dehors : CONN-8a est desarme"

    # `ide_launch` n'est pas une commande ENVOYEE a une IDE : c'est le lanceur.
    # Il doit rester visible, sinon le modele n'a plus de porte d'entree et ne
    # peut jamais faire exister l'IDE qui exposerait les autres.
    assert "ide_launch" in registry.tools, "le lanceur IDE n'est plus atteignable"

    # Proteges malgre l'invisibilite : c'est tout l'interet du snapshot.
    for nom in sorted(ide):
        assert registry.unregister_dynamic_handler(nom) is False
