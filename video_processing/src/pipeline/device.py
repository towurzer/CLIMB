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


def load_ctranslate2_cuda() -> bool:
    """
    Loads the CUDA 12 cuBLAS that faster-whisper's ctranslate2 opens by name (`libcublas.so.12`;
    every ctranslate2 4.x wheel is built for CUDA 12 only). pip's nvidia-cublas-cu12, which torch
    for cu126 brings, keeps it where the dynamic loader does not look, so it is loaded from there
    with RTLD_GLOBAL; ctranslate2's own dlopen then finds it already loaded. False when there is
    none, as with torch for CUDA 13, whose venv holds only libcublas.so.13.
    """
    import ctypes
    import importlib.util
    from pathlib import Path

    name = "libcublas.so.12"
    try:
        ctypes.CDLL(name, mode=ctypes.RTLD_GLOBAL)
        return True
    except OSError:
        pass
    spec = importlib.util.find_spec("nvidia.cublas")
    for folder in (spec.submodule_search_locations or []) if spec else []:
        try:
            ctypes.CDLL(str(Path(folder) / "lib" / name), mode=ctypes.RTLD_GLOBAL)
            return True
        except OSError:
            continue
    return False


def describe(device: str) -> str:
    import torch

    if device == "cuda":
        name = torch.cuda.get_device_name(0)
        total = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
        return f"{device} ({name}, {total:.0f} GB)"
    return device


def log_device(stage: str, device: str):
    custom_logger.get_logger(stage).info(f"Running on {describe(device)}")
