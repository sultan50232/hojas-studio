#!/usr/bin/env python3
"""Studio voice-over with automatic fallbacks and one locked voice identity.

  python3 empire/_kit/voice.py check                              # key + free models + one tiny test call
  python3 empire/_kit/voice.py audition [Gacrux Charon Algieba]   # same sample in each voice -> empire/_kit/audition/
  python3 empire/_kit/voice.py <video>                            # narrate script.json via the chain in voice_profile.json
  python3 empire/_kit/voice.py <video> --brief                    # write vo/VOICE_BRIEF.md for a human/external recording
  python3 empire/_kit/voice.py <video> --import <audio file>      # use an uploaded recording, split + sync it to the lines

Chain (voice_profile.json "chain"): gemini -> elevenlabs -> manual. A provider that fails (no key, quota,
outage) on ANY line is dropped for the whole video, so a video never mixes two voices. Nothing here buys
credits or turns on billing; "manual" writes the recording brief and stops.
Output: vo/<line>.wav, vo/narration.wav, vo/timeline.json (read by build.py).
"""
import base64, hashlib, json, os, re, subprocess, sys, time, urllib.error, urllib.request, wave
import numpy as np

KIT = os.path.dirname(os.path.abspath(__file__))
SR = 48000
SAMPLE = "يناير، ألف وتسعمئة وتسعة عشر. موجةٌ بارتفاع ثمانية أمتار تقريبًا تجتاح مدينة بوسطن… لكنها لم تكن ماء."


class ProviderDown(Exception):
    pass


def profile(folder=None):
    p = json.load(open(os.path.join(KIT, "voice_profile.json"), encoding="utf-8"))
    if folder:
        o = json.load(open(os.path.join(folder, "script.json"), encoding="utf-8")).get("voice_override")
        if o:
            for k, v in o.items():
                p[k] = {**p.get(k, {}), **v} if isinstance(v, dict) else v
    return p


def spoken(text, lex):
    for a, b in sorted(((a, b) for a, b in lex.items() if not a.startswith("_")), key=lambda x: -len(x[0])):
        text = text.replace(a, b)
    return text


