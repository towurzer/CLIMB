"""
The embed loop: batches read ahead while the model works, paged through the pending keyframes.

Against the real database, because the paging is SQL (SELECT_PENDING's `keyframe_id > after`), but
with a stand-in for the model and generated images, so neither media files nor a model download
are needed. Vectors go in under so400m (model 3), which nothing else embeds by default, and are
deleted again.
"""

import numpy as np
import pytest
import torch
from PIL import Image

from config import Config
from pipeline import embed

pytestmark = pytest.mark.integration

MODEL = "google/siglip2-so400m-patch16-384"
DIMS = 1152


class Processor:
    def __call__(self, images, return_tensors, device):
        return {"pixel_values": torch.stack([image.float() for image in images])}


class Batch(dict):
    def to(self, dev):
        return self


class Instant(torch.nn.Module):
    def forward(self, pixel_values):
        return type("Out", (), {"pooler_output": torch.rand(pixel_values.shape[0], DIMS)})()


@pytest.fixture
def model_3(db, corpus, monkeypatch):
    with db.cursor() as cur:
        cur.execute("SELECT model_id FROM embedding_model WHERE name = %s", (MODEL,))
        if cur.fetchone() is None:
            pytest.skip(f"{MODEL} is not registered (migration 010)")
        cur.execute("DELETE FROM keyframe_embedding WHERE model_id = 3")
    db.commit()
    processor = Processor()
    monkeypatch.setattr(embed, "load_model", lambda name, dev: (
        lambda images, return_tensors, device: Batch(processor(images, return_tensors, device)),
        Instant()))
    yield
    with db.cursor() as cur:
        cur.execute("DELETE FROM keyframe_embedding WHERE model_id = 3")
    db.commit()


def _first_pending(db, n):
    with db.cursor() as cur:
        cur.execute("SELECT keyframe_id FROM keyframes ORDER BY keyframe_id LIMIT %s", (n,))
        ids = [r[0] for r in cur.fetchall()]
    db.rollback()
    return ids


def _embedded(db):
    with db.cursor() as cur:
        cur.execute("SELECT keyframe_id FROM keyframe_embedding WHERE model_id = 3 ORDER BY 1")
        ids = [r[0] for r in cur.fetchall()]
    db.rollback()
    return ids


@pytest.fixture
def images(tmp_path, monkeypatch, db):
    """Every keyframe's file is a small generated image, except those named in `missing`."""
    picture = tmp_path / "frame.webp"
    Image.fromarray(np.full((27, 48, 3), 128, np.uint8)).save(picture)
    missing = set()
    with db.cursor() as cur:
        cur.execute("SELECT s.video_id, s.shot_index, k.kf_index, k.keyframe_id "
                    "FROM keyframes k JOIN scenes s USING (scene_id)")
        id_of = {(v, s, k): kid for v, s, k, kid in cur.fetchall()}
    db.rollback()
    monkeypatch.setattr(embed.paths, "keyframe_path", lambda v, s, k: (
        tmp_path / "missing.webp" if id_of[(v, s, k)] in missing else picture))
    return missing


def test_missing_files_do_not_stall_the_stage(db, model_3, images):
    """
    A keyframe whose file is missing stays pending. Re-querying from the start returned those
    first every time, so a batch full of them used to keep the stage from reaching anything else.
    """
    first = _first_pending(db, 40)
    images.update(first[:16])
    result = embed.embed_pending(db, model_name=MODEL, batch_size=8, limit=40, prefer_device="cpu")
    assert (result.embedded, result.skipped) == (24, 16)
    assert _embedded(db) == first[16:40]


def test_the_limit_is_kept_with_batches_read_ahead(db, model_3, images, monkeypatch):
    # Passed in rather than patched on the class: Config is a dataclass, its instances keep the
    # defaults it was defined with.
    monkeypatch.setattr(embed, "Config", lambda: Config(EMBED_LOADERS=4))
    result = embed.embed_pending(db, model_name=MODEL, batch_size=8, limit=50, prefer_device="cpu")
    assert result.embedded == 50
    assert _embedded(db) == _first_pending(db, 50)


def test_a_second_run_continues_where_the_first_stopped(db, model_3, images):
    embed.embed_pending(db, model_name=MODEL, limit=20, prefer_device="cpu")
    embed.embed_pending(db, model_name=MODEL, limit=20, prefer_device="cpu")
    assert _embedded(db) == _first_pending(db, 40)


def test_a_decoded_keyframe_is_its_pixels_as_uint8(tmp_path):
    pixels = np.random.default_rng(0).integers(0, 256, (27, 48, 3), np.uint8)
    Image.fromarray(pixels).save(tmp_path / "frame.png")
    decoded = embed.decode_keyframe(tmp_path / "frame.png")
    assert decoded.dtype == torch.uint8 and tuple(decoded.shape) == (3, 27, 48)
    assert np.array_equal(decoded.permute(1, 2, 0).numpy(), pixels)
