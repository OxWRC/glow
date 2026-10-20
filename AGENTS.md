# GLOW: Core

This project is a reproducible data collection, storage, and dashboarding application.
It is designed for deployment in multiple discrete regions.
Development rules for each subcomponent are listed below.

## API `./api`

The API ingests data and stores it as a .sqlite3 file.
It then exposes that data to the Dashboard for display.

It is **very important** that individual people's data are not retrievable from the system,
including by advanced techniques such as differencing attacks.
To help guard against this we have an any->all rule:
if ANY cell in a filter/aggregate would end up with too small an N (zeros are okay),
then ALL cells are suppressed.
For example, if we group a class by sex (M: 14, F: 13) that's fine,
and if we group them by ethnicity (A: 5, B: 7, C: 15), that's also fine,
but if we group by both the whole lot are suppressed: (MA: 5, MB: **2**, MC: 7, FA: 0, FB: 5, FC: 8)
because there aren't enough in the MB cell.

### Tech stack:
- `Python 3.12`, `uv`, `pyproject.toml`
- `SQLite` (may use Postgres for deployment, but remain SQLite compatible)
- `FastAPI`

### Test Driven Development

We use test driven development.
When changes are to be made, tests are adjusted or added to cover those changes first and observed to fail.
The changes are then made and the tests are shown to pass.

### Clean as you go

Unused imports should be removed in files you touch.
If you orphan a function by removing its last call site you should remove the function.

### SemVer

We use semantic versioning.
The bump rules are different for beta (Major version 0) and release (Major version 1+).

While we are in beta, we **do not need to retain backwards-compatibility**.

#### Beta (Major version 0)
- Breaking changes  -> bump minor; otherwise
- New API surface -> bump patch

#### Release (Major versions 1+)
- Breaking changes -> new major version
- New API surface -> bump minor
- Otherwise: new logic -> bump patch

### Definition of Done:
- If new logic was added, tests are included to cover it
- All tests pass
- All code is Ruff formatted

## Dashboard `./dashboard`

The Dashboard exposes API data in graphic and tabular form to users.

### Tech stack:
- React (React Router)
- TypeScript
- Vite
- Tailwind
- Storybook

### SemVer

Same rules as for API.

### A11y
- All HTML is written using appropriate semantic tags
- Native HTML tags (e.g. <details>, <dialog>) are preferred over home-rolled or package-supplied variants
- All tests use accessible selectors, never class-based or id-based ones.
- Colour contrasts use theming and are appropriate
- We do not use colour gradients
- Animations are gated behind reduced-motion preferences

### i18n
- All text strings pass through an i18n function to resolve them into localised variants
- All links take the form /[locale]/... which defaults to 'en' and is preserved across internal links

### Tests
- All tests use accessible selectors, never class-based or id-based ones
- All stories include an interaction test
- All interaction tests are different and cover the distinguishing features of the story

### Definition of Done:
- The Storybook MSW handlers exactly match the relevant API return surfaces
- If new components were added, they include Storybook stories with tests
- If new logic was added, Storybook tests are included to demonstrate it
- Tests pass (`npm run test`)
- `npx tsc` is clean
- `npm run build` succeeds

## Admin `./admin`

The Admin view is a management and audit platform that allows access to audit logging and user/data management.
It **does not allow read access to data**. Admins are thus _not_ data viewers.

Not yet of concern.

## Development

### Regression tests before fixes

This applies to every subcomponent.
Before fixing a bug, write a test that reproduces it (where possible) and observe it fail.
Then make the fix and show the test passes.
If a reproducing test isn't feasible, say why in the commit message.

We use docker compose to keep stuff working in development environments.
Always ensure that the development environment exposes the backend and frontend appropriately.

Front/backend containers are restarted frequently during development.
Restarting either should not restart the other.

## Deployment

Deployment is being rebuilt as a portable Docker Compose package (see `docs/superpowers/specs/2026-10-20-portable-core-deployment-design.md`). The AWS tooling lives in the sibling repo `glow-deploy-aws`. See `DEPLOYMENT.md` for the current manual workflow.

### Release Tagging

There is one tag namespace: `v*.*.*` (e.g. `v0.1.4`), the release of the docker compose stack (api, dashboard, ODK).
