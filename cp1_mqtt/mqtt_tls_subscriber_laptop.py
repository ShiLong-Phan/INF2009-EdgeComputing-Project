import argparse
import json
import ssl
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CP1 laptop subscriber over MQTTS (TLS)."
    )
    parser.add_argument("--broker-host", required=True, help="MQTT broker host/IP")
    parser.add_argument("--broker-port", type=int, default=8883, help="MQTT broker TLS port")
    parser.add_argument("--topic", default="edge/cp1/hello", help="Topic to subscribe to")
    parser.add_argument("--ca-cert", required=True, help="Path to CA certificate PEM")
    parser.add_argument("--client-cert", required=True, help="Path to client certificate PEM")
    parser.add_argument("--client-key", required=True, help="Path to client private key PEM")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable hostname verification (testing only)",
    )
    return parser


def on_connect(client: mqtt.Client, _userdata, _flags, reason_code, _properties):
    if reason_code == 0:
        print("[SUB] Connected to broker.")
        client.subscribe(client.user_data_get()["topic"], qos=1)
    else:
        print(f"[SUB] Connect failed with reason code: {reason_code}")


def on_subscribe(_client, _userdata, _mid, granted_qos, _properties):
    print(f"[SUB] Subscription acknowledged with QoS: {granted_qos}")


def on_message(_client, _userdata, msg: mqtt.MQTTMessage):
    payload = msg.payload.decode("utf-8", errors="replace")
    ts = datetime.now(timezone.utc).isoformat()
    print("-" * 72)
    print(f"[SUB] Received @ {ts}")
    print(f"[SUB] Topic: {msg.topic}")
    print(f"[SUB] QoS: {msg.qos}")
    print(f"[SUB] Payload: {payload}")

    try:
        parsed = json.loads(payload)
        event_id = parsed.get("event_id")
        device_id = parsed.get("device_id")
        print(f"[SUB] Parsed event_id={event_id}, device_id={device_id}")
    except json.JSONDecodeError:
        print("[SUB] Payload is not JSON (this is acceptable for simple tests).")


def main() -> None:
    args = build_parser().parse_args()

    user_data = {"topic": args.topic}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, userdata=user_data)

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

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
        f"[SUB] Connecting to {args.broker_host}:{args.broker_port} | topic={args.topic}"
    )
    client.connect(args.broker_host, args.broker_port, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
