# Demo Mode

A deployment can be provisioned as a **demo**: populated with the fictional ODK seed data, no Cognito, and a role-picker login so anyone can explore the dashboard as an admin or a school user. A banner on every page explains that the data is fictional, lists the school IDs to log in with, and offers a reset.

Demo mode is the same switch as local development's auth bypass. The existing `DEV_AUTH_BYPASS` flag is renamed `DEMO_MODE` everywhere; local dev runs in demo mode.

## Safety invariant

Demo mode must never be active on a deployment holding real data, because the role picker hands an admin token to anyone. This is enforced by:

1. **Provision-time only.** The mode is fixed when a deployment is provisioned and cannot change afterwards (see Deployment).
2. **Mutual exclusion with Cognito.** The API refuses to start with both `DEMO_MODE` and a Cognito user pool configured (existing check, renamed).
3. **Route absence.** Demo routes are only mounted when `DEMO_MODE` is on; otherwise they 404.

## Naming

| Old | New |
|---|---|
| `settings.DEV_AUTH_BYPASS` / `GLOW_DEV_AUTH_BYPASS` | `settings.DEMO_MODE` / `GLOW_DEMO_MODE` |
| `PUBLIC_DEV_AUTH_BYPASS` / `VITE_PUBLIC_DEV_AUTH_BYPASS` | `PUBLIC_DEMO_MODE` / `VITE_PUBLIC_DEMO_MODE` |
| runtime config `devAuthBypass` | `demoMode` |
| `routers/dev_auth.py`, `POST /auth/dev-login` | `routers/demo.py`, `POST /demo/login` |
| `devLogin()` (dashboard) | `demoLogin()` |

Applies to `compose.yml`, `compose.override.yml`, `compose.test.yml`, the dashboard `config.json` template, CI workflows, and docs. No backwards compatibility (beta).

## Deployment (`deploy/aws`)

- **Input.** CLI flag `--demo`; GUI new-deployment form checkbox "Demo deployment (fictional data, no login)". Sets `Config.demo_mode: bool = False`.
- **Terraform.** New `var.demo_mode` (bool, default `false`), added to `tfvars` in `core.py`. It is written to:
  - a `GlowDemoMode` tag in the common tags, so the GUI deployment list can show a "Demo" badge;
  - the runner userdata, which writes `GLOW_DEMO_MODE=true|false` into `/etc/glow-runner.env`.
- **Immutability.** `update()` never runs Terraform, so it cannot change the mode. Provisioning an existing domain whose Terraform state holds a different `demo_mode` fails with a `DeployError` before `apply`. Existing instances have no `GLOW_DEMO_MODE` line, which reads as `false`.
- **Cognito.** The pool is still created for demo deployments (avoids conditional resources/outputs); it is unused. In demo mode `activate-stack.sh` skips `sync_cognito_env` and writes the `GLOW_COGNITO_*` / `PUBLIC_COGNITO_*` values empty, so the mutual-exclusion check passes.
- **Compose overlay.** New `compose.demo.yml`, added by `activate-stack.sh` (`-f compose.yml -f compose.demo.yml`) when demo mode is on:
  - `postgres14` built from the `dev` target (pre-seeded fictional ODK data);
  - a `glow-init` service (extending `compose.glow-init.yml`) running `glow-api demo reset`;
  - `GLOW_DEMO_MODE=true` on `api`, `PUBLIC_DEMO_MODE=true` on `dashboard`.
- **ODK credentials.** The seed image bakes in `admin@glow.local` / `devpassword` and `api@glow.local` / `devpassword`. Activation already reconciles the admin password to the generated one; it additionally resets `api@glow.local` to a generated password and passes it to the API as `GLOW_ODK_API_PASSWORD`. The public `devpassword` must not work on any deployed instance.

## API

### Routes (`routers/demo.py`, mounted only when `DEMO_MODE`)

- `POST /demo/login` — body `{role: "admin" | "wrc" | "school", school_id?: int}`; identical behaviour to today's `/auth/dev-login`.
- `GET /demo/info` — unauthenticated. Response `{"schools": [{"id": int, "name": str}]}`, ordered by `id`.
- `POST /demo/reset` — unauthenticated. Runs `seed_demo`, returns 204. Added to `AUDIT_ROUTES`.

