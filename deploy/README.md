# Deploying the API on the AAA VM

Run these on the VM (10.10.10.123) as a user with sudo. FreeRADIUS and the CoA worker are
set up separately in `radius/README.md`.

## 1. Service user and code

```bash
sudo useradd --system --home /opt/mapoly --shell /usr/sbin/nologin mapoly
sudo chown -R mapoly:mapoly /opt/mapoly
sudo chmod 600 /opt/mapoly/.env
sudo chown mapoly:mapoly /opt/mapoly/.env
```

`/opt/mapoly/.env` must contain the production values, including:

```
TRUST_PROXY_HEADERS=true
FRONTEND_ORIGIN=https://mapolywifi.vercel.app
COA_NAS_HOST=<MikroTik management IP>
RADIUS_COA_SECRET=<shared with the MikroTik>
```

Check the settings load: `sudo -u mapoly /opt/mapoly/.venv/bin/python -c "from app.config import get_settings; get_settings(); print('settings ok')"`

## 2. systemd

```bash
sudo cp /opt/mapoly/deploy/mapoly-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mapoly-api
sudo systemctl status mapoly-api --no-pager
curl -s http://127.0.0.1:8000/api/health
```

`ExecStartPre` runs `db_setup` on every start, so schema changes apply on restart.

## 3. Caddy

Install Caddy from the official repository (https://caddyserver.com/docs/install), then:

```bash
sudo cp /opt/mapoly/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile        # replace api.example.edu.ng with the real domain
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

## 4. Firewall

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Port 8000 is not opened. Redis listens on localhost and the VM address only, as configured.

## 5. Check from outside the campus network

```bash
curl -s https://<your-domain>/api/health
```

It should return a success response over HTTPS. The domain must already point at the campus
public IP, and the router must forward 80 and 443 to 10.10.10.123.
