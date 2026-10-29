var THREE_VERSION = '0.160.0';

var CDN_THREE = 'https://cdn.jsdelivr.net/npm/three@' + THREE_VERSION + '/build/three.module.js';

// Whatever the width, this many thumbnails is enough: each is its own WebGL render.
var MAX_THUMBS = 60;

var THUMB_PX = 104;

var GALLERY_ROWS = [1, 2, 4];

var _threePromise = null;

/** three.js as an ES module, loaded once and only when something is about to be drawn. */
function loadThree() {
  if (!_threePromise) {
    _threePromise = import(CDN_THREE).catch(function(err) {
      _threePromise = null;
      throw err;
    });
  }
  return _threePromise;
}

// The paper surface, and the light tuning it needs.
var STAGE = {
  background: 0x0d1218, ambient: 0.95, sky: 0xffffff, ground: 0x9c9caa, hemisphere: 0.6,
  key: 2.15, fill: 0.34, rim: 0.28
};

// How far the key light stands off the camera. Fixed in the world it would leave whole sides
// unlit, so turning the back to the front would show a dark shape; riding with the camera at
// an offset, the surface being looked at is always lit while the light still moves relative
// to the object, so the shadows travel across it as you orbit. Head-on would flatten it.
var LIGHT_OFFSET = (35 * Math.PI) / 180;

