// node empire/_kit/render.js <folder> <fps> preview <t,t,...> | video
// Drives scene.html with Playwright frame by frame and pipes JPEGs into FFmpeg.
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
(async () => {
  const [folder, fpsS, mode, times] = process.argv.slice(2); const fps = +fpsS;
  const tl = JSON.parse(fs.readFileSync(path.join(folder, 'out', 'timeline.json'), 'utf8'));
  const b = await chromium.launch(); const pg = await b.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
  await pg.goto('file://' + path.join(folder, 'scene.html')); await pg.evaluate(() => document.fonts.ready);
  await pg.evaluate(tl => setup(tl), tl); await pg.waitForTimeout(300);
  if (mode === 'preview') {
    fs.mkdirSync(path.join(folder, 'prev'), { recursive: true });
    for (const t of times.split(',').map(Number)) {
      await pg.evaluate(([t, f]) => seek(t, f), [t, Math.round(t * fps)]);
      await pg.screenshot({ path: path.join(folder, 'prev', t.toFixed(2).padStart(6, '0') + '.jpg'), type: 'jpeg', quality: 80 });
    }
    await b.close(); console.log('preview ok'); return;
  }
  const out = path.join(folder, 'out', 'final.mp4'), aud = path.join(folder, 'out', 'mix.wav');
  const ff = spawn('ffmpeg', ['-y', '-v', 'error', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-', '-i', aud,
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '19', '-pix_fmt', 'yuv420p', '-r', String(fps), '-c:a', 'aac', '-b:a', '192k',
    '-shortest', '-movflags', '+faststart', out], { stdio: ['pipe', 'inherit', 'inherit'] });
  const N = Math.floor(tl.duration * fps);
  for (let f = 0; f < N; f++) {
    await pg.evaluate(([t, f]) => seek(t, f), [f / fps, f]);
    const buf = await pg.screenshot({ type: 'jpeg', quality: 92 });
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
    if (f % 150 === 0) console.log(`frame ${f}/${N}`);
  }
  ff.stdin.end(); await new Promise(r => ff.on('close', r)); await b.close();
})();
