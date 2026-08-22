"""Génération de PDF via les outils natifs Lumena."""

# create_pdf est injecté par le runtime Lumena (voir guideline Pdf).
create_pdf = globals().get("create_pdf")


def generate_pdf(markdown_content, output_path, **options):
    """Génère un PDF à partir de contenu markdown.

    Utilise l'outil natif Lumena create_pdf (conformément aux guidelines Pdf).

    Args:
        markdown_content: texte markdown (titres, gras, listes, tableaux).
        output_path: chemin du fichier PDF de sortie.
        **options: options supplémentaires passées à create_pdf.

    Returns:
        Le chemin du PDF généré.
    """
    if not markdown_content or not markdown_content.strip():
        raise ValueError("Le contenu markdown ne peut pas être vide.")

    # Résolution DYNAMIQUE de l'outil natif : create_pdf est injecté par le
    # runtime Lumena dans les globals du module. Une capture au moment de
    # l'import (globals().get en haut de module) figerait None si l'injection
    # n'a pas encore eu lieu — d'où la résolution ici, au moment de l'appel.
    tool = globals().get("create_pdf")
    if tool is None:
        raise RuntimeError("create_pdf n'est pas disponible dans ce runtime.")

    # Appel à l'outil natif Lumena create_pdf.
    # L'API native attend ``markdown=`` et ``filename=`` (voir export.py).
    return tool(
        markdown=markdown_content,
        filename=output_path,
        **options,
    )