### Seeding

- The body of the `schools sync` CLI command moves into `database.sync_schools(db, df, min_geographical, min_statistical, rng)`. The CLI becomes a thin wrapper and keeps its current (unseeded) behaviour.
- New `seed_demo(db, df)`, in one transaction:
  1. delete all rows from `api_keys`, `user_schools`, `school_geographical_neighbors`, `school_statistical_neighbors`, `users`, `schools`;
  2. `sync_schools(...)` with `random.Random(0)`, so a reset always reproduces the same neighbour assignments;
  3. create the `admin` user (`is_admin=True`, all schools).
- New CLI command `glow-api demo reset` calls `seed_demo`; it exits non-zero unless `DEMO_MODE` is on. `glow-init` (local dev and the demo overlay) runs it instead of today's `users create admin` + `schools sync`.
- After a reset, existing tokens reference deleted users and get 401; `POST /demo/login` re-creates users on next login (existing upsert behaviour).

### Contract examples

New `demo.info` example in `api/examples/contracts/`, validated by an API test and served by the Storybook MSW handlers.

### Out of scope

Rate limiting `/demo/reset`; verifying that loaded ODK data is the seed set.

## Dashboard

- **Runtime config / API client.** Rename per the table above; add `demoInfo()` (`GET /demo/info`) and `demoReset()` (`POST /demo/reset`).
- **`DemoBanner`** (`src/lib/components/DemoBanner.tsx`), rendered by `Layout` before the no-chrome `/login` branch so it appears on the login page and every `/{locale}` page, only when `demoMode`:
  - `<aside aria-label>` containing a `<details>`; the `<summary>` reads "Demo — all data is fictional. Click to see login details".
  - Body: an "Admin" heading with "Use *Sign in as admin* on the sign-in page"; a "Schools" heading with a `<table>` of ID and name from `demoInfo()` (fetched on mount; on failure, a plain error line and the rest still works); a "Reset demo" `<button>`.
  - Reset opens a native `<dialog>` ("Reset deletes all changes and signs everyone out", Cancel / Reset). Confirm calls `demoReset()`, clears the auth identity, navigates to `/{locale}/login`, and announces "Demo reset" in an `aria-live="polite"` region. A failed reset shows an error in the same region.
  - Theme colours, no gradients, no animation.
- **Login page.** Picker copy changes from "dev" to demo wording; calls `demoLogin`. The school-ID field is unchanged.
- **i18n.** All new strings under `demo.*` in `en.ts`.

## Testing

**API (pytest)**
- Demo routes 404 when `DEMO_MODE` is off.
- `/demo/info` shape matches the `demo.info` contract example.
- `/demo/reset`: removes users and API keys; restores a deleted school; a pre-reset token gets 401; two resets yield identical neighbour assignments; `admin` exists afterwards.
- `glow-api demo reset` refuses without `DEMO_MODE`.
- Renamed settings: startup fails with `DEMO_MODE` plus a Cognito pool.

**Deploy (pytest)**
- `demo_mode` reaches `tfvars`.
- Provisioning with a `demo_mode` differing from existing state raises `DeployError` before apply.
- GUI form posts the checkbox; deployment list shows the badge from the tag.

**Dashboard (Storybook interaction tests, each distinct)**
- `DemoBanner/Collapsed` — summary visible; school table not visible until expanded.
- `DemoBanner/Expanded` — table shows the schools from the MSW `demo.info` response.
- `DemoBanner/ResetConfirm` — Cancel closes the dialog without a request; Reset calls the endpoint and the status message appears.
- `DemoBanner/InfoError` — error line shown; Reset still available.
- Existing dashboard page story asserts no banner when `demoMode` is off.
- `runtimeConfig` unit test updated for `demoMode`.

## Definition of done

Per `AGENTS.md`: API tests pass and are Ruff formatted; dashboard `npm run test`, `npx tsc`, `npm run build` pass; deploy tests pass; the CI smoke test (`compose.test.yml`, which uses `glow-init`) passes. Versions are already at the unreleased `0.2.0` / `0.0.3`; bump again only if those have been tagged before this lands (breaking rename: minor in beta).
