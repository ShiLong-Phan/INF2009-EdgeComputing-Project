import argparse
import json
import ssl
import time
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CP1 Raspberry Pi publisher over MQTTS (TLS)."
    )
    parser.add_argument("--broker-host", required=True, help="MQTT broker host/IP")
    parser.add_argument("--broker-port", type=int, default=8883, help="MQTT broker TLS port")
    parser.add_argument("--topic", default="edge/cp1/hello", help="Topic to publish to")
    parser.add_argument("--device-id", default="pi-edge-01", help="Publisher device identifier")
    parser.add_argument("--interval-sec", type=float, default=2.0, help="Publish interval")
    parser.add_argument("--count", type=int, default=0, help="Number of messages (0 = infinite)")
    parser.add_argument("--ca-cert", required=True, help="Path to CA certificate PEM")
    parser.add_argument("--client-cert", required=True, help="Path to client certificate PEM")
    parser.add_argument("--client-key", required=True, help="Path to client private key PEM")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable hostname verification (testing only)",
    )
    return parser


def on_connect(_client, _userdata, _flags, reason_code, _properties):
    if reason_code == 0:
        print("[PUB] Connected to broker.")
    else:
        print(f"[PUB] Connect failed with reason code: {reason_code}")


def on_publish(_client, _userdata, mid, _reason_code, _properties):
    print(f"[PUB] Publish acknowledged | mid={mid}")


def make_payload(device_id: str, sequence: int) -> str:
    payload = {
        "event_id": str(uuid.uuid4()),
        "device_id": device_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "cp1-connectivity-test",
        "sequence": sequence,
        "message": "hello-from-pi",
    }
    return json.dumps(payload)


def main() -> None:
    args = build_parser().parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_publish = on_publish

    client.tls_set(
        ca_certs=args.ca_cert,
        certfile=args.client_cert,
        keyfile=args.client_key,
        cert_reqs=ssl.CERT_REQUIRED,
        tls_version=ssl.PROTOCOL_TLS_CLIENT,
    )
    if args.insecure:
        client.tls_insecure_set(True)

    print(
        f"[PUB] Connecting to {args.broker_host}:{args.broker_port} | topic={args.topic}"
    )
    client.connect(args.broker_host, args.broker_port, keepalive=60)
    client.loop_start()

    sent = 0
    sequence = 1

    try:
        while True:
            payload = make_payload(args.device_id, sequence)
            result = client.publish(args.topic, payload=payload, qos=1, retain=False)
            print(f"[PUB] Sent sequence={sequence} | mid={result.mid}")

            sent += 1
            sequence += 1
            if args.count > 0 and sent >= args.count:
                break
            time.sleep(args.interval_sec)
    except KeyboardInterrupt:
        print("\n[PUB] Stopped by user.")
    finally:
        client.loop_stop()
        client.disconnect()
        print(f"[PUB] Total sent: {sent}")


if __name__ == "__main__":
    main()
