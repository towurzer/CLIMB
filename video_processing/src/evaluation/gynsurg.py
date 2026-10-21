"""
Text-to-frame retrieval on GynSurg's labelled frames: SigLIP2 alone, PeskaVLP alone, and both fused
the way the search fuses them (evaluation/search.py, SigLIP2 at weight 1, PeskaVLP at CLIMB_W_DOMAIN).
This is what README 1.7's table for LHE comes from: `python main.py eval-gynsurg --data <folder>`.

GynSurg (Nasirihaghighi et al., ACM MM 2025) is gynaecological laparoscopy from the same group as LHE,
for research use only, to be cited. Its two segmentation sets go into one folder, unpacked:
  https://ftp.itec.aau.at/datasets/GynSurge/Semantic_Segmentation_Dataset/GynSurg_Instrument_Dataset.zip
  https://ftp.itec.aau.at/datasets/GynSurge/Semantic_Segmentation_Dataset/GynSurg_Anatomy_Dataset.zip

A frame is relevant to a class when its mask holds the class. Instrument queries rank the instrument
frames, anatomy queries the anatomy frames, two VBS-style phrasings per class, averaged. Average
precision is over every frame of the set, fused from the full rankings; the first page is what the
search shows, from each model's CLIMB_RETRIEVER_DEPTH best frames. Frames are
scaled to 270 lines, the height of the VBS copies of LHE, and go through the pipeline's own decoding and
preprocessing. Vectors are cached in CLIMB_WORK_DIR/gynsurg-eval with the list of frames they belong
to, so a second run with the same frames only re-ranks.
"""

import json
from pathlib import Path

import custom_logger
from config import Config
from evaluation import search
from pipeline import peskavlp, visual_models

# Mask value -> class. No label map ships with the data; these are the values whose frame counts match
# the paper's (Fig. 2) exactly, for the classes it says the released sets keep (Sec. 3.2): grasper
# 2864, scissors 476, irrigator 1107, bipolar forceps 630, sealer-divider 1210, hook 241, suturing
# instrument (needle, needle holder, suture carrier, knot pusher) 788; uterus 475, tube 146, ovary 389.
INSTRUMENTS = {36: "grasper", 73: "scissors", 109: "irrigator", 146: "bipolar forceps",
               182: "sealer-divider", 219: "hook", 255: "suturing instrument"}
ANATOMY = {85: "uterus", 170: "fallopian tube", 255: "ovary"}

QUERIES = {
    "grasper": ["a grasper holding tissue", "laparoscopic grasping forceps"],
    "scissors": ["laparoscopic scissors", "scissors cutting tissue"],
    "irrigator": ["a suction irrigation tube", "the suction irrigator rinsing the area"],
    "bipolar forceps": ["bipolar forceps", "bipolar forceps coagulating tissue"],
    "sealer-divider": ["a vessel sealing device", "a sealer-divider cutting and sealing tissue"],
    "hook": ["a monopolar hook", "an electrosurgical hook burning tissue"],
    "suturing instrument": ["a needle holder holding a curved needle",
                            "suturing with a needle and thread"],
    "uterus": ["the uterus", "a view of the uterus"],
    "fallopian tube": ["the fallopian tube", "a thin fallopian tube"],
    "ovary": ["an ovary", "the ovary"],
}

SIGLIP = "google/siglip2-large-patch16-384"
HEIGHT = 270     # the VBS copies of LHE are 480x270
PAGE = 48        # one page of results
WEIGHTS = (0.25, 0.5, 1.0, 2.0)
# Every 4th instrument frame: neighbours within a clip are near duplicates.
INSTRUMENT_STEP = 4


def labels_of(mask, classes) -> list:
    """The classes a mask (a 2-D array of label values) holds, sorted."""
    import numpy as np
    return sorted(classes[v] for v in np.unique(mask).tolist() if v in classes)


def labelled_frames(data: Path, instrument_step=INSTRUMENT_STEP) -> list:
    """[{path, set, labels}] for the instrument and anatomy frames under `data`."""
    import numpy as np
    from PIL import Image

    frames = []
    for kind, folder, classes, step in (("instrument", "insseg", INSTRUMENTS, instrument_step),
                                        ("anatomy", "ganseg", ANATOMY, 1)):
        roots = sorted(p for p in data.rglob(folder) if p.is_dir())
        if not roots:
            raise SystemExit(f"no '{folder}' folder under {data}: unpack both GynSurg sets there")
        root = roots[0]
        for image in sorted(root.glob("*/*/*.png"))[::step]:
            # insseg/V/clip/x.png -> insseg_mask/V/clip/x_mask.png
            mask = root.with_name(f"{folder}_mask") / image.relative_to(root)
            mask = mask.with_name(f"{mask.stem}_mask.png")
            frames.append({"path": str(image), "set": kind,
                           "labels": labels_of(np.array(Image.open(mask)), classes)})
    return frames


