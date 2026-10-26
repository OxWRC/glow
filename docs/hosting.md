# Hosting approaches

GLOW runs on one Linux server with Docker Compose, managed by `glowctl`. Everything in [DEPLOYMENT.md](../DEPLOYMENT.md) applies to every approach below. This page covers what to provision and what each approach adds in risk. We do not ship cloud-specific tooling: you provision the server, DNS, firewall, disks and off-site storage.

Constant requirements, wherever you host:

- Linux amd64 with Docker Engine and Compose v2, root access.
- Inbound: `GLOW_HTTP_PORT` and `GLOW_HTTPS_PORT` (80 and 443 by default) plus your admin access (SSH or equivalent). Nothing else.
- Outbound: Let's Encrypt (acme mode), your OIDC issuer, GitHub/GHCR (online installs and updates), your SMTP relay if used.
- Disk: a state volume (`GLOW_STATE_DIR`) and a separate backup volume (`GLOW_BACKUP_PATH`) on a different disk.
- An off-site copy of `GLOW_BACKUP_PATH`, and the backup key stored off the server. Both are required.

## 1. Local, on-prem or bare-metal VM

```
users --> your firewall/NAT (80, 443) --> [ Linux VM: Docker + glowctl ]
                                            |-- /var/lib/glow    (state, disk A)
                                            |-- /srv/glow-backups (backups, disk B)
                                            '-- off-site sync (rclone/rsync) --> other site
```

- Give the VM a stable IP, a public DNS name for the three hostnames, and port-forward 80/443. If it is not internet-reachable, use `GLOW_TLS_MODE=files` with a certificate from your own or institutional CA, or `acme` only if Let's Encrypt can validate (port 80 reachable).
- Disk B should be a different physical disk or a network share, not a second partition of disk A. `glowctl doctor` warns if both are on one filesystem.
- Off-site: rsync or rclone to another building, another host, or object storage.

Added risks: you own hardware failure, power, hypervisor snapshots (a snapshot of a running VM is not a database backup), UPS and physical security. A single disk or a single VM host is a single point of failure. Time must be NTP-synced.

## 2. Cloud VMs (AWS, Azure, GCP, Tencent, Alibaba)

```
users --> DNS --> public IP --> [ provider firewall: 80, 443, admin ] --> VM (Docker + glowctl)
                                                                          |-- OS disk
                                                                          |-- data volume A  -> GLOW_STATE_DIR
                                                                          '-- data volume B  -> GLOW_BACKUP_PATH
                                                          object storage <-- scheduled sync of GLOW_BACKUP_PATH
```

Provision the same things everywhere, under each provider's names:

| Need | AWS | Azure | GCP | Tencent / Alibaba |
| --- | --- | --- | --- | --- |
| VM | EC2 | Virtual Machines | Compute Engine | CVM / ECS |
| Firewall | Security group | Network security group | VPC firewall rule | Security group |
| Block storage | EBS | Managed disks | Persistent Disk | CBS / Cloud disk |
| Off-site object storage | S3 | Blob Storage | Cloud Storage | COS / OSS |

- **Firewall:** allow inbound 80 and 443 from the internet (80 is needed for ACME). Allow SSH only from your own addresses, or use the provider's session tool (see approach 3). Do not open any other port: ODK, the API and the databases are reachable only through the proxy.
- **Disks:** put `GLOW_STATE_DIR` on a data volume, not the OS disk, so you can detach and reattach it. Put `GLOW_BACKUP_PATH` on a second volume. Volume snapshots are an extra safety net; they do not replace `glowctl` backups.
- **Off-site copy:** a volume attached to the same VM, even in another zone, does not count as off-site if the account or region is the failure. Sync `GLOW_BACKUP_PATH` to object storage in another region or account (rclone supports all of the above), with a retention or object-lock policy so a compromised server cannot delete its own history.
- **Backup key:** store it outside the cloud account that holds the server.

Added risks: public attack surface (keep the OS patched; restrict admin access), account compromise, cost of idle volumes, region and data-residency rules for object storage, and provider-specific egress to GHCR.

## 3. AWS without SSH (SSM only)

```
admin --> AWS SSM Session Manager --> EC2 (no inbound 22) --> sudo glowctl ...
users --> 80/443 only
```

- Attach an instance profile with the SSM managed policy, install or use an AMI with the SSM agent, and close port 22 in the security group. Operators run `glowctl` through an SSM session (`aws ssm start-session --target <instance-id>`).
- Run `update` in `tmux` or `screen` inside the session: if the session drops, an update in a bare shell is interrupted and rolls back.
- Give the instance profile write access to the off-site S3 bucket only, and run the sync (for example `rclone sync`) from cron or a systemd timer on the host.
- `glowctl` itself does not know about AWS; this is access and sync set-up on your side.

Added risks: SSM outages or IAM mistakes can lock you out (keep a break-glass path), and an instance role that can delete the bucket weakens the off-site copy: scope it to write-only or enable object lock.

## 4. Demo and production on one host

