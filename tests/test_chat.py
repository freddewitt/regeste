"""Chat (RAG): retrieval, prompt building, config persistence. Providers are mocked."""

from pathlib import Path

from regeste.chat import ChatEngine, CorpusIndex, DEFAULT_CHAT_PROMPT
from regeste.core.project import ProjectConfig, ProviderConfig
from regeste.pivot import Piece
from regeste.translation.provider import TranslationProvider, TranslationResult


class FakeProvider(TranslationProvider):
    name = "fake"
    requires_api_key = False

    def __init__(self):
        self.prompts = []

    def translate(self, prompt, *, model):
        self.prompts.append(prompt)
        return TranslationResult(text="Réponse [1]", tokens_in=10, tokens_out=3, model=model)


def _pieces():
    return [
        Piece(id="a", call_number="AD-1", date="1789-07-14", sender="Marie Dupont",
              recipient="Jean Lefèvre", transcription="Cher Jean, les marchands de Rouen ont vendu le blé."),
        Piece(id="b", call_number="AD-2", date="1790-01-02", sender="Paul Martin",
              recipient="Marie Dupont", transcription="Le vin de Bourgogne arrive demain par bateau."),
        Piece(id="c", call_number="AD-3", transcription=""),  # nothing to search
    ]


def test_search_ignores_accents_case_and_plurals():
    index = CorpusIndex(_pieces())
    assert [c.piece.id for c in index.search("MARCHAND rouen")][:1] == ["a"]
    assert [c.piece.id for c in index.search("Lefevre")][:1] == ["a"]
    assert index.search("introuvable") == []


def test_empty_pieces_are_not_indexed():
    assert {p.id for p in CorpusIndex(_pieces()).pieces} == {"a", "b"}


def test_long_transcription_is_split_into_chunks():
    long = Piece(id="l", transcription="mot " * 2000)
    assert len(CorpusIndex([long]).chunks) > 1


def test_engine_builds_prompt_with_sources_catalogue_and_history():
    provider = FakeProvider()
    engine = ChatEngine(_pieces(), provider, "m", top_k=2)
    answer = engine.ask("vin de Bourgogne", [("user", "avant"), ("assistant", "réponse avant")])
    prompt = provider.prompts[0]
    assert DEFAULT_CHAT_PROMPT[:30] in prompt
    assert "CATALOGUE (2 documents)" in prompt and "AD-1" in prompt
    assert "[1]\nCote : AD-2" in prompt
    assert "Utilisateur : avant" in prompt and prompt.rstrip().endswith("QUESTION : vin de Bourgogne")
    assert answer.text == "Réponse [1]" and answer.sources[0].piece.id == "b"
    assert (answer.tokens_in, answer.tokens_out) == (10, 3)


def test_short_follow_up_reuses_previous_question_for_search():
    engine = ChatEngine(_pieces(), FakeProvider(), "m", top_k=1)
    _, sources = engine.build_prompt("et qui d'autre ?", [("user", "marchands de Rouen")])
    assert sources[0].piece.id == "a"


def test_custom_instruction_replaces_default():
    provider = FakeProvider()
    ChatEngine(_pieces(), provider, "m", instruction="CONSIGNE PERSO").ask("blé")
    assert "CONSIGNE PERSO" in provider.prompts[0] and DEFAULT_CHAT_PROMPT[:30] not in provider.prompts[0]


def test_no_match_still_asks_model_with_explicit_notice():
    provider = FakeProvider()
    answer = ChatEngine(_pieces(), provider, "m").ask("zzzz")
    assert answer.sources == [] and "aucun extrait" in provider.prompts[0]


def test_chat_settings_roundtrip_in_project_meta(tmp_path: Path):
    config = ProjectConfig(
        project_name="p", source_dir=tmp_path, output_dir=tmp_path,
        provider=ProviderConfig(kind="claude", model="x"),
        chat_provider=ProviderConfig(kind="ollama", model="llama3", base_url="http://localhost:11434/v1"),
        chat_same_as_ocr=False, chat_prompt="perso", chat_top_k=12,
    )
    restored = ProjectConfig.from_meta(config.to_meta())
    assert restored.chat_provider.kind == "ollama" and restored.chat_provider.model == "llama3"
    assert (restored.chat_same_as_ocr, restored.chat_prompt, restored.chat_top_k) == (False, "perso", 12)


def test_old_project_without_chat_keys_loads_with_defaults(tmp_path: Path):
    meta = ProjectConfig(
        project_name="p", source_dir=tmp_path, output_dir=tmp_path,
        provider=ProviderConfig(kind="claude", model="x"),
    ).to_meta()
    for key in ("chat_provider", "chat_same_as_ocr", "chat_prompt", "chat_top_k"):
        meta.pop(key)
    restored = ProjectConfig.from_meta(meta)
    assert restored.chat_provider is None and restored.chat_same_as_ocr and restored.chat_top_k == 8
