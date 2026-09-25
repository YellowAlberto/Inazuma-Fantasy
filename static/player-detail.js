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

    function renderValueChart(history) {
      var wrap = document.getElementById('player-detail-value-chart');
      if (history.length < 2) {
        wrap.innerHTML = '<p class="muted">Todavía no hay suficientes jornadas jugadas para mostrar la evolución.</p>';
        return;
      }
      var w = 100, h = 40, pad = 4;
      var values = history.map(function (d) { return d.value_millions; });
      var min = Math.min.apply(null, values);
      var max = Math.max.apply(null, values);
      if (min === max) { min -= 1; max += 1; }
      var stepX = (w - pad * 2) / (history.length - 1);
      var points = history.map(function (d, i) {
        var x = pad + i * stepX;
        var y = h - pad - ((d.value_millions - min) / (max - min)) * (h - pad * 2);
        return x.toFixed(2) + ',' + y.toFixed(2);
      });
      var dots = history.map(function (d, i) {
        var x = pad + i * stepX;
        var y = h - pad - ((d.value_millions - min) / (max - min)) * (h - pad * 2);
        return '<circle cx="' + x.toFixed(2) + '" cy="' + y.toFixed(2) + '" r="1.6" fill="var(--yellow)"></circle>';
      }).join('');
      var svg =
        '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" class="value-chart-svg">' +
        '<polyline points="' + points.join(' ') + '" fill="none" stroke="var(--blue-accent)" stroke-width="1.4" vector-effect="non-scaling-stroke"></polyline>' +
        dots +
        '</svg>';
      var first = history[0], last = history[history.length - 1];
      wrap.innerHTML =
        '<div class="value-chart-wrap">' + svg + '</div>' +
        '<div class="value-chart-labels"><span>J' + first.gameweek + ': ' + first.value_label + '</span>' +
        '<span>J' + last.gameweek + ': ' + last.value_label + '</span></div>';
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
