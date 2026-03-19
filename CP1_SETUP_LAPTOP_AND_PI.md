# CP1 Setup Guide: MQTTS Between Raspberry Pi and Laptop/Desktop

Purpose: implement Checkpoint 1 (secure MQTT connectivity) with TLS-enabled publish/subscribe between Raspberry Pi (edge) and Laptop/Desktop (broker + subscriber).

This guide is split clearly by machine role.

## Folder Contents For CP1

- cp1_mqtt/mqtt_tls_subscriber_laptop.py
- cp1_mqtt/mqtt_tls_publisher_pi.py
- cp1_mqtt/requirements-laptop.txt
- cp1_mqtt/requirements-pi.txt

## Architecture For CP1

1. Laptop/Desktop runs Mosquitto broker with TLS on port 8883.
2. Laptop/Desktop also runs subscriber script.
3. Raspberry Pi runs publisher script and sends test JSON events.

## Part A - Laptop/Desktop Setup (Broker + Subscriber)

### A1) Install Required Software

Windows (PowerShell):

1. Install Python 3.10+.
2. Install OpenSSL (for certificate generation) or use Git Bash OpenSSL.
3. Install Mosquitto broker.
4. Ensure mosquitto, openssl, and python are available in PATH.

Quick verification:

```powershell
python --version
openssl version
mosquitto -h
```

### A2) Create Python Virtual Environment (Laptop)

Run from INF2009-EdgeComputing-Project folder:

```powershell
python -m venv .venv-laptop
.\.venv-laptop\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r cp1_mqtt\requirements-laptop.txt
```

### A3) Create TLS Certificates (Local Test CA)

Run from INF2009-EdgeComputing-Project folder:

```powershell
New-Item -ItemType Directory -Force certs | Out-Null
Set-Location certs

# 1) CA
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -out ca.crt -subj "/CN=cp1-local-ca"

# 2) Broker server certificate
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=YOUR_LAPTOP_HOSTNAME"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 825 -sha256

# 3) Laptop subscriber client certificate
openssl genrsa -out laptop-client.key 2048
openssl req -new -key laptop-client.key -out laptop-client.csr -subj "/CN=laptop-subscriber"
openssl x509 -req -in laptop-client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out laptop-client.crt -days 825 -sha256

# 4) Pi publisher client certificate (create here, copy to Pi later)
openssl genrsa -out pi-client.key 2048
openssl req -new -key pi-client.key -out pi-client.csr -subj "/CN=pi-publisher"
openssl x509 -req -in pi-client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out pi-client.crt -days 825 -sha256

Set-Location ..
```

Important:
1. Replace YOUR_LAPTOP_HOSTNAME with your machine hostname used by Pi.
2. If you connect by IP and get hostname mismatch errors, use --insecure only for initial testing.

### A4) Configure Mosquitto TLS Broker

Create file mosquitto_tls.conf in INF2009-EdgeComputing-Project:

```conf
listener 8883
cafile certs/ca.crt
certfile certs/server.crt
keyfile certs/server.key

require_certificate true
use_identity_as_username true
allow_anonymous false
```

Start broker:

```powershell
mosquitto -c .\mosquitto_tls.conf -v
```

Keep this terminal open.

### A5) Run Laptop Subscriber

Open another terminal in INF2009-EdgeComputing-Project:

```powershell
.\.venv-laptop\Scripts\Activate.ps1
python .\cp1_mqtt\mqtt_tls_subscriber_laptop.py `
  --broker-host YOUR_LAPTOP_HOSTNAME_OR_IP `
  --broker-port 8883 `
  --topic edge/cp1/hello `
  --ca-cert .\certs\ca.crt `
  --client-cert .\certs\laptop-client.crt `
  --client-key .\certs\laptop-client.key
```

## Part B - Raspberry Pi Setup (Publisher)

### B1) Install Required Software

On Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

### B2) Copy Project Files To Pi

Copy at least:

1. cp1_mqtt/mqtt_tls_publisher_pi.py
2. cp1_mqtt/requirements-pi.txt
3. certs/ca.crt
4. certs/pi-client.crt
5. certs/pi-client.key

Suggested Pi layout:

```text
~/edge-project/
  cp1_mqtt/
  certs/
```

### B3) Create Python Virtual Environment (Pi)

```bash
cd ~/edge-project
python3 -m venv .venv-pi
source .venv-pi/bin/activate
pip install --upgrade pip
pip install -r cp1_mqtt/requirements-pi.txt
```

### B4) Run Pi Publisher

```bash
cd ~/edge-project
source .venv-pi/bin/activate
python3 cp1_mqtt/mqtt_tls_publisher_pi.py \
  --broker-host YOUR_LAPTOP_HOSTNAME_OR_IP \
  --broker-port 8883 \
  --topic edge/cp1/hello \
  --device-id pi-edge-01 \
  --interval-sec 2 \
  --count 20 \
  --ca-cert certs/ca.crt \
  --client-cert certs/pi-client.crt \
  --client-key certs/pi-client.key
```

## CP1 Success Criteria

CP1 is complete when all are true:

1. Pi publisher connects via TLS and publishes messages.
2. Laptop subscriber receives JSON messages on topic edge/cp1/hello.
3. Broker logs show successful TLS client connections.
4. 20/20 test messages received in one run.

## Troubleshooting

1. TLS handshake failed:
- Re-check cert paths and file permissions.
- Confirm server CN/hostname match.
- For initial smoke test only, run scripts with --insecure.

2. Connection refused:
- Verify broker is running on 8883.
- Check firewall allows inbound TCP 8883.

3. Subscriber receives nothing:
- Confirm topic names match exactly.
- Confirm both scripts point to same broker host and port.

4. Permission denied for key files:
- On Pi, run chmod 600 certs/pi-client.key.

## Next Step After CP1

Proceed to CP2 by adding:
1. event schema validation on subscriber/server side
2. dedup by event_id in local database
3. QoS and retry behavior test cases