def post(url, body, headers, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(raw); msg = (j.get("error") or j.get("detail") or {}).get("message", raw)
        except Exception:
            msg = raw
        raise ProviderDown(f"HTTP {e.code}: {' '.join(str(msg).split())[:160]}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise ProviderDown(f"network: {e}")


# ---------------- providers: each returns (pcm float32 mono @SR, words or None) ----------------
def gemini_key(P):
    """GEMINI_API_KEY from the environment, else from empire/.env (git-ignored). Never printed.
    With neither, a placeholder is sent: in a cloud session the network proxy swaps in the
    key saved under Network secrets (host generativelanguage.googleapis.com, header x-goog-api-key)."""
    name = P["gemini"].get("key_env", "GEMINI_API_KEY")
    if os.environ.get(name): return os.environ[name], "env"
    envf = os.path.join(os.path.dirname(KIT), ".env")
    if os.path.exists(envf):
        for ln in open(envf, encoding="utf-8"):
            k, _, v = ln.strip().partition("=")
            if k.strip() == name and v.strip(): return v.strip().strip('"').strip("'"), ".env"
    return "proxy-injected", "proxy"


_MODELS = None


def gemini_models(P, key):
    """TTS models this key can use. free_only drops 'pro' TTS models (no free tier, billed if billing is on)."""
    global _MODELS
    if _MODELS is not None: return _MODELS
    g = P["gemini"]; found = []
    try:
        req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000", headers={"x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=30) as r:
            found = [m["name"].split("/")[-1] for m in json.load(r).get("models", []) if "tts" in m["name"].lower()]
    except Exception:
        pass
    pref = [m for m in g["models"] if not found or m in found] + sorted(m for m in found if m not in g["models"])
    if P.get("free_only", True):
        pref = [m for m in pref if "pro" not in m.lower()]
    _MODELS = pref or g["models"]
    return _MODELS


def gemini(text, P, voice=None):
    g = P["gemini"]; voice = voice or g["voice"]
    key, _ = gemini_key(P)
    last = None
    for model in gemini_models(P, key):
        for attempt in range(4):
            try:
                r = post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                         {"contents": [{"parts": [{"text": g["direction"] + "\n" + text}]}],
                          "generationConfig": {"responseModalities": ["AUDIO"],
                                               "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}},
                         {"x-goog-api-key": key})
                part = r["candidates"][0]["content"]["parts"][0]["inlineData"]
                mt = part.get("mimeType", "")
                rate = int(re.search(r"rate=(\d+)", mt).group(1)) if "rate=" in mt else 24000
                pcm = np.frombuffer(base64.b64decode(part["data"]), "<i2").astype(np.float32) / 32768
                return resample(pcm, rate), None
            except ProviderDown as e:
                last = e; msg = str(e)
                if "HTTP 429" in msg and attempt < 3 and "per day" not in msg.lower() and "PerDay" not in msg:
                    wait = 20 * (attempt + 1); print(f"  gemini: free-tier rate limit, waiting {wait}s", flush=True)
                    time.sleep(wait); continue  # per-minute limit: wait it out. Daily limit: fall through
                break
            except (KeyError, IndexError) as e:
                last = ProviderDown(f"unexpected response ({model}): {e}"); break
        if last and ("HTTP 404" in str(last) or ("HTTP 400" in str(last) and "key" not in str(last).lower())):
            continue  # this model is unknown or retired for this key: try the next one
        raise last
    raise last or ProviderDown("no Gemini TTS model available")


def check():
    """Safe setup check: says WHERE the key comes from (never its value), lists usable free TTS models, makes one tiny call."""
    P = profile(); key, src = gemini_key(P)
    print(f"key source: {src}" + ("  (Network secret, injected by the proxy)" if src == "proxy" else ""))
    print("free-tier TTS models:", ", ".join(gemini_models(P, key)))
    try:
        x, _ = gemini("مرحبًا.", P); print(f"test call: ok ({len(x)/SR:.1f}s of audio, voice {P['gemini']['voice']})")
    except ProviderDown as e:
        print("test call: failed:", e); sys.exit(1)


def elevenlabs(text, P, prev=None, nxt=None):
    e = P["elevenlabs"]
    body = {"text": text, "model_id": e["model_id"],
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.25, "use_speaker_boost": True}}
    if prev: body["previous_text"] = prev
    if nxt: body["next_text"] = nxt
    r = post(f"https://api.elevenlabs.io/v1/text-to-speech/{e['voice_id']}/with-timestamps?output_format=pcm_24000",
             body, {"xi-api-key": os.environ.get("ELEVENLABS_API_KEY", "proxy-injected")})
    pcm = np.frombuffer(base64.b64decode(r["audio_base64"]), "<i2").astype(np.float32) / 32768
    al = r["alignment"]; words, cur, st = [], "", None
    for ch, s, en in zip(al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]):
        if ch.isspace():
            if cur: words.append([st, last])
            cur, st = "", None; continue
        if st is None: st = s
        cur += ch; last = en
    if cur: words.append([st, last])
    return resample(pcm, 24000), words


