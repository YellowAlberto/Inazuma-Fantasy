// Global player-detail popup: works on any page that renders a
// `.player-card-clickable` element with `data-player-id` and
// `data-league-id` attributes (market, mi equipo, ver equipo, etc.).
// The modal markup itself lives once in base.html.
(function () {
  document.addEventListener('DOMContentLoaded', function () {
    var modal = document.getElementById('player-detail-modal');
    if (!modal) return;

    var loadingEl = document.getElementById('player-detail-loading');
    var contentEl = document.getElementById('player-detail-content');
    var closeBtn = document.getElementById('player-detail-close');
    var statMax = 180;

    function openModal() {
      modal.style.display = 'flex';
      loadingEl.style.display = 'block';
      loadingEl.textContent = 'Cargando…';
      contentEl.style.display = 'none';
    }
    function closeModal() {
      modal.style.display = 'none';
    }
    closeBtn.addEventListener('click', closeModal);
    modal.addEventListener('click', function (e) {
      if (e.target === modal) closeModal();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && modal.style.display !== 'none') closeModal();
    });

    function renderStats(stats) {
      var wrap = document.getElementById('player-detail-stats');
      wrap.innerHTML = '';
      Object.keys(stats).forEach(function (label) {
        var value = stats[label] || 0;
        var pct = Math.max(4, Math.min(100, (value / statMax) * 100));
        var row = document.createElement('div');
        row.className = 'stat-bar-row';
        row.innerHTML =
          '<span class="stat-bar-label">' + label + '</span>' +
          '<div class="stat-bar-track"><div class="stat-bar-fill" style="width:' + pct + '%"></div></div>' +
          '<span class="stat-bar-value">' + value + '</span>';
        wrap.appendChild(row);
      });
    }

    function renderPoints(history) {
      var wrap = document.getElementById('player-detail-points');
      if (!history.length) {
        wrap.innerHTML = '<p class="muted">Todavía no se ha jugado ninguna jornada.</p>';
        return;
      }
      var rows = history.map(function (h) {
        return '<tr><td>Jornada ' + h.gameweek + '</td><td><strong>' + h.points + '</strong></td></tr>';
      }).join('');
      wrap.innerHTML =
        '<table class="simple-table player-points-table"><thead><tr><th>Jornada</th><th>Puntos</th></tr></thead><tbody>' +
        rows + '</tbody></table>';
    }

    // ---------------------------------------------------------------
    // Gráfica de evolución del valor de mercado
    // ---------------------------------------------------------------
    // Área rellena con rejilla y valores al lado, al estilo de las apps de
    // fantasy. El color dice si el jugador sube o baja, PERO nunca va solo:
    // al lado siempre está el porcentaje con su signo y una flecha, porque
    // el verde y el rojo no se distinguen con daltonismo.
    var TREND_COLOR = { up: '#33c07a', down: '#ff4d5e', flat: '#3fa4ff' };

    // Escoge una escala con números redondos (0,2 / 0,5 / 1...) para que las
    // etiquetas del eje se lean bien en vez de salir 6,37M €.
    function niceScale(min, max, targetTicks) {
      if (!(max > min)) {
        var pad = Math.abs(max) * 0.08 || 0.5;
        min = max - pad;
        max = max + pad;
      }
      var raw = (max - min) / Math.max(1, targetTicks - 1);
      var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
      var norm = raw / mag;
      var step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
      // El epsilon es necesario: 0,6/0,2 da 2,9999... en coma flotante, y sin
      // él la escala se iba un escalón de más y pintaba una línea sobrante.
      var lo = Math.floor(min / step + 1e-9) * step;
      var hi = Math.ceil(max / step - 1e-9) * step;
      var ticks = [];
      for (var v = lo; v <= hi + step * 1e-9; v += step) ticks.push(Math.round(v / step) * step);
      return { lo: lo, hi: hi, step: step, ticks: ticks };
    }

    // Todas las etiquetas del eje en la misma unidad, para no mezclar k y M.
    function axisLabel(v, step, useK) {
      if (useK) return Math.round(v * 1000) + 'k €';
      var dec = step < 0.1 ? 2 : step < 1 ? 1 : 0;
      return v.toFixed(dec).replace('.', ',') + 'M €';
    }

    function renderValueChart(history) {
      var wrap = document.getElementById('player-detail-value-chart');
      if (!history || history.length < 2) {
        wrap.innerHTML = '<p class="muted">Todavía no hay suficientes jornadas jugadas para mostrar la evolución.</p>';
        return;
      }

      var n = history.length;
      var values = history.map(function (d) { return d.value_millions; });
      var first = values[0], last = values[n - 1];
      var dir = last > first ? 'up' : (last < first ? 'down' : 'flat');
      var color = TREND_COLOR[dir];
      var pct = first ? ((last - first) / Math.abs(first)) * 100 : 0;
      var arrow = dir === 'up' ? '▲' : (dir === 'down' ? '▼' : '=');
      var pctText = (pct > 0 ? '+' : '') + pct.toFixed(1).replace('.', ',') + ' %';

      var scale = niceScale(Math.min.apply(null, values), Math.max.apply(null, values), 4);
      var useK = scale.hi < 1;

      // Lienzo sin deformar (nada de preserveAspectRatio="none"): si se
      // estirase, el texto de los ejes saldría aplastado.
      var W = 320, H = 138;
      var padL = 8, padR = 58, padT = 20, padB = 12;
      var x0 = padL, x1 = W - padR, y0 = padT, y1 = H - padB;

      // Un pequeño margen arriba y abajo para que el trazo no quede pegado al
      // borde del recuadro cuando toca el valor máximo o mínimo.
      var inset = 7;
      var yTop = y0 + inset, yBot = y1 - inset;

      function sx(i) { return n === 1 ? (x0 + x1) / 2 : x0 + (i * (x1 - x0)) / (n - 1); }
      function sy(v) { return yBot - ((v - scale.lo) / (scale.hi - scale.lo)) * (yBot - yTop); }

      // Rejilla: líneas finas y continuas, con su valor a la derecha.
      var grid = scale.ticks.map(function (t) {
        var y = sy(t).toFixed(1);
        return '<line x1="' + x0 + '" y1="' + y + '" x2="' + x1 + '" y2="' + y + '" class="vc-grid"></line>' +
               '<text x="' + (x1 + 8) + '" y="' + y + '" class="vc-axis-y">' + axisLabel(t, scale.step, useK) + '</text>';
      }).join('');

      // Etiquetas de jornada arriba; si hay muchas, se reparten unas pocas.
      // La última jornada siempre se muestra, y si la anterior le queda encima
      // se quita: si no, con muchas jornadas salían dos rótulos pisados.
      var maxLabels = 6;
      var stepIdx = Math.max(1, Math.ceil(n / maxLabels));
      var idxs = [];
      for (var i = 0; i < n; i += stepIdx) idxs.push(i);
      if (idxs[idxs.length - 1] !== n - 1) {
        if (n - 1 - idxs[idxs.length - 1] < stepIdx * 0.6) idxs.pop();
        idxs.push(n - 1);
      }
      var xLabels = idxs.map(function (k) {
        var anchor = k === 0 ? 'start' : (k === n - 1 ? 'end' : 'middle');
        return '<text x="' + sx(k).toFixed(1) + '" y="' + (y0 - 8) + '" class="vc-axis-x" text-anchor="' +
               anchor + '">J' + history[k].gameweek + '</text>';
      }).join('');

      var linePts = history.map(function (d, k) { return sx(k).toFixed(1) + ',' + sy(d.value_millions).toFixed(1); });
      var areaPts = [x0 + ',' + y1].concat(linePts).concat([x1 + ',' + y1]);
      var gid = 'vc-grad-' + Math.random().toString(36).slice(2, 8);

      var endX = sx(n - 1).toFixed(1), endY = sy(last).toFixed(1);

      var svg =
        '<svg viewBox="0 0 ' + W + ' ' + H + '" class="value-chart-svg" role="img" ' +
        'aria-label="Evolución del valor: ' + history[0].value_label + ' en la jornada ' + history[0].gameweek +
        ', ' + history[n - 1].value_label + ' en la jornada ' + history[n - 1].gameweek + ' (' + pctText + ')">' +
        '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0%" stop-color="' + color + '" stop-opacity="0.34"></stop>' +
        '<stop offset="100%" stop-color="' + color + '" stop-opacity="0.03"></stop>' +
        '</linearGradient></defs>' +
        grid + xLabels +
        '<polygon points="' + areaPts.join(' ') + '" fill="url(#' + gid + ')"></polygon>' +
        '<polyline points="' + linePts.join(' ') + '" fill="none" stroke="' + color + '" stroke-width="2" ' +
        'stroke-linejoin="round" stroke-linecap="round"></polyline>' +
        '<line class="vc-cross" x1="0" y1="' + y0 + '" x2="0" y2="' + y1 + '" style="display:none"></line>' +
        '<circle class="vc-hover-dot" r="4" fill="' + color + '" stroke="#0a1130" stroke-width="2" style="display:none"></circle>' +
        '<circle cx="' + endX + '" cy="' + endY + '" r="4" fill="' + color + '" stroke="#0a1130" stroke-width="2"></circle>' +
        '</svg>';

      wrap.innerHTML =
        '<div class="value-chart-head">' +
        '<span class="value-chart-current">' + history[n - 1].value_label + '</span>' +
        '<span class="value-chart-delta vc-' + dir + '">' + arrow + ' ' + pctText + '</span>' +
        '</div>' +
        '<div class="value-chart-wrap">' + svg + '<div class="vc-tooltip" style="display:none"></div></div>';

      attachChartHover(wrap, history, { sx: sx, sy: sy, x0: x0, x1: x1, W: W, H: H, n: n });
    }

    // Capa de exploración: al pasar el ratón (o el dedo) se marca la jornada
    // más cercana y se muestra su valor exacto. Los valores también se pueden
    // leer sin interactuar, en la rejilla y en la tabla de puntos por jornada.
    function attachChartHover(wrap, history, geo) {
      var svg = wrap.querySelector('.value-chart-svg');
      var tip = wrap.querySelector('.vc-tooltip');
      var cross = wrap.querySelector('.vc-cross');
      var dot = wrap.querySelector('.vc-hover-dot');
      if (!svg || !tip) return;

      function hide() {
        tip.style.display = 'none';
        cross.style.display = 'none';
        dot.style.display = 'none';
      }

      function move(clientX) {
        var rect = svg.getBoundingClientRect();
        if (!rect.width) return;
        // de píxeles de pantalla a unidades del viewBox
        var vx = ((clientX - rect.left) / rect.width) * geo.W;
        var t = geo.x1 === geo.x0 ? 0 : (vx - geo.x0) / (geo.x1 - geo.x0);
        var idx = Math.round(t * (geo.n - 1));
        idx = Math.max(0, Math.min(geo.n - 1, idx));

        var d = history[idx];
        var px = geo.sx(idx), py = geo.sy(d.value_millions);
        cross.setAttribute('x1', px); cross.setAttribute('x2', px);
        cross.style.display = '';
        dot.setAttribute('cx', px); dot.setAttribute('cy', py);
        dot.style.display = '';

        tip.innerHTML = '<b>Jornada ' + d.gameweek + '</b>' + d.value_label;
        tip.style.display = 'block';
        tip.style.left = ((px / geo.W) * 100) + '%';
        tip.style.top = ((py / geo.H) * 100) + '%';
      }

      svg.addEventListener('mousemove', function (e) { move(e.clientX); });
      svg.addEventListener('mouseleave', hide);
      svg.addEventListener('touchstart', function (e) {
        if (e.touches.length) move(e.touches[0].clientX);
      }, { passive: true });
      svg.addEventListener('touchmove', function (e) {
        if (e.touches.length) move(e.touches[0].clientX);
      }, { passive: true });
      svg.addEventListener('touchend', hide);
    }

    function loadPlayer(leagueId, playerId) {
      openModal();
      fetch('/leagues/' + leagueId + '/players/' + playerId + '/detail')
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data.error) {
            loadingEl.textContent = data.error;
            return;
          }
          document.getElementById('player-detail-name').textContent = data.nombre;
          var metaEl = document.getElementById('player-detail-meta');
          metaEl.textContent = '';
          metaEl.appendChild(document.createTextNode(data.posicion_label + ' · '));
          if (data.elemento_icon_url) {
            var elIcon = document.createElement('img');
            elIcon.src = data.elemento_icon_url;
            elIcon.alt = '';
            elIcon.className = 'element-icon';
            metaEl.appendChild(elIcon);
          }
          metaEl.appendChild(document.createTextNode(data.elemento));
          if (data.arquetipo) {
            metaEl.appendChild(document.createTextNode(' · '));
            if (data.arquetipo_icon_url) {
              var arqIcon = document.createElement('img');
              arqIcon.src = data.arquetipo_icon_url;
              arqIcon.alt = '';
              arqIcon.className = 'element-icon tag-icon-arch';
              metaEl.appendChild(arqIcon);
            }
            metaEl.appendChild(document.createTextNode(data.arquetipo));
          }
          metaEl.appendChild(document.createTextNode(' · ' + data.juego));
          document.getElementById('player-detail-value').textContent = data.current_value_label;
          document.getElementById('player-detail-total-points').textContent = data.total_points;

          var spriteWrap = document.getElementById('player-detail-sprite-wrap');
          spriteWrap.innerHTML = data.sprite_url
            ? '<img src="' + data.sprite_url + '" alt="' + data.nombre + '" class="player-detail-sprite">'
            : '<div class="player-detail-sprite player-detail-sprite-placeholder">⚽</div>';

          renderStats(data.stats);
          renderPoints(data.points_history);
          renderValueChart(data.value_history);

          loadingEl.style.display = 'none';
          contentEl.style.display = 'block';
        })
        .catch(function () {
          loadingEl.textContent = 'No se pudo cargar la información del jugador.';
        });
    }

    function findTrigger(target) {
      return target.closest ? target.closest('.player-card-clickable') : null;
    }

    document.addEventListener('click', function (e) {
      var trigger = findTrigger(e.target);
      if (!trigger) return;
      var playerId = trigger.getAttribute('data-player-id');
      var leagueId = trigger.getAttribute('data-league-id');
      if (!playerId || !leagueId) return;
      loadPlayer(leagueId, playerId);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var trigger = findTrigger(e.target);
      if (!trigger) return;
      var playerId = trigger.getAttribute('data-player-id');
      var leagueId = trigger.getAttribute('data-league-id');
      if (!playerId || !leagueId) return;
      e.preventDefault();
      loadPlayer(leagueId, playerId);
    });
  });
})();