Each stack needs its own parent directory for its releases (it holds that stack's `current` link), `glow.env`, `GLOW_STATE_DIR`, `GLOW_BACKUP_PATH` (production only), domain, ports and Compose project name. `GLOW_INSTANCE=demo` in the demo's `glow.env` gives it the project `glow-demo` and the default state dir `/var/lib/glow-demo`; `glowctl` then manages each stack from its own release directory.

```
users --> :80/:443  --> [ your front proxy (Caddy/nginx/HAProxy) ]
                          |-- glow.example.org, api., odk.      --> 127.0.0.1:8080 (production)
                          '-- demo.example.org, api.demo., odk.demo. --> 127.0.0.1:9080 (demo)
```

Approach:

1. Run your own front proxy on 80/443 with TLS (it owns the certificates).
2. Production (`/opt/glow/glow-<tag>`): `GLOW_TLS_MODE=off`, `GLOW_HTTP_PORT=8080`, `GLOW_STATE_DIR=/var/lib/glow`, `GLOW_BACKUP_PATH=/srv/glow-backups`, `GLOW_DOMAIN=glow.example.org`.
3. Demo (its own release directory, for example `/opt/glow-demo/glow-<tag>`): `GLOW_INSTANCE=demo`, `GLOW_DEMO_MODE=true`, `GLOW_TLS_MODE=off`, `GLOW_HTTP_PORT=9080`, `GLOW_DOMAIN=demo.example.org`, and no `GLOW_STATE_DIR` line (it defaults to `/var/lib/glow-demo`). A production second instance would also need its own `GLOW_BACKUP_PATH`: two stacks must never share a backup path.
4. Install each from its own release directory: `sudo ./glowctl install ...`. Every later `glowctl` command (status, update, backup, users) is run from that stack's release directory.
5. Your front proxy routes by hostname (all three names per site) to the right local port and forwards the original `Host` header. `GLOW_TLS_MODE=files` with a front proxy passing TLS through by SNI also works; `acme` does not, since ACME needs ports 80/443 to itself.
6. In `off` mode the server must reach `https://odk.<domain>` itself (the API and Enketo call it): the hostnames must resolve, from the server, to the front proxy. `glowctl doctor` checks this, and warns when another GLOW stack's proxy is already running.

`GLOW_INSTANCE` cannot change after install: each state directory records its project (`.glow-instance`), and `glowctl` refuses a `glow.env` that names another. `install` and `restore` also refuse to act on a Compose project whose containers use another state directory, so a missing `GLOW_INSTANCE` cannot take over the production stack. For logs by hand, use `--project-name glow-demo`.

Added risks: demo and production share a kernel, disk and Docker daemon, so a demo problem (disk full, runaway container) can hurt production. Demo mode has open login by design: keep it on a separate hostname, never reuse production domains, and never load real data into it. Prefer separate servers if you can.

## 5. Offline and China mainland

```
internet-connected machine --> download release assets --> (USB / internal mirror)
                                                              |
                                  [ server ] <-- sha256sum -c, glowctl install --bundle
```

- **Offline bundle:** use the release assets (archive, `glow-images-*` parts, `digests.json`, `SHA256SUMS`, `glowctl` binary) as described in [Offline install](../DEPLOYMENT.md#offline-install). Install, update and restore with `--bundle` (a disaster restore onto a new offline server needs the release directory plus the bundle; see the restore drill in the deployment guide). The parts are each under 2 GB so they fit hosting and transfer limits. Getting them to the server is up to you; verify checksums on the server.
- **ICP filing (mainland China):** serving a website on a mainland domain from a mainland server requires ICP filing (and for some uses, public security filing). That is a legal process with your provider and takes time. Plan for it before the launch date. Servers outside the mainland (for example Hong Kong) avoid ICP but may have slower or less reliable access from the mainland.
- **Registry mirrors:** GHCR and GitHub can be slow or blocked. The bundle avoids both. If you prefer online pulls, you can use a mirror that holds the same digests as `digests.json` (a Docker registry mirror, or your own registry via `GLOW_REGISTRY`). `update` needs the same registry for the new release. See [DEPLOYMENT.md](../DEPLOYMENT.md#configure-glowenv) for `GLOW_REGISTRY`.
- **ACME:** Let's Encrypt validation needs the three names to resolve and port 80 reachable. Where that is impractical (or the domain is not yet filed), use `GLOW_TLS_MODE=files` with a certificate from a CA you can reach, or `off` behind your provider's certificate and load balancer.
- **OIDC and SMTP:** the issuer must be reachable from the server (token keys are fetched by the API) and from users' browsers. Check this from inside the network before installing. A mail relay reachable from the server is needed for ODK email and for issuing API keys (claim links are emailed).
- **Data residency:** keep the state, the backups and the off-site copy where your rules require. Choose the object-storage region accordingly.
- **ODK browser iframe:** an admin's browser loads ODK's news iframe from `getodk.github.io`; where that is blocked the frame is blank and nothing else is affected.

Added risks: updates are manual and slower (get the bundle, move it, verify, `update --bundle`), you cannot pull a missing image on demand, and Docker's digest handling varies by image store (see the offline section of the deployment guide).

## 6. Managed container platforms (unsupported)

AWS ECS/Fargate, Azure Container Apps, Google Cloud Run, Kubernetes, marketplace images and per-cloud infrastructure modules are **not supported** by this project. You can try them, but you would be on your own. Known gaps:

- **No `glowctl`.** Install, update with rollback, backup, restore, `status` and `doctor` all assume Docker Compose on a host with a Docker socket and bind-mounted directories.
- **Persistent volumes.** The databases, ODK secrets and vintages live in host directories. Platforms with ephemeral containers or network volumes need careful mapping, and Postgres on network file systems is a corruption risk.
- **Backups.** The hourly backup service and weekly restore test are a Compose service; you would rebuild them with the platform's scheduler and prove restores yourself.
- **Single-instance assumptions.** One server, one API and one ODK stack; there is no clustering or autoscaling, so platforms built around them add complexity without benefit.
- **Rollback and migrations.** Update safety (pre-update snapshot, selective database restore) is implemented in `glowctl`.
- **Reverse proxy and ports.** The bundled proxy is the only published surface; replicating that network isolation on a platform is on you.

If you need this, run a VM with Compose inside the platform (approach 2) instead.