def _decoded(path):
    import numpy as np
    import torch
    from PIL import Image

    image = Image.open(path).convert("RGB")
    image = image.resize((round(image.width * HEIGHT / image.height), HEIGHT), Image.BILINEAR)
    return torch.from_numpy(np.array(image)).permute(2, 0, 1)


def _vectors(frames, model_name, cache: Path, dev):
    import numpy as np
    from pipeline import embed

    target = cache / f"{visual_models.signal_name(model_name)}.npy"
    if target.exists():
        return np.load(target)
    processor, vision = visual_models.load_image_side(model_name, dev)
    batches = []
    for i in range(0, len(frames), 16):
        images = [_decoded(f["path"]) for f in frames[i:i + 16]]
        batches.append(embed.embed_images(images, processor, vision, dev))
    vectors = np.concatenate(batches)
    np.save(target, vectors)
    return vectors


def evaluate(frames, vectors, encoders) -> list:
    """One row per class: {variant: (average precision, precision in the first PAGE)}."""
    import numpy as np

    depth = Config().RETRIEVER_DEPTH
    rows = []
    for cls, phrasings in QUERIES.items():
        anatomy = cls in ANATOMY.values()
        pool = np.array([i for i, f in enumerate(frames) if (f["set"] == "anatomy") == anatomy])
        relevant = np.array([cls in frames[i]["labels"] for i in pool])
        scores = {}
        for query in phrasings:
            similarities = {model: vectors[model][pool] @ encode([query])[0]
                            for model, encode in encoders.items()}
            every = search.variants(similarities, WEIGHTS)
            searched = search.variants(similarities, WEIGHTS, depth)
            for name in every:
                scores.setdefault(name, []).append(
                    (search.average_precision(every[name], relevant),
                     float(relevant[searched[name][:PAGE]].mean())))
        rows.append({"class": cls, "frames": len(pool), "relevant": int(relevant.sum()),
                     **{name: (float(np.mean([s[0] for s in v])), float(np.mean([s[1] for s in v])))
                        for name, v in scores.items()}})
    return rows


def run(data, dev="cpu"):
    logger = custom_logger.get_logger("eval")
    data = Path(data).resolve()
    cache = Path(Config().WORK_DIR) / "gynsurg-eval"
    cache.mkdir(parents=True, exist_ok=True)

    frames = labelled_frames(data)
    listing = cache / "frames.json"
    if not listing.exists() or json.loads(listing.read_text()) != frames:
        for stale in cache.glob("*.npy"):
            stale.unlink()
        listing.write_text(json.dumps(frames))
    logger.info(f"{len(frames)} frames, vectors in {cache}")

    vectors = {visual_models.signal_name(m): _vectors(frames, m, cache, dev)
               for m in (SIGLIP, peskavlp.NAME)}
    encoders = {visual_models.signal_name(m): visual_models.load_text_side(m, dev)
                for m in (SIGLIP, peskavlp.NAME)}
    rows = evaluate(frames, vectors, encoders)

    names = [n for n in rows[0] if n not in ("class", "frames", "relevant")]
    print(f"\n{'class':20s} {'relevant':>10s}  " + "  ".join(f"{n:>13s}" for n in names))
    for row in rows:
        print(f"{row['class']:20s} {row['relevant']:>4d}/{row['frames']:<5d}  "
              + "  ".join(f"{row[n][0]:.3f} / {row[n][1]:.2f}" for n in names))
    for label, keep in (("instruments", lambda c: c not in ANATOMY.values()),
                        ("anatomy", lambda c: c in ANATOMY.values()), ("all", lambda c: True)):
        chosen = [r for r in rows if keep(r["class"])]
        print(f"{'mean ' + label:20s} {'':>10s}  " + "  ".join(
            f"{sum(r[n][0] for r in chosen) / len(chosen):.3f} / "
            f"{sum(r[n][1] for r in chosen) / len(chosen):.2f}" for n in names))
    print(f"\naverage precision over the frames of the class's set / precision in the first {PAGE} "
          f"of what the search returns")
    return rows
