"""
The GynSurg evaluation's own arithmetic (evaluation/gynsurg.py), on made-up data.

README 1.7's table for LHE comes from it, so a wrong average precision or a fusion that differs from
the search's would put wrong numbers in the README without anything failing.
"""

import numpy as np
from PIL import Image

from evaluation import gynsurg
from retrieval import fusion


def test_a_perfect_ranking_has_average_precision_one():
    relevant = np.array([True, True, False, False])
    assert gynsurg.average_precision([0, 1, 2, 3], relevant) == 1.0


def test_average_precision_counts_the_ranks_of_the_hits():
    """Hits at ranks 2 and 4: (1/2 + 2/4) / 2."""
    relevant = np.array([False, True, False, True])
    assert gynsurg.average_precision([0, 1, 2, 3], relevant) == 0.5


def test_a_class_without_frames_scores_zero_rather_than_dividing_by_zero():
    assert gynsurg.average_precision([0, 1], np.array([False, False])) == 0.0


def test_fusion_orders_like_the_search():
    """The "both" columns stand for what a search returns, ties included (at weight 1 they are common)."""
    rng = np.random.default_rng(0)
    for weight in gynsurg.WEIGHTS:
        visual, domain = rng.permutation(100), rng.permutation(100)
        search = fusion.fuse({"visual": [(int(i), int(i)) for i in visual],
                              "peskavlp": [(int(i), int(i)) for i in domain]},
                             {"visual": 1.0, "peskavlp": weight})
        assert list(gynsurg.fuse([visual, domain], [1.0, weight], 100)) == [r.keyframe_id for r in search]


def test_a_mask_names_the_classes_it_holds():
    mask = np.zeros((4, 4), np.uint8)
    mask[0, 0], mask[3, 3] = 36, 255
    assert gynsurg.labels_of(mask, gynsurg.INSTRUMENTS) == ["grasper", "suturing instrument"]
    assert gynsurg.labels_of(np.zeros((2, 2), np.uint8), gynsurg.INSTRUMENTS) == []


def test_frames_are_found_with_their_masks_where_the_zips_unpack_them(tmp_path):
    for name, folder, video, value in (("Instrument", "insseg", "INSSEG_01", 146),
                                       ("Anatomy", "ganseg", "GANSEG_02", 85)):
        unpacked = tmp_path / f"GynSurg_{name}_Dataset" / f"GynSurg_{name}_Dataset"
        clip, masks = unpacked / folder / video / "0.mp4_", unpacked / f"{folder}_mask" / video / "0.mp4_"
        clip.mkdir(parents=True)
        masks.mkdir(parents=True)
        Image.new("RGB", (8, 6)).save(clip / "0_015300_08-30-00.png")
        mask = np.zeros((6, 8), np.uint8)
        mask[2, 2] = value
        Image.fromarray(mask).save(masks / "0_015300_08-30-00_mask.png")
    frames = gynsurg.labelled_frames(tmp_path, instrument_step=1)
    assert [(f["set"], f["labels"]) for f in frames] == [("instrument", ["bipolar forceps"]),
                                                         ("anatomy", ["uterus"])]
