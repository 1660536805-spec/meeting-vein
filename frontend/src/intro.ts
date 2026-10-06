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
