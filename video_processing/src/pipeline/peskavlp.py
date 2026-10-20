"""
PeskaVLP (Yuan et al., NeurIPS 2024), a vision-language model trained on surgical video lectures,
used for LHE next to SigLIP2.

Rebuilt here from its released weights instead of installing CAMMA's surgvlp package, which pins
transformers 4.30.2, numpy < 2 and mmengine 0.7.0 and would take the rest of the pipeline down with
it. The architecture is small enough to state:

- image: torchvision's ResNet-50 without its classifier, then a linear layer 2048 -> 768
- text: Bio_ClinicalBERT, no projection. The sentence vector is the hidden states of the last four
  layers, summed, averaged over the words up to [SEP]. Their code first merges word pieces into
  words by summing them and then averages over the padded length, which only scales that sum, so
  the masked sum over the tokens below points the same way.

The weights are CC BY-NC-SA 4.0: non-commercial research use.
"""

import zipfile
from pathlib import Path

NAME = "camma/peskavlp"
DIMS = 768
WEIGHTS_URL = "https://seafile.unistra.fr/f/65a2b1bf113e428280d0/?dl=1"
TEXT_MODEL = "emilyalsentzer/Bio_ClinicalBERT"
TEXT_MAX_TOKENS = 77
TEXT_LAST_LAYERS = 4
# Their transform: Resize((360, 640)), CenterCrop(224), ImageNet normalisation.
RESIZE = (360, 640)
CROP = 224
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)


def weights_dir() -> Path:
    import torch
    return Path(torch.hub.get_dir()) / "climb" / "peskavlp"


def weights_path(download: bool = True) -> Path:
    """The checkpoint, downloaded and unpacked on first use (a 495 MB zip)."""
    path = weights_dir() / "PeskaVLP.pth"
    if path.exists() or not download:
        return path
    import torch
    archive = weights_dir().parent / "PeskaVLP.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        torch.hub.download_url_to_file(WEIGHTS_URL, str(archive))
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(weights_dir())
    return path


def _state(prefix: str) -> dict:
    import torch
    state = torch.load(weights_path(), map_location="cpu", weights_only=True)
    return {key[len(prefix):]: value for key, value in state.items() if key.startswith(prefix)}


class ImageEncoder:
    """`backbone_img` of the checkpoint."""

    def __init__(self, dev: str, dtype):
        import torch
        from torchvision.models import resnet50

        state = _state("backbone_img.")
        self.resnet = resnet50(weights=None)
        self.resnet.fc = torch.nn.Identity()
        self.resnet.load_state_dict({k[len("model."):]: v for k, v in state.items()
                                     if k.startswith("model.")})
        self.project = torch.nn.Linear(2048, DIMS)
        self.project.load_state_dict({k[len("global_embedder."):]: v for k, v in state.items()
                                      if k.startswith("global_embedder.")})
        self.resnet.to(dev, dtype).eval()
        self.project.to(dev, dtype).eval()
        self.dtype = dtype

    def __call__(self, pixel_values):
        import torch
        with torch.no_grad():
            return self.project(self.resnet(pixel_values.to(self.dtype)))


class TextEncoder:
    """`backbone_text` of the checkpoint."""

    def __init__(self, dev: str, dtype):
        from transformers import AutoConfig, AutoTokenizer, BertModel

        self.tokenizer = AutoTokenizer.from_pretrained(TEXT_MODEL)
        self.bert = BertModel(AutoConfig.from_pretrained(TEXT_MODEL))
        state = _state("backbone_text.model.")
        # Older transformers saved position_ids as a buffer; current ones rebuild it.
        state.pop("embeddings.position_ids", None)
        self.bert.load_state_dict(state)
        self.bert.to(dev, dtype).eval()
        self.dev = dev

    def __call__(self, texts):
        import torch
        tokens = self.tokenizer(list(texts), padding="max_length", truncation=True,
                                max_length=TEXT_MAX_TOKENS, return_tensors="pt").to(self.dev)
        with torch.no_grad():
            hidden = self.bert(**tokens, output_hidden_states=True).hidden_states
            layers = torch.stack(hidden[-TEXT_LAST_LAYERS:]).sum(dim=0)
            mask = tokens["attention_mask"].unsqueeze(-1).to(layers.dtype)
            return (layers * mask).sum(dim=1)
