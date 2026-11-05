"""Pakiet do ekstrakcji treści z PDF do Markdown i EPUB."""

import ctypes
from pathlib import Path

# Automatyczna rejestracja dynamicznych bibliotek CUDA / cuDNN / cuBLAS dla ONNX Runtime i PyTorch
try:
    import nvidia
    nvidia_dir = Path(nvidia.__file__).resolve().parent
    if nvidia_dir.is_dir():
        for so in sorted(nvidia_dir.glob("**/*.so*")):
            if so.is_file() and not so.is_symlink():
                try:
                    ctypes.CDLL(str(so), mode=ctypes.RTLD_GLOBAL)
                except Exception:
                    pass
except Exception:
    pass

