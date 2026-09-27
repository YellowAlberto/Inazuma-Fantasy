// Capa de animación del sitio.
//
// Regla de oro: TODO lo que hay aquí es un extra. Si GSAP o canvas-confetti no
// llegan desde el CDN (o el usuario ha pedido menos animación en su sistema),
// la web se ve y funciona exactamente igual, sólo que sin movimiento. Por eso
// nunca se esconde nada con CSS esperando a que una librería lo revele: se
// anima "desde" un estado invisible hacia el estado normal (gsap.from), que ya
// es el que pinta el navegador por su cuenta.
(function () {
  'use strict';

  var reduced = false;
  try {
    reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch (e) { /* navegador antiguo: se asume que sí quiere animación */ }

  function gsapOk() { return typeof window.gsap !== 'undefined' && !reduced; }
  function confettiOk() { return typeof window.confetti === 'function' && !reduced; }

  // ------------------------------------------------------------------
  // Entrada escalonada
  // ------------------------------------------------------------------
  // Se activa poniendo data-anim="stagger" en el contenedor de una rejilla:
  //   <div class="player-grid" data-anim="stagger"> ... </div>
  // Las cartas entran una detrás de otra, como si se repartieran.
  function staggerIn(container) {
    if (!gsapOk() || !container) return;
    var items = container.children;
    if (!items.length) return;
    // Con muchísimas cartas el reparto se haría eterno, así que el retardo
    // entre una y otra se acorta a medida que hay más.
    var step = items.length > 24 ? 0.015 : (items.length > 12 ? 0.03 : 0.045);
    window.gsap.from(items, {
      y: 22,
      opacity: 0,
      scale: 0.97,
      duration: 0.45,
      ease: 'power2.out',
      stagger: step,
      overwrite: true,
      clearProps: 'transform,opacity'
    });
  }

  function initStaggers() {
    var nodes = document.querySelectorAll('[data-anim="stagger"]');
    Array.prototype.forEach.call(nodes, staggerIn);
  }

  // ------------------------------------------------------------------
  // Celebración (fin de draft, gol en la repetición)
  // ------------------------------------------------------------------
  // Se expone como window.inazumaCelebrate para que draft.js y replay.js la
  // usen sin repetir los colores ni las opciones.
  function celebrate(opts) {
    if (!confettiOk()) return;
    opts = opts || {};
    var base = {
      spread: opts.spread || 70,
      ticks: 220,
      gravity: 0.9,
      scalar: opts.scalar || 0.95,
      disableForReducedMotion: true,
      zIndex: 2000,
      colors: ['#ffd23f', '#ff7a1a', '#3fa4ff', '#33c07a']
    };
    var y = (opts.origin && opts.origin.y) || 0.7;
    var count = opts.particleCount || 70;

    if (opts.origin && typeof opts.origin.x === 'number') {
      // un solo chorro, en el punto indicado
      window.confetti(assign(base, { particleCount: count, origin: opts.origin }));
      return;
    }
    // por defecto, dos chorros desde las esquinas inferiores
    window.confetti(assign(base, { particleCount: count, angle: 60, origin: { x: 0.2, y: y } }));
    window.confetti(assign(base, { particleCount: count, angle: 120, origin: { x: 0.8, y: y } }));
  }

  function assign(a, b) {
    var out = {}, k;
    for (k in a) { if (Object.prototype.hasOwnProperty.call(a, k)) out[k] = a[k]; }
    for (k in b) { if (Object.prototype.hasOwnProperty.call(b, k)) out[k] = b[k]; }
    return out;
  }

  // ------------------------------------------------------------------
  // Contador que sube hasta su valor
  // ------------------------------------------------------------------
  // Sin GSAP escribe el número final de golpe, que es lo que se veía antes.
  function countTo(el, value, decimals) {
    if (!el) return;
    decimals = decimals || 0;
    var write = function (n) {
      el.textContent = decimals ? n.toFixed(decimals).replace('.', ',') : Math.round(n);
    };
    if (!gsapOk()) { write(value); return; }
    var obj = { v: 0 };
    window.gsap.to(obj, {
      v: value,
      duration: 0.9,
      ease: 'power2.out',
      overwrite: true,
      onUpdate: function () { write(obj.v); }
    });
  }

  window.inazumaMotion = {
    staggerIn: staggerIn,
    countTo: countTo,
    enabled: function () { return gsapOk(); }
  };
  window.inazumaCelebrate = celebrate;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initStaggers);
  } else {
    initStaggers();
  }
})();
