# C2 models

Tokenizer files live in `classifier/`. The ELECTRA-small weights (`model.safetensors`, ~52 MB) stay off Git (K29).

Local laptop:

```bash
python scripts/link_checkpoint.py
```

That copies `Local_dev/llm-c2-tester/c2_classifier_final/model.safetensors` into this folder, or you can set `C2_CLASSIFIER_CHECKPOINT_PATH` to the Local_dev folder.
