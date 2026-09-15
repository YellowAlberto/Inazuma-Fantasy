// Lets you click a player on the pitch diagram and swap them for a bench
// player of the same position, by toggling the existing "Titular" checkboxes
// behind the scenes. No framework, no build step — just plain DOM code.
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

  function updateSlotVisual(slot, player) {
    slot.dataset.playerId = player.id;
    slot.classList.remove('pitch-slot-empty');
    slot.title = 'Clic para cambiar';

    var badge = slot.querySelector('.pitch-player-badge');
    badge.classList.remove('pitch-player-badge-empty');
    if (player.sprite_url) {
      badge.innerHTML = '<img src="' + player.sprite_url + '" alt="' + player.nombre + '">';
    } else {
      badge.innerHTML = '<span class="pitch-player-placeholder">⚽</span>';
    }

    slot.querySelector('.pitch-player-name').textContent = player.nombre.split(' ')[0];
  }

  document.querySelectorAll('.pitch-player').forEach(function (slot) {
    slot.addEventListener('click', function (e) {
      e.stopPropagation();
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

      if (candidates.length === 0) {
        popup.innerHTML = '<p class="pitch-swap-empty">No tienes suplentes libres en esta posición.</p>';
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
            var outgoing = squadPlayerById(currentId);
            var outgoingCb = getCheckbox(currentId);
            var incomingCb = getCheckbox(c.id);
            if (outgoingCb) outgoingCb.checked = false;
            if (incomingCb) incomingCb.checked = true;

            updateSlotVisual(slot, c);
            closePopup();
          });

          popup.appendChild(item);
        });
      }

      slot.appendChild(popup);
    });
  });

  document.addEventListener('click', closePopup);
});