# ---------------- audio helpers ----------------
def resample(x, rate):
    if rate == SR: return x
    n = int(len(x) * SR / rate)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def ffmpeg_filter(x, af):
    """Run mono float audio through an ffmpeg filter chain (same chain for every provider = same sound)."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-", "-af", af,
                        "-f", "f32le", "-ar", str(SR), "-ac", "1", "-"], input=x.astype("<f4").tobytes(), capture_output=True, check=True)
    return np.frombuffer(p.stdout, "<f4").copy()


# Voice "house sound": remove rumble, a touch of low warmth, tame harshness, gentle compression.
MASTER = ("highpass=f=70,equalizer=f=140:t=q:w=1:g=2,equalizer=f=3500:t=q:w=1.5:g=-1.5,"
          "equalizer=f=7500:t=q:w=2:g=1,acompressor=threshold=-20dB:ratio=2.5:attack=8:release=120:makeup=2")


def trim(x, thr=0.012):
    a = np.abs(x); idx = np.where(np.convolve(a, np.ones(480) / 480, "same") > thr)[0]
    if not len(idx): return x, 0.0
    s, e = max(0, idx[0] - 1200), min(len(x), idx[-1] + 2400)
    return x[s:e], s / SR


def save_wav(path, x, ch=1):
    x = np.clip(x, -1, 1)
    if ch == 2: x = np.stack([x, x], 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(ch); w.setsampwidth(2); w.setframerate(SR); w.writeframes((x * 32767).astype("<i2").tobytes())


def load_audio(path):
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ar", str(SR), "-ac", "1", "-"], capture_output=True, check=True)
    return np.frombuffer(p.stdout, "<f4").copy()


def pitch(x):
    """Median F0 (Hz) over voiced 40 ms frames: a rough 'depth' measure for auditions."""
    f, out = int(.04 * SR), []
    for i in range(0, len(x) - f, f):
        fr = x[i:i + f]
        if np.sqrt(np.mean(fr ** 2)) < 0.03: continue
        c = np.correlate(fr, fr, "full")[f - 1:]
        lo, hi = int(SR / 300), int(SR / 60)
        k = lo + int(np.argmax(c[lo:hi]))
        if c[k] > 0.35 * c[0]: out.append(SR / k)
    return float(np.median(out)) if out else 0.0


# ---------------- timing ----------------
def weights(text):
    """Per-word time weight: letters, plus extra hold for punctuation that the narrator pauses on."""
    ws = text.split(); out = []
    for w in ws:
        k = len(re.sub(r"[ً-ْ\W]", "", w)) + 2
        if w.endswith("…"): k += 6
        elif w[-1] in ".؟?!": k += 4
        elif w[-1] in "،,:": k += 2
        out.append(k)
    return ws, out


def word_times(text, dur, measured=None):
    ws, wt = weights(text)
    if measured and len(measured) == len(ws):
        return [{"w": w, "s": round(a, 3), "e": round(b, 3)} for w, (a, b) in zip(ws, measured)]
    tot, t, out = sum(wt), 0.0, []
    for w, k in zip(ws, wt):
        d = dur * k / tot; out.append({"w": w, "s": round(t, 3), "e": round(t + d * .85, 3)}); t += d
    return out


def assemble(folder, lines, clips, provider, voice):
    P = profile(folder); pace = P["pace"]; vo = os.path.join(folder, "vo")
    t, tl, parts = pace["lead_in"], [], []
    for ln, (x, measured) in zip(lines, clips):
        d = len(x) / SR
        ws = [{"w": w["w"], "s": round(t + w["s"], 3), "e": round(t + w["e"], 3)} for w in word_times(ln["text"], d, measured)]
        tl.append({**ln, "start": round(t, 3), "end": round(t + d, 3), "words": ws})
        parts.append((t, x))
        t += d + (pace["gap_after_ellipsis_line"] if ln["text"].rstrip().endswith("…") else pace["gap_between_lines"])
    D = t + 2.2
    nar = np.zeros(int(D * SR), np.float32)
    for st, x in parts:
        a = int(st * SR); nar[a:a + len(x)] += x[:len(nar) - a]
    nar = ffmpeg_filter(nar, "loudnorm=I=-16:TP=-1.5:LRA=7")
    save_wav(os.path.join(vo, "narration.wav"), nar, 2)
    json.dump({"duration": round(D, 3), "provider": provider, "voice": voice, "lines": tl},
              open(os.path.join(vo, "timeline.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"ok: {provider}/{voice} · {len(tl)} lines · {D:.1f}s -> {vo}/narration.wav")


def narrate(folder):
    P = profile(folder); sc = json.load(open(os.path.join(folder, "script.json"), encoding="utf-8"))
    lines = sc["lines"]; vo = os.path.join(folder, "vo"); os.makedirs(vo, exist_ok=True)
    log = []
    for prov in P["chain"]:
        if prov == "manual":
            brief(folder)
            print("\nno voice service is available right now:\n  " + "\n  ".join(log) +
                  f"\n-> recording brief written: {vo}/VOICE_BRIEF.md\n-> record it, then: voice.py {folder} --import <file>")
            sys.exit(2)
        voice = P[prov].get("voice") or P[prov].get("voice_id")
        clips = []
        try:
            for i, ln in enumerate(lines):
                txt = spoken(ln["text"], P["lexicon"])
                h = hashlib.sha1(json.dumps([prov, voice, P[prov].get("direction", ""), txt]).encode()).hexdigest()[:10]
                cache = os.path.join(vo, f"{ln['id']}.{prov}.{h}.npz")
                if os.path.exists(cache):
                    z = np.load(cache, allow_pickle=True); x, m = z["x"], (z["m"].tolist() or None)
                else:
                    if prov == "gemini": x, m = gemini(txt, P)
                    else: x, m = elevenlabs(txt, P, lines[i - 1]["text"] if i else None, lines[i + 1]["text"] if i + 1 < len(lines) else None)
                    np.savez(cache, x=x, m=np.array(m or [], dtype=float))
                    print(f"  {prov}: {ln['id']} ✓", flush=True)
                x = ffmpeg_filter(x, MASTER); x, off = trim(x)
                if m: m = [[a - off, b - off] for a, b in m]
                clips.append((x, m))
        except ProviderDown as e:
            log.append(f"{prov}: {e}"); print(f"  {prov} unavailable -> next ({e})", flush=True); continue
        return assemble(folder, lines, clips, prov, voice)


def import_audio(folder, path):
    """Split an uploaded recording into the script's lines at its longest silences, then sync."""
    sc = json.load(open(os.path.join(folder, "script.json"), encoding="utf-8")); lines = sc["lines"]
    os.makedirs(os.path.join(folder, "vo"), exist_ok=True)
    x = ffmpeg_filter(load_audio(path), MASTER); x, _ = trim(x)
    env = np.convolve(np.abs(x), np.ones(960) / 960, "same"); quiet = env < max(0.004, 0.1 * np.percentile(env, 95))
    gaps, i, n = [], 0, len(x)
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]: j += 1
            if (j - i) / SR > 0.18: gaps.append(((j - i) / SR, (i + j) // 2))
            i = j
        else: i += 1
    cuts = sorted(c for _, c in sorted(gaps, reverse=True)[:len(lines) - 1])
    if len(cuts) < len(lines) - 1:  # not enough pauses: split by text length instead
        tot = sum(sum(weights(l["text"])[1]) for l in lines); acc, cuts = 0, []
        for l in lines[:-1]:
            acc += sum(weights(l["text"])[1]); cuts.append(int(n * acc / tot))
        print("  warning: recording has fewer pauses than lines; split by text length (check sync in the preview)")
    bounds = [0] + cuts + [n]
    clips = [(trim(x[a:b])[0], None) for a, b in zip(bounds, bounds[1:])]
    assemble(folder, lines, clips, "import", os.path.basename(path))


def brief(folder):
    P = profile(folder); sc = json.load(open(os.path.join(folder, "script.json"), encoding="utf-8"))
    g = P["gemini"]; out = [f"# تسجيل التعليق الصوتي: {sc['title']}", "",
        "## الهوية الصوتية (ثابتة في كل المقاطع)",
        f"- الصوت المعتمد: **{g['voice']}** في Gemini TTS (Google AI Studio ← Generate speech)",
        "- رجل ناضج، عميق، دافئ، هادئ وواثق. سرد وثائقي سينمائي بلا صراخ ولا تمثيل مبالغ فيه",
        "- الإيقاع متمهّل (نحو 2.3 كلمة في الثانية). وقفة قصيرة عند الفاصلة والنقطة، ووقفة أطول فيها تشويق عند (…)",
        "- فصحى سليمة. التشكيل أدناه للنطق فقط", "",
        "## تعليمات الأداء (انسخها كما هي في خانة Style instructions)", "", "```", g["direction"], "```", "",
        "## النص (سطر لكل مشهد، واترك ثانية صمت بين كل سطر والذي يليه)", ""]
    for ln in sc["lines"]:
        out.append(f"{ln['id']}. {spoken(ln['text'], P['lexicon'])}")
    out += ["", "## التسليم",
            "ملف واحد (WAV أو MP3) فيه كل السطور بالترتيب، مع ثانية صمت بينها. ارفعه وقل لي، وأنا أقسّمه وأزامنه وأدمجه."]
    p = os.path.join(folder, "vo", "VOICE_BRIEF.md"); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write("\n".join(out) + "\n"); return p


def audition(voices):
    P = profile(); d = os.path.join(KIT, "audition"); os.makedirs(d, exist_ok=True); rows = []
    for v in voices or P["gemini"]["candidates"]:
        try:
            x, _ = gemini(spoken(SAMPLE, P["lexicon"]), P, v)
        except ProviderDown as e:
            print(f"{v}: unavailable ({e})"); continue
        x, _ = trim(ffmpeg_filter(x, MASTER)); save_wav(os.path.join(d, f"{v}.wav"), x)
        wps = len(SAMPLE.split()) / (len(x) / SR)
        rows.append((v, pitch(x), wps)); print(f"{v}: median pitch {rows[-1][1]:.0f} Hz · {wps:.2f} words/s -> {d}/{v}.wav")
    if rows:
        print("deepest:", min(rows, key=lambda r: r[1])[0], "(depth is one criterion; the final pick is by ear)")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a: sys.exit(__doc__)
    if a[0] == "check": check()
    elif a[0] == "audition": audition(a[1:])
    elif "--brief" in a: print(brief(os.path.abspath(a[0])))
    elif "--import" in a: import_audio(os.path.abspath(a[0]), a[a.index("--import") + 1])
    else: narrate(os.path.abspath(a[0]))
