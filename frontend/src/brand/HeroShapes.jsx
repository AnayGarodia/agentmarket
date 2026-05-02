// Animated jaali grid — canvas-rendered grid with traveling light particles.
// Adapted from 21st.dev Grid Hero pattern, palette-mapped to Aztea tokens
// (terracotta lights on warm-stone grid in light mode, copper lights on
// deep-teal grid in dark mode). Reads as architectural rhythm, not blobs.

import { useEffect, useRef } from 'react'

function readToken(name, fallback) {
  if (typeof document === 'undefined') return fallback
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

function isDark() {
  return document.documentElement.dataset.theme === 'dark'
}

export default function HeroShapes() {
  const canvasRef = useRef(null)
  const rafRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const lights = []
    let dpr = window.devicePixelRatio || 1
    let lastTime = 0
    let stopped = false

    const resize = () => {
      const rect = canvas.getBoundingClientRect()
      dpr = window.devicePixelRatio || 1
      canvas.width = rect.width * dpr
      canvas.height = rect.height * dpr
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.scale(dpr, dpr)
    }

    const spawnLight = () => {
      const w = canvas.width / dpr
      const h = canvas.height / dpr
      const grid = 56
      const horizontal = Math.random() > 0.5
      const speed = 0.18 + Math.random() * 0.32
      const brightness = 0.55 + Math.random() * 0.45
      if (horizontal) {
        const y = Math.floor(Math.random() * (h / grid)) * grid
        return { x: 0, y, tx: w, ty: y, speed, brightness, dir: 'h', t: 0 }
      }
      const x = Math.floor(Math.random() * (w / grid)) * grid
      return { x, y: 0, tx: x, ty: h, speed, brightness, dir: 'v', t: 0 }
    }

    const drawGrid = (w, h) => {
      const dark = isDark()
      ctx.clearRect(0, 0, w, h)
      // grid lines
      ctx.strokeStyle = dark
        ? 'rgba(255, 247, 233, 0.06)'   // soft ivory in dark
        : 'rgba(16, 43, 47, 0.07)'      // deep teal in light
      ctx.lineWidth = 1
      const grid = 56
      for (let x = 0; x <= w; x += grid) {
        ctx.beginPath()
        ctx.moveTo(x + 0.5, 0)
        ctx.lineTo(x + 0.5, h)
        ctx.stroke()
      }
      for (let y = 0; y <= h; y += grid) {
        ctx.beginPath()
        ctx.moveTo(0, y + 0.5)
        ctx.lineTo(w, y + 0.5)
        ctx.stroke()
      }
      // jaali keystone dots at intersections (every 2nd × 2nd)
      ctx.fillStyle = dark
        ? 'rgba(198, 95, 63, 0.45)'    // terracotta in dark
        : 'rgba(198, 95, 63, 0.30)'    // terracotta in light
      for (let x = grid; x < w; x += grid * 2) {
        for (let y = grid; y < h; y += grid * 2) {
          ctx.beginPath()
          ctx.arc(x, y, 1.6, 0, Math.PI * 2)
          ctx.fill()
        }
      }
    }

    const drawLights = () => {
      const dark = isDark()
      // terracotta in light mode, warm copper-orange in dark mode (more luminous)
      const core = dark ? 'rgba(255, 173, 122, '   : 'rgba(198, 95, 63, '
      const halo = dark ? 'rgba(255, 173, 122, '   : 'rgba(198, 95, 63, '
      lights.forEach((l) => {
        const r = 22
        const grad = ctx.createRadialGradient(l.x, l.y, 0, l.x, l.y, r)
        grad.addColorStop(0,    `${halo}${l.brightness * 0.85})`)
        grad.addColorStop(0.45, `${halo}${l.brightness * 0.32})`)
        grad.addColorStop(1,    `${halo}0)`)
        ctx.fillStyle = grad
        ctx.beginPath()
        ctx.arc(l.x, l.y, r, 0, Math.PI * 2)
        ctx.fill()
        // bright core
        ctx.fillStyle = `${core}${l.brightness})`
        ctx.beginPath()
        ctx.arc(l.x, l.y, 1.8, 0, Math.PI * 2)
        ctx.fill()
      })
    }

    const tick = (now) => {
      if (stopped) return
      const dt = lastTime ? now - lastTime : 16
      lastTime = now
      const w = canvas.width / dpr
      const h = canvas.height / dpr

      lights.forEach((l, i) => {
        l.t += (l.speed * dt) / 1000
        if (l.dir === 'h') l.x = l.t * l.tx
        else l.y = l.t * l.ty
        if (l.t >= 1) lights.splice(i, 1)
      })
      if (Math.random() < 0.025 && lights.length < 7) lights.push(spawnLight())

      drawGrid(w, h)
      drawLights()
      rafRef.current = requestAnimationFrame(tick)
    }

    resize()
    window.addEventListener('resize', resize)
    rafRef.current = requestAnimationFrame(tick)

    return () => {
      stopped = true
      window.removeEventListener('resize', resize)
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className="hero-grid-canvas"
      aria-hidden
    />
  )
}
