function renderFileTable() {
  var terms = extentTerms();
  // The unit goes in a span of its own: this row is uppercased, and uppercasing "µm³"
  // gives "ΜM³" - µ becomes a capital Mu, which reads as an M.
  var head = function(word, unit) {
    return escHtml(String(word).toLowerCase())
      + (unit ? ' <span class="unit">(' + unit + ')</span>' : '');
  };
  setHtml('th-extent', head(terms.word, terms.unit));
  setHtml('th-boundary', head(terms.boundaryLabel.replace(/ \(.*\)$/, ''), terms.boundaryUnit));
  setHtml('th-round', head(terms.roundLabel, ''));

  var num = function(v, digits) { return v != null && isFinite(v) ? (+v).toFixed(digits) : ''; };
  setHtml('file-table-body', filteredEnts().slice()
    .sort(function(a, b) {
      return (a.group_id || '').localeCompare(b.group_id || '')
        || (a.object_id || '').localeCompare(b.object_id || '')
        || a.entity_name.localeCompare(b.entity_name);
    })
    .map(function(r) {
      var size = r.file_size_bytes ? (r.file_size_bytes / 1e6).toFixed(2) + ' MB' : '';
      var badge = '<span class="cs-chip cs-chip-' + escHtml(r.entity_kind || '') + '">'
        + escHtml(r.entity_kind) + '</span>';
      // The dot goes in a span, not by making the cell a flex container: `display:flex`
      // on a <td> takes it out of the table's own layout, so its row stopped sharing a
      // height with its neighbours and the borders wandered.
      var dot = r.group_id ? '<span style="display:inline-block;width:8px;height:8px;'
        + 'border-radius:50%;background:' + groupColor(r.group_id) + ';margin-right:.4em;'
        + 'vertical-align:baseline"></span>' : '';
      return '<tr><td class="text-nowrap">' + dot + escHtml(r.object_id || '')
        + '</td><td>' + escHtml(r.entity_name) + '</td><td>' + badge + '</td>'
        + '<td class="text-end">' + (r.instance_count != null ? (+r.instance_count).toLocaleString() : '') + '</td>'
        + '<td class="text-end">' + num(totalExtent(r), 1) + '</td>'
        + '<td class="text-end">' + num(r[terms.boundary], 1) + '</td>'
        + '<td class="text-end">' + num(r[terms.round], 3) + '</td>'
        + '<td class="text-end">' + num(r.aspect_ratio_major_minor, 2) + '</td>'
        + '<td class="text-end">' + size + '</td>'
        + '<td class="text-muted small">' + escHtml(r.file_name || '') + '</td></tr>';
    }).join(''));
}
