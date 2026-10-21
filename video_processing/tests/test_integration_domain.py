"""
A collection's own visual model searching next to SigLIP2 (CLIMB_COLLECTION_MODELS).

Through the worker, against whatever the database holds: a model without vectors is skipped, not
faked, since the point is that the real model's ranking reaches the results.
"""

import pytest

from config import Config
from conftest import post_json
from pipeline import visual_models

pytestmark = pytest.mark.integration

QUERIES = {"MVK": "a fish swimming over a coral reef", "LHE": "bipolar forceps coagulating tissue"}


def _domain_models(db):
    found = []
    for collection, model in Config().collection_models().items():
        with db.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM keyframe_embedding e "
                        "JOIN embedding_model m USING (model_id) WHERE m.name = %s)", (model,))
            if cur.fetchone()[0]:
                found.append((collection, model))
        db.rollback()
    return found


@pytest.fixture
def domain_models(db, corpus):
    found = _domain_models(db)
    if not found:
        pytest.skip("no collection has vectors from its own model yet: python main.py embed")
    return found


def _search(worker, prompt, **kwargs):
    status, body = post_json(f"{worker}/api/search", {"prompt": prompt, "top_k": 48, **kwargs})
    assert status == 200, body
    return body


def test_the_collection_model_adds_its_signal(worker, domain_models):
    for collection, model in domain_models:
        signal = visual_models.signal_name(model)
        body = _search(worker, QUERIES.get(collection, "a person"), collection=collection)
        assert any(signal in r["signals"] for r in body["results"]), f"{collection}: no {signal}"
        assert any("visual" in r["signals"] for r in body["results"]), \
            f"{collection}: SigLIP2 should still search next to {signal}"


def test_an_all_datasets_search_leaves_it_out(worker, domain_models):
    for collection, model in domain_models:
        signal = visual_models.signal_name(model)
        body = _search(worker, QUERIES.get(collection, "a person"))
        assert not any(signal in r["signals"] for r in body["results"])


def test_its_own_tickbox_switches_it_off(worker, domain_models):
    for collection, model in domain_models:
        signal = visual_models.signal_name(model)
        body = _search(worker, QUERIES.get(collection, "a person"), collection=collection,
                       sources=["visual", "caption", "ocr", "asr"])
        assert not any(signal in r["signals"] for r in body["results"])
        assert any("visual" in r["signals"] for r in body["results"])


def test_unticking_visual_leaves_it_on(worker, domain_models):
    """SigLIP2 and the collection's model are two tickboxes now, so either can run alone."""
    for collection, model in domain_models:
        signal = visual_models.signal_name(model)
        body = _search(worker, QUERIES.get(collection, "a person"), collection=collection,
                       sources=[signal])
        assert body["results"] and all(set(r["signals"]) == {signal} for r in body["results"])
