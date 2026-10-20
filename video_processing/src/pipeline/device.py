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
    """
    bf16 on CUDA where the card supports it, else fp16; fp32 everywhere else, since half precision
    on CPU is slower, not faster. The Qwen3-VL weights ship in bf16, and fp16 has a smaller
    range, so activations that fit in bf16 can overflow to inf in fp16.
    """
    import torch
    if device != "cuda":
        return torch.float32
    return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16


def describe(device: str) -> str:
    import torch

    if device == "cuda":
        name = torch.cuda.get_device_name(0)
        total = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
        return f"{device} ({name}, {total:.0f} GB)"
    return device


def log_device(stage: str, device: str):
    custom_logger.get_logger(stage).info(f"Running on {describe(device)}")
