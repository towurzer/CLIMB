"""
Which transcript segments are Whisper hallucinations.

Every segment Whisper produced for the 61 local MVK clips was invented ("Thank you." over
regulator noise), and they matched queries. A flagged segment is neither embedded nor searched, so
a rule that is too eager silently deletes real speech, and one that is too lax lets "You" rank a
clip for "fish on a coral reef". Both directions are pinned here, on the shapes the probe found.
"""

import pytest

from config import Config
from pipeline import asr
from pipeline.asr import Segment, flag_hallucinations


@pytest.fixture(autouse=True)
def thresholds(monkeypatch):
    # Passed in rather than read from the environment, which may set any of them.
    monkeypatch.setattr(asr, "Config", lambda: Config(ASR_MAX_COMPRESSION_RATIO=2.4,
                                                     ASR_ISOLATION_MS=30000,
                                                     ASR_ISOLATED_MIN_AVG_LOGPROB=-0.8))


def seg(start_s, text, logprob=-0.2, ratio=1.5):
    return Segment(int(start_s * 1000), int(start_s * 1000) + 2000, text, 0.0, logprob, ratio)


def test_a_lone_thank_you_is_a_hallucination():
    """The MVK case: one clip, one segment, nobody talking."""
    assert flag_hallucinations([seg(3, "Thank you.", logprob=-0.78)]) == [True]


def test_a_thank_you_inside_a_conversation_is_kept():
    talk = [seg(0, "So what did they say?"), seg(4, "Thank you."), seg(8, "They said yes.")]
    assert flag_hallucinations(talk) == [False, False, False]


def test_phrases_are_matched_case_and_space_insensitive():
    assert flag_hallucinations([seg(0, "  THANK   you. ")]) == [True]


def test_a_confident_lone_phrase_is_still_a_hallucination():
    """'Obrigado.' and 'ご視聴ありがとうございました' came out at -0.34 and -0.32."""
    assert flag_hallucinations([seg(0, "Obrigado.", logprob=-0.34)]) == [True]


def test_phrases_next_to_each_other_do_not_make_each_other_speech():
    """Two 'Thank you.'s ten seconds apart are still nobody talking."""
    assert flag_hallucinations([seg(0, "Thank you."), seg(10, "Thank you.")]) == [True, True]


def test_speech_just_outside_the_window_leaves_a_phrase_isolated():
    assert flag_hallucinations([seg(0, "You"), seg(33, "Here we are now.")]) == [True, False]


def test_text_without_letters_or_digits_is_dropped_even_in_a_conversation():
    talk = [seg(0, "Where are we going?"), seg(4, "..."), seg(8, "To the market.")]
    assert flag_hallucinations(talk) == [False, True, False]


def test_a_repetition_loop_is_dropped():
    loop = "love won't leave me, " * 12
    assert flag_hallucinations([seg(0, loop, ratio=2.9), seg(4, "And then?")]) == [True, False]


def test_an_unsure_lone_segment_is_dropped():
    assert flag_hallucinations([seg(0, "Hmm, maybe there.", logprob=-1.05)]) == [True]


def test_a_sure_lone_segment_is_kept():
    """A single spoken line in a music video, like 'We make music' at -0.24."""
    assert flag_hallucinations([seg(0, "We make music", logprob=-0.24)]) == [False]


def test_unsure_speech_inside_a_conversation_is_kept():
    """Slovenian with a language confidence of 0.57 came out at -2.24, and it was speech."""
    talk = [seg(0, "Ma sem pred blokom.", logprob=-2.24), seg(4, "Zala, da ne vse.", logprob=-2.24)]
    assert flag_hallucinations(talk) == [False, False]


def test_segments_from_before_the_migration_are_judged_by_text_and_timing():
    old = [Segment(0, 2000, "Thank you."), Segment(40000, 42000, "Fine, see you then.")]
    assert flag_hallucinations(old) == [True, False]


def test_flags_come_back_in_the_order_given():
    talk = [seg(8, "They said yes."), seg(60, "You"), seg(0, "So what did they say?")]
    assert flag_hallucinations(talk) == [False, True, False]


def test_the_whisper_model_loads(monkeypatch):
    """Lost once in an edit; only a full ASR run noticed, since nothing else loads the model."""
    import sys
    import types

    built = {}

    class FakeWhisper:
        def __init__(self, name, device, compute_type):
            built.update(name=name, device=device, compute_type=compute_type)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeWhisper))
    asr.load_model("cpu")
    assert built == {"name": Config().ASR_MODEL, "device": "cpu", "compute_type": "int8"}
