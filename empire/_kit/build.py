#!/usr/bin/env python3
"""Render a studio video: scene.html + timeline -> 1080x1920 MP4 with synthesized sound design.

usage: python3 empire/_kit/build.py empire/videos/<id> [--preview 1.0,5.2,...] [--fps 30]
- If vo/timeline.json exists (from voice.py) the video follows the narration.
- Otherwise it builds a reading-pace timeline (text-on-screen version) and no narration.
Everything audible is synthesized here (numpy), so there are no licensing questions.
"""
import json, os, subprocess, sys, wave
import numpy as np

SR = 48000


def reading_timeline(sc):
    t, out = 0.4, []
    for ln in sc["lines"]:
        ws = ln["text"].split()
        per = 0.30
        words = [{"w": w, "s": round(t + 0.25 + i * per, 3), "e": round(t + 0.25 + (i + 1) * per, 3)} for i, w in enumerate(ws)]
        d = 0.25 + len(ws) * per + 1.25  # hold after the last word so it can be read
        if ln["scene"] in ("burst",): d += 0.6
        out.append({**ln, "start": round(t, 3), "end": round(t + d, 3), "words": words})
        t += d
    return {"duration": round(t + 2.0, 3), "lines": out}


# ---------- sound design ----------
def env(n, a, r):
    e = np.ones(n); a = min(int(a * SR), n); r = min(int(r * SR), n)
    if a: e[:a] = np.linspace(0, 1, a)
    if r: e[-r:] *= np.linspace(1, 0, r) ** 2
    return e


def lpf(x, cutoff):
    from numpy.fft import rfft, irfft, rfftfreq
    X = rfft(x); f = rfftfreq(len(x), 1 / SR); X[f > cutoff] *= np.exp(-(f[f > cutoff] - cutoff) / (cutoff * .5))
    return irfft(X, len(x))


rng = np.random.default_rng(5)


def boom(d=2.5):
    n = int(d * SR); t = np.arange(n) / SR
    s = np.sin(2 * np.pi * (55 * np.exp(-t * 2.2) + 28) * t) * np.exp(-t * 1.6)
    s += lpf(rng.standard_normal(n), 400) * np.exp(-t * 3) * 1.6
    s += lpf(rng.standard_normal(n), 3000) * np.exp(-t * 18) * .6
    return s / np.abs(s).max()


def whoosh(d=0.7):
    n = int(d * SR); t = np.linspace(0, 1, n)
    x = rng.standard_normal(n); shape = np.sin(np.pi * t) ** 2
    lo = lpf(x, 900); hi = lpf(x, 3500)
    s = (lo * (1 - t) + hi * t) * shape
    return s / np.abs(s).max()


def rumble(d):
    n = int(d * SR); x = lpf(rng.standard_normal(n), 140); t = np.arange(n) / SR
    x *= (1 + .4 * np.sin(2 * np.pi * 3 * t)); x *= env(n, .4, .8)
    return x / np.abs(x).max()


def creak(d=1.6):
    n = int(d * SR); t = np.arange(n) / SR
    f = 90 + 40 * np.sin(2 * np.pi * .7 * t) + 15 * np.sin(2 * np.pi * 5 * t)
    ph = 2 * np.cumsum(np.pi * f / SR)
    s = np.sign(np.sin(ph)) * (np.sin(ph * 3.01) * .5 + .5)
    s = lpf(s, 1800) * env(n, .2, .5)
    return s / np.abs(s).max()


def tick():
    n = int(.03 * SR); t = np.arange(n) / SR
    return np.sin(2 * np.pi * 2200 * t) * np.exp(-t * 220)


def thud():
    n = int(.6 * SR); t = np.arange(n) / SR
    s = np.sin(2 * np.pi * (120 * np.exp(-t * 9) + 50) * t) * np.exp(-t * 9) + lpf(rng.standard_normal(n), 1200) * np.exp(-t * 40) * .5
    return s / np.abs(s).max()


def drip():
    n = int(.25 * SR); t = np.arange(n) / SR
    return np.sin(2 * np.pi * (700 + 900 * t / .25) * t) * np.exp(-t * 28)


def drone(d, key=(55.0, 65.4, 82.4)):
    n = int(d * SR); t = np.arange(n) / SR; s = np.zeros(n)
    for i, f in enumerate(key):
        s += np.sin(2 * np.pi * f * t + i) * (0.6 + 0.4 * np.sin(2 * np.pi * (0.05 + i * .03) * t))
        s += .3 * np.sin(2 * np.pi * f * 2.003 * t)
    s += lpf(rng.standard_normal(n), 250) * .4
    return s / np.abs(s).max()


def pulse(d, bpm=72):
    n = int(d * SR); s = np.zeros(n); b = 60 / bpm; k = thud() * .8
    for i in range(int(d / b)):
        a = int(i * b * SR)
        s[a:a + len(k)] += k[:max(0, min(len(k), n - a))]
    return s


