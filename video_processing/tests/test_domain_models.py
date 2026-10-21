"""
The domain model next to SigLIP2: PeskaVLP for LHE (pipeline/visual_models.py, pipeline/peskavlp.py).

A preprocessing that differs from the model's own transform, or a checkpoint loaded into the wrong
layers, does not raise anything: the vectors just mean less. So both are pinned against the model's
own definition here.
"""

import numpy as np
import pytest
import torch
import torchvision.transforms as T
from PIL import Image

from pipeline import peskavlp, visual_models


def _frame(seed=0):
    """A 480x270 keyframe, the size of the VBS copies of MVK and LHE."""
    rng = np.random.default_rng(seed)
    smooth = rng.integers(0, 256, (27, 48, 3), np.uint8)
    return Image.fromarray(smooth).resize((480, 270), Image.BILINEAR)


def _as_decoded(image):
    return torch.from_numpy(np.array(image)).permute(2, 0, 1)


def test_peskavlp_sees_the_whole_frame_resized_to_224():
    """Not their centre crop of 640x360 (peskavlp.SIZE): the whole frame, squashed, normalised as theirs."""
    theirs = T.Compose([T.Resize((224, 224)), T.ToTensor(),
                        T.Normalize(mean=list(peskavlp.MEAN), std=list(peskavlp.STD))])
    image = _frame()
    pixels = visual_models.peskavlp_processor()(images=[_as_decoded(image)], device="cpu")["pixel_values"]
    assert tuple(pixels.shape) == (1, 3, 224, 224)
    # PIL and torch resample slightly differently; a channel swap or a missing normalisation would
    # be off by whole units, not hundredths.
    assert (pixels[0] - theirs(image)).abs().max() < 0.05


def test_each_model_has_its_family_and_signal():
    assert visual_models.family(peskavlp.NAME) == "peskavlp"
    assert visual_models.family("google/siglip2-large-patch16-384") == "hf"
    assert visual_models.signal_name(peskavlp.NAME) == "peskavlp"
    assert visual_models.signal_name("google/siglip2-large-patch16-384") == "visual"


# --- PeskaVLP against its own definition, where its weights are on this machine --------------

needs_weights = pytest.mark.skipif(not peskavlp.weights_path(download=False).exists(),
                                   reason="PeskaVLP weights not downloaded (495 MB)")


def _their_sentence_vectors(encoder, texts):
    """
    BertEncoder.forward with last_n_layers=4, aggregate 'sum', agg_tokens=True, transcribed from
    CAMMA-public/SurgVLP surgvlp/codes/models/backbones/text_backbones.py: word pieces summed into
    words up to [SEP], padded with zeros to the token length, averaged, the four layers summed.
    """
    idx_to_word = {v: k for k, v in encoder.tokenizer.get_vocab().items()}
    tokens = encoder.tokenizer(texts, padding="max_length", truncation=True,
                               max_length=peskavlp.TEXT_MAX_TOKENS, return_tensors="pt")
    with torch.no_grad():
        hidden = encoder.bert(**tokens, output_hidden_states=True).hidden_states
    embeddings = torch.stack(hidden[-4:]).permute(1, 0, 2, 3)
    _, layers, length, dim = embeddings.shape
    batch = []
    for embs, ids in zip(embeddings.permute(0, 2, 1, 3), tokens["input_ids"]):
        words, bank = [], []
        for emb, token_id in zip(embs, ids):
            word = idx_to_word[token_id.item()]
            if word == "[SEP]":
                words.append(torch.stack(bank).sum(0))
                words.append(emb)
                break
            if word.startswith("##"):
                bank.append(emb)
            elif bank:
                words.append(torch.stack(bank).sum(0))
                bank = [emb]
            else:
                bank.append(emb)
        words = torch.stack(words)
        batch.append(torch.cat([words, torch.zeros(length - len(words), layers, dim)]))
    return torch.stack(batch).permute(0, 2, 1, 3).mean(axis=2).sum(axis=1)


@needs_weights
def test_the_peskavlp_text_vectors_point_where_theirs_do():
    encoder = peskavlp.TextEncoder("cpu", torch.float32)  # loads strictly: a stray key raises
    texts = ["the surgeon coagulates the uterine artery with bipolar forceps",
             "needle passing through the vaginal cuff", "smoke", "suction and irrigation"]
    cosine = torch.nn.functional.cosine_similarity(encoder(texts),
                                                   _their_sentence_vectors(encoder, texts))
    assert (cosine > 0.9999).all()


@needs_weights
def test_the_peskavlp_image_encoder_loads_every_weight():
    encoder = peskavlp.ImageEncoder("cpu", torch.float32)  # load_state_dict is strict
    assert tuple(encoder(torch.zeros(1, 3, 224, 224)).shape) == (1, peskavlp.DIMS)
