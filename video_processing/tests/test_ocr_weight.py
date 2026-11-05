"""The OCR weight the fusion gets depends on which tokens the query handed to OCR. No database."""

import pytest

import custom_logger
from config import Config
from retrieval import engine as engine_module
from retrieval import retrievers
from retrieval.engine import SearchEngine
from retrieval.query_parser import parse
from retrieval.retrievers import Scope


@pytest.fixture
def fused_weights(monkeypatch):
    """Runs _retrieve with OCR alone and returns the weights the fusion was called with."""
    captured = {}
    monkeypatch.setattr(retrievers, "ocr_lexical", lambda conn, tokens, scope, depth: [(1, 10)])

    def fuse(lists, weights, limit=None, only=None):
        captured.update(weights)
        return []

    monkeypatch.setattr(engine_module.fusion, "fuse", fuse)
    instance = SearchEngine.__new__(SearchEngine)  # no models, no database
    instance.conf, instance.pool, instance._retriever_threads = Config(), None, None
    instance.logger = custom_logger.get_logger("test")
    instance.visual_model, instance.text_model, instance.collection_models = {"model_id": 1}, None, {}

    def run(query, weights=None):
        captured.clear()
        instance._retrieve(None, parse(query), Scope(), weights or Config().rrf_weights(), 200, 48,
                           allowed={"ocr"})
        return captured

    return run


def test_a_lowercase_query_fuses_ocr_at_the_lowercase_weight(fused_weights):
    assert fused_weights("a man walks past a bakery")["ocr"] == Config.RRF_WEIGHT_OCR_LOWERCASE


def test_a_name_keeps_the_full_ocr_weight(fused_weights):
    assert fused_weights("a sign reading Boulangerie Dupont")["ocr"] == Config.RRF_WEIGHT_OCR


def test_a_request_can_tune_the_lowercase_weight(fused_weights):
    weights = {**Config().rrf_weights(), "ocr_lowercase": 0.5}
    assert fused_weights("a man walks past a bakery", weights)["ocr"] == 0.5
