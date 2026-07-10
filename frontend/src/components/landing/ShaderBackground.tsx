import { useEffect, useRef, useState } from 'react'

const VERTEX_SHADER = `
attribute vec2 a_position;
void main() {
  gl_Position = vec4(a_position, 0.0, 1.0);
}
`

// Procedural aurora: layered fbm noise flow + drifting radial glow + soft
// particles + breathing brightness + vignette. Intentionally understated —
// low contrast, no bloom, no saturation spikes.
const FRAGMENT_SHADER = `
precision highp float;

uniform vec2 u_resolution;
uniform float u_time;
uniform vec2 u_mouse;
uniform float u_speed;
uniform float u_glowIntensity;
uniform float u_particleAmount;
uniform float u_purpleFirst;
uniform float u_richFlow;

vec3 palette(float t, vec3 bg) {
  vec3 dark = vec3(0.2667, 0.1647, 0.3490); // #442A59
  vec3 mid = vec3(0.4549, 0.2980, 0.5412);  // #744C8A
  vec3 primary = vec3(0.5608, 0.3843, 0.8745); // #8F62DF
  vec3 t1 = mix(bg, dark, smoothstep(0.0, 0.4, t));
  vec3 t2 = mix(t1, mid, smoothstep(0.35, 0.7, t));
  vec3 t3 = mix(t2, primary, smoothstep(0.65, 1.0, t));
  return t3;
}

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(a, b, u.x) + (c - a) * u.y * (1.0 - u.x) + (d - b) * u.x * u.y;
}

float fbm(vec2 p) {
  float value = 0.0;
  float amp = 0.5;
  for (int i = 0; i < 4; i++) {
    value += amp * noise(p);
    p *= 2.02;
    amp *= 0.5;
  }
  return value;
}

void main() {
  vec2 uv = gl_FragCoord.xy / u_resolution.xy;
  vec2 p = uv * 2.0 - 1.0;
  p.x *= u_resolution.x / u_resolution.y;

  float t = u_time * 0.035 * u_speed; // slow global drift, scaled by caller

  float tFlow = t * 1.35; // main flow noise runs ~35% faster than the rest
  // primary pattern scaled up ~25% (lower spatial frequency) for broader, slower-reading bands
  vec2 warp = vec2(fbm(p * 0.88 + tFlow), fbm(p * 0.88 - tFlow * 0.8));
  // optional second-order domain warp (warp-of-warp) for a richer, more organic flow — opt-in via u_richFlow
  vec2 warp2 = vec2(
    fbm(p * 0.88 + warp * 0.5 + tFlow * 1.15),
    fbm(p * 0.88 - warp * 0.5 - tFlow * 0.9)
  );
  warp = mix(warp, warp2, u_richFlow);
  float flow = fbm(p * 0.72 + warp * 0.84 + vec2(tFlow * 0.5, -tFlow * 0.35)); // domain warp intensity +40%

  // finer-scale, zero-mean detail layer adds local variation without shifting average brightness
  float detail = fbm(p * 3.2 + tFlow * 0.6) - 0.5;
  flow += detail * 0.18;

  // contrast boost around the midpoint keeps mean brightness unchanged while making bands more defined
  flow = clamp((flow - 0.5) * 1.2 + 0.5, 0.0, 1.0);

  vec2 glowCenter = vec2(sin(t * 0.31) * 0.35, cos(t * 0.24) * 0.3);
  glowCenter += (u_mouse - 0.5) * 0.12;
  float glowDist = length(p - glowCenter);
  float glow = exp(-glowDist * glowDist * 1.6) * 0.35 * u_glowIntensity;

  float breathing = 0.94 + 0.06 * sin(u_time * u_speed * 0.12);

  // floor color: near-black navy by default, or brand Dark purple (#442A59) when u_purpleFirst is set —
  // keeps the whole field inside the logo's purple family instead of dipping toward near-black/gray
  vec3 floorColor = mix(vec3(0.0588, 0.0431, 0.1020), vec3(0.2667, 0.1647, 0.3490), u_purpleFirst);

  float shade = flow * 0.7 + glow;
  vec3 color = palette(clamp(shade, 0.0, 1.0), floorColor) * breathing;

  // sparse, very soft procedural particles (skipped entirely when u_particleAmount is 0)
  float particles = 0.0;
  const int COUNT = 12;
  for (int i = 0; i < COUNT; i++) {
    float fi = float(i);
    vec2 seed = vec2(fi * 12.9898, fi * 78.233);
    vec2 basePos = vec2(hash(seed), hash(seed + 1.0)) * 2.0 - 1.0;
    basePos.x *= u_resolution.x / u_resolution.y;
    float drift = t * (0.05 + 0.03 * hash(seed + 2.0));
    vec2 pos = basePos + vec2(sin(drift + fi), cos(drift * 0.7 + fi)) * 0.15;
    float d = length(p - pos);
    float size = 0.006 + hash(seed + 3.0) * 0.006;
    float twinkle = 0.5 + 0.5 * sin(u_time * u_speed * 0.2 + fi * 2.1);
    particles += smoothstep(size, 0.0, d) * 0.12 * twinkle;
  }
  color += particles * u_particleAmount * vec3(0.725, 0.612, 1.0); // highlight tint #B99CFF

  float vig = smoothstep(1.35, 0.2, length(p));
  color = mix(floorColor, color, vig);

  gl_FragColor = vec4(color, 1.0);
}
`

function createShader(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type)
  if (!shader) return null
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader)
    return null
  }
  return shader
}

function createProgram(gl: WebGLRenderingContext) {
  const vertexShader = createShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER)
  const fragmentShader = createShader(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER)
  if (!vertexShader || !fragmentShader) return null

  const program = gl.createProgram()
  if (!program) return null
  gl.attachShader(program, vertexShader)
  gl.attachShader(program, fragmentShader)
  gl.linkProgram(program)
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    gl.deleteProgram(program)
    return null
  }
  return program
}

