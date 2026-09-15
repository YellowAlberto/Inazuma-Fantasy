// Animates a fixture's simulated events (goals, saves, steals...) on a
// pitch showing BOTH full lineups laid out by formation. Instead of just
// moving a ball between fixed dots, the player actually involved in each
// action steps out of their formation spot toward where the play happens,
// glows while it's resolved, then returns to their position — with a
// small ball icon riding alongside them.
// Plain DOM/JS, no framework, no build step.
document.addEventListener('DOMContentLoaded', function () {
  // --- Sound effects: real audio files if present, synthesized tones as
  // an automatic fallback for whichever ones you haven't added yet -------
  var AudioCtxClass = window.AudioContext || window.webkitAudioContext;
  var audioCtx = null;

  function getAudioCtx() {
    if (!audioCtx && AudioCtxClass) audioCtx = new AudioCtxClass();
    return audioCtx;
  }

  function playTone(freq, duration, waveType, volume, delay) {
    if (!AudioCtxClass) return;
    var ctx = getAudioCtx();
    if (!ctx) return;
    var start = ctx.currentTime + (delay || 0);
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.type = waveType || 'sine';
    osc.frequency.setValueAtTime(freq, start);
    gain.gain.setValueAtTime(volume || 0.15, start);
    gain.gain.exponentialRampToValueAtTime(0.001, start + duration);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(start);
    osc.stop(start + duration);
  }

  // Synthesized fallback, used automatically for any event type whose
  // real audio file hasn't been added yet (or fails to load).
  var SYNTH_FALLBACK = {
    key_pass: function () {
      playTone(520, 0.09, 'sine', 0.09, 0);
      playTone(680, 0.08, 'sine', 0.07, 0.06);
    },
    shot_off: function () {
      playTone(260, 0.16, 'sawtooth', 0.13, 0);
      playTone(120, 0.14, 'sawtooth', 0.08, 0.05);
    },
    save: function () {
      playTone(150, 0.1, 'square', 0.15, 0);
      playTone(90, 0.12, 'square', 0.1, 0.04);
    },
    steal: function () {
      playTone(240, 0.07, 'square', 0.1, 0);
    },
    interception: function () {
      playTone(300, 0.07, 'square', 0.1, 0);
    },
    clearance: function () {
      playTone(200, 0.09, 'square', 0.12, 0);
      playTone(160, 0.08, 'square', 0.08, 0.05);
    },
    block: function () {
      playTone(130, 0.11, 'square', 0.16, 0);
    },
    super_technique: function () {
      // A dramatic rising power-up flourish before the actual outcome sound.
      playTone(392, 0.09, 'sawtooth', 0.14, 0);
      playTone(523.25, 0.09, 'sawtooth', 0.16, 0.09);
      playTone(659.25, 0.09, 'sawtooth', 0.18, 0.18);
      playTone(880, 0.16, 'sawtooth', 0.2, 0.27);
    },
    goal: function () {
      // A little triumphant fanfare: C5, E5, G5, C6.
      playTone(523.25, 0.14, 'triangle', 0.2, 0);
      playTone(659.25, 0.14, 'triangle', 0.2, 0.13);
      playTone(783.99, 0.18, 'triangle', 0.22, 0.26);
      playTone(1046.5, 0.32, 'triangle', 0.24, 0.44);
    },
  };

  // Drop a matching file into static/sounds/ with these exact base names
  // and it'll be used automatically instead of the synth fallback — no
  // code changes needed. Any common audio format works (.mp3, .wav, .ogg,
  // .m4a): each name below is tried in that order until one actually
  // loads. Anything you haven't added yet just keeps using the
  // synthesized tone above.
  var SOUND_BASE_NAMES = {
    key_pass: 'pass',
    shot_off: 'shot',
    save: 'save',
    steal: 'tackle',
    interception: 'tackle',
    clearance: 'clearance',
    block: 'block',
    super_technique: 'super',
    goal: 'goal',
  };
  var SOUND_EXTENSIONS = ['mp3', 'wav', 'ogg', 'm4a'];

  var audioElements = {}; // eventType -> HTMLAudioElement once resolved
  var resolving = {}; // eventType -> true while we're still probing extensions
  var unavailable = {}; // eventType -> true once every extension has failed

  function tryLoadExtension(eventType, baseName, extIndex) {
    if (extIndex >= SOUND_EXTENSIONS.length) {
      unavailable[eventType] = true;
      resolving[eventType] = false;
      return;
    }
    var url = '/static/sounds/' + baseName + '.' + SOUND_EXTENSIONS[extIndex];
    var el = new Audio(url);
    el.preload = 'auto';
    el.addEventListener('canplaythrough', function () {
      if (!audioElements[eventType]) {
        audioElements[eventType] = el;
        resolving[eventType] = false;
      }
    });
    el.addEventListener('error', function () {
      tryLoadExtension(eventType, baseName, extIndex + 1);
    });
    el.load();
  }

  function ensureAudioResolving(eventType) {
    var baseName = SOUND_BASE_NAMES[eventType];
    if (!baseName || unavailable[eventType] || resolving[eventType] || audioElements[eventType]) return;
    resolving[eventType] = true;
    tryLoadExtension(eventType, baseName, 0);
  }

  // Kick off probing for every event type as soon as the page loads, so
  // the very first time each sound is needed it's usually already resolved.
  Object.keys(SOUND_BASE_NAMES).forEach(ensureAudioResolving);

  function playSoundEffect(eventType) {
    var el = audioElements[eventType];
    if (el) {
      var clone = el.cloneNode(true);
      clone.volume = 0.6;
      var playPromise = clone.play();
      if (playPromise && playPromise.catch) {
        playPromise.catch(function () {
          var fx = SYNTH_FALLBACK[eventType];
          if (fx) fx();
        });
      }
      return;
    }
    var fx = SYNTH_FALLBACK[eventType];
    if (fx) fx();
  }

  var ICONS = {
    goal: '⚽ ¡GOL!',
    save: '🧤 Parada',
    steal: '🛡️ Robo de balón',
    shot_off: '🚫 Disparo fuera',
    clean_sheet: '🧱 Portería a cero',
    key_pass: '🔑 Pase clave',
    clearance: '🧹 Despeje',
    block: '🚧 Bloqueo',
    dribble: '💫 Regate',
    interception: '🎯 Intercepción',
  };

  function clampPct(value) {
    return Math.min(95, Math.max(5, value));
  }

  // Where the action happens horizontally (0% = home goal line, 100% =
  // away goal line), based on what the event represents rather than a
  // fixed formation slot — this is where the involved player steps to.
  function actionX(ev) {
    var jitter = Math.random() * 5 - 2.5;
    if (ev.type === 'goal' || ev.type === 'shot_off') {
      return clampPct((ev.side === 'home' ? 88 : 12) + jitter);
    }
    if (ev.type === 'save') {
      return clampPct((ev.side === 'home' ? 12 : 88) + jitter);
    }
    if (ev.type === 'block') {
      return clampPct((ev.side === 'home' ? 16 : 84) + jitter);
    }
    if (ev.type === 'steal') {
      return clampPct((ev.side === 'home' ? 32 : 68) + jitter);
    }
    if (ev.type === 'interception') {
      return clampPct((ev.side === 'home' ? 38 : 62) + jitter);
    }
    if (ev.type === 'clearance') {
      return clampPct((ev.side === 'home' ? 18 : 82) + jitter);
    }
    if (ev.type === 'key_pass') {
      return clampPct((ev.side === 'home' ? 70 : 30) + jitter);
    }
    if (ev.type === 'dribble') {
      return clampPct((ev.side === 'home' ? 55 : 45) + jitter);
    }
    return 50;
  }

  function actionY(ev) {
    var tight = ev.type === 'steal' || ev.type === 'interception' || ev.type === 'key_pass';
    var spread = tight ? 14 : 20;
    return clampPct(50 + (Math.random() * spread * 2 - spread));
  }

  document.querySelectorAll('.replay').forEach(function (container) {
    var fixtureKey = container.dataset.fixture;
    var dataEl = document.querySelector('.replay-events-data[data-fixture="' + fixtureKey + '"]');
    if (!dataEl) return;

    var events;
    try {
      events = JSON.parse(dataEl.textContent);
    } catch (e) {
      return;
    }
    events.sort(function (a, b) { return a.minute - b.minute; });

    var homeLabel = container.dataset.homeLabel;
    var awayLabel = container.dataset.awayLabel;

    var card = container.closest('.fixture-card');
    var scoreEl = card ? card.querySelector('.fixture-goals') : null;
    var pointsEl = card ? card.querySelector('.fixture-points') : null;
    var finalHomeGoals = scoreEl ? parseInt(scoreEl.dataset.homeGoals, 10) : null;
    var finalAwayGoals = scoreEl ? parseInt(scoreEl.dataset.awayGoals, 10) : null;

    var ball = container.querySelector('.replay-ball');
    var clock = container.querySelector('.replay-clock');
    var caption = container.querySelector('.replay-caption');
    var playBtn = container.querySelector('.replay-play');
    var restartBtn = container.querySelector('.replay-restart');
    var muteBtn = container.querySelector('.replay-mute');
    var muted = false;

    function playSoundFor(eventType) {
      if (muted) return;
      playSoundEffect(eventType);
    }
    var badges = container.querySelectorAll('.pitch-mini-player');

    var idx = 0;
    var timer = null;
    var homeGoals = 0;
    var awayGoals = 0;
    var activeBadge = null;
    var activeSupportBadges = [];

    function badgeFor(playerId) {
      if (playerId === undefined || playerId === null) return null;
      for (var i = 0; i < badges.length; i++) {
        if (parseInt(badges[i].dataset.playerId, 10) === playerId) return badges[i];
      }
      return null;
    }

    function badgesForSide(sideKey, excludeId) {
      var pool = [];
      badges.forEach(function (b) {
        if (b.dataset.side === sideKey && parseInt(b.dataset.playerId, 10) !== excludeId) pool.push(b);
      });
      return pool;
    }

    function pickRandom(pool, count) {
      var copy = pool.slice();
      var picked = [];
      while (picked.length < count && copy.length) {
        var i = Math.floor(Math.random() * copy.length);
        picked.push(copy.splice(i, 1)[0]);
      }
      return picked;
    }

    function moveBadgeTo(badge, x, y) {
      badge.style.left = x + '%';
      badge.style.top = y + '%';
    }

    function sendBadgeHome(badge) {
      if (!badge) return;
      moveBadgeTo(badge, parseFloat(badge.dataset.x), parseFloat(badge.dataset.y));
      badge.classList.remove('pitch-mini-player-glow', 'pitch-mini-player-glow-goal');
    }

    function placeBall(leftPct, topPct) {
      ball.style.left = leftPct + '%';
      ball.style.top = (topPct - 7) + '%';
    }

    function flashGoal(sideKey) {
      var el = container.querySelector('.goal-flash-' + sideKey);
      if (!el) return;
      el.classList.remove('goal-flash-active');
      void el.offsetWidth; // force reflow so the animation can restart
      el.classList.add('goal-flash-active');
    }

    function resetAllBadges() {
      badges.forEach(function (b) { sendBadgeHome(b); });
      activeBadge = null;
      activeSupportBadges = [];
    }

    function updateScoreboard() {
      if (scoreEl) scoreEl.textContent = homeGoals + ' — ' + awayGoals;
    }

    function hidePoints() {
      if (pointsEl) {
        pointsEl.textContent = 'Puntos fantasy: se revelan al terminar el partido — dale a reproducir 👇';
      }
    }

    function revealPoints() {
      if (pointsEl) {
        pointsEl.textContent =
          'Puntos fantasy: ' + homeLabel + ' ' + pointsEl.dataset.homePoints + ' · ' +
          awayLabel + ' ' + pointsEl.dataset.awayPoints;
      }
    }

    function resetView() {
      idx = 0;
      homeGoals = 0;
      awayGoals = 0;
      resetAllBadges();
      placeBall(50, 50);
      clock.textContent = "Min 0'";
      updateScoreboard();
      hidePoints();
      caption.textContent = 'Pulsa reproducir para ver la repetición del partido.';
      playBtn.textContent = '▶️ Reproducir';
    }

    function renderEvent(ev) {
      // Whoever was out on the ball (and their supporting teammates) return
      // to their formation spot as the new play develops.
      if (activeBadge) sendBadgeHome(activeBadge);
      activeSupportBadges.forEach(sendBadgeHome);
      activeSupportBadges = [];

      var badge = badgeFor(ev.player_id);
      if (badge) {
        var x = actionX(ev);
        var y = actionY(ev);
        moveBadgeTo(badge, x, y);
        placeBall(x, y);
        badge.classList.remove('pitch-mini-player-glow', 'pitch-mini-player-glow-goal');
        void badge.offsetWidth; // force reflow so the glow can restart
        badge.classList.add((ev.type === 'goal' || ev.super_technique) ? 'pitch-mini-player-glow-goal' : 'pitch-mini-player-glow');
        activeBadge = badge;

        // A couple of teammates make a supporting run toward the ball...
        var mates = pickRandom(badgesForSide(ev.side, ev.player_id), 2);
        mates.forEach(function (m) {
          var mx = parseFloat(m.dataset.x);
          var my = parseFloat(m.dataset.y);
          moveBadgeTo(m, mx + (x - mx) * 0.4, my + (y - my) * 0.4);
          activeSupportBadges.push(m);
        });

        // ...while a rival closes in to press.
        var otherSide = ev.side === 'home' ? 'away' : 'home';
        var closers = pickRandom(badgesForSide(otherSide, null), 1);
        closers.forEach(function (c) {
          var cx = parseFloat(c.dataset.x);
          var cy = parseFloat(c.dataset.y);
          moveBadgeTo(c, cx + (x - cx) * 0.3, cy + (y - cy) * 0.3);
          activeSupportBadges.push(c);
        });
      } else {
        activeBadge = null;
      }

      if (ev.type === 'goal') {
        if (ev.side === 'home') homeGoals++; else awayGoals++;
        updateScoreboard();
        flashGoal(ev.side === 'home' ? 'right' : 'left');
      }
      clock.textContent = "Min " + ev.minute + "'";
      if (ev.super_technique) playSoundFor('super_technique');
      playSoundFor(ev.type);

      var teamLabel = ev.side === 'home' ? homeLabel : awayLabel;
      var text;
      if (ev.type === 'clean_sheet') {
        text = ICONS.clean_sheet + ' para ' + teamLabel;
      } else if (ev.super_technique) {
        text = '✨ ¡SÚPER TÉCNICA! ' + ev.technique_name + ' — ' + ev.player + ' (' + teamLabel + ')';
      } else {
        text = (ICONS[ev.type] || '') + (ev.player ? ' — ' + ev.player : '') + ' (' + teamLabel + ')';
        if (ev.type === 'goal' && ev.assist) text += ' [asist. ' + ev.assist + ']';
      }
      caption.textContent = text;
    }

    function step() {
      if (idx >= events.length) {
        timer = null;
        playBtn.textContent = '▶️ Reproducir';
        // Snap to the true final score/points in case of any rounding edge case.
        if (finalHomeGoals !== null) homeGoals = finalHomeGoals;
        if (finalAwayGoals !== null) awayGoals = finalAwayGoals;
        updateScoreboard();
        revealPoints();
        caption.textContent = 'Fin del partido: ' + homeLabel + ' ' + homeGoals + ' - ' + awayGoals + ' ' + awayLabel;
        resetAllBadges();
        placeBall(50, 50);
        return;
      }

      var ev = events[idx];
      renderEvent(ev);
      idx++;

      if (ev.type === 'goal') {
        // Kick-off: everyone drifts back to their formation spot after a
        // goal, just like a real restart, before the next play continues.
        timer = setTimeout(function () {
          resetAllBadges();
          placeBall(50, 50);
          timer = setTimeout(step, 700);
        }, 600);
      } else {
        timer = setTimeout(step, 700);
      }
    }

    playBtn.addEventListener('click', function () {
      var ctx = getAudioCtx();
      if (ctx && ctx.state === 'suspended') ctx.resume();
      if (timer) {
        clearTimeout(timer);
        timer = null;
        playBtn.textContent = '▶️ Reproducir';
        return;
      }
      if (idx >= events.length) resetView();
      playBtn.textContent = '⏸️ Pausa';
      timer = setTimeout(step, 300);
    });

    if (muteBtn) {
      muteBtn.addEventListener('click', function () {
        muted = !muted;
        muteBtn.textContent = muted ? '🔇' : '🔊';
      });
    }

    restartBtn.addEventListener('click', function () {
      clearTimeout(timer);
      timer = null;
      resetView();
    });

    resetView();
  });
});
