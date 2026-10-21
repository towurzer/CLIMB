"""
Text-to-video search on MSC's described MVK clips with SigLIP2 and a second model, when `--model` names
one or CLIMB_COLLECTION_MODELS gives MVK one: that model alone and both fused the way the search fuses
them (evaluation/search.py, SigLIP2 at weight 1, the other at CLIMB_W_DOMAIN), each difference to SigLIP2
alone with a paired bootstrap interval. README 1.7's table for MVK comes from it, with BioCLIP 2.5 as
MVK's model before it was taken out: `python main.py eval-msc --data <folder> [--model <name>]`.

MSC (Truong et al., ACM MM 2025) describes 92 of MVK's own videos, for non-commercial research only, to be
cited. The form at https://msc.hkustvgd.com gives a login for https://hkust-vgd.ust.hk/MSC/; this reads
`videos_480/` and the clip descriptions from a folder holding that download as it is laid out there.

Two kinds of query, the way a VBS text task describes its target:
- a video's human-written summary (81 of the 92 have one), to find a frame of that video;
- a segment's captions, by Gemini, GPT and Qwen (segments are cut at scene changes, median 4 s), to find
  a frame of that segment.
A query is found on the first page when a frame of its target is among the first PAGE of what the search
returns. And the kind a biology model is built for: a species name, two short phrasings each, ranking
the frames of the clips with a summary, where a frame is relevant when its clip's summary names the
species. That is scored like GynSurg, average precision over every frame and precision on the first
page.

Frames are taken every STEP seconds through each segment, at least one per segment, and scaled to
480x270, the size of the VBS copies: MSC's 720x480 clips hold the same 16:9 picture, squeezed. Vectors
are cached in CLIMB_WORK_DIR/msc-eval with the frames and texts they belong to, so a second run with the
same ones only re-ranks.
"""

import csv
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import custom_logger
from config import Config
from evaluation import search
from pipeline import visual_models

DESCRIPTIONS = Path("information/annotations/clip_desciptions/MVK2.0_metadata.csv")  # sic
CAPTIONERS = ("Gemini", "GPT", "Qwen")
SIGLIP = "google/siglip2-large-patch16-384"
SIZE = (480, 270)  # the VBS copies of MVK
STEP = 4.0         # seconds between frames in a segment
PAGE = 48          # one page of results
WEIGHTS = (0.25, 0.5, 1.0, 2.0)

# The species at least 3 summaries name, as patterns over a summary. Labels come from the summaries
# because the captions rarely name a species: wrasse is in 8 summaries and 3 of the 449 segments. The
# stingray is left out: 2 clips, and a third summary names a ray to say there is none.
SPECIES = {
    "sponge": r"sponges?",
    "damselfish": r"damsel ?fish(es)?|chromis|sergeant majors?",
    "wrasse": r"wrasses?",
    "shark": r"sharks?",
    "snapper": r"snappers?",
    "soft coral": r"soft corals?",
    "seagrass": r"sea ?grass(es)?",
    "parrotfish": r"parrot ?fish(es)?",
    "grouper": r"groupers?",
    "sea fan": r"sea fans?|gorgonians?",
    "butterflyfish": r"butterfly ?fish(es)?",
    "surgeonfish": r"(surgeon|unicorn) ?fish(es)?|tangs?",
    "angelfish": r"angel ?fish(es)?",
    "crab": r"crabs?",
    "triggerfish": r"trigger ?fish(es)?",
}
SPECIES_QUERIES = {
    "sponge": ["a sea sponge", "sponges growing on a reef"],
    "damselfish": ["a damselfish", "damselfish swimming"],
    "wrasse": ["a wrasse", "a wrasse swimming"],
    "shark": ["a shark", "a shark swimming"],
    "snapper": ["a snapper", "snapper fish swimming"],
    "soft coral": ["soft coral", "soft corals on a reef"],
    "seagrass": ["seagrass", "seagrass on the sea floor"],
    "parrotfish": ["a parrotfish", "a parrotfish swimming"],
    "grouper": ["a grouper", "a grouper swimming"],
    "sea fan": ["a sea fan", "a gorgonian sea fan"],
    "butterflyfish": ["a butterflyfish", "butterflyfish swimming"],
    "surgeonfish": ["a surgeonfish", "surgeonfish swimming"],
    "angelfish": ["an angelfish", "an angelfish swimming"],
    "crab": ["a crab", "a crab on the sea floor"],
    "triggerfish": ["a triggerfish", "a triggerfish swimming"],
}


