# HC-AttackCorpus

Full train/val/frozen sets live on Google Drive (`MyDrive/LLM-C2/`) and are hashed in `manifest.json` once frozen (G4). Only small synthetic samples are committed.

Sources: CARES-18K + deepset/prompt-injections, group-split by `base_prompt` to avoid the CARES train/test leak.
