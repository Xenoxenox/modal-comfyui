# Modal smoke-debug notes

## 2026-05-13 smoke-debug server/ui.py rerun

Branch: `debug-modal-smoke-env`

Command:

```powershell
uv run python -m modal serve --env smoke-debug server/ui.py
```

Environment setup:

- `smoke-debug` exists.
- `ComfyUI` secret exists with `HF_TOKEN`.
- `huggingface-secret` secret exists with `HF_TOKEN`.
- `civitai-api-key` secret exists with `CIVITAI_API_KEY`.

Observed output:

```text
Warning: F:\Modal-ComfyUI\modal-comfyui\workflow_api.json not found. API endpoint might not work without a workflow.
✓ Initialized. View run at
https://modal.com/apps/litardphobia/smoke-debug/ap-mQa4J5ovGvtsaaxw8LgvRx
```

After waiting 35 seconds, then another 90 seconds:

- No `Created objects` line.
- No `Created web function` line.
- No `Serving...` line.
- App state: `ephemeral`.
- Tasks: `0`.
- Containers: none.
- Local `modal serve` process remained running until manually stopped.

Cleanup:

- Stopped app `ap-mQa4J5ovGvtsaaxw8LgvRx`.
- Stopped local `modal serve` processes.
- Confirmed no active containers in `smoke-debug`.

Conclusion:

- Missing `smoke-debug` secrets are not the root cause.
- Minimal `modal_smoke_min.py` works in both `main` and `smoke-debug`; requesting its dev URL returns HTTP 200 and starts one container.
- Real `server/ui.py` still stalls after app initialization and before web function creation.
- Current evidence points toward `server/app.py` image build or image hydration, especially the build-time `image.run_function(download_all, ...)` path, rather than Modal token, environment, secrets, or basic web endpoint capability.
