# Deploy on Modal

Public app: https://yjoy1572--datastraw-roster.us-east.modal.run

Dashboard: https://modal.com/apps/yjoy1572/main/deployed/datastraw-roster

This deployment creates a separate `datastraw-roster` app and `datastraw-roster-state-v2` storage volume. It does not change other apps in the account. Compute/storage use shares the account's Modal credits and billing.

## Deploy from PowerShell

Run from the project root:

```powershell
uv sync --locked --group deploy
```

If this machine has not been authenticated, use browser login once:

```powershell
uv run --locked --group deploy python -X utf8 -m modal token new
```

Sign in to the intended Modal account in the browser. Credentials remain in Modal's local configuration; never paste tokens into source files, chat or Git. The existing `yjoy1572` profile can be selected explicitly for deployment:

```powershell
cd frontend
npm ci --legacy-peer-deps
npm run build
cd ..
uv run --locked --group deploy python -X utf8 -m modal deploy --profile yjoy1572 --strategy recreate modal_app.py
```

Use the HTTPS URL printed by Modal. Deployment remains available after closing your terminal. `modal serve` creates a temporary development deployment and should not be used for the submission URL.

## Persistence and scaling

- FastAPI serves the built frontend and API from the same HTTPS origin, with secure cookies.
- There is at most one web container and one solver executor. It scales to zero after idle time; the first request after that may take a few seconds.
- The live SQLite/WAL files are local to the container. SQLite's backup API creates a consistent standalone snapshot, which is atomically replaced on the named Modal volume and committed before successful API responses are acknowledged.
- Read-only requests without a database change do not rewrite the snapshot. Background job results are persisted when observed, and graceful shutdown waits for active work before taking a final snapshot.
- Use `--strategy recreate` on redeployments so old and new containers do not write the shared snapshot concurrently.
- The image uploads only application source and the built frontend. Private documents, credentials and the local development database are excluded.

## Verify the public application

```powershell
uv run --locked python scripts/smoke_test.py https://YOUR-DEPLOYED-URL.modal.run
```

Also open the URL in a private browser, generate November 2026, approve leave and repair, publish a valid version, and check CSV export.

For an explicit restart/persistence check, when nobody is editing or recording:

```powershell
uv run --locked --group deploy python scripts/modal_persistence_check.py https://yjoy1572--datastraw-roster.us-east.modal.run yjoy1572
```

The script identifies the deployed roster app, verifies container ownership, and restarts only that app's web container. It checks the same workspace cookie, exact saved configuration and published roster after a cold start. Other applications are excluded by name and app-ID guards.

The app can be stopped independently from the Modal dashboard or with:

```powershell
uv run --locked --group deploy python -X utf8 -m modal app stop --profile yjoy1572 datastraw-roster
```

Stopping this app does not delete its volume. Do not delete the volume if you want to retain saved workspaces.

## Platform references

- [Modal web applications](https://modal.com/docs/guide/webhooks)
- [Persistent volumes and commit semantics](https://modal.com/docs/guide/volumes)
- [Web Function URLs](https://modal.com/docs/guide/webhook-urls)

## Verified deployment

On 5 November 2026: 26 backend tests, the public HTTP workflow, all 13 Chromium workflow scenarios, and the coverage/history/mobile checks passed. A targeted cold-start test restarted only this app's web container and confirmed that the same workspace cookie, exact saved configuration and published roster survived in a new container.
