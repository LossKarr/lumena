/* Constellation Missions — couche 3D progressive.
 *
 * Le SVG produit par mission_views.js reste toujours dans le DOM et constitue
 * le repli accessible. Cette couche ne remplace le SVG qu'apres la creation
 * effective d'un renderer WebGL. Une machine sans acceleration graphique garde
 * donc exactement la meme topologie et toutes les commandes du panneau.
 */
(function (root) {
  'use strict';

  var THREE_URL = '../vendor/three.module.min.js';
  var instances = [];
  var generation = 0;

  function decode(stage) {
    try {
      return JSON.parse(decodeURIComponent(stage.getAttribute('data-mp-scene-data') || ''));
    } catch (e) {
      return null;
    }
  }

  function css(stage, name, fallback) {
    try {
      var value = getComputedStyle(stage).getPropertyValue(name).trim();
      return value || fallback;
    } catch (e) {
      return fallback;
    }
  }

  function color(THREE, stage, name, fallback) {
    var out = new THREE.Color();
    try { out.setStyle(css(stage, name, fallback)); }
    catch (e) { out.setStyle(fallback); }
    return out;
  }

  function workerColor(THREE, stage, state) {
    if (state === 'failed') return color(THREE, stage, '--danger', 'crimson');
    if (state === 'waiting' || state === 'stalled') {
      return color(THREE, stage, '--warn', 'goldenrod');
    }
    if (state === 'done') return color(THREE, stage, '--ok', 'seagreen');
    if (state === 'cancelled') {
      return color(THREE, stage, '--muted-strong', 'gray');
    }
    return color(THREE, stage, '--accent', 'darkorange');
  }

  function pixelProof(renderer, stage) {
    try {
      var gl = renderer.getContext();
      var width = gl.drawingBufferWidth;
      var height = gl.drawingBufferHeight;
      if (!width || !height) return 0;
      var pixels = new Uint8Array(width * height * 4);
      gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      var visibles = 0, colores = 0, maxRgb = 0;
      // Un echantillon sur huit suffit pour distinguer une scene vide d'une
      // topologie rendue, sans garder le tampon apres cette preuve initiale.
      for (var i = 3; i < pixels.length; i += 32) {
        if (pixels[i] > 0) visibles += 1;
        var rgb = pixels[i - 3] + pixels[i - 2] + pixels[i - 1];
        if (rgb > 36) colores += 1;
        if (rgb > maxRgb) maxRgb = rgb;
      }
      stage.setAttribute('data-mp-canvas-pixels', String(visibles));
      stage.setAttribute('data-mp-canvas-color-pixels', String(colores));
      stage.setAttribute('data-mp-canvas-max-rgb', String(maxRgb));
      return colores;
    } catch (e) {
      stage.setAttribute('data-mp-canvas-pixels', 'unknown');
      return -1;
    }
  }

  function dispatchWorker(stage, id) {
    if (!id || typeof CustomEvent === 'undefined') return;
    stage.dispatchEvent(new CustomEvent('mission:worker-select', {
      bubbles: true,
      detail: { workerId: id, missionId: stage.closest('.mp-mission')?.querySelector('[data-mp-fold]')?.dataset.mpFold || '' }
    }));
  }

  function disposeInstance(inst) {
    if (!inst || inst.dead) return;
    inst.dead = true;
    if (inst.frame) cancelAnimationFrame(inst.frame);
    if (inst.resize) inst.resize.disconnect();
    (inst.off || []).forEach(function (fn) { try { fn(); } catch (e) {} });
    if (inst.scene) {
      inst.scene.traverse(function (obj) {
        if (obj.geometry && obj.geometry.dispose) obj.geometry.dispose();
        if (obj.material) {
          var mats = Array.isArray(obj.material) ? obj.material : [obj.material];
          mats.forEach(function (m) {
            if (m && m.map && m.map.dispose) m.map.dispose();
            if (m && m.dispose) m.dispose();
          });
        }
      });
    }
    if (inst.renderer) {
      try { inst.renderer.dispose(); } catch (e) {}
      try { inst.renderer.forceContextLoss(); } catch (e) {}
    }
    if (inst.stage) inst.stage.classList.remove('is-ready', 'is-dragging');
  }

  function dispose(container) {
    generation += 1;
    var keep = [];
    instances.forEach(function (inst) {
      if (!container || (inst.stage && container.contains(inst.stage))) disposeInstance(inst);
      else keep.push(inst);
    });
    instances = keep;
  }

  function labelSprite(THREE, text, tint, small) {
    if (!root.document || !root.document.createElement) return null;
    var canvas = root.document.createElement('canvas');
    var ctx = canvas.getContext('2d');
    if (!ctx) return null;
    var dpr = Math.min(root.devicePixelRatio || 1, 2);
    var font = small ? 22 : 26;
    ctx.font = '700 ' + font + 'px Inter, system-ui, sans-serif';
    var width = Math.ceil(ctx.measureText(text).width + 34);
    canvas.width = Math.max(128, Math.ceil(width * dpr));
    canvas.height = Math.ceil((small ? 42 : 48) * dpr);
    ctx.scale(dpr, dpr);
    ctx.font = '700 ' + font + 'px Inter, system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = 'rgba(7, 13, 22, 0.88)';
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.14)';
    ctx.lineWidth = 1;
    var boxW = canvas.width / dpr;
    var boxH = canvas.height / dpr;
    ctx.beginPath();
    if (typeof ctx.roundRect === 'function') {
      ctx.roundRect(0.5, 0.5, boxW - 1, boxH - 1, 7);
    } else {
      ctx.rect(0.5, 0.5, boxW - 1, boxH - 1);
    }
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = '#' + tint.getHexString();
    ctx.fillText(text, boxW / 2, boxH / 2 + 1);
    var texture = new THREE.CanvasTexture(canvas);
    if (THREE.SRGBColorSpace) texture.colorSpace = THREE.SRGBColorSpace;
    texture.minFilter = THREE.LinearFilter;
    var sprite = new THREE.Sprite(new THREE.SpriteMaterial({
      map: texture, transparent: true, depthTest: false, depthWrite: false
    }));
    var ratio = canvas.width / canvas.height;
    sprite.scale.set((small ? 0.44 : 0.58) * ratio, small ? 0.44 : 0.58, 1);
    sprite.renderOrder = 20;
    return sprite;
  }

  async function create(stage, token) {
    var payload = decode(stage);
    var canvas = stage.querySelector('.mp-scene-canvas');
    if (!payload || !canvas || !Array.isArray(payload.workers)) return;

    var THREE;
    try {
      THREE = await import(THREE_URL);
    } catch (e) {
      stage.classList.add('is-fallback');
      return;
    }
    if (token !== generation || !stage.isConnected) return;

    var renderer;
    try {
      renderer = new THREE.WebGLRenderer({
        canvas: canvas,
        alpha: true,
        antialias: true,
        powerPreference: 'low-power',
        failIfMajorPerformanceCaveat: false
      });
    } catch (e) {
      stage.classList.add('is-fallback');
      return;
    }

    var inst = {
      stage: stage, renderer: renderer, scene: null, resize: null,
      frame: 0, dead: false, off: []
    };
    instances.push(inst);

    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.08;

    var scene = new THREE.Scene();
    inst.scene = scene;
    var camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
    var sceneCenterX = payload.workers.length ? 0.3 : 1.2;
    var cameraDistance = 10.4;
    var zoomOffset = 0;

    function applyCamera() {
      var distance = cameraDistance + zoomOffset;
      camera.position.set(sceneCenterX, 3.25 + Math.max(0, distance - 10) * 0.16, distance);
      camera.lookAt(sceneCenterX, -0.25, 0);
    }
    applyCamera();

    var rootGroup = new THREE.Group();
    rootGroup.rotation.x = -0.08;
    scene.add(rootGroup);

    scene.add(new THREE.HemisphereLight(
      color(THREE, stage, '--text-strong', 'white'),
      color(THREE, stage, '--bg', 'black'), 1.15
    ));
    var key = new THREE.DirectionalLight(color(THREE, stage, '--text-strong', 'white'), 2.25);
    key.position.set(-3.5, 7, 6.5);
    scene.add(key);
    var rim = new THREE.DirectionalLight(color(THREE, stage, '--warn', 'goldenrod'), 1.85);
    rim.position.set(6, 2, -5);
    scene.add(rim);

    var neutral = color(THREE, stage, '--border-strong', 'gray');
    var panel = color(THREE, stage, '--bg-elevated', 'black');
    var missionColor = color(THREE, stage, '--text-strong', 'white');
    var warn = color(THREE, stage, '--warn', 'goldenrod');
    var accent = color(THREE, stage, '--accent', 'orange');

    // Repere spatial : discret mais utile pour lire la profondeur et les
    // distances. Il remplace l'orbite plate qui faisait maquette scolaire.
    var floor = new THREE.GridHelper(12, 24, neutral, neutral);
    floor.position.y = -1.35;
    floor.material.transparent = true;
    floor.material.opacity = 0.12;
    rootGroup.add(floor);
    var polar = new THREE.PolarGridHelper(4.8, 12, 4, 72, neutral, neutral);
    polar.position.y = -1.32;
    polar.material.transparent = true;
    polar.material.opacity = 0.11;
    rootGroup.add(polar);

    var corePos = new THREE.Vector3(-0.75, 0, 0);
    var gatePos = new THREE.Vector3(3.25, 0.1, 0);
    var coreGroup = new THREE.Group();
    coreGroup.position.copy(corePos);
    rootGroup.add(coreGroup);

    var coreBase = new THREE.Mesh(
      new THREE.CylinderGeometry(0.84, 1.02, 0.2, 32),
      new THREE.MeshStandardMaterial({
        color: panel, metalness: 0.72, roughness: 0.42,
        emissive: accent, emissiveIntensity: 0.05
      })
    );
    coreBase.position.y = -0.72;
    coreGroup.add(coreBase);
    var core = new THREE.Mesh(
      new THREE.DodecahedronGeometry(0.56, 1),
      new THREE.MeshPhysicalMaterial({
        color: panel, emissive: accent, emissiveIntensity: 0.48,
        roughness: 0.24, metalness: 0.68, clearcoat: 0.72, clearcoatRoughness: 0.22
      })
    );
    core.userData.kind = 'mission';
    coreGroup.add(core);
    var coreShell = new THREE.Mesh(
      new THREE.IcosahedronGeometry(0.73, 1),
      new THREE.MeshBasicMaterial({
        color: missionColor, wireframe: true, transparent: true, opacity: 0.34
      })
    );
    coreGroup.add(coreShell);
    var coreRings = [];
    for (var cr = 0; cr < 3; cr++) {
      var ring = new THREE.Mesh(
        new THREE.TorusGeometry(0.92 + cr * 0.12, 0.018, 8, 72),
        new THREE.MeshBasicMaterial({ color: cr === 1 ? accent : neutral, transparent: true, opacity: 0.56 })
      );
      ring.rotation.set(cr * 0.58, cr * 0.78, cr * 0.31);
      coreGroup.add(ring);
      coreRings.push(ring);
    }
    var coreLabel = labelSprite(THREE, 'LEAD', missionColor, false);
    if (coreLabel) { coreLabel.position.set(0, 1.24, 0); coreGroup.add(coreLabel); }

    // Le CodeAgent devient un portail partage, pas un cube decoratif. Les
    // deux anneaux montrent visuellement qu'il s'agit d'un verrou traversable.
    var gateGroup = new THREE.Group();
    gateGroup.position.copy(gatePos);
    rootGroup.add(gateGroup);
    var portalDisk = new THREE.Mesh(
      new THREE.CircleGeometry(0.64, 48),
      new THREE.MeshBasicMaterial({ color: warn, transparent: true, opacity: 0.08, side: THREE.DoubleSide })
    );
    gateGroup.add(portalDisk);
    var gateOuter = new THREE.Mesh(
      new THREE.TorusGeometry(0.78, 0.105, 14, 64),
      new THREE.MeshPhysicalMaterial({
        color: panel, emissive: warn, emissiveIntensity: 0.72,
        roughness: 0.26, metalness: 0.78, clearcoat: 0.6
      })
    );
    gateGroup.add(gateOuter);
    var gateInner = new THREE.Mesh(
      new THREE.TorusGeometry(0.57, 0.025, 8, 64),
      new THREE.MeshBasicMaterial({ color: warn, transparent: true, opacity: 0.82 })
    );
    gateGroup.add(gateInner);
    [-1, 1].forEach(function (side) {
      var rail = new THREE.Mesh(
        new THREE.BoxGeometry(0.11, 1.85, 0.11),
        new THREE.MeshStandardMaterial({ color: neutral, emissive: warn, emissiveIntensity: 0.18, metalness: 0.75, roughness: 0.35 })
      );
      rail.position.x = side * 0.96;
      gateGroup.add(rail);
    });
    var gateLabel = labelSprite(THREE, 'CODEAGENT', warn, true);
    if (gateLabel) { gateLabel.position.set(0, 1.25, 0); gateGroup.add(gateLabel); }
    var gateLight = new THREE.PointLight(warn, 1.7, 5.5, 2);
    gateGroup.add(gateLight);

    var targets = [];
    var flowPackets = [];
    var workerPods = [];

    function route(from, to, tint, active, lift) {
      var middle = from.clone().lerp(to, 0.5);
      middle.y += lift == null ? 0.42 : lift;
      middle.z += (from.x - to.x) * 0.08;
      var curve = new THREE.CatmullRomCurve3([from.clone(), middle, to.clone()]);
      var tube = new THREE.Mesh(
        new THREE.TubeGeometry(curve, 40, active ? 0.018 : 0.009, 6, false),
        new THREE.MeshBasicMaterial({
          color: tint, transparent: true, opacity: active ? 0.78 : 0.25
        })
      );
      rootGroup.add(tube);
      if (active) {
        var packet = new THREE.Mesh(
          new THREE.OctahedronGeometry(0.065, 0),
          new THREE.MeshBasicMaterial({ color: tint })
        );
        rootGroup.add(packet);
        flowPackets.push({ mesh: packet, curve: curve, phase: flowPackets.length * 0.23, speed: 0.14 + (flowPackets.length % 3) * 0.025 });
      }
      return curve;
    }

    var missionActive = payload.state === 'running' || payload.state === 'waiting' || payload.state === 'stalled';
    route(corePos, gatePos, warn, missionActive, 0.55);

    payload.workers.forEach(function (worker, index) {
      // Le premier worker part a gauche du lead. Avec l'ancien depart a -90°,
      // le premier noeud se projetait derriere le noyau sur les missions a
      // trois workers et son libelle devenait illisible.
      var angle = Math.PI + index * Math.PI * 2 / Math.max(1, payload.workers.length);
      var radius = payload.workers.length < 4 ? 2.35 : 2.7;
      var x = corePos.x + Math.cos(angle) * radius;
      var z = Math.sin(angle) * radius * 0.72;
      var y = 0.05 + Math.sin(angle * 2) * 0.28;
      var stateColor = workerColor(THREE, stage, worker.state);
      var selected = payload.selected && payload.selected === worker.id;
      var pod = new THREE.Group();
      pod.position.set(x, y, z);
      rootGroup.add(pod);
      var node = new THREE.Mesh(
        new THREE.OctahedronGeometry(selected ? 0.38 : 0.32, 1),
        new THREE.MeshPhysicalMaterial({
          color: panel, emissive: stateColor,
          emissiveIntensity: selected ? 0.92 : 0.5,
          roughness: 0.26, metalness: 0.62, clearcoat: 0.5
        })
      );
      node.userData.kind = 'worker';
      node.userData.workerId = worker.id;
      pod.add(node);
      var podRing = new THREE.Mesh(
        new THREE.TorusGeometry(selected ? 0.54 : 0.47, selected ? 0.035 : 0.022, 8, 48),
        new THREE.MeshBasicMaterial({ color: stateColor, transparent: true, opacity: selected ? 1 : 0.66 })
      );
      podRing.rotation.x = Math.PI / 2;
      pod.add(podRing);
      var workerLabel = labelSprite(THREE, worker.name || worker.id || 'worker', stateColor, true);
      if (workerLabel) {
        var labelX = Math.cos(angle) * 0.7;
        var labelY = 0.76;
        if (Math.sin(angle) < -0.25) {
          labelX = 1.05;
          labelY = 0.05;
        }
        workerLabel.position.set(labelX, labelY, 0);
        pod.add(workerLabel);
      }
      targets.push(node);
      workerPods.push({ group: pod, body: node, ring: podRing, state: worker.state, phase: index * 0.7 });

      var workerPos = new THREE.Vector3(x, y, z);
      route(corePos, workerPos, stateColor, worker.state === 'running' && !worker.queueRank, 0.28 + (index % 2) * 0.22);
      if (worker.queueRank || worker.state === 'waiting') {
        route(workerPos, gatePos, warn, true, 0.65 + worker.queueRank * 0.12);
      }
    });

    stage.setAttribute('data-mp-scene-quality', 'operational');
    var sceneState = stage.querySelector('.mp-scene-state');
    if (sceneState) sceneState.textContent = 'Carte 3D opérationnelle';

    var raycaster = new THREE.Raycaster();
    var pointer = new THREE.Vector2();
    var dragging = false, moved = false, px = 0, py = 0;

    function size() {
      if (inst.dead) return;
      var rect = stage.getBoundingClientRect();
      var width = Math.max(1, Math.round(rect.width));
      var height = Math.max(260, Math.round(rect.height));
      renderer.setPixelRatio(Math.min(root.devicePixelRatio || 1, 1.6));
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      var verticalFov = THREE.MathUtils.degToRad(camera.fov);
      var contentWidth = payload.workers.length ? 8.1 : 5.7;
      var contentHeight = payload.workers.length ? 5.8 : 4.7;
      var byHeight = contentHeight / (2 * Math.tan(verticalFov / 2));
      var byWidth = contentWidth / (2 * Math.tan(verticalFov / 2) * camera.aspect);
      cameraDistance = Math.max(9.4, Math.min(18.5, Math.max(byHeight, byWidth)));
      applyCamera();
      camera.updateProjectionMatrix();
    }

    function on(target, type, handler, options) {
      target.addEventListener(type, handler, options);
      inst.off.push(function () { target.removeEventListener(type, handler, options); });
    }

    function hit(ev) {
      var rect = canvas.getBoundingClientRect();
      pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      return raycaster.intersectObjects(targets, false)[0] || null;
    }

    on(canvas, 'pointerdown', function (ev) {
      dragging = true; moved = false; px = ev.clientX; py = ev.clientY;
      stage.classList.add('is-dragging');
      try { canvas.setPointerCapture(ev.pointerId); } catch (e) {}
    });
    on(canvas, 'pointermove', function (ev) {
      if (!dragging) return;
      var dx = ev.clientX - px, dy = ev.clientY - py;
      if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
      rootGroup.rotation.y += dx * 0.008;
      rootGroup.rotation.x = Math.max(-0.7, Math.min(0.35, rootGroup.rotation.x + dy * 0.004));
      px = ev.clientX; py = ev.clientY;
    });
    on(canvas, 'pointerup', function (ev) {
      dragging = false; stage.classList.remove('is-dragging');
      if (!moved) {
        var found = hit(ev);
        if (found && found.object.userData.workerId) {
          dispatchWorker(stage, found.object.userData.workerId);
        }
      }
    });
    on(canvas, 'pointercancel', function () {
      dragging = false; stage.classList.remove('is-dragging');
    });
    on(canvas, 'wheel', function (ev) {
      ev.preventDefault();
      zoomOffset = Math.max(-2.2, Math.min(3.2, zoomOffset + ev.deltaY * 0.006));
      applyCamera();
    }, { passive: false });

    var reset = stage.querySelector('[data-mp-scene-reset]');
    if (reset) on(reset, 'click', function () {
      rootGroup.rotation.set(-0.08, 0, 0);
      zoomOffset = 0;
      applyCamera();
    });

    var reduced = false;
    try { reduced = matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}
    var clock = new THREE.Clock();
    var renderedFrames = 0;
    function draw() {
      if (inst.dead) return;
      if (!reduced && !dragging) {
        var delta = Math.min(0.04, clock.getDelta());
        var elapsed = clock.elapsedTime;
        core.rotation.y += delta * 0.24;
        coreShell.rotation.y -= delta * 0.19;
        coreRings.forEach(function (ring, index) {
          ring.rotation.z += delta * (index % 2 ? -0.22 : 0.18);
        });
        gateOuter.rotation.z += delta * 0.19;
        gateInner.rotation.z -= delta * 0.31;
        portalDisk.material.opacity = 0.065 + Math.sin(elapsed * 1.8) * 0.018;
        workerPods.forEach(function (pod) {
          var pulse = 1 + Math.sin(elapsed * 1.7 + pod.phase) * 0.035;
          pod.ring.scale.setScalar(pulse);
          pod.body.rotation.y += delta * 0.22;
        });
        flowPackets.forEach(function (packet) {
          var at = (elapsed * packet.speed + packet.phase) % 1;
          packet.mesh.position.copy(packet.curve.getPointAt(at));
        });
      }
      renderer.render(scene, camera);
      renderedFrames += 1;
      stage.setAttribute('data-mp-frame', String(renderedFrames));
      inst.frame = requestAnimationFrame(draw);
    }

    if (typeof ResizeObserver !== 'undefined') {
      inst.resize = new ResizeObserver(size);
      inst.resize.observe(stage);
    }
    size();
    renderer.render(scene, camera);
    var preuve = pixelProof(renderer, stage);
    if (preuve === 0) {
      disposeInstance(inst);
      stage.classList.add('is-fallback');
      return;
    }
    stage.classList.add('is-ready');
    stage.classList.remove('is-fallback');
    draw();
  }

  function mount(container) {
    if (!container || !container.querySelectorAll) return;
    var token = ++generation;
    var stages = container.querySelectorAll('[data-mp-scene]');
    Array.prototype.forEach.call(stages, function (stage) { create(stage, token); });
  }

  var api = { mount: mount, dispose: dispose, decode: decode };
  root.missionScene = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