function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function staticFallbackBackground(purpleFirst: boolean) {
  const floor = purpleFirst ? '#442a59' : '#0f0b1a'
  return (
    'radial-gradient(ellipse 60% 50% at 30% 20%, rgba(143, 98, 223, 0.14), transparent 60%),' +
    'radial-gradient(ellipse 55% 45% at 75% 70%, rgba(116, 76, 138, 0.16), transparent 65%),' +
    floor
  )
}

const FIXED_LAYER_STYLE: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  width: '100%',
  height: '100%',
  zIndex: 0,
  pointerEvents: 'none',
}

interface ShaderBackgroundProps {
  /** Overall visibility of the background layer, 0–1. Default 1 (full intensity). */
  opacity?: number
  /** Whether to render the floating particle layer. Default true. */
  particles?: boolean
  /** Multiplier on ambient glow strength. Default 1. */
  glowIntensity?: number
  /** Multiplier on overall animation speed. Default 1. */
  speed?: number
  /** Bias the palette's darkest floor/vignette color toward brand Dark (#442A59) instead of near-black. Default false. */
  purpleFirst?: boolean
  /** Use a second-order domain warp for a richer, more organic flow pattern. Default false. */
  richFlow?: boolean
}

export function ShaderBackground({
  opacity = 1,
  particles = true,
  glowIntensity = 1,
  speed = 1,
  purpleFirst = false,
  richFlow = false,
}: ShaderBackgroundProps = {}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [useStaticFallback, setUseStaticFallback] = useState(false)

  useEffect(() => {
    if (prefersReducedMotion()) {
      setUseStaticFallback(true)
      return
    }

    const canvas = canvasRef.current
    if (!canvas) return

    const gl = canvas.getContext('webgl') || (canvas.getContext('experimental-webgl') as WebGLRenderingContext | null)
    if (!gl) {
      setUseStaticFallback(true)
      return
    }

    const program = createProgram(gl)
    if (!program) {
      setUseStaticFallback(true)
      return
    }

    const positionBuffer = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer)
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 3, -1, -1, 3]),
      gl.STATIC_DRAW,
    )

    const positionLocation = gl.getAttribLocation(program, 'a_position')
    const resolutionLocation = gl.getUniformLocation(program, 'u_resolution')
    const timeLocation = gl.getUniformLocation(program, 'u_time')
    const mouseLocation = gl.getUniformLocation(program, 'u_mouse')
    const speedLocation = gl.getUniformLocation(program, 'u_speed')
    const glowIntensityLocation = gl.getUniformLocation(program, 'u_glowIntensity')
    const particleAmountLocation = gl.getUniformLocation(program, 'u_particleAmount')
    const purpleFirstLocation = gl.getUniformLocation(program, 'u_purpleFirst')
    const richFlowLocation = gl.getUniformLocation(program, 'u_richFlow')

    gl.useProgram(program)
    gl.enableVertexAttribArray(positionLocation)
    gl.vertexAttribPointer(positionLocation, 2, gl.FLOAT, false, 0, 0)
    gl.uniform1f(speedLocation, speed)
    gl.uniform1f(glowIntensityLocation, glowIntensity)
    gl.uniform1f(particleAmountLocation, particles ? 1 : 0)
    gl.uniform1f(purpleFirstLocation, purpleFirst ? 1 : 0)
    gl.uniform1f(richFlowLocation, richFlow ? 1 : 0)

    const dpr = Math.min(window.devicePixelRatio || 1, 1.5)
    let rafId = 0
    let startTime = 0
    let mouse = { x: 0.5, y: 0.5 }
    let targetMouse = { x: 0.5, y: 0.5 }
    let visible = true

    function resize() {
      if (!canvas) return
      const width = Math.floor(window.innerWidth * dpr)
      const height = Math.floor(window.innerHeight * dpr)
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width
        canvas.height = height
        gl!.viewport(0, 0, width, height)
      }
    }

    function onPointerMove(e: PointerEvent) {
      targetMouse = { x: e.clientX / window.innerWidth, y: 1 - e.clientY / window.innerHeight }
    }

    function onVisibilityChange() {
      visible = document.visibilityState === 'visible'
    }

    function render(timeMs: number) {
      if (!startTime) startTime = timeMs
      if (visible) {
        mouse.x += (targetMouse.x - mouse.x) * 0.03
        mouse.y += (targetMouse.y - mouse.y) * 0.03

        resize()
        gl!.uniform2f(resolutionLocation, canvas!.width, canvas!.height)
        gl!.uniform1f(timeLocation, (timeMs - startTime) / 1000)
        gl!.uniform2f(mouseLocation, mouse.x, mouse.y)
        gl!.drawArrays(gl!.TRIANGLES, 0, 3)
      }
      rafId = requestAnimationFrame(render)
    }

    resize()
    window.addEventListener('resize', resize)
    window.addEventListener('pointermove', onPointerMove)
    document.addEventListener('visibilitychange', onVisibilityChange)
    rafId = requestAnimationFrame(render)

    return () => {
      cancelAnimationFrame(rafId)
      window.removeEventListener('resize', resize)
      window.removeEventListener('pointermove', onPointerMove)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [speed, glowIntensity, particles, purpleFirst, richFlow])

  if (useStaticFallback) {
    return (
      <div
        aria-hidden="true"
        style={{ ...FIXED_LAYER_STYLE, opacity, background: staticFallbackBackground(purpleFirst) }}
      />
    )
  }

  return <canvas ref={canvasRef} aria-hidden="true" style={{ ...FIXED_LAYER_STYLE, opacity }} />
}
