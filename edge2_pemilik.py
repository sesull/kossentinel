"""KosSentinel - Edge 2 (HP pemilik).

Menerima alert (getar + notifikasi), menampilkan balasan status dari
Edge 1, mengirim perintah, dan mengirim data sensor sendiri.

Jalankan:  py edge2_pemilik.py              (HP Android + Termux)
           py edge2_pemilik.py --simulator  (tanpa HP, hanya cetak di layar)

Saat jalan: ketik  s + Enter = minta status
                   r + Enter = reset belajar
                   q + Enter = keluar
"""
import argparse
import json
import math
import random
import subprocess
import threading
import time

import paho.mqtt.client as mqtt

PROJECT_ID = "kossentinel_rsn_2026"
# ====================================================================

BROKER = "broker.hivemq.com"
PORT = 8883

T_ALERTS = f"kossentinel/{PROJECT_ID}/alerts"
T_REPLY = f"kossentinel/{PROJECT_ID}/edge1/reply"
T_COMMAND = f"kossentinel/{PROJECT_ID}/edge1/command"
T_EDGE2 = f"kossentinel/{PROJECT_ID}/edge2/telemetry"

GRAVITY = 9.81
SIMULATOR = False


def run_quiet(cmd):
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[!] gagal menjalankan {cmd[0]}: {e}")


def notify(title, text):
    if SIMULATOR:
        print(f"[NOTIFIKASI] {title}: {text}  (HP bergetar)")
        return
    run_quiet(["termux-vibrate", "-d", "700"])
    run_quiet(["termux-notification", "--title", title, "--content", text])


def on_connect(client, userdata, flags, reason_code, properties):
    print(f"[MQTT] terhubung ({reason_code})")
    client.subscribe([(T_ALERTS, 1), (T_REPLY, 1)])


def on_message(client, userdata, msg):
    try:
        d = json.loads(msg.payload.decode())
    except Exception:
        return
    if msg.topic == T_ALERTS:
        print(f"[ALERT] {d.get('status')} skor={d.get('score')} - {d.get('message')}")
        notify(f"KosSentinel: {d.get('status')}", d.get("message", ""))
    elif msg.topic == T_REPLY:
        print(f"[BALASAN STATUS] {d.get('status')} | getaran={d.get('accel_rms')} "
              f"z={d.get('z_accel')} | cahaya={d.get('light')} z={d.get('z_light')}")


def read_vibration():
    """Getaran HP pemilik (0 kalau diam). Simulator: acak kecil."""
    if SIMULATOR:
        return abs(random.gauss(0, 0.05))
    try:
        out = subprocess.run(["termux-sensor", "-s", "accelerometer", "-n", "1"],
                             capture_output=True, text=True, timeout=15)
        data = json.loads(out.stdout)
        x, y, z = next(iter(data.values()))["values"][:3]
        return abs(math.sqrt(x * x + y * y + z * z) - GRAVITY)
    except Exception:
        return 0.0


def telemetry_loop(client):
    seq = 0
    while True:
        seq += 1
        vib = read_vibration()
        payload = {
            "device_id": "edge2",
            "sequence": seq,
            "vibration": round(vib, 3),
            "held": vib > 0.5,  # tanda HP sedang dipegang
            "ts": int(time.time()),
        }
        client.publish(T_EDGE2, json.dumps(payload), qos=0)
        time.sleep(2)


def main():
    global SIMULATOR
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulator", action="store_true")
    SIMULATOR = ap.parse_args().simulator

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{PROJECT_ID}-edge2-{random.randint(1000, 9999)}",
    )
    client.tls_set()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, PORT, 60)
    client.loop_start()
    threading.Thread(target=telemetry_loop, args=(client,), daemon=True).start()

    print("Edge 2 jalan.  s = minta status | r = reset belajar | q = keluar")
    while True:
        try:
            key = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            break
        if key == "s":
            client.publish(T_COMMAND, json.dumps({"cmd": "status"}), qos=1)
            print("[CMD] minta status dikirim")
        elif key == "r":
            client.publish(T_COMMAND, json.dumps({"cmd": "reset_learning"}), qos=1)
            print("[CMD] reset belajar dikirim")
        elif key == "q":
            break
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()