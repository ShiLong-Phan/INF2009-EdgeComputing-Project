# CP7 Demo Runbook (Laptop + 2 Pis)

This guide is for demo day with:
1. One laptop acting as TLS MQTT broker + receiver + CP7 dashboard.
2. Two Pis acting as edge devices (`pi-edge-01`, `pi-edge-02`).

## 0) Topology and Naming

Use these identities consistently:
1. Laptop hostname: `DOMCOM2`
2. Pi 1 device id: `pi-edge-01`
3. Pi 2 device id: `pi-edge-02`

Topics:
1. Metadata topic: `edge/events/v1`
2. Image topic prefix: `edge/images/v1`

## 1) Laptop One-Time Setup

From repo root on laptop:

```powershell
python -m venv .venv-cp2-laptop
.\.venv-cp2-laptop\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r cp2_cp6\requirements-laptop.txt
```

Open firewall for MQTT TLS once (admin PowerShell):

```powershell
netsh advfirewall firewall add rule name="MQTTS 8883" dir=in action=allow protocol=TCP localport=8883
```

## 2) TLS Cert Setup for 2 Pis (Laptop)

Create certs folder and CA/server certs if you do not already have them:

```powershell
New-Item -ItemType Directory -Force certs | Out-Null
Set-Location certs

# CA (keep ca.key on laptop only)
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -out ca.crt -subj "/CN=cp1-local-ca"

# Server cert for laptop broker
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=DOMCOM2"
Set-Content -Path server.ext -Value "subjectAltName=DNS:DOMCOM2,IP:192.168.1.232`nextendedKeyUsage=serverAuth"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 825 -sha256 -extfile server.ext
```

Generate receiver client cert for laptop receiver process:

```powershell
openssl genrsa -out laptop-client.key 2048
openssl req -new -key laptop-client.key -out laptop-client.csr -subj "/CN=laptop-receiver"
Set-Content -Path laptop-client.ext -Value "extendedKeyUsage=clientAuth"
openssl x509 -req -in laptop-client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out laptop-client.crt -days 825 -sha256 -extfile laptop-client.ext
```

Generate Pi 1 client cert:

```powershell
openssl genrsa -out pi-edge-01.key 2048
openssl req -new -key pi-edge-01.key -out pi-edge-01.csr -subj "/CN=pi-edge-01"
Set-Content -Path pi-edge-01.ext -Value "extendedKeyUsage=clientAuth"
openssl x509 -req -in pi-edge-01.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out pi-edge-01.crt -days 825 -sha256 -extfile pi-edge-01.ext
```

Generate Pi 2 client cert:

```powershell
openssl genrsa -out pi-edge-02.key 2048
openssl req -new -key pi-edge-02.key -out pi-edge-02.csr -subj "/CN=pi-edge-02"
Set-Content -Path pi-edge-02.ext -Value "extendedKeyUsage=clientAuth"
openssl x509 -req -in pi-edge-02.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out pi-edge-02.crt -days 825 -sha256 -extfile pi-edge-02.ext

Set-Location ..
```

## 3) Start Mosquitto Broker (Laptop)

Create `mosquitto_tls.conf` in repo root:

```conf
listener 8883
cafile certs/ca.crt
certfile certs/server.crt
keyfile certs/server.key

require_certificate true
use_identity_as_username true
allow_anonymous false
```

Run broker (Terminal 1):

```powershell
mosquitto -c mosquitto_tls.conf -v
```

## 4) Start Receiver (Laptop)

Set API key (optional for full verify, can be omitted and receiver will mark `skipped`):

```powershell
$env:NANOGPT_API_KEY = "<YOUR_API_KEY>"
```

Run receiver (Terminal 2):

```powershell
.\.venv-cp2-laptop\Scripts\Activate.ps1
python cp2_cp6\server_event_receiver_laptop.py --broker-host DOMCOM2 --broker-port 8883 --topic edge/events/v1 --image-topic-prefix edge/images/v1 --ca-cert .\certs\ca.crt --client-cert .\certs\laptop-client.crt --client-key .\certs\laptop-client.key --db-path .\data\edge_events.db --image-store-dir .\data\images --nanogpt-model qwen3.5-27b
```

## 5) Start CP7 Dashboard (Laptop)

Run dashboard (Terminal 3):

```powershell
.\.venv-cp2-laptop\Scripts\Activate.ps1
python cp2_cp6\dashboard_cp7.py --db-path .\data\edge_events.db --host 0.0.0.0 --port 5050
```

Open:
1. `http://localhost:5050/`
2. Click each device in leaderboard for per-device analytics.

