// Lets you click a player on the pitch diagram and swap them for a bench
// player of the same position, by toggling the (now hidden) "player_ids"
// checkboxes behind the scenes. Esos input son los que llevan la alineación
// al enviar el formulario. No framework, no build step — just plain DOM code.
document.addEventListener('DOMContentLoaded', function () {
  var squadDataEl = document.getElementById('squad-data');
  if (!squadDataEl) return;

  var squad = JSON.parse(squadDataEl.textContent);

  function squadPlayerById(id) {
    return squad.find(function (p) { return p.id === id; });
  }

  function getCheckbox(playerId) {
    return document.querySelector('input[name="player_ids"][value="' + playerId + '"]');
  }

  function closePopup() {
    var existing = document.querySelector('.pitch-swap-popup');
    if (existing) existing.remove();
  }

  // --- Capitán -------------------------------------------------------------
  // El brazalete vive en un input oculto del formulario, así que basta con
  // moverlo ahí y repintar; se guarda junto con la alineación.
  var captainInput = document.getElementById('captain-id');

  function captainId() {
    return captainInput ? parseInt(captainInput.value, 10) || 0 : 0;
  }

  function setCaptain(playerId) {
    if (!captainInput) return;
    captainInput.value = playerId || '';
    document.querySelectorAll('.pitch-player').forEach(function (s) {
      s.classList.toggle('is-captain', parseInt(s.dataset.playerId, 10) === playerId);
    });
  }

  // Como la casilla "Titular" ya no se ve, la carta de la plantilla es ahora
  // la única señal de quién juega: al intercambiar dos jugadores marcamos y
  // desmarcamos su carta en el momento, sin esperar a recargar la página.
  function setCardStarter(playerId, isStarter) {
    var clickable = document.querySelector('.player-card-clickable[data-player-id="' + playerId + '"]');
    if (!clickable) return;
    var card = clickable.closest('.player-card');
    if (card) card.classList.toggle('player-card-starter', isStarter);
  }

  function updateSlotVisual(slot, player) {
    slot.dataset.playerId = player.id;
    slot.classList.remove('pitch-slot-empty');
    slot.title = 'Clic para cambiarlo o nombrarlo capitán';

    var badge = slot.querySelector('.pitch-player-badge');
    badge.classList.remove('pitch-player-badge-empty');
    if (player.sprite_url) {
      badge.innerHTML = '<img src="' + player.sprite_url + '" alt="' + player.nombre + '">';
    } else {
      badge.innerHTML = '<span class="pitch-player-placeholder">⚽</span>';
    }
    // La chapa del capitán cuelga del slot, no del badge (que la recortaría),
    // así que innerHTML no se la lleva por delante; pero una casilla que
    // estaba vacía no la tiene todavía.
    if (!slot.querySelector('.captain-badge')) {
      var c = document.createElement('span');
      c.className = 'captain-badge';
      c.title = 'Capitán';
      c.textContent = 'C';
      slot.insertBefore(c, slot.querySelector('.pitch-player-name'));
    }

    slot.querySelector('.pitch-player-name').textContent = player.nombre.split(' ')[0];
  }

  // ------------------------------------------------------------------
  // Panel de afinidad en vivo
  // ------------------------------------------------------------------
  // Cuenta los arquetipos de los titulares que tengas puestos AHORA y dice
  // qué ganas. Las reglas (cuántos hacen falta, cuántos puntos da cada
  // escalón, qué hace cada efecto) vienen del servidor en #affinity-rules,
  // que las saca de scoring.py: así no hay una segunda copia que se quede
  // vieja si algún día cambian los números del juego.
  var rulesEl = document.getElementById('affinity-rules');
  var RULES = null;
  try { RULES = rulesEl ? JSON.parse(rulesEl.textContent) : null; } catch (e) { RULES = null; }

  var ICONS = {
    'Justicia': 'justicia.png', 'Contraataque': 'contraataque.png',
    'Juego Sucio': 'juego_sucio.png', 'Tensión': 'tension.png',
    'Afinidad': 'afinidad.png', 'Brecha': 'brecha.png'
  };

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // Mismo cálculo que scoring.affinity_scale()
  function scaleFor(count) {
    if (count < RULES.match_threshold) return 0;
    return Math.min(RULES.scale_cap, 1 + (count - RULES.match_threshold) * RULES.scale_per_extra);
  }

  // Mismo recorrido que scoring.compute_affinity_bonuses()
  function bonusFor(count) {
    for (var i = 0; i < RULES.thresholds.length; i++) {
      if (count >= RULES.thresholds[i][0]) return RULES.thresholds[i][1];
    }
    return 0;
  }

  // Sólo los primeros N jugadores de un arquetipo cobran el bono, igual que
  // el tope de scoring.compute_affinity_bonuses(). Sin esto el panel prometía
  // +16 con ocho jugadores cuando el servidor sólo paga +14.
  function cobran(count) {
    var tope = RULES.bonus_player_cap;
    return tope ? Math.min(count, tope) : count;
  }

  function starters() {
    return squad.filter(function (p) {
      var cb = getCheckbox(p.id);
      return cb && cb.checked;
    });
  }

  function iconTag(arq) {
    var f = ICONS[arq];
    return f ? '<img class="tag-icon tag-icon-arch" src="/static/icons/archetypes/' + f + '" alt="">' : '';
  }

  function renderAffinity() {
    var body = document.getElementById('affinity-body');
    var countEl = document.getElementById('affinity-count');
    if (!body || !RULES) return;

    var line = starters();
    if (countEl) countEl.textContent = line.length + '/11 titulares';

    // "Unknown" no es un arquetipo de verdad, no suma (igual que en el servidor)
    var counts = {};
    line.forEach(function (p) {
      if (!p.arquetipo || p.arquetipo === 'Unknown') return;
      counts[p.arquetipo] = (counts[p.arquetipo] || 0) + 1;
    });

    var all = Object.keys(RULES.effects).map(function (arq) {
      var n = counts[arq] || 0;
      return { arq: arq, n: n, bonus: bonusFor(n), scale: scaleFor(n) };
    }).sort(function (a, b) { return b.n - a.n; });

    var activas = all.filter(function (x) { return x.n >= RULES.match_threshold; });
    var cerca = all.filter(function (x) {
      return x.n > 0 && x.n < RULES.match_threshold && x.n >= RULES.match_threshold - 2;
    });
    var totalPts = activas.reduce(function (t, x) { return t + x.bonus * cobran(x.n); }, 0);

    var html = '';

    if (activas.length) {
      html += '<div class="aff-group">';
      activas.forEach(function (x) {
        var pct = Math.min(100, (x.n / 11) * 100);
        html += '<div class="aff-item on">' +
          '<div class="aff-top">' + iconTag(x.arq) +
            '<b>' + esc(x.arq) + '</b>' +
            '<span class="aff-n">' + x.n + ' jug.</span>' +
          '</div>' +
          '<div class="aff-bar"><i style="width:' + pct.toFixed(0) + '%"></i></div>' +
          '<div class="aff-gain">+' + x.bonus + ' pto' + (x.bonus === 1 ? '' : 's') +
            (cobran(x.n) < x.n
              ? ' a ' + cobran(x.n) + ' de ellos (es el máximo)'
              : ' a cada uno') +
            ' · intensidad ×' + x.scale.toFixed(2).replace('.', ',') + '</div>' +
          '<div class="aff-eff">' + esc(RULES.effects[x.arq]) + '</div>' +
        '</div>';
      });
      html += '</div>';
      html += '<div class="aff-total">Extra esta jornada: <b>+' + totalPts + ' puntos</b></div>';
    } else {
      html += '<p class="aff-empty">Ningún arquetipo llega todavía a ' + RULES.match_threshold +
              ' titulares, así que tu equipo no tiene ninguna ventaja activa.</p>';
    }

    if (cerca.length) {
      html += '<div class="aff-near-title">A un paso</div>';
      cerca.forEach(function (x) {
        var faltan = RULES.match_threshold - x.n;
        var pct = (x.n / RULES.match_threshold) * 100;
        html += '<div class="aff-item">' +
          '<div class="aff-top">' + iconTag(x.arq) +
            '<b>' + esc(x.arq) + '</b>' +
            '<span class="aff-n">' + x.n + '/' + RULES.match_threshold + '</span>' +
          '</div>' +
          '<div class="aff-bar"><i style="width:' + pct.toFixed(0) + '%"></i></div>' +
          '<div class="aff-gain muted">Te falta' + (faltan === 1 ? '' : 'n') + ' ' + faltan +
            ' para activarla</div>' +
        '</div>';
      });
    }

    body.innerHTML = html;
  }

  renderAffinity();

  // ------------------------------------------------------------------
  // Tarjeta al pasar el ratón por un jugador del campo
  // ------------------------------------------------------------------
  // Vive colgada del <body>, no dentro del campo: el campo tiene
  // overflow:hidden y recortaría la tarjeta en cuanto se saliera un poco.
  var mapsEl = document.getElementById('ui-maps');
  var MAPS = null;
  try { MAPS = mapsEl ? JSON.parse(mapsEl.textContent) : null; } catch (e) { MAPS = null; }

  var STATS = [
    ['potencia', 'Potencia'], ['control', 'Control'], ['tecnica', 'Técnica'],
    ['presion', 'Presión'], ['fisico', 'Físico'], ['agilidad', 'Agilidad'],
    ['inteligencia', 'Inteligencia']
  ];
  var STAT_MAX = 180;   // el mismo tope que usa la ficha completa del jugador

  var hoverCard = null;
  function ensureCard() {
    if (hoverCard) return hoverCard;
    hoverCard = document.createElement('div');
    hoverCard.className = 'pitch-hover-card';
    hoverCard.hidden = true;
    document.body.appendChild(hoverCard);
    return hoverCard;
  }

  // cuántos titulares comparten cada arquetipo ahora mismo
  function starterArchetypeCounts() {
    var c = {};
    starters().forEach(function (p) {
      if (!p.arquetipo || p.arquetipo === 'Unknown') return;
      c[p.arquetipo] = (c[p.arquetipo] || 0) + 1;
    });
    return c;
  }

  function cardHTML(p) {
    var elEs = (MAPS && MAPS.element_es[p.elemento]) || p.elemento || '—';
    var elIcon = MAPS && MAPS.element_icon[p.elemento];
    var arqIcon = MAPS && p.arquetipo && MAPS.archetype_icon[p.arquetipo];
    var pos = (MAPS && MAPS.position_labels[p.posicion]) || p.posicion;

    var counts = starterArchetypeCounts();
    var n = (p.arquetipo && counts[p.arquetipo]) || 0;
    var activa = RULES && n >= RULES.match_threshold;

    var stats = STATS.map(function (pair) {
      var v = p[pair[0]] || 0;
      var pct = Math.max(4, Math.min(100, (v / STAT_MAX) * 100));
      return '<div class="phc-stat">' +
        '<span class="phc-stat-name">' + pair[1] + '</span>' +
        '<span class="phc-bar"><i style="width:' + pct.toFixed(0) + '%"></i></span>' +
        '<b>' + v + '</b></div>';
    }).join('');

    var arqLinea = '';
    if (p.arquetipo && p.arquetipo !== 'Unknown') {
      arqLinea = '<div class="phc-aff' + (activa ? ' on' : '') + '">' +
        (arqIcon ? '<img src="/static/icons/archetypes/' + arqIcon + '" alt="">' : '') +
        '<span>' + esc(p.arquetipo) + '</span>' +
        '<em>' + (activa
            ? 'afinidad activa · ' + n + ' titulares'
            : (n > 1 ? n + ' titulares' : 'sin afinidad activa')) + '</em>' +
      '</div>';
    } else {
      arqLinea = '<div class="phc-aff"><span>Sin arquetipo</span><em>no suma afinidad</em></div>';
    }

    return '<div class="phc-head">' +
        (p.sprite_url ? '<img class="phc-sprite" src="' + esc(p.sprite_url) + '" alt="">'
                      : '<span class="phc-sprite phc-sprite-none">⚽</span>') +
        '<div class="phc-id">' +
          '<b>' + esc(p.nombre) + '</b>' +
          '<span>' + esc(pos) + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="phc-tags">' +
        '<span class="phc-tag">' +
          (elIcon ? '<img src="/static/icons/elements/' + elIcon + '" alt="">' : '') +
          esc(elEs) + '</span>' +
      '</div>' +
      arqLinea +
      '<div class="phc-stats">' + stats + '</div>';
  }

  function placeCard(card, anchor) {
    var r = anchor.getBoundingClientRect();
    card.hidden = false;                       // hay que medirla ya pintada
    var w = card.offsetWidth, h = card.offsetHeight;
    var margen = 12;
    // a la derecha del jugador si cabe; si no, a la izquierda
    var x = r.right + margen;
    if (x + w > window.innerWidth - 8) x = r.left - margen - w;
    if (x < 8) x = 8;
    // centrada en vertical, sin salirse por arriba ni por abajo
    var y = r.top + r.height / 2 - h / 2;
    y = Math.max(8, Math.min(y, window.innerHeight - h - 8));
    card.style.left = Math.round(x) + 'px';
    card.style.top = Math.round(y) + 'px';
  }

  function hideCard() { if (hoverCard) hoverCard.hidden = true; }

  document.querySelectorAll('.pitch-player[data-player-id]').forEach(function (slot) {
    slot.addEventListener('mouseenter', function () {
      var p = squadPlayerById(parseInt(slot.dataset.playerId, 10));
      if (!p) return;
      var card = ensureCard();
      card.innerHTML = cardHTML(p);
      placeCard(card, slot);
    });
    slot.addEventListener('mouseleave', hideCard);
  });

  // si te mueves por la página o abres el menú de cambio, la tarjeta estorba
  window.addEventListener('scroll', hideCard, { passive: true });
  document.addEventListener('click', hideCard);

  document.querySelectorAll('.pitch-player').forEach(function (slot) {
    slot.addEventListener('click', function (e) {
      e.stopPropagation();
      // el clic no llega al <document> (stopPropagation), así que la tarjeta
      // se cierra aquí para que no tape el menú de cambio
      hideCard();
      closePopup();

      var currentId = parseInt(slot.dataset.playerId, 10);
      var position = slot.dataset.position;

      var candidates = squad.filter(function (p) {
        if (p.posicion !== position) return false;
        if (p.id === currentId) return false;
        var cb = getCheckbox(p.id);
        return cb && !cb.checked;
      });

      var popup = document.createElement('div');
      popup.className = 'pitch-swap-popup';

      // El brazalete va primero: es lo que vas a tocar más a menudo una vez
      // tienes la alineación hecha.
      if (currentId !== captainId()) {
        var cap = document.createElement('button');
        cap.type = 'button';
        cap.className = 'pitch-swap-item pitch-swap-captain';
        cap.innerHTML = '<span class="pitch-swap-c">C</span><span>Nombrar capitán</span>';
        cap.addEventListener('click', function (ev) {
          ev.stopPropagation();
          setCaptain(currentId);
          closePopup();
        });
        popup.appendChild(cap);
      }

      if (candidates.length === 0) {
        if (!popup.childNodes.length) {
          popup.innerHTML = '<p class="pitch-swap-empty">No tienes suplentes libres en esta posición.</p>';
        } else {
          var nota = document.createElement('p');
          nota.className = 'pitch-swap-empty';
          nota.textContent = 'No tienes suplentes libres en esta posición.';
          popup.appendChild(nota);
        }
      } else {
        candidates.forEach(function (c) {
          var item = document.createElement('button');
          item.type = 'button';
          item.className = 'pitch-swap-item';
          item.innerHTML =
            (c.sprite_url ? '<img src="' + c.sprite_url + '" alt="">' : '<span>⚽</span>') +
            '<span>' + c.nombre + '</span>';

          item.addEventListener('click', function (ev) {
            ev.stopPropagation();
            var outgoingCb = getCheckbox(currentId);
            var incomingCb = getCheckbox(c.id);
            if (outgoingCb) outgoingCb.checked = false;
            if (incomingCb) incomingCb.checked = true;

            setCardStarter(currentId, false);
            setCardStarter(c.id, true);

            updateSlotVisual(slot, c);
            // Si el que sale llevaba el brazalete, se lo queda el que entra:
            // si no, el capitán se iría al banquillo y la jornada se jugaría
            // sin él sin que te enteres.
            if (captainId() === currentId) setCaptain(c.id);
            renderAffinity();   // el panel se recalcula con el cambio hecho
            closePopup();
          });

          popup.appendChild(item);
        });
      }

      // El menú cuelga del <body>, no del jugador: dentro del campo lo
      // recortaba el overflow:hidden, y al portero (última fila) se le abría
      // hacia abajo, fuera del campo, así que no se podía usar.
      document.body.appendChild(popup);
      placePopup(popup, slot);
    });
  });

  // Debajo del jugador si cabe; si no, encima. Y sin salirse por los lados.
  function placePopup(popup, anchor) {
    var r = anchor.getBoundingClientRect();
    var w = popup.offsetWidth, h = popup.offsetHeight;
    var hueco = 8;

    var top = r.bottom + hueco;
    if (top + h > window.innerHeight - 8) {
      var arriba = r.top - hueco - h;
      // Sólo se voltea hacia arriba si ahí cabe ENTERO. Comprobar únicamente
      // que no se salga por arriba no basta: con el jugador fuera de la
      // pantalla, "arriba" también queda fuera y el menú se iba igualmente.
      top = (arriba >= 8 && arriba + h <= window.innerHeight - 8)
        ? arriba
        : Math.max(8, window.innerHeight - h - 8);
    }

    var left = r.left + r.width / 2 - w / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));

    popup.style.left = Math.round(left) + 'px';
    popup.style.top = Math.round(top) + 'px';
  }

  // Al ir en position:fixed, el menú se quedaría flotando si se hace scroll.
  window.addEventListener('scroll', closePopup, { passive: true });
  window.addEventListener('resize', closePopup);

  document.addEventListener('click', closePopup);
});
