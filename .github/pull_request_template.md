## What changed
<!-- One or two sentences. Which component or part? -->

## Checklist
- [ ] I only changed files in my own folder (or the owner is a reviewer)
- [ ] Tests pass locally (`pytest tests/unit tests/contract` in my component)
- [ ] Requests, responses and events still validate against `contracts/`
- [ ] No secrets, no `.env`, no model weights, no real patient data
- [ ] Logs and tests contain no prompt or answer text
- [ ] Documentation updated (`BLUEPRINT.md` or `README.md`) if behaviour changed
- [ ] If `contracts/` changed: label `contract-change`, `contracts/VERSION` bumped, all four owners tagged
