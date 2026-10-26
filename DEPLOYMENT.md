# GLOW deployment guide

This guide is for the team that runs a GLOW server. It covers one server running Docker Compose, managed with the `glowctl` binary. For where to put that server (cloud VM, on-prem, offline, China mainland, demo and production on one host) see [docs/hosting.md](docs/hosting.md).

The release archive ships this file, so it matches the version you installed.

## What you get

One release (`vX.Y.Z`) is one set of images plus one archive. `glowctl install` starts these services:

- `proxy` (Caddy): the only service that publishes ports. It serves `<domain>`, `api.<domain>` and `odk.<domain>`.
- GLOW dashboard, GLOW API and their Postgres.
- ODK Central (service, nginx, Postgres, Enketo, Redis, pyxform) for data collection.
- `backup`: hourly encrypted backups and a weekly restore test.

Images are public on GHCR and pinned by digest. The server never builds images and never needs `git`.

## Requirements

- A Linux server (amd64; the published images are amd64 only). Size it for the data: allow several GB for the state directory and at least as much again on a separate disk for backups.
- Docker Engine with the Compose v2 plugin (`docker compose version` works). Install a recent stable version.
- root (or sudo) on that server. `restore` and `update` write into the state directory and must run as root.
- A domain you control, with DNS records for three names (see [DNS](#dns)).
- An OIDC identity provider you trust (Microsoft Entra ID, Google, Keycloak, Cognito, or any compliant issuer). Not needed for a demo.
- An SMTP relay the server can reach (`GLOW_SMTP_*`). GLOW emails API key claim links through it, and ODK Central sends its own mail. Without one, admins cannot issue API keys. Not needed for a demo (it has a built-in inbox at `<domain>/mail/`).
- Outbound internet to GitHub and GHCR, or the offline bundle (see [Offline install](#offline-install)).
- Somewhere off the server to keep a copy of the backups (see [Backups](#backups)).

## Download and verify

Download these assets from the GitHub release page into one directory:

| Asset | Use |
| --- | --- |
| `glow-<tag>.tar.gz` | Release archive: compose files, proxy config, `odk-forms/`, `glow.env.example`, this guide, a `glowctl` binary (amd64). |
| `glowctl-linux-amd64`, `glowctl-linux-arm64` | The `glowctl` binary alone, per CPU architecture. |
| `glow.env.example` | The settings template. |
| `SHA256SUMS` | Checksums for every asset. |
| `glow-images-<tag>.tar.gz.part-NN`, `digests.json` | Offline bundle only: every image, split into parts. |

Verify, then unpack:

```bash
sha256sum -c SHA256SUMS --ignore-missing
tar xzf glow-<tag>.tar.gz
cd glow-<tag>
```

`--ignore-missing` skips assets you did not download. The unpacked `glow-<tag>/` is the release directory. Keep release directories side by side in one parent directory (for example `/opt/glow/glow-v0.2.0`): updates create siblings of the current one, and `install` adds a symlink `/opt/glow/current` to the running release. Use one parent directory per stack.

Checksums catch corrupt downloads. They do not prove who published the files, because they come from the same place. Download from the project's official GitHub release page only.

## Configure `glow.env`

```bash
cp glow.env.example glow.env
chmod 600 glow.env
```

Edit `glow.env` in the release directory. One `KEY=VALUE` per line. Comments go on their own lines (no inline comments); quote a value that contains ` #`. Unknown keys, duplicate keys and bad values are rejected, with one error per key. Relative paths are resolved against the directory holding `glow.env`.

| Key | Default | Meaning |
| --- | --- | --- |
| `GLOW_DOMAIN` | none (required) | Public domain. Dashboard at `<domain>`, API at `api.<domain>`, ODK Central at `odk.<domain>`. |
| `GLOW_OIDC_ISSUER` | none (required unless demo) | OIDC issuer URL. Must be `https://`. |
| `GLOW_OIDC_CLIENT_ID` | none (required unless demo) | Client ID registered for GLOW. |
| `GLOW_TLS_MODE` | `acme` | `acme`, `files` or `off`. See [TLS modes](#tls-modes). |
| `GLOW_TLS_CERT_DIR` | `/etc/glow/certs` in the example | Directory with `fullchain.pem` and `privkey.pem`. Used when `GLOW_TLS_MODE=files`. |
| `GLOW_ACME_EMAIL` | empty | Contact email for Let's Encrypt (`acme` mode). |
| `GLOW_HTTP_PORT` | `80` | Host port for HTTP. |
| `GLOW_HTTPS_PORT` | `443` | Host port for HTTPS. |
| `GLOW_DEMO_MODE` | `false` | `true` for a demo with fictional data: no login, no backups, no OIDC settings. |
| `GLOW_INSTANCE` | empty | Name of a second stack on the same host (lowercase letters, digits, `-`; up to 31). The Compose project becomes `glow-<instance>` instead of `glow`. Cannot change after install. See [docs/hosting.md](docs/hosting.md#4-demo-and-production-on-one-host). |
| `GLOW_STATE_DIR` | `/var/lib/glow` (`/var/lib/glow-<instance>` with `GLOW_INSTANCE`) | Persistent state: databases, ODK secrets, generated secrets, audit logs, vintages, certificates. |
| `GLOW_BACKUP_PATH` | none (required unless demo) | Existing, writable directory for the backup repository. Use a different disk from `GLOW_STATE_DIR`. Each instance needs its own: two stacks must never share a backup path. |
| `GLOW_PERIOD_TIMEZONE` | `Europe/London` | IANA timezone of the school calendar. See [Time handling](#time-handling). |
| `GLOW_SMTP_HOST` | empty | Mail relay for ODK Central and GLOW (API key claim links). Empty disables email: ODK can't send mail and admins can't issue API keys. Ignored in demo mode, which uses a built-in inbox at `<domain>/mail/`. |
| `GLOW_SMTP_PORT` | `587` | SMTP port. |
| `GLOW_SMTP_SECURE` | `false` | Use TLS for the SMTP connection. |
| `GLOW_SMTP_USER`, `GLOW_SMTP_PASSWORD` | empty | SMTP credentials. |
| `GLOW_SMTP_FROM` | `no-reply@<domain>` | From address for outgoing mail. |

`GLOW_REGISTRY` and `GLOW_VERSION` are also accepted, but they are set by the release; do not set them unless told to. Setting `GLOW_VERSION` blocks `glowctl update`.

You never type database passwords, secret keys or the backup key. `glowctl install` generates them into `GLOW_STATE_DIR/secrets.env` (mode 0600).

Run `glowctl doctor` before installing (see [Monitoring](#monitoring-statusdoctor)).

## OIDC setup

GLOW does not run a login system. It checks tokens from your identity provider.

1. In your provider, register a **public client** for the dashboard (authorization code flow with PKCE, no client secret).
2. Set the redirect URI to `https://<domain>/auth/callback`. Set the post-logout redirect to `https://<domain>/<locale>/login` if your provider requires one (for example `https://<domain>/en/login`).
3. Request the scopes `openid email profile`.
4. Put the issuer in `GLOW_OIDC_ISSUER` and the client ID in `GLOW_OIDC_CLIENT_ID`. The API finds the provider's keys through `<issuer>/.well-known/openid-configuration` and checks the token's issuer and audience (the client ID).

### Trust model

- **Users are pre-registered by email.** An admin creates a user by email address (dashboard or `glowctl users add`). The account starts unlinked. GLOW never creates users itself. A person who signs in without a matching account sees a "no access" page.
- **Linking happens on first sign-in only when `email_verified` is `true`.** If the token's email matches an unlinked user and the token says `email_verified: true`, GLOW binds the token's `sub` to that user. A token without a true `email_verified` never links.
- **After linking, `sub` is the identity.** Email is for display. If the provider later reports a changed, verified email, GLOW updates the displayed email.
- **GLOW trusts your provider to verify email ownership.** A provider that sets `email_verified: true` for addresses nobody has checked lets anyone claim a pre-registered account. Do not use a provider or tenant that lets users self-assert email addresses.
- **Microsoft Entra ID does not emit `email_verified` by default.** Its `email` claim is mutable and not guaranteed to be verified. GLOW will not link Entra users unless the ID token carries `email_verified: true`. Test with a real sign-in token before rollout. If your tenant cannot provide a verified-email claim, use another OIDC provider (or a broker or federation in front of Entra) that does.
- **Switching OIDC provider means re-creating users.** Users are bound to the old provider's `sub`. Add the users again after the switch (disable the old accounts). Plan this before you collect data under one provider.

## Install

```bash
sudo ./glowctl install --admin-email you@example.org
```

Run it from the release directory (or pass `--dir`). Production install needs the first GLOW admin's email: pass `--admin-email`, or you are prompted on a terminal. In demo mode the flag is ignored.

Install is idempotent: if it stops part way, fix the cause and run it again. It does, in order:

1. Validates `glow.env`.
2. Creates the state directory and generates secrets.
3. Checks the API configuration (`glow-api config check`).
4. Starts the stack and waits for ODK Central.
5. Creates the ODK accounts and project, then publishes the central ODK forms (`odk-forms/`).
6. Waits for the API (database migrations run on API start) and creates the first GLOW admin.
7. Checks every hostname through the proxy.
8. In production: takes the first backup.

The last output of the first production install is the **backup key**. It is shown once.

> **Keep the backup key off this server (password manager, safe).** If you lose it, the backups are unrecoverable: they are encrypted and nothing else can open them. The key also lives in `GLOW_STATE_DIR/secrets.env`, but that is lost with the disk, which is exactly when you need it.

The ODK Central admin email is printed at the end; its password is `ODK_ADMIN_PASSWORD` in `GLOW_STATE_DIR/secrets.env`. ODK web users use ODK's own local passwords; ODK sign-in is not linked to OIDC.

If install fails in `acme` mode, check DNS and that ports 80 and 443 are reachable from the internet, then `compose logs proxy`.

## DNS

Create three records pointing at the server's public IP (A, and AAAA if you have IPv6):

```
glow.example.org
api.glow.example.org
odk.glow.example.org
```

All three must exist before an `acme` install: Let's Encrypt validates each name. In `files` mode the certificate must be valid for all three names (a wildcard or SAN certificate).

## TLS modes

| `GLOW_TLS_MODE` | Behaviour | You provide |
| --- | --- | --- |
| `acme` (default) | The proxy obtains and renews Let's Encrypt certificates. | The three DNS records; ports 80 and 443 reachable from the internet; `GLOW_ACME_EMAIL` recommended. |
| `files` | The proxy serves your certificate. | `fullchain.pem` and `privkey.pem` in `GLOW_TLS_CERT_DIR`, valid for all three names. Renew them yourself and restart the proxy. |
| `off` | The proxy serves plain HTTP only; TLS ends in your own proxy or load balancer in front. | A terminator that forwards all three hostnames to `GLOW_HTTP_PORT`. The server must also be able to reach `https://odk.<domain>` (its own public name) because the API and Enketo call ODK Central through it. `glowctl doctor` checks this. |

Only the proxy publishes ports: `GLOW_HTTP_PORT` and, except in `off` mode, `GLOW_HTTPS_PORT`. Everything else is reachable only on internal Docker networks. Open those ports in your firewall and nothing else except SSH (or your remote-access method).

With a private CA in `files` mode, Enketo does not trust that CA by default; use a public CA or `acme`.

## First admin and users

The first admin is the email you gave to `install`. They sign in through your OIDC provider; the account links on first sign-in.

```bash
glowctl users list
glowctl users add teacher@example.org --school "Oak Primary" --school "Elm Primary"
glowctl users add second-admin@example.org --admin
glowctl users grant teacher@example.org --school "Birch Primary"
glowctl users grant teacher@example.org --admin
glowctl users grant teacher@example.org --no-admin
glowctl users disable teacher@example.org
```

- `add <email>` creates an unlinked user. `--school <name>` is repeatable. `--admin` makes an admin.
- `grant <email>` needs at least one of `--school`, `--admin`, `--no-admin`.
- `disable <email>` deactivates a user.
- Admins can also create users in the dashboard. Admins manage users and audit logs; they cannot read survey data.

## Backups

**What.** Every hour, the `backup` service writes one encrypted, deduplicated snapshot (restic) to `GLOW_BACKUP_PATH`. A snapshot holds: `pg_dump` of the GLOW and ODK databases, ODK secrets, `secrets.env`, `glow.env`, audit logs, vintages (released datasets, which are identifiable and need the same protection as the databases), the ODK forms state and `deployment.json`. The two database dumps are taken seconds apart, not at the same instant.

**When.** The first backup runs at install; the loop then runs hourly, starting a few minutes after the stack starts. Retention is 48 hourly, 30 daily and 12 monthly snapshots. Quiet periods cost almost nothing. There is no backup in demo mode.

Take one now, for example before maintenance:

```bash
sudo ./glowctl backup
```

**Off-site copy (required).** `GLOW_BACKUP_PATH` is on the same server (ideally another disk). It does not survive losing the server, the site, or ransomware. Copying it elsewhere is your job, and a deployment without that copy is not backed up. Sync the whole directory on a schedule (rclone to object storage, rsync to another host, your cloud's sync tool). The repository is encrypted, so an untrusted store is acceptable. `glowctl doctor` warns when the backup path shares a filesystem with the state directory. See [docs/hosting.md](docs/hosting.md) for provider options.

**Backup disk not mounted.** The first backup creates the repository. After that, a missing repository at `GLOW_BACKUP_PATH` (for example a backup disk that did not mount after a reboot, leaving an empty mountpoint) fails the backup with "no restic repository at /backup (backup disk unmounted?)" and `status` fails, rather than silently starting a new repository on the root disk. Mount the disk and run `sudo ./glowctl backup`. To move backups to a new, empty path on purpose, set `GLOW_BACKUP_PATH`, delete `GLOW_STATE_DIR/backup-status/backup-status.json`, then run `sudo ./glowctl backup`.

**Backup key custody.** See [Install](#install). Store it off the server, in at least two places held by different people. Without it, an off-site copy is useless after the disk is gone.

**Restore test.** Weekly, the backup service restores the latest snapshot into a throwaway Postgres and checks that it loads and the row counts match. Run it on demand:

```bash
sudo ./glowctl backup --verify
```

The result shows in `glowctl status`.

**Restore drill.** A backup you have not restored is a guess. Do this on a spare server before you need it, and again after major updates:

1. Install Docker, then download and unpack the release that made the backup, or an older one (a snapshot newer than the release is refused). For a disaster restore, use the release matching the newest snapshot, then update.
2. Create `glow.env` as for the original (same `GLOW_STATE_DIR` layout; `GLOW_DOMAIN` can differ for a drill).
3. Copy the backup repository from the off-site store to the spare server.
4. Put the backup key in a file readable only by root, then run:

```bash
sudo ./glowctl restore --from /path/to/backup-copy --key-file /root/glow-backup.key
```

`--snapshot ID` picks a snapshot (default `latest`); without `--key-file` you are prompted for the key on a terminal. `restore` checks the key first (a wrong key changes nothing), restores the databases, secrets, config and vintages, starts the stack and checks it through the proxy. The snapshot's `glow.env` is saved as `glow.env.restored` in the release directory for you to compare with yours and merge by hand.

Offline (no GitHub or GHCR, for example a disaster restore onto a new server): unpack the release and add `--bundle /path/to/bundle-dir` (see [Offline install](#offline-install)). The images are loaded from the bundle before the key check, so `restore` needs no registry.

On a server that already holds live data, `restore` refuses unless you add `--force`. It then replaces the data and keeps the originals as `*.pre-restore`. Delete those once you are satisfied. `restore` must run as root.

If `GLOW_BACKUP_PATH` on the restored server holds a different repository from the restored key, `restore` warns that backups will fail. Point `GLOW_BACKUP_PATH` at a repository for this key, or empty it.

## Updates

Each release's notes say whether it is a security release and which services change: GLOW-only releases leave ODK Central running; releases touching ODK interrupt collection briefly. Read the notes, and time ODK-touching or form-changing releases outside collection waves.

From the running release, through the `current` link (or its real directory):

```bash
cd /opt/glow/current
sudo ./glowctl update v0.3.0
```

Offline: `sudo ./glowctl update v0.3.0 --bundle /path/to/glow-v0.3.0.tar.gz` (the archive, or the directory holding the bundle files). `--yes` skips the confirmation prompt (required without a terminal).

How it works:

- The new release is unpacked into a **sibling directory** `glow-<tag>` next to the current one, and your `glow.env` is copied in. The old directory is kept for rollback. `install` keeps a symlink `current` beside the release directories pointing at the running release, and `glowctl` always runs compose through it, so file paths in the containers' config stay the same across releases. The update switches `current` to the new directory (atomically) and a rollback switches it back. **Run `glowctl` from `current`** (or the running release's real directory; an update from another directory is refused).
- Order: download or load images, check `glow.env` against the new API image, check suppression rules, take a backup, recreate only the containers that changed (new image, changed compose config, or a changed release file they mount, such as a Caddyfile or ODK nginx template), run migrations on API start, health-check. Anything that fails before the backup changes nothing and leaves no downtime.
- **GLOW-only releases do not restart ODK Central or the proxy**, so collection continues during the update. Releases that change ODK or proxy images or files restart those services, briefly interrupting collection; the prompt and the "changed services" line say which kind of update it is. The first update of a deployment installed before `current` existed recreates every service that mounts release files once, to move it onto the stable path.
- **Downgrades are refused**, as is an update from an unknown running version (demo excepted).
- **Suppression rule changes are refused in production.** If the new release's suppression rules differ from the running ones, the update stops: the same dataset must never be releasable under two coarsenings. A rule change needs a fresh deployment. Demo mode continues with a note.
- **Rollback is automatic.** If the swap or health check fails, `glowctl` restores the previous images and, from the pre-update snapshot, the databases of the services that changed. A GLOW-only release restores only the GLOW database, so ODK submissions taken during the update survive. A release that touches ODK restores the ODK database, losing submissions from before the swap started. Demo mode has no backup, so a failed demo update restores images only.
- **Form publishes are not undone.** Changed central ODK forms are published after the update succeeds, because ODK cannot cleanly un-publish. If publishing fails, the update still stands; rerun `glowctl install --admin-email <an existing admin>` from the new release directory to publish them. Your own local forms in ODK are never touched.
- If a rollback itself fails, the error says what state things are in and how to recover by hand.

Run updates in `tmux` or `screen`: they can take a long time (image pulls, health waits). A dropped SSH session triggers the rollback path rather than leaving a half update; a multiplexer avoids that.

To undo a successful update you no longer want, restore from a backup (see [Backups](#backups)); downgrading in place is refused.

## Monitoring (`status`/`doctor`)

```bash
sudo ./glowctl status
```

`status` prints one line per check: version, each service, each hostname through the proxy, the last backup, the restore test and pruning. Wire it into your monitoring (cron plus alerting, a Nagios-style check). It exits **1** when any of these holds:

- the last successful backup is more than 2 hours old (or the last backup failed);
- the last restore test failed, or last passed more than 15 days ago (it runs weekly; more than 8 days is a warning);
- a service is unhealthy, exited with an error, or a hostname does not answer HTTP 200.

A service that is still starting, and a failed prune, are warnings only (exit 0). A fresh install gets 2 hours for the first backup. Demo has no backup checks.

```bash
./glowctl doctor
```

`doctor` checks the host and `glow.env`. It exits 1 on errors (Docker or Compose v2 missing, invalid `glow.env`) and prints warnings for: backups on the same filesystem as the state directory, `GLOW_TLS_MODE=off` (and an unreachable `https://odk.<domain>`), default ports already in use (before install), another GLOW stack's proxy running on this host (ACME needs 80/443 to itself), and missing SMTP. Run it before the first install and after config changes.

Logs: run compose the way `glowctl` does, from the release directory (`/opt/glow/current`):

```bash
docker compose --project-name glow --env-file /var/lib/glow/runtime.env logs backup
```

Use your `GLOW_STATE_DIR` in place of `/var/lib/glow`, and `--project-name glow-<instance>` if you set `GLOW_INSTANCE`; other subcommands (`ps`, `logs proxy`, `logs odk-service`) work the same way. Below this is shortened to `compose logs <service>`. Container logs rotate by size. Audit logs live in `GLOW_STATE_DIR/api-audit-logs` and are backed up. GLOW does not alert; it exposes `status` for you to alert on.

## Time handling

Everything is UTC internally: storage, logs, schedulers and API payloads. The host or container timezone does not affect behaviour. The dashboard shows times in the viewer's own timezone, with the zone labelled, so users anywhere see their local time.

`GLOW_PERIOD_TIMEZONE` is the one deliberate local-time setting: it is the school calendar's zone, used to assign data to academic periods (converted explicitly from UTC). Set it to where the schools are.

`status` and backup timestamps are UTC (`Z`).

## Privacy

No telemetry leaves the deployment. GLOW sends none, and ODK Central's defaults are disabled: Sentry error reporting and CSP reports are off, and ODK's analytics and mailing-list reporting are off.

One exception remains in the browser: ODK Central's admin interface embeds a news iframe loaded from `https://getodk.github.io/central/`. An admin's browser loads it when using `odk.<domain>`; the server sends nothing there.

## Offline install

For servers that cannot reach GitHub or GHCR, the release assets are also the offline bundle. Download them elsewhere (`glow-<tag>.tar.gz`, every `glow-images-<tag>.tar.gz.part-NN`, `digests.json`, `glowctl-linux-<arch>`, `glow.env.example`, `SHA256SUMS`), move them to the server in one directory, then:

```bash
sha256sum -c SHA256SUMS
tar xzf glow-<tag>.tar.gz
cd glow-<tag>
cp glow.env.example glow.env   # edit
sudo ./glowctl install --bundle /path/to/bundle-dir --admin-email you@example.org
```

`install --bundle` and `update --bundle` verify the checksums, run `docker load` on the image parts, and re-point compose from digest names to tag names (via `digests.json`) when the Docker image store drops digest names. Each image is loaded under its tag name as well as its digest name. Running `docker compose` by hand from the bundle needs a containerd image store.

## Troubleshooting

- **`glow.env is invalid`**: the message lists every bad key. Fix them all and rerun.
- **Install stuck waiting for ODK Central**: first start takes minutes. `compose ps` and `compose logs odk-service`.
- **Certificate not issued (`acme`)**: DNS for all three names, ports 80/443 open, `compose logs proxy`.
- **User signs in and sees "no access"**: the email is not pre-registered, is disabled, or the provider's token lacks `email_verified: true`. Check with `glowctl users list`.
- **`status` says backup older than 2h**: `compose logs backup`; check free space on `GLOW_BACKUP_PATH`.
- **Update refused**: the message says why (downgrade, suppression rules, config rejected, wrong release directory). Nothing was changed.
- **Restore says wrong key**: nothing was changed. Check the key file holds only the key, on one line.
- **Where are the ODK admin credentials**: `ODK_ADMIN_PASSWORD` in `GLOW_STATE_DIR/secrets.env` (root-only).

## Releasing (maintainers)

Push a tag `vX.Y.Z`. `.github/workflows/release.yml` builds and pushes the images, builds the `glowctl` binaries (linux amd64 and arm64), and publishes the archive, the offline bundle, `digests.json`, `glow.env.example` and `SHA256SUMS` as a GitHub release.

GHCR packages created with `GITHUB_TOKEN` start private. After the first release, set each `glow-*` package to Public and link it to the repository; the release job's "Verify anonymous pull works" step fails until you do. The first release is therefore expected to fail at that step: make the `glow-*` packages Public, then re-run the failed jobs on the same tag.

The release notes must flag security releases, list which services changed (GLOW-only or ODK-touching), and say when forms change.
