"""Read C1's YAML settings from the configs/ folder (lists and rules live there, not in code)."""

from pathlib import Path

import yaml

CONFIG_FOLDER = Path(__file__).resolve().parents[2] / "configs"


# Load one YAML file from configs/, for example load_config("reid_lists.yaml")
def load_config(file_name: str, folder: Path = CONFIG_FOLDER) -> dict:
    with open(folder / file_name, encoding="utf-8") as handle:
        return yaml.safe_load(handle)
