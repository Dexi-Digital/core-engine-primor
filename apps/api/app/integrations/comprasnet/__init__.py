"""ComprasNet (legacy SIASG) edital fallback adapter."""
from app.integrations.comprasnet.client import (
    ComprasnetCaptchaRequired,
    ComprasnetClient,
    extract_edital_links,
)

__all__ = ["ComprasnetClient", "ComprasnetCaptchaRequired", "extract_edital_links"]
