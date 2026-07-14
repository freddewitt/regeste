"""Piece translation — builds the prompt (source text + glossary + validated
named entities), calls a `TranslationProvider`, and writes the result into
`Piece.translations`. The only other writer of the pivot besides `review/`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from regeste.pivot import Piece, Translation, hash_transcription

from .guards import check_guards
from .provider import TranslationProvider


class TranslationBlocked(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULT_TRANSLATION_PROMPT = """\
Tu es un traducteur spécialisé dans la correspondance et les documents administratifs/judiciaires français du début du XXe siècle.

Langue source : {langue_source}
Langue cible : {langue_cible}

Consignes :
1. Traduis fidèlement le texte ci-dessous de {langue_source} vers {langue_cible}, en conservant le registre, le ton et les tournures d'époque autant que la langue cible le permet.
2. Ne traduis jamais les entités suivantes (noms propres, lieux, institutions) — reproduis-les telles quelles : {entites_a_preserver}
3. Utilise ce glossaire de corpus pour les termes récurrents : {glossaire}
4. Si un terme est ambigu, intraduisible, ou spécifique au contexte d'époque, conserve le terme original entre crochets après ta proposition.
5. Ne résume pas, ne complète pas, ne corrige pas le texte source.

Texte à traduire :
{texte_source}

Réponds uniquement avec le texte traduit, sans commentaire ni mise en forme.
"""


def build_prompt(
    piece: Piece,
    target_language: str,
    *,
    glossary: dict[str, str] | None = None,
    source_language: str = "",
    template: str | None = None,
) -> str:
    """Fill the (editable) translation prompt template with the piece's text,
    validated named entities and the corpus glossary.

    Only the known placeholders are substituted (targeted replace), so a user
    who removes {entites_a_preserver} or {glossaire} from the template simply
    disables that injection — nothing else breaks.
    """
    template = template if template is not None else DEFAULT_TRANSLATION_PROMPT
    validated_entities = [e.text for e in piece.entities if e.validation.status == "validated"]
    entities_str = ", ".join(validated_entities)
    glossary_str = "; ".join(f"{source} -> {target}" for source, target in (glossary or {}).items())
    return (
        template.replace("{langue_source}", source_language or "")
        .replace("{langue_cible}", target_language)
        .replace("{entites_a_preserver}", entities_str)
        .replace("{glossaire}", glossary_str)
        .replace("{texte_source}", piece.transcription)
    )


def translate_piece(
    piece: Piece,
    target_language: str,
    provider: TranslationProvider,
    model: str,
    *,
    glossary: dict[str, str] | None = None,
    source_language: str = "",
    template: str | None = None,
    enforce_guard: bool = True,
) -> Piece:
    """Translate `piece.transcription` into `target_language` and store the
    result in `piece.translations[target_language]`.

    Raises `TranslationBlocked` if the piece isn't fully validated
    (`review.global_status(piece) != "validated"`), unless `enforce_guard` is
    False — the headless CLI translates raw OCR without a review step.
    """
    if enforce_guard:
        guard = check_guards(piece)
        if not guard.allowed:
            raise TranslationBlocked(guard.blocked_reason or "Pièce non validée.")

    prompt = build_prompt(
        piece,
        target_language,
        glossary=glossary,
        source_language=source_language,
        template=template,
    )
    result = provider.translate(prompt, model=model)

    translations = dict(piece.translations or {})
    translations[target_language] = Translation(
        text=result.text,
        provider=provider.name,
        model=result.model,
        date=_now(),
        status="draft",
        source_hash=hash_transcription(piece.transcription),
    )
    piece.translations = translations
    return piece