/** A renderer, scene and camera for one canvas, plus the orbit interaction. */
function makeStage(THREE, canvas, opts) {
  var shadows = !opts || opts.shadows !== false;
  var renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  // Self-shadowing is what makes a pile of blobs read as a pile: nothing else tells you which
  // granule is in front of which. It is one extra pass over the same merged geometry, so the
  // gallery, which draws one instance per thumbnail dozens of times, leaves it off.
  renderer.shadowMap.enabled = shadows;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(45, 1, 0.001, 1e6);
  var target = new THREE.Vector3();
  var spherical = { radius: 5, theta: Math.PI * 0.25, phi: Math.PI * 0.35 };
  var framed = null;                 // { centre, size } of what is drawn

  // Depth comes from the light falling off, so the ambient term stays low and the rest is
  // directional: a key from above front, a weak fill so the shadow side is not black, and a
  // rim from behind to lift each surface off the one behind it.
  var ambient = new THREE.AmbientLight(0xffffff, STAGE.ambient);
  var hemisphere = new THREE.HemisphereLight(STAGE.sky, STAGE.ground, STAGE.hemisphere);
  var key = new THREE.DirectionalLight(0xffffff, STAGE.key);
  var keyAt = new THREE.Object3D();      // a directional light shadows towards its target
  key.castShadow = shadows;
  key.shadow.mapSize.set(1024, 1024);
  key.target = keyAt;
  var fill = new THREE.DirectionalLight(0xffffff, STAGE.fill);
  fill.position.set(-1, -0.5, 0.5);
  var rim = new THREE.DirectionalLight(0xffffff, STAGE.rim);
  rim.position.set(-0.6, 0.4, -1.6);
  scene.add(ambient, hemisphere, key, keyAt, fill, rim);
  // Transparent, so the stage's own vignette (CSS) is what the cell sits on; the fog is the
  // stage's edge colour, which is what the far side of the cell fades into.
  renderer.setClearColor(0x000000, 0);

  var placeKey = function() {
    if (!framed) return;
    var centre = framed.centre, size = framed.size;
    var theta = spherical.theta + LIGHT_OFFSET;
    // Above the camera, but never straight overhead: a raking light is what shows the form.
    var phi = Math.max(0.25, spherical.phi - 0.45);
    var sinPhi = Math.sin(phi);
    key.position.set(
      centre.x + size * 2 * sinPhi * Math.sin(theta),
      centre.y + size * 2 * Math.cos(phi),
      centre.z + size * 2 * sinPhi * Math.cos(theta));
    keyAt.position.copy(centre);
  };

  /**
   * Measure what is drawn and fit the key light's shadow frustum to it. Called on every
   * repaint, not only when the camera reframes: turning a structure on grows the scene, and
   * a stale frustum clips the shadow off it.
   */
  var frameShadow = function() {
    var box = new THREE.Box3();
    scene.children.forEach(function(child) {
      if (child.isMesh || child.isLineSegments) box.expandByObject(child);
    });
    if (box.isEmpty()) return;
    var size = box.getSize(new THREE.Vector3()).length() || 1;
    framed = { centre: box.getCenter(new THREE.Vector3()), size: size };
    placeKey();
    var shadow = key.shadow.camera;
    shadow.left = -size * 0.75; shadow.right = size * 0.75;
    shadow.top = size * 0.75; shadow.bottom = -size * 0.75;
    shadow.near = size * 0.05; shadow.far = size * 6;
    shadow.updateProjectionMatrix();
    key.shadow.bias = -0.0008;               // acne otherwise, at grazing angles
    if ('normalBias' in key.shadow) key.shadow.normalBias = size * 0.003;
  };

  // A turn laid over everything drawn: the cell rotated about its centre, which is how the
  // viewer lays a chart's plane flat to the camera without moving a single vertex.
  var pose = null;
  var applyPose = function() {
    scene.children.forEach(function(child) {
      if (!(child.isMesh || child.isLineSegments || child.isGroup || child.isPoints)) return;
      if (pose) {
        child.matrixAutoUpdate = false;
        child.matrix.copy(pose);
        child.matrixWorldNeedsUpdate = true;
        child.userData.posed = true;
      } else if (child.userData.posed) {
        child.matrixAutoUpdate = true;
        child.userData.posed = false;
        child.updateMatrix();
      }
    });
  };

  var spinning = false, lastCost = 0;
  var apply = function() {
    var began = performance.now();
    var done = apply_();
    lastCost = performance.now() - began;
    return done;
  };
  var apply_ = function() {
    applyPose();
    var sinPhi = Math.sin(spherical.phi);
    camera.position.set(
      target.x + spherical.radius * sinPhi * Math.sin(spherical.theta),
      target.y + spherical.radius * Math.cos(spherical.phi),
      target.z + spherical.radius * sinPhi * Math.cos(spherical.theta));
    camera.lookAt(target);
    placeKey();                              // the light rides with the camera
    renderer.render(scene, camera);
  };

  var stage = {
    THREE: THREE, scene: scene, camera: camera, renderer: renderer,
    draw: apply, frameShadow: frameShadow,
    /**
     * Turn slowly while nobody is touching it, so a still cell reads as a solid and not a
     * picture. Only where a frame is cheap: on a machine that takes a long time over each,
     * a spinning scene is a frozen page.
     */
    spin: function(on) {
      spinning = on;
      if (!on || stage.spinLoop) return;
      stage.spinLoop = true;
      var tick = function() {
        if (!spinning || !canvas.isConnected) { stage.spinLoop = false; return; }
        if (!document.hidden && lastCost < 40 && !pose) {
          spherical.theta += 0.0035;
          apply();
        }
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    },
    /** The camera's angles, read or set: how far round, and how far up from the pole. */
    view: function(to, andDraw) {
      if (to) {
        spherical.theta = to.theta; spherical.phi = to.phi;
        if (andDraw !== false) apply();
      }
      return { theta: spherical.theta, phi: spherical.phi };
    },
    /** Turn what is drawn (a Matrix4), or put it back (null). Drawn by the next draw. */
    setPose: function(matrix) { pose = matrix; },
    /**
     * Make what is drawn more or less see-through, remembering what each thing was to begin
     * with: the cell fades out as the points that stand for it fade in.
     */
    fade: function(amount) {
      scene.children.forEach(function(child) {
        var material = child.material;
        if (!material || !(child.isMesh || child.isLineSegments)) return;
        if (!material.userData.base) {
          material.userData.base = { opacity: material.opacity, transparent: material.transparent,
                                     depthWrite: material.depthWrite };
        }
        var base = material.userData.base;
        material.opacity = base.opacity * amount;
        material.transparent = base.transparent || amount < 1;
        material.depthWrite = base.depthWrite && amount > 0.999;
        material.needsUpdate = true;
        child.visible = amount > 0.02;
      });
    },
    /** Frame the geometry. The lights are in the scene too, and sit outside it. */
    fit: function() {
      frameShadow();
      if (!framed) return;
      target.copy(framed.centre);
      spherical.radius = framed.size * 1.1;
      camera.near = framed.size / 1000;
      camera.far = framed.size * 100;
      camera.updateProjectionMatrix();
      // Fog over the depth of the object itself, so the far side fades into the paper. It
      // starts behind the near face, so nothing in front of the middle is dulled.
      scene.fog = new THREE.Fog(STAGE.background, spherical.radius * 0.85, spherical.radius * 2.8);
      apply();
    },
    resize: function(width, height) {
      if (!width || !height) return;
      // A frame costs what the scene costs, so a resize to the size it already is is no frame.
      if (stage.width === width && stage.height === height) return;
      stage.width = width; stage.height = height;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      apply();
    },
    /** Drop every object drawn so far, freeing its GPU buffers. */
    clear: function() {
      scene.children.slice().forEach(function(child) {
        if (!child.isMesh && !child.isLineSegments) return;
        if (child.geometry) child.geometry.dispose();
        if (child.material) child.material.dispose();
        scene.remove(child);
      });
    },
    dispose: function() {
      stage.clear();
      renderer.dispose();
      if (renderer.forceContextLoss) renderer.forceContextLoss();
    }
  };

  // Orbit, dolly and pan by hand: three's OrbitControls is a separate module that imports
  // three by bare specifier, which needs an import map this page does not have.
  var dragging = null;
  canvas.style.touchAction = 'none';
  canvas.addEventListener('pointerdown', function(event) {
    spinning = false;
    dragging = { x: event.clientX, y: event.clientY, pan: event.button === 2 || event.shiftKey };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', function(event) {
    if (!dragging) return;
    var dx = event.clientX - dragging.x, dy = event.clientY - dragging.y;
    dragging.x = event.clientX; dragging.y = event.clientY;
    if (dragging.pan) {
      camera.updateMatrixWorld();
      var e = camera.matrixWorld.elements;
      var scale = spherical.radius * 0.0015;
      target.x -= (e[0] * dx - e[4] * dy) * scale;
      target.y -= (e[1] * dx - e[5] * dy) * scale;
      target.z -= (e[2] * dx - e[6] * dy) * scale;
    } else {
      spherical.theta -= dx * 0.006;
      spherical.phi = Math.max(0.02, Math.min(Math.PI - 0.02, spherical.phi - dy * 0.006));
    }
    apply();
  });
  var stop = function(event) {
    if (!dragging) return;
    dragging = null;
    if (canvas.releasePointerCapture) canvas.releasePointerCapture(event.pointerId);
  };
  canvas.addEventListener('pointerup', stop);
  canvas.addEventListener('pointercancel', stop);
  canvas.addEventListener('contextmenu', function(event){ event.preventDefault(); });
  canvas.addEventListener('wheel', function(event) {
    event.preventDefault();
    spherical.radius = Math.max(1e-4, spherical.radius * (1 + Math.sign(event.deltaY) * 0.12));
    apply();
  }, { passive: false });

  return stage;
}
