const cards = document.querySelector<HTMLElement>('.capability-list');

if (cards && window.matchMedia('(hover: hover) and (prefers-reduced-motion: no-preference)').matches) {
  cards.addEventListener('pointermove', (event) => {
    const card = (event.target as Element).closest<HTMLElement>('.feature-card');
    if (!card) return;
    const bounds = card.getBoundingClientRect();
    card.style.setProperty('--spot-x', `${event.clientX - bounds.left}px`);
    card.style.setProperty('--spot-y', `${event.clientY - bounds.top}px`);
  });
}

// Keep the supplied demo's 1600 × 900 cover layers aligned with the responsive image.
const coverFrame = document.querySelector<HTMLElement>('.reference-frame');
const coverCanvases = document.querySelectorAll<HTMLElement>('.demo-cover-canvas');
if (coverFrame && coverCanvases.length) {
  const syncCoverScale = () => coverCanvases.forEach((canvas) => canvas.style.setProperty('--cover-scale', String(coverFrame.clientWidth / 1600)));
  new ResizeObserver(syncCoverScale).observe(coverFrame);
  syncCoverScale();
}

// Same deterministic voiceprint construction as the supplied HTML demo.
const coverWaves = document.querySelectorAll<HTMLElement>('.cover-wave-bars');
coverWaves.forEach((coverWave) => {
  for (let i = 0; i < 58; i++) {
    const distance = Math.abs((i - 28.5) / 28.5);
    const rhythm = Math.abs(Math.sin(i * .83) * Math.cos(i * .19));
    const bar = document.createElement('i');
    bar.style.setProperty('--h', `${Math.round(9 + (1 - distance * .62) * rhythm * 48)}px`);
    bar.style.setProperty('--d', `${(-i * .073).toFixed(2)}s`);
    bar.style.setProperty('--speed', `${(.67 + (i % 7) * .11).toFixed(2)}s`);
    coverWave.appendChild(bar);
  }
});
