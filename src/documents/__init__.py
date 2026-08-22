"""Document Studio domain services.

The package is additive: legacy document handlers keep using ``DocumentHub``
until an explicit template selection opts into the studio pipeline.
"""

from .template_catalog import TemplateCatalog
from .template_models import TemplateManifest, TemplateRecord

__all__ = ["TemplateCatalog", "TemplateManifest", "TemplateRecord"]

