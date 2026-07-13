"""Validacao local do CPF -- re-export de `app.core.cpf`.

Implementacao movida para `app/core/cpf.py` quando a integracao
OnSafety passou a precisar dela (integrations nao importa de modules).
Este modulo permanece para compatibilidade dos imports existentes.
"""
from __future__ import annotations

from app.core.cpf import format_cpf, is_valid_cpf, normalize_cpf

__all__ = ["format_cpf", "is_valid_cpf", "normalize_cpf"]
