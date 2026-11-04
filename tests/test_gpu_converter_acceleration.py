from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import AcceleratorDevice, EasyOcrOptions
from src.pipeline import get_converter
import torch


def test_gpu_converter_acceleration_auto_detected():
    """Weryfikuje, czy get_converter automatycznie konfiguruje akcelerator CUDA przy dostępnym GPU."""
    converter = get_converter(do_ocr=True, langs=["pl", "en"])
    assert InputFormat.PDF in converter.format_to_options
    pdf_opts = converter.format_to_options[InputFormat.PDF].pipeline_options

    if torch.cuda.is_available():
        assert pdf_opts.accelerator_options is not None, "Opcje akceleratora powinny być skonfigurowane."
        assert pdf_opts.accelerator_options.device == AcceleratorDevice.CUDA, (
            "Dla środowiska z dostępnym CUDA (np. RTX 4080 SUPER) urządzeniem powinien być AcceleratorDevice.CUDA!"
        )
        assert pdf_opts.accelerator_options.num_threads >= 4
    else:
        # Fallback dla maszyn bez GPU
        assert pdf_opts.do_ocr is True

