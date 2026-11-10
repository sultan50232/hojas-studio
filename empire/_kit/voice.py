#!/usr/bin/env python3
"""Generate per-line voice-over with ElevenLabs (character timestamps) for a video folder.

usage: python3 empire/_kit/voice.py empire/videos/<id>
reads  script.json, writes vo/<line>.mp3, vo/<line>.json (word timings), vo/timeline.json
Needs ELEVENLABS_API_KEY in the environment, or a proxy that injects it.
"""
import base64, json, os, subprocess, sys, urllib.request

GAP = 0.28  # breath between lines (s)


def tts(text, voice, model, prev=None, nxt=None):
    body = {"text": text, "model_id": model,
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.35, "use_speaker_boost": True}}
    if prev: body["previous_text"] = prev
    if nxt: body["next_text"] = nxt
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps?output_format=mp3_44100_128",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "xi-api-key": os.environ.get("ELEVENLABS_API_KEY", "proxy")})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def words(al):
    out, cur, st = [], "", None
    for ch, s, e in zip(al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]):
        if ch.isspace():
            if cur: out.append({"w": cur, "s": st, "e": last})
            cur, st = "", None
            continue
        if st is None: st = s
        cur += ch; last = e
    if cur: out.append({"w": cur, "s": st, "e": last})
    return out


def dur(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).strip())


def main(folder):
    sc = json.load(open(os.path.join(folder, "script.json"), encoding="utf-8"))
    vo = os.path.join(folder, "vo"); os.makedirs(vo, exist_ok=True)
    v = sc["voice"]; lines = sc["lines"]; t = 0.35; tl = []
    for i, ln in enumerate(lines):
        mp3 = os.path.join(vo, ln["id"] + ".mp3"); js = os.path.join(vo, ln["id"] + ".json")
        if not os.path.exists(mp3):
            r = tts(ln["text"], v["voice_id"], v["model_id"],
                    lines[i - 1]["text"] if i else None, lines[i + 1]["text"] if i + 1 < len(lines) else None)
            open(mp3, "wb").write(base64.b64decode(r["audio_base64"]))
            json.dump(words(r["alignment"]), open(js, "w", encoding="utf-8"), ensure_ascii=False)
        d = dur(mp3); ws = json.load(open(js, encoding="utf-8"))
        tl.append({**ln, "start": round(t, 3), "end": round(t + d, 3),
                   "words": [{"w": w["w"], "s": round(t + w["s"], 3), "e": round(t + w["e"], 3)} for w in ws]})
        t += d + GAP
    json.dump({"duration": round(t + 1.2, 3), "lines": tl}, open(os.path.join(vo, "timeline.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    # one continuous narration track
    parts = []
    for ln in tl:
        parts.append(f"[{len(parts)}:a]adelay={int(ln['start']*1000)}|{int(ln['start']*1000)}[a{len(parts)}]")
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for ln in tl: cmd += ["-i", os.path.join(vo, ln["id"] + ".mp3")]
    mix = ";".join(parts) + ";" + "".join(f"[a{i}]" for i in range(len(tl))) + f"amix=inputs={len(tl)}:normalize=0,apad=whole_dur={t+1.2}[o]"
    cmd += ["-filter_complex", mix, "-map", "[o]", "-ar", "48000", "-ac", "2", os.path.join(vo, "narration.wav")]
    subprocess.check_call(cmd)
    print(f"ok: {len(tl)} lines, {t+1.2:.1f}s")


if __name__ == "__main__":
    main(sys.argv[1])
