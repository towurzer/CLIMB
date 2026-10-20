"""
Two visual model settings, large by default for both. With a bigger GPU model set, `embed` embeds
with both and a search uses the one for its hardware.

A wrong choice does not raise. A query embedded by one model and compared against another
model's vectors, or against a model that covers only part of the keyframes, just finds less, so
the choice is pinned here.
"""

import pytest

import main
from config import Config
from pipeline import embed
from retrieval.engine import choose_visual_model

SO400M = "google/siglip2-so400m-patch16-384"
LARGE = "google/siglip2-large-patch16-384"


def two_models():
    # Passed in rather than read from the environment: the local .env may pin either one.
    return Config(KIS_MODEL_NAME=SO400M, KIS_CPU_MODEL_NAME=LARGE)


@pytest.fixture
def config_with_two_models(monkeypatch):
    monkeypatch.setattr(embed, "Config", two_models)


# --- which models, on which device ---------------------------------------------------------------

def test_embed_uses_both_models_cpu_one_first():
    """Every machine can search with the CPU model, so it must not wait behind the second pass."""
    assert two_models().embed_models() == [LARGE, SO400M]


def test_the_same_model_twice_is_embedded_once():
    assert Config(KIS_MODEL_NAME=LARGE, KIS_CPU_MODEL_NAME=LARGE).embed_models() == [LARGE]


def test_a_cpu_searches_with_the_cpu_model():
    assert two_models().search_model("cpu") == LARGE


@pytest.mark.parametrize("dev", ["cuda", "mps"])
def test_a_gpu_searches_with_the_gpu_model(dev):
    assert two_models().search_model(dev) == SO400M


# --- falling back when the preferred model does not cover every keyframe ------------------------

def test_the_preferred_model_is_used_when_it_covers_every_keyframe():
    assert choose_visual_model(SO400M, LARGE, {SO400M: 10, LARGE: 10}, 10) == (SO400M, None)


def test_a_half_finished_pass_falls_back_to_the_complete_model():
    """A second pass stopped half way would hide every keyframe it has not reached."""
    model, warning = choose_visual_model(SO400M, LARGE, {SO400M: 4, LARGE: 10}, 10)
    assert model == LARGE
    assert "4 of 10" in warning


def test_a_model_without_vectors_falls_back_to_the_other():
    model, warning = choose_visual_model(LARGE, SO400M, {SO400M: 10}, 10)
    assert model == SO400M
    assert LARGE in warning


def test_with_neither_complete_the_one_with_more_vectors_is_used():
    model, warning = choose_visual_model(SO400M, LARGE, {SO400M: 3, LARGE: 7}, 10)
    assert model == LARGE
    assert warning


def test_an_empty_database_needs_no_warning():
    assert choose_visual_model(SO400M, LARGE, {}, 0) == (SO400M, None)


def test_one_model_without_vectors_has_nothing_to_fall_back_to():
    model, warning = choose_visual_model(LARGE, LARGE, {}, 10)
    assert model == LARGE
    assert warning


# --- the embed stage -----------------------------------------------------------------------------

@pytest.fixture
def embedded_with(monkeypatch):
    seen = []

    def fake(conn, model_name, *args):
        seen.append(model_name)
        return embed.EmbedResult(embedded=3, batches=1)

    monkeypatch.setattr(embed, "_embed_with", fake)
    return seen


def test_embed_runs_every_model_and_adds_up(config_with_two_models, embedded_with):
    result = embed.embed_pending(object())
    assert embedded_with == [LARGE, SO400M]
    assert (result.embedded, result.batches) == (6, 2)


def test_a_named_model_runs_alone(config_with_two_models, embedded_with):
    embed.embed_pending(object(), model_name=LARGE)
    assert embedded_with == [LARGE]


def test_the_model_flag_reaches_the_stage(monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "embed_pending", lambda conn, **kwargs: seen.update(kwargs))
    main.dispatch(main.build_parser().parse_args(["embed", "--model", LARGE]), conn=object())
    assert seen["model_name"] == LARGE


def test_run_embeds_with_every_model(monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "embed_pending", lambda conn, **kwargs: seen.update(kwargs))
    main.dispatch(main.build_parser().parse_args(["run", "--stages", "embed"]), conn=object())
    assert seen["model_name"] is None
