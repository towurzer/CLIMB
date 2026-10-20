"""
The visual models, by family: SigLIP2 through Hugging Face transformers, BioCLIP through open_clip,
PeskaVLP through pipeline/peskavlp.py.

Each loads two ways, for the two sides of retrieval. The image side is for the embed stage and is
shaped like a Hugging Face image processor and vision tower, so embed.embed_images treats all three
alike. The text side is for the search engine: a function from query strings to unit vectors.
"""

from types import SimpleNamespace

from pipeline import device, peskavlp

OPEN_CLIP_MODELS = {"imageomics/bioclip-2.5-vith14", "imageomics/bioclip-2"}

# The name a model's results go by in the fusion and on the result badges. SigLIP2 is "visual".
SIGNALS = {"imageomics/bioclip-2.5-vith14": "bioclip", "imageomics/bioclip-2": "bioclip",
           peskavlp.NAME: "peskavlp"}


def family(name: str) -> str:
    if name in OPEN_CLIP_MODELS:
        return "open_clip"
    if name == peskavlp.NAME:
        return "peskavlp"
    return "hf"


def signal_name(name: str) -> str:
    return SIGNALS.get(name, "visual")


class FramePreprocess:
    """
    Resizes, crops and normalises decoded keyframes (uint8, channels x height x width) on the
    model's device, the way the model's own transform does on a PIL image. Called like a Hugging
    Face image processor, which is how embed.embed_images calls it.
    """

    def __init__(self, resize, crop, mean, std, interpolation):
        self.resize, self.crop = resize, crop
        self.mean, self.std = list(mean), list(std)
        self.interpolation = interpolation

    def __call__(self, images, return_tensors="pt", device="cpu"):
        import torch
        from torchvision.transforms.v2 import functional as F
        from transformers import BatchFeature

        frames = []
        for image in images:
            frame = image.to(device, torch.float32)
            frame = F.resize(frame, self.resize, interpolation=self.interpolation, antialias=True)
            frames.append(F.center_crop(frame, self.crop).clamp(0, 255))
        pixels = F.normalize(torch.stack(frames) / 255, self.mean, self.std)
        return BatchFeature({"pixel_values": pixels})


class _Pooled:
    """A vision encoder called the way embed.embed_images calls a Hugging Face vision tower."""

    def __init__(self, encode):
        self.encode = encode

    def __call__(self, pixel_values):
        return SimpleNamespace(pooler_output=self.encode(pixel_values))


def load_image_side(name: str, dev: str):
    """(processor, vision) for the embed stage."""
    kind, dtype = family(name), device.pick_dtype(dev)
    if kind == "open_clip":
        import open_clip
        import torch
        from torchvision.transforms import InterpolationMode

        model = open_clip.create_model(f"hf-hub:{name}")
        cfg = model.visual.preprocess_cfg
        visual = model.visual.to(dev, dtype).eval()
        del model
        size = cfg["size"][0] if isinstance(cfg["size"], (tuple, list)) else cfg["size"]

        def encode(pixel_values):
            with torch.no_grad():
                return visual(pixel_values.to(dtype))

        # resize_mode 'shortest', open_clip's default: the short side to `size`, then the centre.
        processor = FramePreprocess(size, size, cfg["mean"], cfg["std"],
                                    InterpolationMode(cfg["interpolation"]))
        return processor, _Pooled(encode)
    if kind == "peskavlp":
        from torchvision.transforms import InterpolationMode

        processor = FramePreprocess(list(peskavlp.RESIZE), peskavlp.CROP, peskavlp.MEAN,
                                    peskavlp.STD, InterpolationMode.BILINEAR)
        return processor, _Pooled(peskavlp.ImageEncoder(dev, dtype))

    from transformers import AutoModel, AutoProcessor

    # The whole checkpoint, then its vision tower. Loading Siglip2VisionModel directly would skip
    # the text weights, but this checkpoint's config type is siglip_vision_model, so transformers
    # reports mismatched patch and position embeddings and offers to reinitialise them at random.
    # It raises today; relying on that is one ignore_mismatched_sizes default away from embedding
    # millions of keyframes with random weights.
    processor = AutoProcessor.from_pretrained(name)
    model = AutoModel.from_pretrained(name, dtype=dtype)
    return processor, model.vision_model.to(dev).eval()


def load_text_side(name: str, dev: str):
    """A function from query strings to an (n, dims) float32 array of unit vectors."""
    import torch

    kind, dtype = family(name), device.pick_dtype(dev)
    if kind == "open_clip":
        import open_clip

        model = open_clip.create_model(f"hf-hub:{name}")
        model.visual = None  # 632M parameters a query never needs
        model = model.to(dev, dtype).eval()
        tokenizer = open_clip.get_tokenizer(f"hf-hub:{name}")

        def run(texts):
            with torch.no_grad():
                return model.encode_text(tokenizer(list(texts)).to(dev))
    elif kind == "peskavlp":
        run = peskavlp.TextEncoder(dev, dtype)
    else:
        from transformers import AutoModel, AutoProcessor

        processor = AutoProcessor.from_pretrained(name)
        full = AutoModel.from_pretrained(name, dtype=dtype)
        tower = full.text_model.to(dev).eval()
        # The projection head lives outside text_model on SigLIP, so keep whichever exists.
        head = getattr(full, "text_projection", None)
        head = head.to(dev) if head is not None else None
        del full

        def run(texts):
            inputs = processor(text=list(texts), return_tensors="pt", padding="max_length",
                               truncation=True).to(dev)
            # The processor hands the vision tower's keys over too; the text tower rejects them.
            inputs = {k: v for k, v in inputs.items() if k in ("input_ids", "attention_mask")}
            with torch.no_grad():
                output = tower(**inputs)
                # transformers 5.x returns an output object here, not a tensor.
                vectors = output.pooler_output if hasattr(output, "pooler_output") else output[1]
                return head(vectors) if head is not None else vectors

    def encode(texts):
        vectors = run(texts).float()
        return (vectors / vectors.norm(p=2, dim=-1, keepdim=True)).cpu().numpy()

    return encode
