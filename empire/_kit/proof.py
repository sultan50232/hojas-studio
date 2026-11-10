#!/usr/bin/env python3
"""Pronunciation QC: each scene's narration is transcribed by Gemini (free-tier text model, ONE request)
and compared with the script. Prints a per-line verdict and writes vo/proof.json.

usage: python3 empire/_kit/proof.py empire/videos/<id>
"""
import base64, json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice as V

MODEL = "gemini-flash-lite-latest"  # Flash family = AI Studio free tier (on a key without billing, over-quota fails instead of charging)


def clip_mp3(wav, a, b):
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(a), "-to", str(b), "-i", wav, "-ac", "1", "-ar", "16000",
                           "-b:a", "48k", "-f", "mp3", "-"], capture_output=True, check=True).stdout


def main(folder):
    folder = os.path.abspath(folder); P = V.profile(folder)
    tl = json.load(open(os.path.join(folder, "vo", "timeline.json"), encoding="utf-8"))
    wav = os.path.join(folder, "vo", "narration.wav")
    head = {"text": "You are an expert Arabic phonetics reviewer for a documentary voice-over. For each numbered audio clip, "
                    "(1) transcribe exactly what is SPOKEN, with full diacritics, (2) compare it with the reference script line, "
                    "(3) list any word that is mispronounced, skipped, added, or has a wrong case ending, or with clearly unnatural stress. "
                    "Ignore punctuation differences. Reply ONLY with JSON: "
                    "[{\"id\":\"s1\",\"heard\":\"...\",\"ok\":true|false,\"issues\":[{\"word\":\"...\",\"heard_as\":\"...\",\"note\":\"...\"}]}]"}
    key, _ = V.gemini_key(P); res = []; L = tl["lines"]
    for i in range(0, len(L), 5):  # small batches: the proxy cuts requests that run over ~30 s
        parts = [head]
        for ln in L[i:i + 5]:
            parts.append({"text": f"Clip {ln['id']}. Reference: {V.spoken(ln['text'], P['lexicon'])}"})
            parts.append({"inlineData": {"mimeType": "audio/mp3",
                                         "data": base64.b64encode(clip_mp3(wav, max(0, ln["start"] - .1), ln["end"] + .1)).decode()}})
        for attempt in range(6):
            try:
                r = V.post(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                           {"contents": [{"parts": parts}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0}},
                           {"x-goog-api-key": key}, timeout=90)
                break
            except V.ProviderDown as e:
                if attempt == 5 or not ("HTTP 5" in str(e) or "network" in str(e)): raise
                print("  retry:", e, flush=True); time.sleep(10 * (attempt + 1))
        res += json.loads(r["candidates"][0]["content"]["parts"][0]["text"])
    json.dump(res, open(os.path.join(folder, "vo", "proof.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = 0
    for x in res:
        print(("✓ " if x.get("ok") else "✗ ") + x["id"] + ": " + x.get("heard", ""))
        for i in x.get("issues", []):
            bad += 1; print(f"    - {i.get('word')} → {i.get('heard_as')} ({i.get('note')})")
    print(f"\n{bad} issue(s)")


if __name__ == "__main__":
    main(sys.argv[1])
