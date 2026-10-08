# 🚀 Deploy Server Licențe pe VPS — Ghid Pas cu Pas

Serverul de licențe (FastAPI + MongoDB) trebuie să ruleze **24/7** pe un VPS (Virtual Private Server) accesibil public prin HTTPS. Clienții tăi (aplicațiile desktop instalate la primării) vor comunica cu acest server pentru activare și validare.

**Timp estimat:** 30-45 minute prima dată.

**Cost estimat:** €4-6/lună (VPS + domeniu).

---

## 📋 Ce vei obține la final

- Un URL de tipul `https://api.cimitir.ro` care rulează serverul de licențe.
- Un panou admin: `https://cimitir.ro` unde te loghezi și gestionezi licențele.
- Certificat HTTPS gratuit auto-reînnoit (Let's Encrypt).
- Backup automat MongoDB zilnic.

---

## 🎯 Pasul 1 — Alege un provider de VPS

Recomandare pentru buget mic:

| Provider | Plan minim | Preț/lună | Locație |
|---|---|---|---|
| **Hetzner Cloud** ⭐ | CX22 (2 vCPU, 4 GB RAM) | **€4.51** | Nürnberg / Falkenstein |
| DigitalOcean | Basic Droplet (1 GB RAM) | $6 | Frankfurt |
| Vultr | Cloud Compute (1 GB RAM) | $6 | Frankfurt |
| Contabo | VPS S SSD (4 GB RAM) | €5.99 | Nürnberg / Bucharest |

**Recomand Hetzner** — cel mai bun raport calitate/preț în Europa. Ne referim la această opțiune în restul ghidului.

### Cum îți faci cont Hetzner:
1. Mergi la https://accounts.hetzner.com/signUp
2. Creează cont, verifică emailul.
3. Adaugă card de credit (nu se retrage nimic până nu creezi VPS-ul).
4. Creează proiect nou: „Cimitir Production".

### Cum creezi VPS-ul:
1. Click **„Add server"**
2. **Locație:** Nürnberg (Germania) sau Helsinki (Finlanda) — cel mai aproape de România.
3. **Image:** Ubuntu 24.04 LTS
4. **Type:** CX22 (€4.51/lună) — 2 vCPU AMD, 4 GB RAM, 40 GB SSD, 20 TB traffic.
5. **SSH key:** click „Add SSH key" — dacă nu ai una, urmează secțiunea **1.1** de mai jos.
6. **Name:** `cimitir-license-server`
7. Click **„Create & Buy Now"**

Vei primi IP-ul în ~30 secunde (ex: `95.216.123.45`).

### 1.1 (Opțional) Cum îți faci un SSH key
Pe Windows deschide **PowerShell** și rulează:
```powershell
ssh-keygen -t ed25519 -C "cimitir-admin"
# apasă Enter, Enter, Enter (fără parolă)
type $env:USERPROFILE\.ssh\id_ed25519.pub
```
Copiază textul afișat (începe cu `ssh-ed25519 ...`) și paste-uiește-l în Hetzner la crearea SSH key-ului.

---

## 🌐 Pasul 2 — Alege și configurează un domeniu

Recomand **[Namecheap](https://www.namecheap.com/)** sau **[Cloudflare Registrar](https://www.cloudflare.com/products/registrar/)**.

### Cumpără un domeniu (~$10/an):
- Exemple: `cimitir-server.ro`, `licente-cimitir.ro`, `myservice.ro`

### Configurează DNS-ul:
Adaugă 2 înregistrări **A record** care pointează către IP-ul VPS-ului tău:

| Type | Name | Value | TTL |
|---|---|---|---|
| A | @ (sau blank) | `95.216.123.45` (IP-ul tău) | Auto |
| A | api | `95.216.123.45` | Auto |

După 5-10 minute, `cimitir-server.ro` și `api.cimitir-server.ro` vor pointa la VPS-ul tău.

Testează: `ping api.cimitir-server.ro` — dacă vezi IP-ul VPS-ului = OK.

---

## 🐧 Pasul 3 — Configurare inițială VPS

Conectează-te la VPS din **PowerShell / Terminal**:
```
ssh root@95.216.123.45
```

Actualizează sistemul + instalează Docker:
```bash
apt update && apt upgrade -y
apt install -y curl wget vim ufw

# Docker (metoda oficială)
curl -fsSL https://get.docker.com | sh
apt install -y docker-compose-plugin

# Firewall: doar SSH + HTTP + HTTPS
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```

Creează un user non-root (opțional, dar recomandat):
```bash
adduser cimitir --gecos "" --disabled-password
usermod -aG docker cimitir
mkdir -p /home/cimitir/.ssh
cp ~/.ssh/authorized_keys /home/cimitir/.ssh/
chown -R cimitir:cimitir /home/cimitir/.ssh
chmod 700 /home/cimitir/.ssh
chmod 600 /home/cimitir/.ssh/authorized_keys
# Acum poți te loga cu: ssh cimitir@95.216.123.45
```

---

## 📦 Pasul 4 — Copiază serverul pe VPS

Din **calculatorul tău local**, deschide un terminal ÎN FOLDERUL cu codul serverului (`/app/backend/` din acest proiect + admin dashboard din `/app/frontend/`).

Copiază tot proiectul pe VPS:
```bash
# Windows PowerShell / Mac Terminal
scp -r /path/to/cimitir_license_server root@95.216.123.45:/opt/cimitir
```

Sau (mai bine) folosește Git:
```bash
# pe VPS:
mkdir -p /opt && cd /opt
git clone https://github.com/USERNAME/REPO cimitir
cd cimitir
```

---

## ⚙️ Pasul 5 — Configurare `.env` de producție

Pe VPS:
```bash
cd /opt/cimitir/backend
nano .env
```

Setează:
```env
MONGO_URL="mongodb://mongo:27017"
DB_NAME="cimitir_licenses"
CORS_ORIGINS="https://cimitir-server.ro"
JWT_SECRET="<64-char-hex-generat-random>"
ADMIN_EMAIL="admin@cimitir-server.ro"
ADMIN_PASSWORD="<parolă-puternică-min-16-caractere>"
FRONTEND_URL="https://cimitir-server.ro"
# LICENSE_PRIVATE_KEY_B64 și LICENSE_PUBLIC_KEY_B64 — generează-le NOI pentru producție:
```

**Generează chei RSA noi pentru producție** (NU folosi cele din dev!):
```bash
python3 -c "
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import base64
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
print('LICENSE_PRIVATE_KEY_B64=\"' + base64.b64encode(priv.encode()).decode() + '\"')
print('LICENSE_PUBLIC_KEY_B64=\"' + base64.b64encode(pub.encode()).decode() + '\"')
"
```

Copiază output-ul în `.env`. **IMPORTANT:** păstrează cheia publică (`PUB`) — o vei embeda în EXE (fișier `security/config.py::LICENSE_PUBLIC_KEY_PEM`).

Generează JWT secret:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## 🐳 Pasul 6 — Creează docker-compose.yml

Pe VPS, în `/opt/cimitir/`:
```bash
nano docker-compose.yml
```

Conținut:
```yaml
version: "3.9"

services:
  mongo:
    image: mongo:7
    restart: unless-stopped
    volumes:
      - ./data/mongo:/data/db
    networks:
      - cimitir_net

  backend:
    build: ./backend
    restart: unless-stopped
    depends_on:
      - mongo
    env_file:
      - ./backend/.env
    networks:
      - cimitir_net
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.backend.rule=Host(`cimitir-server.ro`) && PathPrefix(`/api`)"
      - "traefik.http.routers.backend.entrypoints=websecure"
      - "traefik.http.routers.backend.tls.certresolver=le"
      - "traefik.http.services.backend.loadbalancer.server.port=8001"

  frontend:
    build: ./frontend
    restart: unless-stopped
    networks:
      - cimitir_net
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.frontend.rule=Host(`cimitir-server.ro`)"
      - "traefik.http.routers.frontend.entrypoints=websecure"
      - "traefik.http.routers.frontend.tls.certresolver=le"
      - "traefik.http.services.frontend.loadbalancer.server.port=80"

  traefik:
    image: traefik:v3.0
    restart: unless-stopped
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
      - --entrypoints.web.http.redirections.entrypoint.to=websecure
      - --entrypoints.web.http.redirections.entrypoint.scheme=https
      - --entrypoints.websecure.address=:443
      - --certificatesresolvers.le.acme.email=YOUR_EMAIL@example.com
      - --certificatesresolvers.le.acme.storage=/letsencrypt/acme.json
      - --certificatesresolvers.le.acme.httpchallenge=true
      - --certificatesresolvers.le.acme.httpchallenge.entrypoint=web
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./data/letsencrypt:/letsencrypt
    networks:
      - cimitir_net

networks:
  cimitir_net:
```

Înlocuiește:
- `cimitir-server.ro` cu domeniul tău
- `YOUR_EMAIL@example.com` cu emailul tău (pentru Let's Encrypt)

Creează Dockerfile pentru backend:
```bash
nano /opt/cimitir/backend/Dockerfile
```
Conținut:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
```

Creează Dockerfile pentru frontend:
```bash
nano /opt/cimitir/frontend/Dockerfile
```
Conținut:
```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile
COPY . .
RUN yarn build

FROM nginx:alpine
COPY --from=build /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

`/opt/cimitir/frontend/nginx.conf`:
```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;
    location / {
        try_files $uri /index.html;
    }
}
```

Setează în `frontend/.env` variabila pentru production:
```
REACT_APP_BACKEND_URL=https://cimitir-server.ro
```

---

## 🚀 Pasul 7 — Pornește totul

```bash
cd /opt/cimitir
docker compose up -d --build
```

Așteaptă 30-60 secunde (primul start compilează frontend).

Verifică:
```bash
docker compose ps
# Toate serviciile trebuie să fie „Up" și „healthy"

curl -k https://cimitir-server.ro/api/health
# Trebuie să răspundă {"status":"ok",...}
```

Deschide în browser:
- **Panou admin:** `https://cimitir-server.ro`
- Autentifică-te cu `ADMIN_EMAIL` / `ADMIN_PASSWORD` din `.env`

---

## 💾 Pasul 8 — Backup automat MongoDB

Creează scriptul `/opt/cimitir/backup.sh`:
```bash
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR=/opt/cimitir/backups
mkdir -p "$BACKUP_DIR"
docker compose exec -T mongo mongodump --archive --gzip --db=cimitir_licenses > "$BACKUP_DIR/backup-$DATE.gz"
find "$BACKUP_DIR" -name "backup-*.gz" -mtime +30 -delete
```

Fă-l executabil și rulează-l zilnic la 3 AM prin cron:
```bash
chmod +x /opt/cimitir/backup.sh
crontab -e
```
Adaugă linia:
```
0 3 * * * cd /opt/cimitir && /opt/cimitir/backup.sh >> /var/log/cimitir_backup.log 2>&1
```

**BONUS:** copiază backup-urile pe un serviciu extern (S3, backblaze, sau simplu `scp` la un alt VPS):
```bash
# în backup.sh, adaugă:
rsync -av --delete "$BACKUP_DIR" backup-user@backup-server:/backups/cimitir/
```

---

## 🔄 Pasul 9 — Actualizări viitoare

Când modifici cod:
```bash
cd /opt/cimitir
git pull
docker compose up -d --build
```

Zero downtime (Traefik face rolling update).

---

## 🆘 Troubleshooting

### „Cannot connect to server" în client
- Verifică pe VPS: `docker compose logs backend --tail 100`
- Verifică că firewall-ul permite 443: `ufw status`
- Testează endpoint: `curl https://cimitir-server.ro/api/health`

### Let's Encrypt eșuează
- Portul 80 TREBUIE să fie deschis (nu doar 443) — LE verifică prin HTTP.
- Verifică DNS: `dig cimitir-server.ro` → trebuie să returneze IP-ul VPS.
- Logs Traefik: `docker compose logs traefik`

### MongoDB nu pornește
- Verifică spațiu pe disk: `df -h`
- Logs: `docker compose logs mongo`

### Am uitat parola admin
- Editează `.env` cu ADMIN_PASSWORD nou.
- Restart: `docker compose restart backend`
- Serverul detectează schimbarea și actualizează parola în DB automat.

---

## 💡 Îmbunătățiri viitoare recomandate

1. **Monitoring:** [Uptime Robot](https://uptimerobot.com/) gratuit — te sună dacă serverul cade.
2. **Alertă email** la evenimente critice (revocări, tentative de brute-force).
3. **CDN Cloudflare** — cache + DDoS protection gratuit.
4. **Certificate pinning** în clientul desktop — protecție împotriva MITM.
