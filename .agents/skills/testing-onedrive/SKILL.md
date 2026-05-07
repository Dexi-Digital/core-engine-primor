# Testing the Microsoft Graph (OneDrive/SharePoint) integration

Applies to any flow whose `EditaisStorage` is backed by `OneDriveStorage`
(today: Fiscal C, Licitacoes anexos D.4, OneDrive sync D.1 fase 2, ART D.6).

## Devin Secrets Needed

All org-scoped:
- `MS_GRAPH_TENANT_ID`
- `MS_GRAPH_CLIENT_ID`
- `MS_GRAPH_CLIENT_SECRET`
- `MS_GRAPH_DRIVE_ID`
- `ADMIN_EMAIL` / `ADMIN_PASSWORD` (or just use the seeded admin from `.env`)

If any are missing, request them via `secrets(action="request")` with the
3-option pattern (skip / temporary / permanent).

## Quick health check (10s)

Before investing time in a full e2e, validate the credentials:

```
STORAGE_BACKEND=onedrive python scripts/test_onedrive_credentials.py
```

Expected: 5/5 OK (auth, drive root, upload, download, delete).
If this fails, the rest will too — fix env first.

## Choose the upload entrypoint

The **Fiscal documentos** form is the cleanest UI -> API -> OneDrive path
because:
- It is a real `<form encType="multipart/form-data">` with a Server Action
  that does `fetch` directly to `/api/v1/fiscal/documentos` (no proxy, no
  transformation).
- Parser is deterministic from the XML bytes.
- Duplicate detection (`_find_duplicate`) gives a free regression assertion.

Licitacoes anexos work too but trigger via PNCP scrape — non-deterministic.

## HTTP-only test recipe (when GUI is unavailable)

The Server Action is a literal multipart passthrough — curl is equivalent
to the UI:

```bash
# 1. Login
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"...","password":"..."}' | jq -r .access_token)

# 2. Upload XML
RESP=$(curl -s -X POST http://localhost:8000/api/v1/fiscal/documentos \
  -H "Authorization: Bearer $TOKEN" \
  -F "arquivo=@nfe-sample.xml;type=application/xml")
XML_PATH=$(echo $RESP | jq -r .xml_path)

# 3. Verify in Graph
GRAPH_TOKEN=$(curl -s -X POST \
  "https://login.microsoftonline.com/$MS_GRAPH_TENANT_ID/oauth2/v2.0/token" \
  -d "client_id=$MS_GRAPH_CLIENT_ID" \
  -d "client_secret=$MS_GRAPH_CLIENT_SECRET" \
  -d "grant_type=client_credentials" \
  -d "scope=https://graph.microsoft.com/.default" | jq -r .access_token)

curl -s "https://graph.microsoft.com/v1.0/drives/$MS_GRAPH_DRIVE_ID/items/$XML_PATH" \
  -H "Authorization: Bearer $GRAPH_TOKEN"
```

## ADVERSARIAL assertions that catch silent fallbacks

The critical assertion that distinguishes "real OneDrive" from "silently
fell back to LocalStorage":

- **`xml_path` returned by the API must look like a Graph item id**
  (regex `^[A-Z0-9]{20,}!?[A-Z0-9]+$`, NOT starting with `/` or
  `MotorCentral/`). Example real value:
  `01YFIXT2QF3BURZNMWW5GZRHCXOX2B3WTS`.
  If it starts with `/tmp/...` or `MotorCentral/...`, the OneDrive backend
  was NOT used.
- **SHA-256 of bytes downloaded via Graph must equal SHA-256 of bytes
  uploaded.** Anything else means corruption or wrong file.
- **Listing `MotorCentral/<subdir>/` recursively before vs after must show
  exactly +1 file.**

Without these adversarial checks, status code 201 + parser fields populated
is NOT enough — those would still pass with LocalStorage.

## Bucket/filename conventions to expect

Fiscal `import_xml` (`apps/api/app/modules/fiscal/service.py`):
- bucket = `int(xml_hash[:8], 16) % 100_000` (used as `licitacao_id` arg to
  storage)
- filename = `f"{xml_hash[:16]}-{original_filename}"`
- Final path in SharePoint: `MotorCentral/fiscal/<bucket>/<hash16>-<file>.xml`

If you see this pattern, the storage path was built correctly.

## Duplicate-detection regression

Re-uploading the same XML must:
- Return 409 with `detail.message` containing "ja importado" and
  `detail.existing_id`
- NOT create a second file in SharePoint (listing unchanged)

This proves `_find_duplicate(...)` runs BEFORE `storage.save(...)`.

## Cleanup

The app's `DELETE /api/v1/fiscal/documentos/{id}` does NOT call
`storage.delete()` (known issue). For test cleanup, delete via Graph
directly:

```
DELETE https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{xml_path}
```

Expect 204; subsequent GET returns 404 with `error.code=itemNotFound`.

## Common pitfalls

- **Admin consent**: `Files.ReadWrite.All` + `Sites.ReadWrite.All` need
  Global/Application Administrator consent. The `sistemas@primorsolucoes.srv.br`
  account is NOT admin — needs another GA to click the consent URL.
- **MFA forced on first login**: tenant requires Microsoft Authenticator
  push. Setup is a one-time human-in-the-loop. After client_credentials
  flow is working, no MFA prompts ever again.
- **`.env` parsing**: if `RESEND_FROM_EMAIL` contains spaces/angle brackets,
  `source apps/api/.env` will fail. Export only what you need.
- **`CORS_ORIGINS`**: must be valid JSON for Pydantic settings
  (`'["http://localhost:3000"]'`).
- **Chrome may not launch**: the snapshot has DBus/crashpad issues that
  prevent Chrome from staying up. If GUI testing is needed, retry with
  `dbus-launch chrome ...` or pivot to HTTP-only since the Server Action
  is a passthrough anyway.

## Browser fallback (if you do need GUI)

If Chrome refuses to start, before pivoting try:
```bash
dbus-launch /opt/.devin/chrome/chrome/linux-*/chrome-linux64/chrome \
  --no-sandbox --disable-gpu --disable-dev-shm-usage \
  --user-data-dir=/home/ubuntu/.browser_data_dir \
  --remote-debugging-port=29229
```

If still dies, pivot — the underlying API behavior is identical.
