import { useEffect, useRef } from 'react'

function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

const BOXES: { x: number; y: number; rot: number; delay: string; duration: string; variant: 'a' | 'b' }[] = [
  { x: 220, y: 150, rot: -8, delay: '0s', duration: '11s', variant: 'a' },
  { x: 1360, y: 210, rot: 12, delay: '-3s', duration: '14s', variant: 'b' },
  { x: 180, y: 690, rot: 6, delay: '-6s', duration: '16s', variant: 'a' },
  { x: 1300, y: 720, rot: -14, delay: '-2s', duration: '13s', variant: 'b' },
  { x: 760, y: 90, rot: 10, delay: '-8s', duration: '18s', variant: 'a' },
  { x: 980, y: 800, rot: -6, delay: '-4s', duration: '15s', variant: 'b' },
  { x: 60, y: 380, rot: 16, delay: '-9s', duration: '19s', variant: 'a' },
  { x: 1520, y: 480, rot: -10, delay: '-1s', duration: '12s', variant: 'b' },
]

const PATH_A = 'M -50 120 L 300 120 L 300 420 L 750 420 L 750 650 L 1250 650 L 1250 300 L 1650 300'
const PATH_B = 'M 1650 760 L 1300 760 L 1300 500 L 900 500 L 900 190 L 450 190 L 450 -50'
const PATH_C = 'M -50 550 L 220 550 L 220 250 L 620 250 L 620 780 L 1080 780 L 1080 480 L 1650 480'

const NODES_A: [number, number][] = [
  [300, 420],
  [750, 650],
  [1250, 300],
]
const NODES_B: [number, number][] = [
  [1300, 500],
  [900, 190],
]
const NODES_C: [number, number][] = [
  [220, 250],
  [620, 780],
]

/**
 * Abstract logistics-motif layer for the login page — grid/rack lines, floating
 * crate outlines, three flow-paths (one hero) with a traveling highlight
 * (inventory moving through the system), two periodic scan sweeps, slow hex
 * fragments, and a barcode texture accent. Pure CSS transform/opacity
 * animation; only a pointermove listener runs in JS, driving a subtle
 * per-layer parallax via CSS variables.
 */
export function WarehouseMotifs() {
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (prefersReducedMotion()) return

    const root = rootRef.current
    if (!root) return

    let target = { x: 0, y: 0 }
    let current = { x: 0, y: 0 }
    let rafId = 0

    function onPointerMove(e: PointerEvent) {
      target = {
        x: e.clientX / window.innerWidth - 0.5,
        y: e.clientY / window.innerHeight - 0.5,
      }
    }

    function tick() {
      current.x += (target.x - current.x) * 0.04
      current.y += (target.y - current.y) * 0.04
      root!.style.setProperty('--motif-parallax-x', current.x.toFixed(4))
      root!.style.setProperty('--motif-parallax-y', current.y.toFixed(4))
      rafId = requestAnimationFrame(tick)
    }

    window.addEventListener('pointermove', onPointerMove)
    rafId = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(rafId)
      window.removeEventListener('pointermove', onPointerMove)
    }
  }, [])

  return (
    <div ref={rootRef} className="motif-root" aria-hidden="true">
      <svg
        className="motif-svg"
        viewBox="0 0 1600 900"
        preserveAspectRatio="xMidYMid slice"
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          <pattern id="motifGrid" width="160" height="160" patternUnits="userSpaceOnUse">
            <path d="M0 0H160M0 0V160" stroke="#744C8A" strokeWidth="1" />
          </pattern>
        </defs>

        {/* Layer: sparse rack/grid lines — very slow drift */}
        <g className="motif-parallax-soft">
          <rect className="motif-grid" width="1900" height="1200" x="-150" y="-150" fill="url(#motifGrid)" />
        </g>

        {/* Layer: slow-rotating hex fragments, screen edges only */}
        <g className="motif-parallax-soft">
          <polygon
            className="motif-hex motif-hex-a"
            points="1480,60 1560,110 1560,210 1480,260 1400,210 1400,110"
          />
          <polygon
            className="motif-hex motif-hex-b"
            points="90,620 170,670 170,770 90,820 10,770 10,670"
          />
          <polygon
            className="motif-hex motif-hex-c"
            points="1500,745 1560,783 1560,858 1500,895 1440,858 1440,783"
          />
        </g>

        {/* Layer: floating crate/pallet outlines */}
        <g className="motif-parallax-strong">
          {BOXES.map((box, i) => (
            <g key={i} transform={`translate(${box.x} ${box.y}) rotate(${box.rot})`}>
              <rect
                className={`motif-box motif-box-${box.variant}`}
                style={{ animationDelay: box.delay, animationDuration: box.duration }}
                x={-26}
                y={-18}
                width={52}
                height={36}
              />
            </g>
          ))}
        </g>

        {/* Layer: logistics flow paths — the visual hero */}
        <g className="motif-parallax-medium">
          <path className="motif-flow-path motif-flow-path-hero" d={PATH_A} />
          <path className="motif-flow-path motif-flow-path-secondary" d={PATH_B} />
          <path className="motif-flow-path motif-flow-path-tertiary" d={PATH_C} />

          {NODES_A.map(([cx, cy], i) => (
            <circle key={`a${i}`} className="motif-node motif-node-hero" cx={cx} cy={cy} r={5} />
          ))}
          {NODES_B.map(([cx, cy], i) => (
            <circle key={`b${i}`} className="motif-node motif-node-secondary" cx={cx} cy={cy} r={4} />
          ))}
          {NODES_C.map(([cx, cy], i) => (
            <circle key={`c${i}`} className="motif-node motif-node-tertiary" cx={cx} cy={cy} r={4} />
          ))}

          <circle className="motif-travel-dot" r={4.5} />
        </g>

        {/* Layer: barcode texture accent — distinct from the moving scan sweeps */}
        <g className="motif-barcode" transform="translate(1440 780)">
          {[3, 6, 2, 8, 4, 3, 7, 2, 5, 3, 6, 2].map((w, i) => {
            const x = i * 9
            return <rect key={i} x={x} y={0} width={w} height={70} fill="#442A59" />
          })}
        </g>
      </svg>

      <div className="motif-scan-sweep motif-scan-sweep-vertical" />
      <div className="motif-scan-sweep motif-scan-sweep-horizontal" />
    </div>
  )
}
