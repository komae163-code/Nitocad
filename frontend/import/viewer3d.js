// Dependency-free WebGL mesh viewer: depth-tested flat shading, orbit / pan / zoom, grid, optional edges.
// Z is up (CAD convention). Input is a flat Float32Array of triangles (9 numbers each).
export class Viewer3D {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = canvas.getContext('webgl', { antialias: true, preserveDrawingBuffer: true }) || canvas.getContext('experimental-webgl');
    if (!gl) throw new Error('WebGL is not available in this browser, so the 3D view cannot be shown. Editing and export still work.');
    this.gl = gl; this.yaw = -0.8; this.pitch = 0.5; this.dist = 5; this.target = [0, 0, 0]; this.tris = 0; this.showEdges = true; this.color = [0.62, 0.72, 0.88]; this.pointers = new Map();
    const sh = (t, s) => { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o); if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) throw new Error('Shader error: ' + gl.getShaderInfoLog(o)); return o; };
    const mk = (vs, fs) => { const p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p); if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error('Link error'); return p; };
    this.solid = mk('attribute vec3 p;attribute vec3 n;uniform mat4 m;varying vec3 vn;void main(){vn=n;gl_Position=m*vec4(p,1.);}',
      'precision mediump float;varying vec3 vn;uniform vec3 L;uniform vec3 c;void main(){vec3 n=normalize(vn);if(!gl_FrontFacing)n=-n;float d=max(dot(n,L),0.);gl_FragColor=vec4(c*(.32+.68*d)+(gl_FrontFacing?vec3(0.):vec3(.18,.0,.0)),1.);}');
    this.line = mk('attribute vec3 p;uniform mat4 m;void main(){gl_Position=m*vec4(p,1.);gl_Position.z-=.0006*gl_Position.w;}', 'precision mediump float;uniform vec4 c;void main(){gl_FragColor=c;}');
    this.bufs = {}; this._bind(); this.resize();
  }
  setMesh(p) {
    const gl = this.gl, n = p.length / 9; if (n > 3_000_000) throw new Error('This mesh is too large to display (over 3 million triangles).');
    const nr = new Float32Array(p.length);
    for (let i = 0; i < p.length; i += 9) {
      const ux = p[i + 3] - p[i], uy = p[i + 4] - p[i + 1], uz = p[i + 5] - p[i + 2], vx = p[i + 6] - p[i], vy = p[i + 7] - p[i + 1], vz = p[i + 8] - p[i + 2];
      let x = uy * vz - uz * vy, y = uz * vx - ux * vz, z = ux * vy - uy * vx; const l = Math.hypot(x, y, z) || 1; x /= l; y /= l; z /= l;
      for (let k = 0; k < 3; k++) { nr[i + k * 3] = x; nr[i + k * 3 + 1] = y; nr[i + k * 3 + 2] = z; }
    }
    this._buf('pos', p); this._buf('nrm', nr); this.tris = n;
    if (n <= 250000) { const e = new Float32Array(n * 18); for (let t = 0; t < n; t++) for (let k = 0; k < 3; k++) { const a = t * 9 + k * 3, b = t * 9 + ((k + 1) % 3) * 3; e.set(p.subarray(a, a + 3), t * 18 + k * 6); e.set(p.subarray(b, b + 3), t * 18 + k * 6 + 3); } this._buf('edge', e); this.edgeCount = n * 6; } else this.edgeCount = 0;
    let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < p.length; i += 3) for (let k = 0; k < 3; k++) { if (p[i + k] < lo[k]) lo[k] = p[i + k]; if (p[i + k] > hi[k]) hi[k] = p[i + k]; }
    this.bb = { lo, hi }; this.fit();
  }
  _buf(name, data) { const gl = this.gl; if (!this.bufs[name]) this.bufs[name] = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs[name]); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW); }
  fit() {
    if (!this.bb) return; const { lo, hi } = this.bb; this.target = lo.map((v, i) => (v + hi[i]) / 2);
    this.radius = Math.max(1e-6, Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2); this.dist = this.radius * 2.6;
    const g = []; const z = lo[2], s = this.radius * 2, step = niceStep(s / 10), x0 = Math.floor((this.target[0] - s) / step) * step, y0 = Math.floor((this.target[1] - s) / step) * step;
    for (let i = 0; i <= 20; i++) { g.push(x0 + i * step, y0, z, x0 + i * step, y0 + 20 * step, z, x0, y0 + i * step, z, x0 + 20 * step, y0 + i * step, z); }
    this._buf('grid', new Float32Array(g)); this.gridCount = g.length / 3; this.draw();
  }
  resize() { const r = this.canvas.getBoundingClientRect(), d = window.devicePixelRatio || 1; this.canvas.width = Math.max(1, Math.round(r.width * d)); this.canvas.height = Math.max(1, Math.round(r.height * d)); this.draw(); }
  _matrix() {
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw), cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const eye = [this.target[0] + this.dist * cp * cy, this.target[1] + this.dist * cp * sy, this.target[2] + this.dist * sp];
    const f = norm(sub(this.target, eye)), s = norm(cross(f, [0, 0, 1])), u = cross(s, f);
    const v = [s[0], u[0], -f[0], 0, s[1], u[1], -f[1], 0, s[2], u[2], -f[2], 0, -dot(s, eye), -dot(u, eye), dot(f, eye), 1];
    const a = this.canvas.width / this.canvas.height, n = Math.max(this.dist * 0.01, 1e-4), fa = this.dist + this.radius * 6, t = 1 / Math.tan(0.4);
    const p = [t / a, 0, 0, 0, 0, t, 0, 0, 0, 0, (fa + n) / (n - fa), -1, 0, 0, 2 * fa * n / (n - fa), 0];
    this.light = [-f[0] + 0.3 * s[0] + 0.4 * u[0], -f[1] + 0.3 * s[1] + 0.4 * u[1], -f[2] + 0.3 * s[2] + 0.4 * u[2]]; this.light = norm(this.light);
    return mul4(p, v);
  }
  draw() {
    const gl = this.gl; gl.viewport(0, 0, this.canvas.width, this.canvas.height); gl.clearColor(0.1, 0.11, 0.13, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); gl.enable(gl.DEPTH_TEST);
    if (!this.tris) return; const M = new Float32Array(this._matrix());
    const attr = (prog, name, buf, size = 3) => { const l = gl.getAttribLocation(prog, name); gl.bindBuffer(gl.ARRAY_BUFFER, this.bufs[buf]); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, size, gl.FLOAT, false, 0, 0); };
    gl.useProgram(this.solid); gl.uniformMatrix4fv(gl.getUniformLocation(this.solid, 'm'), false, M); gl.uniform3fv(gl.getUniformLocation(this.solid, 'L'), this.light); gl.uniform3fv(gl.getUniformLocation(this.solid, 'c'), this.color);
    attr(this.solid, 'p', 'pos'); attr(this.solid, 'n', 'nrm'); gl.drawArrays(gl.TRIANGLES, 0, this.tris * 3);
    gl.useProgram(this.line); gl.uniformMatrix4fv(gl.getUniformLocation(this.line, 'm'), false, M);
    if (this.gridCount) { gl.uniform4f(gl.getUniformLocation(this.line, 'c'), 0.35, 0.37, 0.42, 1); attr(this.line, 'p', 'grid'); gl.drawArrays(gl.LINES, 0, this.gridCount); }
    if (this.showEdges && this.edgeCount) { gl.uniform4f(gl.getUniformLocation(this.line, 'c'), 0.05, 0.06, 0.08, 0.9); attr(this.line, 'p', 'edge'); gl.drawArrays(gl.LINES, 0, this.edgeCount); }
  }
  setView(name) { const v = { top: [0, Math.PI / 2 - 0.001], front: [-Math.PI / 2, 0], right: [0, 0], iso: [-0.8, 0.5] }[name]; if (v) { [this.yaw, this.pitch] = v; this.draw(); } }
  _bind() {
    const cv = this.canvas; cv.style.touchAction = 'none';
    cv.addEventListener('wheel', (e) => { e.preventDefault(); this.dist = Math.max(this.radius * 0.05, Math.min(this.radius * 60, this.dist * Math.exp(e.deltaY * 0.0012))); this.draw(); }, { passive: false });
    cv.addEventListener('contextmenu', (e) => e.preventDefault());
    cv.addEventListener('pointerdown', (e) => { cv.setPointerCapture(e.pointerId); this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY, b: e.button, shift: e.shiftKey }); });
    cv.addEventListener('pointermove', (e) => {
      const q = this.pointers.get(e.pointerId); if (!q) return; const dx = e.clientX - q.x, dy = e.clientY - q.y; q.x = e.clientX; q.y = e.clientY;
      if (this.pointers.size >= 2) {
        const [a, b] = [...this.pointers.values()], d1 = Math.hypot(a.x - b.x, a.y - b.y), d0 = Math.hypot(a.x - dx - (b.x), a.y - dy - (b.y)) || d1;
        if (d0 > 0) { this.dist = Math.max(this.radius * 0.05, Math.min(this.radius * 60, this.dist * (d0 / d1))); } this._pan(dx / 2, dy / 2);
      } else if (q.b === 2 || q.b === 1 || q.shift) this._pan(dx, dy);
      else { this.yaw -= dx * 0.008; this.pitch = Math.max(-1.55, Math.min(1.55, this.pitch + dy * 0.008)); }
      this.draw();
    });
    const up = (e) => this.pointers.delete(e.pointerId); cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(() => this.resize()).observe(cv);
  }
  _pan(dx, dy) {
    const k = (this.dist * 0.9) / this.canvas.clientHeight, cy = Math.cos(this.yaw), sy = Math.sin(this.yaw), cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const f = [-cp * cy, -cp * sy, -sp], s = norm(cross(f, [0, 0, 1])), u = cross(s, f);
    for (let i = 0; i < 3; i++) this.target[i] += (-dx * s[i] + dy * u[i]) * k;
  }
  dispose() { const e = this.gl.getExtension('WEBGL_lose_context'); if (e) e.loseContext(); }
}
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]], dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
function mul4(a, b) { const o = new Array(16).fill(0); for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) o[c * 4 + r] += a[k * 4 + r] * b[c * 4 + k]; return o; }
function niceStep(x) { const p = 10 ** Math.floor(Math.log10(x)), m = x / p; return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p; }
