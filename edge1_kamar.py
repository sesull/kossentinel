"""KosSentinel - Edge 1 (HP kamar / "satpam").

Baca sensor tiap detik -> moving average -> belajar 30 detik -> z-score
-> kirim ke cloud lewat MQTT. Mendengarkan perintah: status, reset_learning.

Jalankan:  py edge1_kamar.py              (HP Android + Termux)
           py edge1_kamar.py --simulator  (tanpa HP, sensor palsu)
"""
import argparse
import json
import math
import random
import statistics
import subprocess
import time
from collections import deque

import paho.mqtt.client as mqtt

PROJECT_ID = "kossentinel_rsn_2026"
# ====================================================================

BROKER = "broker.hivemq.com"
PORT = 8883
DEVICE_ID = "edge1"

T_TELEMETRY = f"kossentinel/{PROJECT_ID}/edge1/telemetry"
T_COMMAND = f"kossentinel/{PROJECT_ID}/edge1/command"
T_REPLY = f"kossentinel/{PROJECT_ID}/edge1/reply"

WINDOW = 5            # jendela moving average
LEARN_SECONDS = 30    # lama fase belajar
MIN_STD_ACCEL = 0.02  # batas minimal simpangan baku getaran
MIN_STD_LIGHT = 5.0   # batas minimal simpangan baku cahaya
Z_WASPADA = 2.0
Z_ANOMALI = 3.0
GRAVITY = 9.81

pending = deque()  # perintah masuk dari MQTT


# ---------------------------------------------------------------- MQTT
def on_connect(client, userdata, flags, reason_code, properties):
    print(f"[MQTT] terhubung ({reason_code})")
    client.subscribe(T_COMMAND, qos=1)


def on_message(client, userdata, msg):
    try:
        cmd = json.loads(msg.payload.decode()).get("cmd")
    except Exception:
        return
    if cmd in ("status", "reset_learning"):
        print(f"[CMD] perintah masuk: {cmd}")
        pending.append(cmd)


def make_client():
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{PROJECT_ID}-edge1-{random.randint(1000, 9999)}",
    )
    client.tls_set()  # TLS dengan sertifikat bawaan sistem
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, PORT, 60)
    client.loop_start()  # thread terpisah
    return client


# ------------------------------------------------------------- Sensor
def find_sensors():
    out = subprocess.run(["termux-sensor", "-l"], capture_output=True,
                         text=True, timeout=15)
    names = json.loads(out.stdout).get("sensors", [])
    accel = next((n for n in names if "accelerometer" in n.lower()), None)
    light = next((n for n in names if "light" in n.lower()), None)
    if not accel or not light:
        raise SystemExit("Sensor accelerometer/light tidak ditemukan di HP ini.")
    return accel, light


def read_real(accel_name, light_name):
    out = subprocess.run(
        ["termux-sensor", "-s", f"{accel_name},{light_name}", "-n", "1"],
        capture_output=True, text=True, timeout=15)
    data = json.loads(out.stdout)
    x, y, z = data[accel_name]["values"][:3]
    lux = data[light_name]["values"][0]
    return x, y, z, lux


def read_sim(t):
    """Sensor palsu. Setelah fase belajar, gangguan muncul berkala."""
    x, y, z = (random.gauss(0, 0.02), random.gauss(0, 0.02),
               GRAVITY + random.gauss(0, 0.02))
    lux = 120 + random.gauss(0, 1.5)
    phase = (t - LEARN_SECONDS) % 40
    if t > LEARN_SECONDS and 10 <= phase < 18:  # gangguan 8 detik
        x += random.uniform(-2, 2)
        y += random.uniform(-2, 2)
        z += random.uniform(-2, 2)
        lux += 400
    return x, y, z, lux


# -------------------------------------------------------------- Model
class Learner:
    def __init__(self, min_std):
        self.min_std = min_std
        self.reset()

    def reset(self):
        self.samples, self.mean, self.std = [], None, None

    @property
    def ready(self):
        return self.mean is not None

    def finish(self):
        self.mean = statistics.fmean(self.samples)
        self.std = max(statistics.pstdev(self.samples), self.min_std)

    def z(self, v):
        return abs((v - self.mean) / self.std)


def status_from_z(z):
    if z >= Z_ANOMALI:
        return "ANOMALI"
    if z >= Z_WASPADA:
        return "WASPADA"
    return "NORMAL"


# --------------------------------------------------------------- Main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulator", action="store_true")
    args = ap.parse_args()

    if args.simulator:
        reader = None
    else:
        accel_name, light_name = find_sensors()
        reader = lambda t: read_real(accel_name, light_name)  # noqa: E731
    if reader is None:
        reader = read_sim

    client = make_client()

    ma_a, ma_l = deque(maxlen=WINDOW), deque(maxlen=WINDOW)
    lrn_a, lrn_l = Learner(MIN_STD_ACCEL), Learner(MIN_STD_LIGHT)
    start = time.time()
    learn_start = start
    seq = 0
    last_payload = None
    print(f"Learning... {LEARN_SECONDS}s (jangan sentuh HP)")

    while True:
        loop_start = time.time()
        x, y, z, lux = reader(loop_start - start)

        vib = abs(math.sqrt(x * x + y * y + z * z) - GRAVITY)
        ma_a.append(vib)
        ma_l.append(lux)
        sa = sum(ma_a) / len(ma_a)
        sl = sum(ma_l) / len(ma_l)

        want_status = False
        while pending:
            cmd = pending.popleft()
            if cmd == "reset_learning":
                lrn_a.reset()
                lrn_l.reset()
                learn_start = time.time()
                print(f"Learning... {LEARN_SECONDS}s (diulang)")
            elif cmd == "status":
                want_status = True

        if not lrn_a.ready:
            lrn_a.samples.append(sa)
            lrn_l.samples.append(sl)
            za = zl = 0.0
            status = "LEARNING"
            if time.time() - learn_start >= LEARN_SECONDS:
                lrn_a.finish()
                lrn_l.finish()
                print(f"Selesai belajar: getaran {lrn_a.mean:.3f}±{lrn_a.std:.3f}, "
                      f"cahaya {lrn_l.mean:.1f}±{lrn_l.std:.1f}")
        else:
            za, zl = lrn_a.z(sa), lrn_l.z(sl)
            status = status_from_z(max(za, zl))

        seq += 1
        payload = {
            "device_id": DEVICE_ID,
            "sequence": seq,
            "accel_rms": round(sa, 4),
            "light": round(sl, 2),
            "z_accel": round(za, 2),
            "z_light": round(zl, 2),
            "status": status,
            "ts": int(time.time()),
        }
        last_payload = payload
        client.publish(T_TELEMETRY, json.dumps(payload), qos=1)
        print(f"#{seq} {status:8s} getaran={sa:.3f} z={za:.1f} | "
              f"cahaya={sl:.1f} z={zl:.1f}")

        if want_status and last_payload:
            reply = dict(last_payload, reply_to="status")
            client.publish(T_REPLY, json.dumps(reply), qos=1)
            print("[REPLY] status dikirim")

        time.sleep(max(0, 1.0 - (time.time() - loop_start)))


if __name__ == "__main__":
    main()