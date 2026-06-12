"""Normalização de nomes de times/seleções para casamento entre casas."""

from __future__ import annotations

import re

from unidecode import unidecode

from beto.matching.aliases import ALIAS_MAP

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")
# sufixos/ruído comuns em nomes de clubes; inofensivos para seleções
_NOISE_TOKENS = frozenset(
    {"fc", "ec", "sc", "ac", "afc", "cf", "cd", "se", "club", "clube", "futebol", "esporte"}
)


def normalize_team(name: str) -> str:
    """unidecode → minúsculas → sem pontuação → sem tokens de ruído → alias canônico."""
    n = _PUNCT.sub(" ", unidecode(name).lower())
    tokens = [t for t in _WS.split(n) if t and t not in _NOISE_TOKENS]
    canonical = " ".join(tokens)
    return ALIAS_MAP.get(canonical, canonical)
