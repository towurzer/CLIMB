"""
The GPU checks of `main.py check` (pipeline/preflight.py) and the CUDA 12 cuBLAS that the ASR stage
loads for faster-whisper (pipeline/device.py).

None of this runs on a laptop's CPU, so it is pinned here: a driver too old for torch's CUDA, or a
venv without CUDA 12's cuBLAS, would otherwise only show up on the GPU machine, hours into a run.
"""

import ctypes
import importlib.util
import shutil
import subprocess
import types
from pathlib import Path

import _ctypes
import pytest
import torch

from pipeline import asr, device, preflight


def test_cublas_12_is_loaded_from_pips_nvidia_package(tmp_path, monkeypatch):
    """Where torch for cu126 puts it, nvidia/cublas/lib, which the dynamic loader does not search."""
    lib = tmp_path / "nvidia" / "cublas" / "lib"
    lib.mkdir(parents=True)
    shutil.copy(_ctypes.__file__, lib / "libcublas.so.12")  # any shared object loads
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *rest: (
        types.SimpleNamespace(submodule_search_locations=[str(lib.parent)])
        if name == "nvidia.cublas" else real(name, *rest)))
    assert device.load_ctranslate2_cuda() is True


def test_without_cublas_12_it_says_so(monkeypatch):
    def unloadable(*args, **kwargs):
        raise OSError("not found")

    monkeypatch.setattr(ctypes, "CDLL", unloadable)
    assert device.load_ctranslate2_cuda() is False


def test_asr_on_cuda_without_cublas_12_stops_with_the_fix(monkeypatch):
    pytest.importorskip("faster_whisper")
    monkeypatch.setattr(device, "load_ctranslate2_cuda", lambda: False)
    with pytest.raises(RuntimeError, match="cu126"):
        asr.load_model("cuda")


def test_the_driver_is_read_from_nvidia_smi(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda tool: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(preflight, "_run", lambda command, timeout=60: subprocess.CompletedProcess(
        command, 0, stdout="NVIDIA GeForce RTX 4090, 560.35.03\n", stderr=""))
    assert preflight._nvidia_gpu() == ("NVIDIA GeForce RTX 4090", "560.35.03")


@pytest.mark.parametrize("cuda, advice", [("13.0", "cu126"), ("12.6", "driver")])
def test_a_gpu_torch_cannot_use_fails_the_check(monkeypatch, cuda, advice):
    """A driver older than torch's CUDA leaves torch without CUDA; every stage would crawl on CPU."""
    monkeypatch.setattr(torch.version, "cuda", cuda)
    monkeypatch.setattr(torch.backends.cudnn, "version", lambda: 91002)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(preflight, "_nvidia_gpu", lambda: ("NVIDIA RTX A5000", "550.54.14"))
    report = preflight.Report()
    preflight.check_gpu(report)
    [(level, area, message)] = report.findings
    assert level == preflight.FAIL and "550.54.14" in message and advice in message


def test_a_torch_that_does_not_import_fails_the_check_with_its_pins(monkeypatch):
    """paddlepaddle-gpu's older NCCL under torch 2.12: "undefined symbol: ncclGetLsaMultimem..."."""
    import builtins

    real_import = builtins.__import__

    def broken_torch(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("libtorch_cuda.so: undefined symbol: ncclGetLsaMultimemDevicePointer")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_torch)
    report = preflight.Report()
    preflight.check_gpu(report)
    [(level, area, message)] = report.findings
    assert level == preflight.FAIL and "undefined symbol" in message and "pip install nvidia-" in message


def test_a_cudnn_older_than_torchs_fails_the_check(monkeypatch):
    """torch refuses a runtime cuDNN older than the one it was built with, on its first convolution."""
    def incompatible():
        raise RuntimeError("cuDNN version incompatibility: PyTorch was compiled  against (9, 10, 2) "
                           "but found runtime version (9, 5, 1).\nmore")

    monkeypatch.setattr(torch.version, "cuda", "12.6")
    monkeypatch.setattr(torch.backends.cudnn, "version", incompatible)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(preflight, "_nvidia_gpu", lambda: None)
    report = preflight.Report()
    preflight.check_gpu(report)
    assert any(level == preflight.FAIL and "(9, 5, 1)" in message and "pip install" in message
               for level, _, message in report.findings)


def test_no_gpu_at_all_is_only_a_warning(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(preflight, "_nvidia_gpu", lambda: None)
    report = preflight.Report()
    preflight.check_gpu(report)
    assert [level for level, _, _ in report.findings] == [preflight.WARN]


def _one_cuda_card(monkeypatch, memory_gb=24, bf16=True):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 1)
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda i: "NVIDIA RTX A5000")
    monkeypatch.setattr(torch.cuda, "get_device_properties",
                        lambda i: types.SimpleNamespace(total_memory=memory_gb * 1024 ** 3))
    monkeypatch.setattr(torch.cuda, "is_bf16_supported", lambda: bf16)


def test_on_cuda_a_whisper_without_cublas_12_fails_the_check(monkeypatch):
    pytest.importorskip("ctranslate2")
    _one_cuda_card(monkeypatch)
    monkeypatch.setattr(torch.version, "cuda", "13.0")
    monkeypatch.setattr(device, "load_ctranslate2_cuda", lambda: False)
    report = preflight.Report()
    preflight.check_gpu(report)
    assert report.findings[0][0] == preflight.OK
    assert any(level == preflight.FAIL and message.startswith("asr:") and "cu126" in message
               for level, _, message in report.findings)


def test_a_small_or_fp16_only_card_is_warned_about(monkeypatch):
    """Qwen3-VL-4B's weights are ~9 GB in bf16; without bf16 the models run in fp16."""
    _one_cuda_card(monkeypatch, memory_gb=8, bf16=False)
    monkeypatch.setattr(preflight.Config, "CAPTION_MODEL", "Qwen/Qwen3-VL-4B-Instruct")
    monkeypatch.setattr(preflight, "_check_other_cuda_users", lambda report, torch: None)
    report = preflight.Report()
    preflight.check_gpu(report)
    warnings = [message for level, _, message in report.findings if level == preflight.WARN]
    assert any("CLIMB_CAPTION_BATCH" in m for m in warnings) and any("fp16" in m for m in warnings)
