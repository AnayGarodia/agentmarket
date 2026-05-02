// Animated jaali grid — canvas-rendered grid with traveling light particles.
// Adapted from 21st.dev grid-hero, palette-mapped to Aztea tokens.
//
// Performance:
//   - Capped to 30fps (the slow drift doesn't need 60fps).
//   - Pauses entirely when hero leaves the viewport (IntersectionObserver).
//   - Pauses when the tab is hidden (visibilitychange).
//   - Honours prefers-reduced-motion: renders a static grid, no RAF loop.
//   - Resize is debounced; grid is re-drawn only on resize/theme change.
//
// Theme: reads document.documentElement.dataset.theme — colours flip live.

import { useEffect, useRef } from 'react'

const FPS_CAP    = 30
const FRAME_MS   = 1000 / FPS_CAP
const GRID       = 56
const MAX_LIGHTS = 6
const SPAWN_PROB = 0.018  // per frame at FPS_CAP

function isDark() {
  return document.documentElement.dataset.theme === 'dark'
}

function prefersReducedMotion() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

export default function HeroShapes() {
  const canvasRef = useRef(null)
  const rafRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d', { alpha: true })
    if (!ctx) return

    const lights = []
    let dpr = window.devicePixelRatio || 1
    let lastFrame = 0
    let stopped = false
    let visible = true   // intersection-observer state
    let pageVisible = !document.hidden
    const reduce = prefersReducedMotion()

    const resize = () => {
      const rect = canvas.getBoundingClientRect()
      dpr = Math.min(window.devicePixelRatio || 1, 2)  // cap DPR — grid doesn't need retina sharpness
      canvas.width  = Math.round(rect.width  * dpr)
      canvas.height = Math.round(rect.height * dpr)
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.scale(dpr, dpr)
      drawGrid()
    }

    const drawGrid = () => {
      const w = canvas.width  / dpr
      const h = canvas.height / dpr
      const dark = isDark()
      ctx.clearRect(0, 0, w, h)

      ctx.strokeStyle = dark
        ? 'rgba(255, 247, 233, 0.06)'
        : 'rgba(16, 43, 47, 0.07)'
      ctx.lineWidth = 1
      ctx.beginPath()
      for (let x = 0; x <= w; x += GRID) { ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, h) }
      for (let y = 0; y <= h; y += GRID) { ctx.moveTo(0, y + 0.5); ctx.lineTo(w, y + 0.5) }
      ctx.stroke()

      // jaali keystone dots at every other intersection
      ctx.fillStyle = dark
        ? 'rgba(198, 95, 63, 0.45)'
        : 'rgba(198, 95, 63, 0.30)'
      for (let x = GRID; x < w; x += GRID * 2) {
        for (let y = GRID; y < h; y += GRID * 2) {
          ctx.beginPath()
          ctx.arc(x, y, 1.6, 0, Math.PI * 2)
          ctx.fill()
        }
      }
    }

    const drawLights = () => {
      const dark = isDark()
      const tone = dark ? 'rgba(255, 173, 122, ' : 'rgba(198, 95, 63, '
      lights.forEach((l) => {
        const r = 22
        const grad = ctx.createRadialGradient(l.x, l.y, 0, l.x, l.y, r)
        grad.addColorStop(0,    `${tone}${l.brightness * 0.85})`)
        grad.addColorStop(0.45, `${tone}${l.brightness * 0.32})`)
        grad.addColorStop(1,    `${tone}0)`)
        ctx.fillStyle = grad
        ctx.beginPath()
        ctx.arc(l.x, l.y, r, 0, Math.PI * 2)
        ctx.fill()
        ctx.fillStyle = `${tone}${l.brightness})`
        ctx.beginPath()
        ctx.arc(l.x, l.y, 1.8, 0, Math.PI * 2)
        ctx.fill()
      })
    }

    const spawnLight = () => {
      const w = canvas.width  / dpr
      const h = canvas.height / dpr
      const horizontal = Math.random() > 0.5
      const speed = 0.18 + Math.random() * 0.32
      const brightness = 0.55 + Math.random() * 0.45
      if (horizontal) {
        const y = Math.floor(Math.random() * (h / GRID)) * GRID
        return { x: 0, y, tx: w, ty: y, speed, brightness, dir: 'h', t: 0 }
      }
      const x = Math.floor(Math.random() * (w / GRID)) * GRID
      return { x, y: 0, tx: x, ty: h, speed, brightness, dir: 'v', t: 0 }
    }

    const tick = (now) => {
      if (stopped) return
      // skip frame if too soon (FPS cap)
      if (now - lastFrame < FRAME_MS) {
        rafRef.current = requestAnimationFrame(tick)
        return
      }
      const dt = lastFrame ? Math.min(now - lastFrame, 64) : FRAME_MS
      lastFrame = now

      // update lights
      for (let i = lights.length - 1; i >= 0; i--) {
        const l = lights[i]
        l.t += (l.speed * dt) / 1000
        if (l.dir === 'h') l.x = l.t * l.tx
        else l.y = l.t * l.ty
        if (l.t >= 1) lights.splice(i, 1)
      }
      if (Math.random() < SPAWN_PROB && lights.length < MAX_LIGHTS) lights.push(spawnLight())

      drawGrid()
      drawLights()
      rafRef.current = requestAnimationFrame(tick)
    }

    const start = () => {
      if (rafRef.current || reduce) return
      lastFrame = 0
      rafRef.current = requestAnimationFrame(tick)
    }
    const pause = () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
    }

    // Debounced resize
    let resizeTimer = 0
    const onResize = () => {
      clearTimeout(resizeTimer)
      resizeTimer = setTimeout(resize, 120)
    }

    // Pause when offscreen
    const io = new IntersectionObserver(
      (entries) => {
        visible = entries[0]?.isIntersecting ?? true
        if (visible && pageVisible) start()
        else pause()
      },
      { threshold: 0.01 }
    )
    io.observe(canvas)

    const onVis = () => {
      pageVisible = !document.hidden
      if (pageVisible && visible) start()
      else pause()
    }
    document.addEventListener('visibilitychange', onVis)

    // Theme change → redraw grid colours instantly even if paused
    const themeObserver = new MutationObserver(() => drawGrid())
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })

    resize()
    window.addEventListener('resize', onResize)
    if (reduce) {
      // static render — no animation loop
      drawGrid()
    } else {
      start()
    }

    return () => {
      stopped = true
      pause()
      io.disconnect()
      themeObserver.disconnect()
      window.removeEventListener('resize', onResize)
      document.removeEventListener('visibilitychange', onVis)
      clearTimeout(resizeTimer)
    }
  }, [])

  return <canvas ref={canvasRef} className="hero-grid-canvas" aria-hidden />
}