def read_descriptions(data: Path) -> tuple:
    """({video: summary}, [{video, start, end, captions: {captioner: text}}]) from MSC's table."""
    with open(data / DESCRIPTIONS, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    summaries, segments = {}, []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        video = row[0].strip()
        if len(row) > 1 and row[1].strip():
            summaries[video] = row[1].strip()
        # After Video and Summary: Start, End (seconds) and one caption per captioner, per segment.
        for i in range(2, len(row) - 1, 5):
            if not row[i].strip():
                continue
            captions = {name: text.strip() for name, text in zip(CAPTIONERS, row[i + 2:i + 5])
                        if text.strip()}
            segments.append({"video": video, "start": float(row[i]), "end": float(row[i + 1]),
                             "captions": captions})
    return summaries, segments


def frame_times(segments, step=STEP) -> list:
    """[{video, segment, time}]: every `step` seconds through each segment, centred, at least one."""
    frames = []
    for index, segment in enumerate(segments):
        length = max(0.0, segment["end"] - segment["start"])
        count = max(1, round(length / step))
        frames += [{"video": segment["video"], "segment": index,
                    "time": round(segment["start"] + (k + 0.5) * length / count, 3)}
                   for k in range(count)]
    return frames


def queries(summaries, segments) -> list:
    """[{set, text, video, segment}]: a summary looks for its video (segment None), a caption its segment."""
    found = [{"set": "summary", "text": text, "video": video, "segment": None}
             for video, text in sorted(summaries.items())]
    for index, segment in enumerate(segments):
        found += [{"set": captioner, "text": text, "video": segment["video"], "segment": index}
                  for captioner, text in segment["captions"].items()]
    return found


def species_labels(summaries) -> dict:
    """{video: the SPECIES its summary names}."""
    patterns = {name: re.compile(rf"\b(?:{p})\b", re.I) for name, p in SPECIES.items()}
    return {video: {name for name, p in patterns.items() if p.search(text)}
            for video, text in summaries.items()}


def decoded(path: Path, time: float):
    """The frame at `time` s as a uint8 CHW tensor of SIZE; one from the last half second past the end."""
    import numpy as np
    import torch

    width, height = SIZE
    for seek in (["-ss", f"{time:.3f}"], ["-sseof", "-0.5"]):
        raw = subprocess.run(["ffmpeg", "-v", "error", *seek, "-i", str(path), "-frames:v", "1",
                              "-vf", f"scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "rgb24",
                              "pipe:1"], capture_output=True, check=True).stdout
        if len(raw) == width * height * 3:
            image = np.frombuffer(raw, np.uint8).reshape(height, width, 3).copy()
            return torch.from_numpy(image).permute(2, 0, 1)
    raise RuntimeError(f"no frame at {time} s in {path}")


def model_key(model_name) -> str:
    """A second model's name in the fusion and the tables: its own name, without the owner."""
    return model_name.rsplit("/", 1)[-1]


def _cached(cache: Path, model_name, kind=None) -> Path:
    """Cache files are named by model: two SigLIP2 models share the signal "visual"."""
    slug = model_name.replace("/", "--")
    return cache / (f"{slug}-{kind}.npy" if kind else f"{slug}.npy")


def interval(differences, rounds=10_000, seed=0) -> tuple:
    """(mean, low, high): the mean of paired differences and its 95% bootstrap interval."""
    import numpy as np

    d = np.asarray(differences, dtype=float)
    means = d[np.random.default_rng(seed).integers(0, len(d), (rounds, len(d)))].mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return float(d.mean()), float(low), float(high)


def _vectors(frames, model_name, videos: Path, cache: Path, dev):
    import numpy as np
    from tqdm import tqdm

    from pipeline import embed

    target = _cached(cache, model_name)
    if target.exists():
        return np.load(target)
    processor, vision = visual_models.load_image_side(model_name, dev)
    batches = []
    with ThreadPoolExecutor(4) as pool, \
            tqdm(total=len(frames), desc=f"Embedding with {model_name}", unit="frame") as progress:
        for i in range(0, len(frames), 16):
            batch = frames[i:i + 16]
            images = list(pool.map(lambda f: decoded(videos / f"{f['video']}.mp4", f["time"]), batch))
            batches.append(embed.embed_images(images, processor, vision, dev))
            progress.update(len(batch))
    vectors = np.concatenate(batches)
    np.save(target, vectors)
    return vectors


def _text_vectors(sets: dict, model_name, cache: Path, dev) -> dict:
    """{kind: vectors} for {kind: texts}, cached per kind; the model loads only for what is missing."""
    import numpy as np

    targets = {kind: _cached(cache, model_name, kind) for kind in sets}
    missing = [kind for kind, target in targets.items() if not target.exists()]
    if missing:
        encode = visual_models.load_text_side(model_name, dev)
        for kind in missing:
            texts = sets[kind]
            np.save(targets[kind],
                    np.concatenate([encode(texts[i:i + 32]) for i in range(0, len(texts), 32)]))
    return {kind: np.load(target) for kind, target in targets.items()}


def _keep(listing: Path, content, stale):
    """Writes `content` to `listing`; when it differs from what was there, deletes the `stale` caches."""
    if not listing.exists() or json.loads(listing.read_text()) != content:
        for path in stale:
            path.unlink(missing_ok=True)
        listing.write_text(json.dumps(content))


def first_hit(order, target):
    """The rank (from 1) of the first target frame in `order`, None when the search does not return one."""
    import numpy as np

    hits = np.flatnonzero(target[np.asarray(order, dtype=int)])
    return int(hits[0]) + 1 if len(hits) else None


def evaluate(frames, asked, image, text) -> dict:
    """
    {query set: {variant: (share found on the first PAGE, mean reciprocal rank)}}, with "captions" for
    the three captioners together. "delta" holds each variant's difference to SigLIP2 alone in both,
    as interval() gives it, paired by query.
    """
    import numpy as np

    depth = Config().RETRIEVER_DEPTH
    videos = np.array([f["video"] for f in frames])
    segments = np.array([f["segment"] for f in frames])
    similarity = {signal: image[signal] @ text[signal].T for signal in image}  # frames x queries
    ranks = {}
    for q, query in enumerate(asked):
        target = videos == query["video"] if query["segment"] is None else segments == query["segment"]
        found = search.variants({signal: s[:, q] for signal, s in similarity.items()}, WEIGHTS, depth)
        for name, order in found.items():
            ranks.setdefault(query["set"], {}).setdefault(name, []).append(first_hit(order, target))
    ranks["captions"] = {name: [r for s in CAPTIONERS if s in ranks for r in ranks[s][name]]
                         for name in ranks["summary"]}

    def per_query(found):
        return (np.array([r is not None and r <= PAGE for r in found], float),
                np.array([1 / r if r is not None else 0.0 for r in found]))

    rows = {}
    for s, by_name in ranks.items():
        values = {name: per_query(found) for name, found in by_name.items()}
        base = values["SigLIP2"]
        rows[s] = {"queries": len(base[0]),
                   **{name: (float(v[0].mean()), float(v[1].mean())) for name, v in values.items()},
                   "delta": {name: (interval(v[0] - base[0]), interval(v[1] - base[1]))
                             for name, v in values.items() if name != "SigLIP2"}}
    return rows


def species_delta(species) -> dict:
    """Each variant's difference to SigLIP2 alone in average precision and first page, paired by species."""
    names = [n for n in species[0] if n not in ("species", "clips", "frames", "SigLIP2")]
    return {n: (interval([r[n][0] - r["SigLIP2"][0] for r in species]),
                interval([r[n][1] - r["SigLIP2"][1] for r in species])) for n in names}


def evaluate_species(frames, summaries, image, text) -> list:
    """
    One row per species: {variant: (average precision, precision in the first PAGE)}, over the frames of
    the clips with a summary; `text` holds the vectors of SPECIES_QUERIES' phrasings, in order.
    """
    import numpy as np

    depth = Config().RETRIEVER_DEPTH
    labels = species_labels(summaries)
    pool = np.array([i for i, f in enumerate(frames) if f["video"] in summaries])
    phrasings = [p for ps in SPECIES_QUERIES.values() for p in ps]
    rows = []
    for name, queries_of in SPECIES_QUERIES.items():
        relevant = np.array([name in labels[frames[i]["video"]] for i in pool])
        scores = {}
        for query in queries_of:
            q = phrasings.index(query)
            similarities = {signal: image[signal][pool] @ text[signal][q] for signal in image}
            every = search.variants(similarities, WEIGHTS)
            searched = search.variants(similarities, WEIGHTS, depth)
            for variant in every:
                scores.setdefault(variant, []).append(
                    (search.average_precision(every[variant], relevant),
                     float(relevant[searched[variant][:PAGE]].mean())))
        rows.append({"species": name, "clips": sum(name in found for found in labels.values()),
                     "frames": int(relevant.sum()),
                     **{variant: (float(np.mean([s[0] for s in v])), float(np.mean([s[1] for s in v])))
                        for variant, v in scores.items()}})
    return rows


def _signed(value) -> str:
    mean, low, high = value
    mark = "*" if low > 0 or high < 0 else " "
    return f"{mean:+.3f} [{low:+.3f}, {high:+.3f}]{mark}"


def run(data, dev="cpu", model=None):
    """`model`: the second visual model to measure next to SigLIP2, by default MVK's own, if it has one."""
    logger = custom_logger.get_logger("eval")
    data = Path(data).resolve()
    videos = data / "videos_480"
    cache = Path(Config().WORK_DIR) / "msc-eval"
    cache.mkdir(parents=True, exist_ok=True)

    if not (data / DESCRIPTIONS).exists():
        raise SystemExit(f"no {DESCRIPTIONS} under {data}: download MSC there as it is laid out")
    summaries, segments = read_descriptions(data)
    frames, asked = frame_times(segments), queries(summaries, segments)
    missing = sorted({f["video"] for f in frames} - {p.stem for p in videos.glob("*.mp4")})
    if missing:
        raise SystemExit(f"{len(missing)} described clips are not in {videos}, e.g. {missing[0]}")

    other = model or Config().collection_models().get("MVK")
    models = {"visual": SIGLIP, **({model_key(other): other} if other else {})}
    texts = {"text": [q["text"] for q in asked],
             "species": [p for ps in SPECIES_QUERIES.values() for p in ps]}
    _keep(cache / "frames.json", frames,
          [p for p in cache.glob("*.npy") if not p.stem.endswith(("-text", "-species"))])
    _keep(cache / "queries.json", texts["text"], list(cache.glob("*-text.npy")))
    _keep(cache / "species.json", texts["species"], list(cache.glob("*-species.npy")))
    logger.info(f"{len(frames)} frames of {len(segments)} segments, {len(asked)} queries, "
                f"models {', '.join(models.values())}, vectors in {cache}")

    image = {key: _vectors(frames, m, videos, cache, dev) for key, m in models.items()}
    text = {key: _text_vectors(texts, m, cache, dev) for key, m in models.items()}
    rows = evaluate(frames, asked, image, {key: t["text"] for key, t in text.items()})
    species = evaluate_species(frames, summaries, image, {key: t["species"] for key, t in text.items()})

    labels = {"summary": "summary -> its video", **{c: f"{c} -> its segment" for c in CAPTIONERS},
              "captions": "all captions"}
    names = [n for n in rows["summary"] if n not in ("queries", "delta")]
    width = max(13, *(len(n) for n in names))
    print(f"\n{'queries':22s} {'n':>5s}  " + "  ".join(f"{n:>{width}s}" for n in names))
    for key, label in labels.items():
        if key in rows:
            print(f"{label:22s} {rows[key]['queries']:5d}  " + "  ".join(
                f"{f'{rows[key][n][0]:.2f} / {rows[key][n][1]:.2f}':>{width}s}" for n in names))
    print(f"\nfound among the first {PAGE} of what the search returns / mean reciprocal rank")

    print(f"\n{'species':16s} {'clips':>5s} {'frames':>6s}  " + "  ".join(f"{n:>{width}s}" for n in names))
    for row in species:
        print(f"{row['species']:16s} {row['clips']:5d} {row['frames']:6d}  " + "  ".join(
            f"{f'{row[n][0]:.3f} / {row[n][1]:.2f}':>{width}s}" for n in names))
    means = {n: [sum(r[n][i] for r in species) / len(species) for i in (0, 1)] for n in names}
    print(f"{'mean':16s} {'':5s} {'':6s}  " + "  ".join(
        f"{f'{means[n][0]:.3f} / {means[n][1]:.2f}':>{width}s}" for n in names))
    print(f"\naverage precision over the frames of the clips with a summary / precision in the first "
          f"{PAGE} of what the search returns")

    deltas = species_delta(species)
    if deltas:
        print("\ndifference to SigLIP2 alone with its 95% interval, paired bootstrap over queries or "
              "species (* where the interval leaves out 0)")
        for title, by_name in (("summary -> its video: first page, reciprocal rank",
                                rows["summary"]["delta"]),
                               ("all captions: first page, reciprocal rank", rows["captions"]["delta"]),
                               ("species: average precision, first 48", deltas)):
            print(title)
            for name, (first, second) in by_name.items():
                print(f"  {name:{width}s}  {_signed(first)}  {_signed(second)}")
    return rows, species
