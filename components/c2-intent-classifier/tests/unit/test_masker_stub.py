from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stubs"))
from masker_stub import mask  # noqa: E402


def test_masker_replaces_nic_and_keeps_patient_id() -> None:
    text, manifest = mask("Patient Nimal Perera NIC 199012345V review P0012")
    assert "[NIC_1]" in text
    assert "P0012" in text
    assert any(token.startswith("[") for token in manifest)
