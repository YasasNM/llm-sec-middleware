# C2 models

Tokenizer and config files live in `classifier/`. The ELECTRA-base weights (`model.safetensors`, ~418 MB) stay off Git (K29).

Current model: `c2-electra-base-v1` (`google/electra-base-discriminator`, trained at max_length 256). See `manifest.json` for hashes.

Get the weights from Google Drive `MyDrive/LLM-C2/model_comparison/google__electra-base-discriminator_final/` and place `model.safetensors` in `classifier/`, or set `C2_CLASSIFIER_CHECKPOINT_PATH` to a folder containing all four files.

Do not use `scripts/link_checkpoint.py` unless you pass an explicit source path (see script).
