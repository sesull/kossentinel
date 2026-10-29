"""KosSentinel - Server (kepala keamanan, jalan di laptop).

Baca telemetri Edge 1 -> hitung skor risiko 0-100 -> kirim alert
ke pemilik hanya saat status BERUBAH.

Jalankan:  py server_kossentinel.py
"""
import json
import random
import time

import paho.mqtt.client as mqtt

PROJECT_ID = "kossentinel_rsn_2026"
# ====================================================================

BROKER = "broker.hivemq.com"
PORT = 8883

T_TELEMETRY = f"kossentinel/{PROJECT_ID}/edge1/telemetry"
T_ALERTS = f"kossentinel/{PROJECT_ID}/alerts"

W_ACCEL = 60        # bobot getaran
W_LIGHT = 40        # bobot cahaya
Z_MAX = 3.0         # z-score yang dianggap "penuh"
SCORE_WASPADA = 30  # skor >= ini -> WASPADA
SCORE_ANOMALI = 55  # skor >= ini -> ANOMALI

last_status = "NORMAL"


def risk_score(z_accel, z_light):
    a = min(abs(z_accel) / Z_MAX, 1.0)
    l = min(abs(z_light) / Z_MAX, 1.0)
    return round(W_ACCEL * a + W_LIGHT * l)


def status_from_score(score):
    if score >= SCORE_ANOMALI:
        return "ANOMALI"
    if score >= SCORE_WASPADA:
        return "WASPADA"
    return "NORMAL"


def on_connect(client, userdata, flags, reason_code, properties):
    print(f"[MQTT] server terhubung ({reason_code})")
    client.subscribe(T_TELEMETRY, qos=1)


def on_message(client, userdata, msg):
    global last_status
    try:
        d = json.loads(msg.payload.decode())
    except Exception:
        return

    if d.get("status") == "LEARNING":
        last_status = "NORMAL"
        print(f"#{d.get('sequence')} Edge 1 sedang belajar...")
        return

    score = risk_score(d.get("z_accel", 0), d.get("z_light", 0))
    status = status_from_score(score)
    print(f"#{d.get('sequence')} skor={score:3d} {status:8s} "
          f"(z_getar={d.get('z_accel')}, z_cahaya={d.get('z_light')})")

    if status != last_status:
        if status == "NORMAL":
            text = "Kamar kembali normal."
        else:
            text = f"{status}! Ada yang tidak biasa di kamar (skor {score})."
        alert = {
            "status": status,
            "score": score,
            "message": text,
            "z_accel": d.get("z_accel"),
            "z_light": d.get("z_light"),
            "ts": int(time.time()),
        }
        client.publish(T_ALERTS, json.dumps(alert), qos=1)
        print(f"[ALERT] {text}")
        last_status = status


def main():
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{PROJECT_ID}-server-{random.randint(1000, 9999)}",
    )
    client.tls_set()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, PORT, 60)
    print("Server jalan. Tekan Ctrl+C untuk berhenti.")
    client.loop_forever()


if __name__ == "__main__":
    main()