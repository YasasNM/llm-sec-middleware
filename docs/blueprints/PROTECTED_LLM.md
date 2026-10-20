# Protected LLM: Deployed Service and How Components Use It

**Status:** deployed on the onsite Ubuntu server and healthy. Containers:

| Container | Status | Ports |
|---|---|---|
| `protected-llm-ollama-1` | healthy | `127.0.0.1:11434` |
| `protected-llm-hospital-db-1` | healthy | `127.0.0.1:5432` |
| `protected-llm-llm-api-1` | healthy | `127.0.0.1:8010` |
| `protected-llm-chat-ui-1` | up | `0.0.0.0:8080` |

**Model:** `OLLAMA_MODEL=qwen2.5:7b`, `LLM_MODE=auto` (database tools, context fallback).
**Source in the repository:** `llm/protected-llm/` (same files as on the server). **Docs:** `llm/protected-llm/README.md` (deploy, commands, troubleshooting) and `llm/protected-llm/docs/API.md` (endpoints, Python example).

---

## 1. What It Is in the Research

The protected LLM is the system being protected. It is a hospital assistant that answers from a synthetic patient database through 7 parameterised, read-only tools. It is **not** one of the four components and it contains no security logic of the middleware. Its own safeguards (read-only role, no identifier columns, id validation) are part of the setup, not part of any component's evaluation.

| Fact | Value |
|---|---|
| Data | 24 doctors, 200 patients (`P0001` to `P0200`), 825 appointments, 341 diagnoses, 676 notes, all synthetic |
| What the model can read | `v_*` views only: ids, age, sex, blood group, diagnoses, appointments, notes, doctor specialty. Never name, NIC, phone, email, MRN or DOB |
| Tools | `get_patient_summary`, `search_patients`, `get_appointments`, `get_diagnoses`, `get_clinical_notes`, `list_doctors`, `get_statistics` |
| Placeholders | Its system prompt already says text like `[PATIENT_NAME_1]` was masked upstream and cannot be looked up, so it asks for the patient id instead |
| Latency | About 10 to 90 s per answer on CPU with 7b. Outside the middleware budget (K15) |

---

## 2. API (unchanged)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | open |
| POST | `/v1/chat` | `{message, history, request_id}` → `{request_id, answer, mode, model, tool_calls, latency_ms}` |
| GET | `/v1/tools`, POST `/v1/tools/{name}` | development and test data building |

Every `/v1/*` call needs the `X-Service-Key` header. Set the client timeout to 300 s.

---

## 3. Access During Individual Development

The API listens on `127.0.0.1` only, so use an SSH tunnel from your laptop:

```bash
ssh -N -L 8010:localhost:8010 <user>@<llm-server>
curl -s localhost:8010/health
```

| Owner | Typical use |
|---|---|
| Linuka (C1) | Check that masked prompts still get correct answers (masking utility) |
| Nisith (C2) | See which PASSed attacks produce harmful answers (system ablation only) |
| Yasas (C3) | Build the answer pool for the compliance dataset from real `qwen2.5:7b` answers |
| Sandul (C4) | Not needed (C4 never sees text) |
| Gateway | The real `/v1/chat` target from Week 3 |

Rules:
- Synthetic data only.
- Never paste real patient details into it.
- Do not run load tests on it without telling the team, because one CPU model serves everyone.

---

## 4. Integration Changes (Week 14, on the server)

**Important:** ports published by Docker bypass `ufw`. A `ufw deny` rule does **not** protect a container port. Use one of the options below.

1. **Open the API to the middleware host only.**

   **Option A (simplest, recommended): keep `127.0.0.1:8010` and tunnel.** On the middleware host, run a persistent tunnel:

   ```bash
   sudo apt install autossh      # or install infra/tunnels/autossh-llm.service as a systemd unit
   autossh -M 0 -f -N -o ServerAliveInterval=30 -L 172.17.0.1:18010:localhost:8010 <user>@<llm-server>
   ```

   Binding to `172.17.0.1` (Docker's host address) means only containers on the middleware host can use the tunnel, not the LAN. The gateway then uses `GW_LLM_API_URL=http://host.docker.internal:18010`, with `extra_hosts: ["host.docker.internal:host-gateway"]` in its Compose service. Use a key-only SSH user with no shell if possible.

   **Option B: bind to the LAN and filter in Docker's own chain.**
   - Set `LLM_API_BIND=<server LAN IP>` in `.env`, then run `docker compose up -d`.
   - Run:

     ```bash
     sudo iptables -I DOCKER-USER -p tcp -m conntrack --ctorigdstport 8010 ! -s <middleware host IP> -j DROP
     sudo apt install iptables-persistent && sudo netfilter-persistent save
     ```

   - Test from a third machine: `curl -m 5 http://<server>:8010/health` must time out.

2. **Restrict the built-in chat UI (port 8080)** (IR10).
   - It talks to the LLM **without** the middleware, so clinicians must not use it.
   - Keep it for the team and as the `none` arm reference.
   - Set `UI_PORT=127.0.0.1:8080` in `.env` and run `docker compose up -d`. The mapping becomes `127.0.0.1:8080:80`.
   - The team opens it through `ssh -L 8080:localhost:8080 <user>@<llm-server>`.
   - From then on, run the smoke test as `UI=localhost:8080 bash scripts/smoke_test.sh`, because `UI_PORT` now holds an address.
3. **Keys.**
   - Rotate `SERVICE_KEY` before integration, since it was shared during development.
   - Give the gateway the new value as `GW_LLM_API_KEY`.
   - Changing `SERVICE_KEY` only needs `docker compose up -d`; the database is not affected.
4. **Freeze the model for evaluation.**
   - Record the exact model digest (`docker compose exec ollama ollama list`) in `evaluation/system/README.md` at G4.
   - Do not change `OLLAMA_MODEL` after the system test set is frozen, or the ablation is not comparable.

---

## 5. Health and Operations

```bash
docker compose ps
curl -s localhost:8010/health            # expect "status":"ok","model_pulled":true
docker compose logs -f llm-api
docker stats --no-stream                 # RAM use (decides if middleware can share this server)
```

If answers are slow for everyone, check `docker stats` for CPU saturation. The service is set to one parallel request (`OLLAMA_NUM_PARALLEL=1`). The gateway's 300 s timeout returns `OUTPUT_WITHHELD` rather than hanging.
