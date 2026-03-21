# CP1 Setup Guide: MQTTS Between Raspberry Pi and Laptop/Desktop

Purpose: implement Checkpoint 1 (secure MQTT connectivity) with TLS-enabled publish/subscribe between Raspberry Pi (edge) and Laptop/Desktop (broker + subscriber).

This runbook is updated for repeated demos and multiple Pis.

## Folder Contents For CP1

- cp1_mqtt/mqtt_tls_subscriber_laptop.py
- cp1_mqtt/mqtt_tls_publisher_pi.py
- cp1_mqtt/requirements-laptop.txt
- cp1_mqtt/requirements-pi.txt
- cp1_mqtt/mosquitto_tls.conf.sample

## Architecture For CP1

1. Laptop/Desktop runs Mosquitto broker with TLS on port 8883.
2. Laptop/Desktop also runs subscriber script.
3. Raspberry Pi runs publisher script and sends test JSON events.

## Important Identity Rule (Prevents Most TLS Errors)

Use hostname for broker connection, not raw IP.

Reason:
1. TLS certificate is issued to server identity (hostname/SAN).
2. If you connect by IP but cert only has hostname, verification fails.

Recommended convention:
1. Laptop hostname: DOMCOM2
2. Broker host used by scripts: DOMCOM2
3. Pi resolves DOMCOM2 to current laptop IP (via mDNS or hosts mapping)

## Part A - Laptop/Desktop Setup (Broker + Subscriber)

### A1) Install Required Software

Windows (PowerShell):

1. Install Python 3.10+.
2. Install OpenSSL.
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

### A3) Create TLS Certificates (CA + Server + Clients)

Run from INF2009-EdgeComputing-Project folder:

```powershell
New-Item -ItemType Directory -Force certs | Out-Null
Set-Location certs

# 1) CA
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -out ca.crt -subj "/CN=cp1-local-ca"

# 2) Broker server cert with SAN (hostname + current IP)
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=DOMCOM2"
Set-Content -Path server.ext -Value "subjectAltName=DNS:DOMCOM2,IP:192.168.1.232`nextendedKeyUsage=serverAuth"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 825 -sha256 -extfile server.ext

# 3) Laptop subscriber client cert
openssl genrsa -out laptop-client.key 2048
openssl req -new -key laptop-client.key -out laptop-client.csr -subj "/CN=laptop-subscriber"
Set-Content -Path laptop-client.ext -Value "extendedKeyUsage=clientAuth"
openssl x509 -req -in laptop-client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out laptop-client.crt -days 825 -sha256 -extfile laptop-client.ext

# 4) First Pi client cert
openssl genrsa -out pi-client.key 2048
openssl req -new -key pi-client.key -out pi-client.csr -subj "/CN=pi-publisher"
Set-Content -Path pi-client.ext -Value "extendedKeyUsage=clientAuth"
openssl x509 -req -in pi-client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out pi-client.crt -days 825 -sha256 -extfile pi-client.ext

Set-Location ..
```

Notes:
1. Replace 192.168.1.232 with your current laptop IP before generating server.crt.
2. If laptop IP changes often, regenerate only server.crt/server.key/server.csr/server.ext.
3. Keep ca.key on laptop only. Do not copy ca.key to Pi.

### A4) Configure Mosquitto TLS Broker

Use cp1_mqtt/mosquitto_tls.conf.sample or copy it to mosquitto_tls.conf.

Config content:

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
mosquitto -c mosquitto_tls.conf -v
```

Keep this terminal open.

### A5) Run Laptop Subscriber

Open another terminal in INF2009-EdgeComputing-Project:

```powershell
.\.venv-laptop\Scripts\Activate.ps1
python .\cp1_mqtt\mqtt_tls_subscriber_laptop.py 
  --broker-host DOMCOM2 
  --broker-port 8883 
  --topic edge/cp1/hello 
  --ca-cert .\certs\ca.crt 
  --client-cert .\certs\laptop-client.crt 
  --client-key .\certs\laptop-client.key
```

### A6) Windows Firewall Rule (Required)

Run once in admin PowerShell:

```powershell
netsh advfirewall firewall add rule name="MQTTS 8883" dir=in action=allow protocol=TCP localport=8883
```

## Part B - Raspberry Pi Setup (Publisher)

### B1) Install Required Software

On Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip netcat-openbsd
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
chmod 600 certs/pi-client.key
```

### B4) Ensure Pi Can Resolve Laptop Hostname

Option 1 (preferred if available): mDNS / local DNS.

Option 2 (always works): add hosts mapping.

```bash
echo "192.168.1.232 DOMCOM2" | sudo tee -a /etc/hosts
getent hosts DOMCOM2
```

Update IP in /etc/hosts whenever network changes.

### B5) Connectivity Smoke Test

```bash
nc -vz DOMCOM2 8883
```

### B6) Run Pi Publisher

```bash
cd ~/edge-project
source .venv-pi/bin/activate
python3 cp1_mqtt/mqtt_tls_publisher_pi.py \
  --broker-host DOMCOM2 \
  --broker-port 8883 \
  --topic edge/cp1/hello \
  --device-id pi-edge-01 \
  --interval-sec 2 \
  --count 20 \
  --ca-cert certs/ca.crt \
  --client-cert certs/pi-client.crt \
  --client-key certs/pi-client.key
```

## Multi-Pi Expansion (Recommended)

Issue one client cert per Pi to keep identity clean.

On laptop in certs folder, repeat for each Pi (example pi-edge-02):

```powershell
openssl genrsa -out pi-edge-02.key 2048
openssl req -new -key pi-edge-02.key -out pi-edge-02.csr -subj "/CN=pi-edge-02"
Set-Content -Path pi-edge-02.ext -Value "extendedKeyUsage=clientAuth"
openssl x509 -req -in pi-edge-02.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out pi-edge-02.crt -days 825 -sha256 -extfile pi-edge-02.ext
```

Copy to each Pi as certs/pi-client.crt and certs/pi-client.key or keep unique names and update publisher command accordingly.

## Hotspot and Demo Mode

When switching networks:
1. Check laptop IP.
2. Update Pi /etc/hosts mapping for DOMCOM2.
3. If IP changed and you use server cert SAN with IP, regenerate server cert SAN.

Fallback for urgent demo only:
1. Add --insecure to publisher/subscriber.
2. This keeps encryption but skips hostname verification.
3. Remove --insecure after demo.

## CP1 Success Criteria

CP1 is complete when all are true:

1. Pi publisher connects via TLS and publishes messages.
2. Laptop subscriber receives JSON messages on topic edge/cp1/hello.
3. Broker logs show successful TLS client connections.
4. 20/20 test messages received in one run.

## Troubleshooting

1. Name or service not known:
- Pi cannot resolve broker hostname.
- Fix /etc/hosts or DNS/mDNS.

2. CERTIFICATE_VERIFY_FAILED with IP mismatch:
- You connected by IP but cert identity is hostname.
- Connect by hostname or regenerate server cert with SAN IP.

3. Broker log says bad certificate:
- Pi client cert not trusted by broker CA, wrong key/cert pair, or stale files copied.
- Verify cert chain and key modulus match on Pi.

4. Connection refused:
- Broker not running or firewall blocks 8883.

5. Permission denied for key file:
- Run chmod 600 certs/pi-client.key on Pi.

## Next Step After CP1

Proceed to CP2 by adding:
1. event schema validation on subscriber/server side
2. dedup by event_id in local database
3. QoS and retry behavior test cases