def place(buf, clip, at, gain):
    a = int(at * SR)
    if a >= len(buf): return
    m = min(len(clip), len(buf) - a); buf[a:a + m] += clip[:m] * gain


def sound(tl, out, narration=None):
    D = tl["duration"]; n = int(D * SR); mus = np.zeros(n); fx = np.zeros(n)
    L = {l["scene"]: l for l in tl["lines"]}
    # music bed: drone the whole way, a heartbeat pulse that enters with the tank and stops at the burst
    mus += drone(D) * .22 * env(n, 1.5, 3)
    pa, pb = L["tank"]["start"], L["burst"]["start"]
    bw = next((w["s"] for w in L["burst"]["words"] if w["w"].startswith("انفجر")), pb)
    p = pulse(pb - pa, 76) * env(int((pb - pa) * SR), 2, .2); place(mus, p, pa, .35)
    pc = L["wave"]["start"]; p2 = pulse(L["toll"]["start"] - pc, 96); place(mus, p2, pc, .3)
    # silence before the burst (drop) for impact
    a, b = int((bw - .35) * SR), int((bw + .02) * SR); mus[a:b] *= np.linspace(1, 0, b - a)
    mus[b:b + int(1.2 * SR)] *= np.linspace(0, 1, int(1.2 * SR))
    # SFX
    for l in tl["lines"][1:]:
        place(fx, whoosh(), l["start"] - .35, .22)
    place(fx, rumble(L["hook"]["end"] - L["hook"]["start"] + .5), L["hook"]["start"] + .5, .35)
    place(fx, thud(), L["reveal"]["start"] + 1.0, .5)
    for i in range(10): place(fx, tick(), L["tank"]["start"] + .4 + i * .32, .25)
    place(fx, creak(), L["leak"]["start"] + .2, .35)
    for i in range(6): place(fx, drip(), L["leak"]["start"] + .6 + i * .55, .2)
    place(fx, boom(), bw - .03, 1.0)
    place(fx, rumble(L["wave"]["end"] - pc + .5), pc, .7)
    for i in range(5): place(fx, drip(), L["trap"]["start"] + .8 + i * .9, .25)
    for i in range(21): place(fx, tick(), L["toll"]["start"] + .2 + i * .09, .2)
    place(fx, thud(), L["verdict"]["start"] + (L["verdict"]["end"] - L["verdict"]["start"]) * .52, .9)
    if narration is not None:
        # duck music and effects under the voice: smooth speech envelope -> gain 0.35 while talking, 1.0 in pauses
        v = np.zeros(n); m = min(n, len(narration)); v[:m] = np.abs(narration[:m]).mean(1)
        k = int(.25 * SR); sp = np.convolve(v, np.ones(k) / k, "same"); sp = np.clip(sp / (np.percentile(sp[sp > 1e-4], 60) + 1e-9), 0, 1)
        duck = 1 - .65 * np.convolve(sp, np.ones(k) / k, "same")
        mix = mus * duck * .8 + fx * (.45 + .55 * duck)
    else:
        mix = mus + fx * .8
    st = np.stack([mix, mix], 1)
    if narration is not None:
        st = st / max(1e-9, np.abs(st).max()) * .35  # bed sits well under the voice
        st[:m] += narration[:m] * .9
    st = st / max(1e-9, np.abs(st).max()) * .89
    with wave.open(out, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((st * 32767).astype("<i2").tobytes())


def read_wav(p):
    with wave.open(p) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, w.getnchannels()).astype(float) / 32768
    return a


def main():
    folder = os.path.abspath(sys.argv[1]); args = sys.argv[2:]
    fps = int(args[args.index("--fps") + 1]) if "--fps" in args else 30
    preview = [float(x) for x in args[args.index("--preview") + 1].split(",")] if "--preview" in args else None
    sc = json.load(open(os.path.join(folder, "script.json"), encoding="utf-8"))
    vtl = os.path.join(folder, "vo", "timeline.json")
    voiced = os.path.exists(vtl)
    tl = json.load(open(vtl, encoding="utf-8")) if voiced else reading_timeline(sc)
    os.makedirs(os.path.join(folder, "out"), exist_ok=True)
    json.dump(tl, open(os.path.join(folder, "out", "timeline.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    render = [ "node", os.path.join(os.path.dirname(os.path.abspath(__file__)), "render.js"), folder, str(fps)]
    if preview:
        subprocess.check_call(render + ["preview", ",".join(map(str, preview))]); return
    aud = os.path.join(folder, "out", "mix.wav")
    nar = read_wav(os.path.join(folder, "vo", "narration.wav")) if voiced else None
    sound(tl, aud, nar)
    subprocess.check_call(render + ["video"])
    print("ok:", os.path.join(folder, "out", "final.mp4"), "voiced" if voiced else "text-only")

if __name__ == "__main__":
    main()
