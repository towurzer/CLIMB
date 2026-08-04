"""Device and dtype selection for the GPU stages."""

import custom_logger


def pick_device(prefer: str | None = None) -> str:
    import torch

    if prefer:
        return prefer
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def pick_dtype(device: str):
    """fp16 on CUDA, fp32 everywhere else; half precision on CPU is slower, not faster."""
    import torch
    return torch.float16 if device == "cuda" else torch.float32


def describe(device: str) -> str:
    import torch

    if device == "cuda":
        name = torch.cuda.get_device_name(0)
        total = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
        return f"{device} ({name}, {total:.0f} GB)"
    return device


def log_device(stage: str, device: str):
    custom_logger.get_logger(stage).info(f"Running on {describe(device)}")
