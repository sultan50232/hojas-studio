# Quality check: 001

- [x] Every number has a source in sources.md (the smell story is phrased as hearsay)
- [x] Hook within 2 seconds: "1919" appears at 0.3s, and the wave starts rising from 0.6s
- [x] No static scene: every scene has continuous motion (wave, counter, drips, needle, counters)
- [x] Captions sit between y=1270 and 1570, inside the safe zone and clear of TikTok's buttons
- [x] Tank drawn to its real proportions (27 m wide × 15 m high, with a person for scale), corrected after review
- [x] Audio: synthesized in code (drone, heartbeat, a silent drop then the explosion, drips, counter ticks, stamp). No licences needed
- [x] Checked on real frames from the video (contact sheet every 5 seconds)
- [x] Voice-over: Gemini TTS (gemini-2.5-flash-preview-tts, free tier), voice Algieba, generated as one continuous take (one request)
- [x] Voice chosen from an audition of three voices: Algieba = deep (~93 Hz), the warmest timbre (spectral centroid 3336 Hz vs 3835/4272) and the most natural pitch movement
- [x] Pronunciation checked by Gemini transcription (proof.py). Fixed: «تجتاح» (was garbled) and «ظُهْرَ» (it was read as ظَهَرَ, "appeared"). Remaining note: the case ending on «ألف» in the date, normal in spoken dates
- [x] Scene split checked: every transcribed clip matches its scene line
- [x] Scenes and captions follow the voice; the explosion lands on the word «انفجر»; music ducks under the voice
- [ ] Final human review on a phone before publishing
