// Reading the fragments of a multiresolution mesh: a byte range out of the object's meshes.bin, inflated.
//
// The bytes come from a URL (a served report, or any static host that answers Range) or from a File (a folder the reader
// dropped on the page, where File.slice is the same thing). What a fragment *is* once inflated is a quantised payload,
// which the 3D code decodes: this layer only gets the bytes.

/** Where one object's fragment file is read from, or null if its geometry came with none. */
function fragmentSourceOf(entry, fileName) {
  if (!entry || !entry.meshes || !fileName) return null;
  if (entry.meshes.files) {
    var file = entry.meshes.files[fileName];
    return file ? { kind: 'file', read: function(offset, size) {
      return file.slice(offset, offset + size).arrayBuffer();
    } } : null;
  }
  var url = entry.meshes.url + encodeURIComponent(fileName);
  var whole = null;
  return { kind: 'url', read: function(offset, size) {
    // A host that ignores Range answers 200 with the whole file: read it once and slice that, not again for every fragment.
    if (whole) return whole.then(function(buffer){ return buffer.slice(offset, offset + size); });
    return fetch(url, { headers: { Range: 'bytes=' + offset + '-' + (offset + size - 1) } }).then(function(response) {
      if (response.status === 206) return response.arrayBuffer();
      if (response.status !== 200) throw new Error('fragment file ' + url + ': HTTP ' + response.status);
      whole = response.arrayBuffer();
      return whole.then(function(buffer){ return buffer.slice(offset, offset + size); });
    });
  } };
}

/** Raw-deflated bytes back to what was deflated. */
async function inflateRaw(buffer) {
  var stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/**
 * Runs reads a few at a time, in the order asked, and lets the ones not yet started be dropped when the camera has moved
 * on: a fragment wanted a second ago is often not wanted now, and starting it would only delay the one that is.
 */
function FragmentQueue(limit) {
  this.limit = limit || 6;
  this.running = 0;
  this.pending = [];          // { key, start, resolve, reject }
  this.inflight = new Map();  // key -> promise, so asking twice is one read
}
FragmentQueue.prototype.load = function(key, start) {
  var self = this;
  if (this.inflight.has(key)) return this.inflight.get(key);
  var promise = new Promise(function(resolve, reject) {
    self.pending.push({ key: key, start: start, resolve: resolve, reject: reject });
  });
  this.inflight.set(key, promise);
  var forget = function(){ self.inflight.delete(key); };
  promise.then(forget, forget);
  this._pump();
  return promise;
};
FragmentQueue.prototype._pump = function() {
  var self = this;
  while (this.running < this.limit && this.pending.length) {
    var job = this.pending.shift();
    this.running++;
    job.start().then(job.resolve, job.reject).then(function() { self.running--; self._pump(); });
  }
};
/** Drop what has not started and is not in `keep`. Their promises reject with `dropped`, which callers ignore. */
FragmentQueue.prototype.keepOnly = function(keep) {
  var self = this;
  this.pending = this.pending.filter(function(job) {
    if (keep.has(job.key)) return true;
    self.inflight.delete(job.key);
    job.reject({ dropped: true });
    return false;
  });
};

var FRAGMENT_QUEUE = new FragmentQueue(6);

/** The inflated payload of one fragment of an object's mesh file. */
function readFragment(source, node, key) {
  return FRAGMENT_QUEUE.load(key, async function() {
    return inflateRaw(await source.read(node.offset, node.size));
  });
}
