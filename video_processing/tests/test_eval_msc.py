"""
The MSC evaluation's reading of MSC's table and its own arithmetic (evaluation/msc.py), on made-up data.

README 1.7's table for MVK comes from it: a segment read from the wrong columns, a frame taken outside its
segment or a rank counted from 0 would put wrong numbers there without anything failing.
"""

import subprocess

import numpy as np
import pytest
import torch

from evaluation import msc

HEADER = ["Video", "Summary"] + ["Start", "End", "Gemini", "GPT", "Qwen"] * 3


def test_the_table_gives_summaries_and_segments_with_their_captions(tmp_path):
    rows = [HEADER,
            ["Reef_0001", "A turtle rests on the reef.", "0", "5", "a turtle", "", "a sea turtle",
             "5", "9", "a diver", "a scuba diver", "a diver swimming"] + [""] * 5,
            ["Reef_0002", "", "0", "4", "fish", "", ""] + [""] * 10]
    table = tmp_path / msc.DESCRIPTIONS
    table.parent.mkdir(parents=True)
    table.write_text("".join("\t".join(row) + "\n" for row in rows), encoding="utf-8")

    summaries, segments = msc.read_descriptions(tmp_path)
    assert summaries == {"Reef_0001": "A turtle rests on the reef."}
    assert segments == [
        {"video": "Reef_0001", "start": 0.0, "end": 5.0,
         "captions": {"Gemini": "a turtle", "Qwen": "a sea turtle"}},
        {"video": "Reef_0001", "start": 5.0, "end": 9.0,
         "captions": {"Gemini": "a diver", "GPT": "a scuba diver", "Qwen": "a diver swimming"}},
        {"video": "Reef_0002", "start": 0.0, "end": 4.0, "captions": {"Gemini": "fish"}}]


def test_frames_are_spread_through_each_segment_and_every_segment_gets_one():
    segments = [{"video": "a", "start": 0.0, "end": 8.0}, {"video": "a", "start": 8.0, "end": 9.0},
                {"video": "b", "start": 3.0, "end": 3.0}]
    frames = msc.frame_times(segments, step=4.0)
    assert [(f["segment"], f["time"]) for f in frames] == [(0, 2.0), (0, 6.0), (1, 8.5), (2, 3.0)]


def test_a_summary_looks_for_its_video_and_a_caption_for_its_segment():
    segments = [{"video": "a", "start": 0, "end": 4, "captions": {"Gemini": "x", "Qwen": "y"}}]
    asked = msc.queries({"a": "all of a"}, segments)
    assert [(q["set"], q["text"], q["video"], q["segment"]) for q in asked] == [
        ("summary", "all of a", "a", None), ("Gemini", "x", "a", 0), ("Qwen", "y", "a", 0)]


def test_a_summary_labels_its_clip_with_the_species_it_names():
    labels = msc.species_labels({
        "a": "A school of fish - some of them can be identified as parrot fish - over seagrass.",
        "b": "Soft corals and sponges; a crab hides. Craboid is not a word.",
        "c": "A diver swims over sand."})
    assert labels == {"a": {"parrotfish", "seagrass"}, "b": {"soft coral", "sponge", "crab"}, "c": set()}


def test_species_rank_only_the_frames_of_clips_with_a_summary():
    """Clip "b" has no summary, so nothing says whether it shows a shark: its frame is left out."""
    frames = [{"video": "a", "segment": 0}, {"video": "a", "segment": 1}, {"video": "b", "segment": 2}]
    vectors = np.ones((3, 2), np.float32)
    phrasings = [p for ps in msc.SPECIES_QUERIES.values() for p in ps]
    text = np.ones((len(phrasings), 2), np.float32)
    rows = msc.evaluate_species(frames, {"a": "A shark."}, {"visual": vectors, "peskavlp": vectors},
                                {"visual": text, "peskavlp": text})
    shark = next(r for r in rows if r["species"] == "shark")
    assert shark["clips"] == 1 and shark["frames"] == 2 and shark["SigLIP2"] == (1.0, 1.0)
    assert next(r for r in rows if r["species"] == "crab")["frames"] == 0


def test_the_first_hit_is_counted_from_one_and_a_miss_is_none():
    target = np.array([False, True, True, False])
    assert msc.first_hit([3, 2, 1], target) == 2
    assert msc.first_hit([0, 3], target) is None
    assert msc.first_hit([], target) is None


def test_each_query_set_scores_where_its_target_first_appears():
    frames = [{"video": "a", "segment": 0}, {"video": "a", "segment": 1},
              {"video": "b", "segment": 2}, {"video": "b", "segment": 3}]
    asked = [{"set": "summary", "video": "a", "segment": None},
             {"set": "Gemini", "video": "b", "segment": 2}]
    eye = np.eye(4, dtype=np.float32)
    # SigLIP2 points both queries at their targets. The other model points the summary at frame 2, of b,
    # so a's first frame comes second, and the caption at frame 3, so its segment's frame comes fourth.
    rows = msc.evaluate(frames, asked, {"visual": eye, "peskavlp": eye},
                        {"visual": eye[[0, 2]], "peskavlp": eye[[2, 3]]})
    assert rows["summary"]["SigLIP2"] == (1.0, 1.0) and rows["summary"]["PeskaVLP"] == (1.0, 0.5)
    assert rows["Gemini"]["SigLIP2"] == (1.0, 1.0) and rows["Gemini"]["PeskaVLP"] == (1.0, 0.25)
    assert rows["captions"] == rows["Gemini"]
    assert rows["summary"]["queries"] == 1


@pytest.fixture
def clip(tmp_path):
    """Two seconds of ffmpeg's moving test pattern at MSC's 720x480."""
    path = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=720x480:rate=30:duration=2", "-pix_fmt", "yuv420p", str(path)],
                   check=True)
    return path


def test_a_frame_comes_from_its_time_at_the_vbs_copies_size(clip):
    early, late = msc.decoded(clip, 0.2), msc.decoded(clip, 1.5)
    assert tuple(early.shape) == (3, 270, 480) and early.dtype == torch.uint8
    assert not torch.equal(early, late)


def test_a_time_past_the_end_still_gives_a_frame(clip):
    """A zero-length segment can sit at the very end of a clip, where seeking finds nothing to decode."""
    assert tuple(msc.decoded(clip, 2.5).shape) == (3, 270, 480)
