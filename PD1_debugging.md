# PD1 Debugging Log (CP1 MQTTS Setup)

Purpose: document setup issues encountered during CP1 and their fixes, so future Pi onboarding and demo prep are faster.

## Scope

- Phase: PD1 / CP1 (basic secure MQTTS connectivity)
- Topology: Raspberry Pi publisher -> laptop Mosquitto broker -> laptop subscriber
- Transport: MQTT over TLS (mTLS enabled)

## Issue 1: Pi could not connect using laptop hostname

### Symptom

Pi publisher failed with:
- socket.gaierror: [Errno -2] Name or service not known

### Root Cause

Pi could not resolve laptop hostname (DOMCOM2) to an IP address.

### Fix

1. Use laptop IP temporarily to verify path.
2. Add hostname mapping on Pi:

```bash
echo "<LAPTOP_IP> DOMCOM2" | sudo tee -a /etc/hosts
getent hosts DOMCOM2
```

3. Run publisher with --broker-host DOMCOM2.

### Prevention

For each new network (router/hotspot), update /etc/hosts on Pi with current laptop IP.

## Issue 2: TLS failed when using laptop IP directly

### Symptom

Pi publisher failed with:
- ssl.SSLCertVerificationError
- certificate verify failed: IP address mismatch

### Root Cause

Connection was made to IP, but broker server certificate identity was hostname-based.

### Fix

1. Prefer hostname-based connection:
- use broker host DOMCOM2
- ensure Pi resolves DOMCOM2

2. Optional stronger fix:
- regenerate server cert with SAN including both hostname and current IP.

### Prevention

Always connect using the same identity used in server certificate (recommended: hostname).

## Issue 3: Broker reported bad certificate

### Symptom

Mosquitto log showed:
- ssl/tls alert bad certificate
- client disconnected: protocol error

### Root Cause Candidates

1. Client cert not signed by the CA loaded by broker.
2. Client cert and private key mismatch.
3. Stale cert files copied to Pi.

### Verification Steps Used

On Pi:

```bash
openssl x509 -in certs/pi-client.crt -noout -subject -issuer -dates
openssl verify -CAfile certs/ca.crt certs/pi-client.crt
openssl x509 -in certs/pi-client.crt -noout -modulus | openssl md5
openssl rsa -in certs/pi-client.key -noout -modulus | openssl md5
```

On laptop and Pi, compare CA fingerprint:

```bash
openssl x509 -in certs/ca.crt -noout -fingerprint -sha256
```

### Outcome

Cert chain and key match checks passed. Final blocker was identity usage (IP vs hostname), resolved by hostname mapping + hostname-based broker connection.

## Issue 4: Confusion about key regeneration on network changes

### Question

Do all certs/keys need regeneration when switching to hotspot/school network?

### Answer

No, usually not.

- Client cert/key pairs can be reused across networks.
- CA cert can be reused.
- Regenerate server cert only if certificate identity requirements change (for example, connecting by IP not covered in SAN).

### Practical Demo Strategy

1. Keep broker host as DOMCOM2.
2. Update Pi /etc/hosts with current laptop IP.
3. Reuse existing CA and client certs.

## Issue 5: Potential firewall blind spot

### Symptom

Ping worked, but MQTT connection can still fail.

### Root Cause

ICMP allow rule is not the same as TCP 8883 allow rule.

### Fix

On laptop (admin PowerShell):

```powershell
netsh advfirewall firewall add rule name="MQTTS 8883" dir=in action=allow protocol=TCP localport=8883
```

## Working Baseline Commands

### Laptop Broker

```powershell
mosquitto -c .\cp1_mqtt\mosquitto_tls.conf.sample -v
```

### Laptop Subscriber

```powershell
python .\cp1_mqtt\mqtt_tls_subscriber_laptop.py `
  --broker-host DOMCOM2 `
  --broker-port 8883 `
  --topic edge/cp1/hello `
  --ca-cert .\certs\ca.crt `
  --client-cert .\certs\laptop-client.crt `
  --client-key .\certs\laptop-client.key
```

### Pi Publisher

```bash
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

## CP1 Exit Confirmation Checklist

1. Broker starts and listens on 8883.
2. Subscriber connects and subscribes to edge/cp1/hello.
3. Pi publisher connects using TLS and sends 20 events.
4. Subscriber receives all events.
5. No TLS errors in broker logs.

## Notes For Next Device (New Pi)

1. Generate unique client cert/key per Pi.
2. Copy only ca.crt + that Pi cert/key to the Pi.
3. Set file permission on key: chmod 600.
4. Update /etc/hosts mapping for current network.
