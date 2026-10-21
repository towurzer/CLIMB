"""
The GynSurg evaluation's reading of GynSurg's masks (evaluation/gynsurg.py), on made-up data.

README 1.7's table for LHE comes from it, so a frame paired with another frame's mask or a mask value
read as the wrong class would put wrong numbers in the README without anything failing. Its ranking,
fusion and average precision are evaluation/search.py's (tests/test_eval_search.py).
"""

import numpy as np
from PIL import Image

from evaluation import gynsurg


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
