"""
Batched captioning: padding side and text that Postgres refuses.

Both fail quietly or late. With right padding every shorter prompt in a batch of differently sized
keyframes generates an empty caption or junk, which only shows as missing captions. A NUL in one
caption fails the whole insert and ends the stage, hours into a run.
"""

import torch
import transformers
from pipeline import caption


class FakeInputs(dict):
    def to(self, dev):
        return self


class FakeTokenizer:
    padding_side = "right"


class FakeProcessor:
    def __init__(self, decoded=None):
        self.tokenizer = FakeTokenizer()
        self.decoded = decoded or []

    def apply_chat_template(self, messages, add_generation_prompt):
        return "prompt"

    def __call__(self, text, images, return_tensors, padding):
        return FakeInputs(input_ids=torch.zeros((len(text), 3), dtype=torch.long))

    def batch_decode(self, sequences, skip_special_tokens):
        return self.decoded


class FakeModel:
    def to(self, dev):
        return self

    def eval(self):
        return self

    def generate(self, input_ids, max_new_tokens, do_sample):
        return torch.zeros((input_ids.shape[0], 5), dtype=torch.long)


def test_the_processor_pads_on_the_left(monkeypatch):
    monkeypatch.setattr(transformers.AutoProcessor, "from_pretrained", lambda *a, **k: FakeProcessor())
    monkeypatch.setattr(transformers.AutoModelForImageTextToText, "from_pretrained",
                        lambda *a, **k: FakeModel())
    processor, _ = caption.load_model("cpu")
    assert processor.tokenizer.padding_side == "left"


def test_nul_characters_are_removed():
    processor = FakeProcessor(decoded=["a dog\x00 on a beach ", "\x00"])
    captions = caption.caption_images([None, None], processor, FakeModel(), "cpu")
    assert captions == ["a dog on a beach", ""]
