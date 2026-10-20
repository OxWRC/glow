# Glow Deployment

> **Note:** this document will be rewritten for `glowctl`, and the AWS tooling has moved to the sibling repo `glow-deploy-aws`.

## Self-Hosted Deployment (Manual)

The repository does not provide a deployment orchestrator.

For a manual VM/server deployment, use the checked-in `compose.yml` stack, persistent storage for `docker-mount-data/`, and your own reverse proxy or load balancer.

### Prerequisites
- Ubuntu 22.04+ or Debian 11+ server
- Root or sudo access
- Domain name pointing to your server
- Ports 80/443 open for web traffic

### Deployment Steps

1. **Clone the repository:**
```bash
git clone https://github.com/OxWRC/glow.git
cd glow
git checkout v1.2.3  # Use latest release tag
```

2. **Prepare persistent storage:**
```bash
mkdir -p docker-mount-data
```

3. **Install Docker Engine and Docker Compose plugin.**

4. **Create the runtime environment and start the stack manually.**

This repository does not ship a host activation script for that workflow, so any manual deployment should be treated as an operator-managed compose installation rather than a supported automated path.

5. **Access services:**
- Dashboard: `http://glow.example.com` 
- API: `http://glow.example.com:8000`
- ODK Central: `http://glow.example.com:8080`

6. **Set up reverse proxy for HTTPS:**

You'll need to configure a reverse proxy (nginx, Apache, Caddy, etc.) to:
- Terminate HTTPS with your SSL certificate
- Forward requests to the appropriate ports

Example nginx configuration:
```nginx
server {
    listen 80;
    server_name glow.example.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name glow.example.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

server {
    listen 443 ssl;
    server_name api.glow.example.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

server {
    listen 443 ssl;
    server_name odk.glow.example.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Data Persistence

All persistent data is stored in `./docker-mount-data/`:
- `glow-postgres/` - Glow API database
- `odk-postgres/` - ODK Central database
- `odk-secrets/` - ODK encryption keys
- `.deploy/.env.admin` - Admin credentials
- Other service data

**Backup this directory regularly!**

Recommended backup strategy:
```bash
# Stop services
cd /path/to/glow
sudo docker compose --profile odk stop

# Backup data
sudo tar czf glow-backup-$(date +%Y%m%d).tar.gz docker-mount-data/

# Restart services
sudo docker compose --profile odk start
```

### Updates

To update to a new version:

```bash
cd /path/to/glow
git fetch --tags
git checkout v1.3.0  # New version
docker compose --profile odk up -d --build
```

This manual workflow preserves data in `docker-mount-data/`, but version compatibility checks are your responsibility.

### Retrieving Credentials

Admin credentials are stored in:
```bash
cat ./docker-mount-data/.deploy/.env.admin
```

This file contains:
- ODK Central admin email
- ODK Central admin password

### Troubleshooting

**Services not starting:**
```bash
sudo docker compose ps
sudo docker compose logs
```

**Check versions:**
```bash
cat docker-mount-data/.glow-deployment-version
```

**Major version upgrade blocked:**
If you see an error about major version upgrades, consult `UPGRADING.md` for migration instructions.

**Data location:**
If you want data on a separate partition, you can:
1. Mount your partition at `/data`
2. Create symlink: `ln -s /data ./docker-mount-data`
3. Start the stack as normal