## 6) Pi Setup (repeat on each Pi)

## 6.1) Install packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip netcat-openbsd
```

## 6.2) Project layout

Copy repository and cert files to each Pi.

Pi 1 cert files to copy:
1. `certs/ca.crt`
2. `certs/pi-edge-01.crt` (rename to `certs/pi-client.crt` on Pi 1)
3. `certs/pi-edge-01.key` (rename to `certs/pi-client.key` on Pi 1)

Pi 2 cert files to copy:
1. `certs/ca.crt`
2. `certs/pi-edge-02.crt` (rename to `certs/pi-client.crt` on Pi 2)
3. `certs/pi-edge-02.key` (rename to `certs/pi-client.key` on Pi 2)

Set key permissions on each Pi:

```bash
chmod 600 certs/pi-client.key
```

## 6.3) Resolve laptop hostname on each Pi

```bash
echo "192.168.1.232 DOMCOM2" | sudo tee -a /etc/hosts
getent hosts DOMCOM2
nc -vz DOMCOM2 8883
```

## 6.4) Python env on each Pi

```bash
python3 -m venv .venv-cp2-pi
source .venv-cp2-pi/bin/activate
pip install --upgrade pip
pip install -r cp2_cp6/requirements-pi.txt
```

## 7) Run Pi 1 Publisher

```bash
source .venv-cp2-pi/bin/activate
python3 cp2_cp6/edge_event_publisher_pi.py \
  --broker-host DOMCOM2 \
  --broker-port 8883 \
  --topic edge/events/v1 \
  --image-topic-prefix edge/images/v1 \
  --device-id pi-edge-01 \
  --trigger-mode inside_bin \
  --ca-cert certs/ca.crt \
  --client-cert certs/pi-client.crt \
  --client-key certs/pi-client.key \
  --model-path mobilenet_v2_1.0_224.tflite \
  --label-path labels.txt \
  --edge-model-version mobilenetv2-baseline \
  --capture-dir captures \
  --sound-file /home/pi/sounds/beep.wav \
  --sound-device plughw:3,0 \
  --min-speed-cm-s 65 \
  --outbox-db-path data/pi_outbox.db \
  --retry-base-sec 2 \
  --max-retry-backoff-sec 60 \
  --max-image-bytes 400000
```

## 8) Run Pi 2 Publisher

```bash
source .venv-cp2-pi/bin/activate
python3 cp2_cp6/edge_event_publisher_pi.py \
  --broker-host DOMCOM2 \
  --broker-port 8883 \
  --topic edge/events/v1 \
  --image-topic-prefix edge/images/v1 \
  --device-id pi-edge-02 \
  --trigger-mode inside_bin \
  --ca-cert certs/ca.crt \
  --client-cert certs/pi-client.crt \
  --client-key certs/pi-client.key \
  --model-path mobilenet_v2_1.0_224.tflite \
  --label-path labels.txt \
  --edge-model-version mobilenetv2-baseline \
  --capture-dir captures \
  --sound-file /home/pi/sounds/beep.wav \
  --sound-device plughw:3,0 \
  --min-speed-cm-s 65 \
  --outbox-db-path data/pi_outbox.db \
  --retry-base-sec 2 \
  --max-retry-backoff-sec 60 \
  --max-image-bytes 400000
```

## 9) Demo Checklist

1. Broker terminal shows TLS clients connected.
2. Receiver terminal shows event + image + verify status lines.
3. Dashboard landing page shows both devices in leaderboard.
4. Device detail pages show numbered mix list and bar chart.

## 10) Optional Fast Reset Before Demo

On laptop:

```powershell
Remove-Item .\data\edge_events.db -ErrorAction SilentlyContinue
Remove-Item .\data\images\*.jpg -ErrorAction SilentlyContinue
```

On each Pi:

```bash
sqlite3 data/pi_outbox.db "DELETE FROM outbox;"
rm -f captures/*.jpg
```

## 11) Common Gotchas

1. `CERTIFICATE_VERIFY_FAILED`: Use `DOMCOM2` host and ensure server cert SAN matches current laptop IP/hostname.
2. No incoming events: Check Pi `nc -vz DOMCOM2 8883` and firewall rule.
3. Too many cloud API errors: ensure model is `qwen3.5-27b` and dedup is enabled in receiver.
4. No cloud key during grading: receiver will mark verification as `skipped`, pipeline still demonstrates CP2-CP7.
