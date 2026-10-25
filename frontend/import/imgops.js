// Pure image maths for the image editor (no DOM): adjustments on RGBA bytes, crop clamping, resize fitting.
export function clampCrop(r, w, h) {
  let x0 = Math.max(0, Math.min(w, Math.min(r.x, r.x + r.w))), y0 = Math.max(0, Math.min(h, Math.min(r.y, r.y + r.h)));
  let x1 = Math.max(0, Math.min(w, Math.max(r.x, r.x + r.w))), y1 = Math.max(0, Math.min(h, Math.max(r.y, r.y + r.h)));
  x0 = Math.round(x0); y0 = Math.round(y0); x1 = Math.round(x1); y1 = Math.round(y1);
  return x1 - x0 >= 1 && y1 - y0 >= 1 ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : null;
}
export function fitSize(w, h, newW, newH, keepAspect) {
  newW = Math.round(newW); newH = Math.round(newH);
  if (keepAspect) { if (newW && !newH) newH = Math.round((h * newW) / w); else if (newH && !newW) newW = Math.round((w * newH) / h); }
  if (!(newW >= 1 && newH >= 1) || newW * newH > 100e6) return null;
  return { w: newW, h: newH };
}
// brightness/contrast -100..100, saturation -100..100, plus grayscale / invert. Alpha is untouched. Returns a new array.
export function adjust(rgba, { brightness = 0, contrast = 0, saturation = 0, gray = false, invert = false } = {}) {
  const out = new Uint8ClampedArray(rgba.length), b = brightness * 2.55, k = (259 * (contrast * 2.55 + 255)) / (255 * (259 - contrast * 2.55)), s = 1 + saturation / 100;
  for (let i = 0; i < rgba.length; i += 4) {
    let r = rgba[i], g = rgba[i + 1], bl = rgba[i + 2];
    if (saturation) { const l = 0.299 * r + 0.587 * g + 0.114 * bl; r = l + (r - l) * s; g = l + (g - l) * s; bl = l + (bl - l) * s; }
    r = k * (r + b - 128) + 128; g = k * (g + b - 128) + 128; bl = k * (bl + b - 128) + 128;
    if (gray) { const l = 0.299 * r + 0.587 * g + 0.114 * bl; r = g = bl = l; }
    if (invert) { r = 255 - r; g = 255 - g; bl = 255 - bl; }
    out[i] = r; out[i + 1] = g; out[i + 2] = bl; out[i + 3] = rgba[i + 3];
  }
  return out;
}
export function rotate90(rgba, w, h, cw = true) {   // returns { data, w: h, h: w }
  const out = new Uint8ClampedArray(rgba.length);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const nx = cw ? h - 1 - y : y, ny = cw ? x : w - 1 - x, s = (y * w + x) * 4, d = (ny * h + nx) * 4; out[d] = rgba[s]; out[d + 1] = rgba[s + 1]; out[d + 2] = rgba[s + 2]; out[d + 3] = rgba[s + 3]; }
  return { data: out, w: h, h: w };
}
export function flip(rgba, w, h, horizontal) {
  const out = new Uint8ClampedArray(rgba.length);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const s = (y * w + x) * 4, d = ((horizontal ? y : h - 1 - y) * w + (horizontal ? w - 1 - x : x)) * 4; out[d] = rgba[s]; out[d + 1] = rgba[s + 1]; out[d + 2] = rgba[s + 2]; out[d + 3] = rgba[s + 3]; }
  return out;
}
export const MIME = { png: 'image/png', jpeg: 'image/jpeg', webp: 'image/webp' };
