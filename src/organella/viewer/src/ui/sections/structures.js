function renderEntityStats() {
  var container = document.getElementById('entity-stats-container');
  container.innerHTML = '';
  setHtml('entity-coverage', coverageNote('per-instance measurements'));

  var instRows = filteredInstances();
  var entRows = filteredEnts();
  var terms = extentTerms();
  var facets = facetsOf(instRows);

  // Every chart says what its colours are and how much is behind each (see sizeLine), so the
  // block carries no legend of its own: a second one said the same thing without the counts.
  var presentLabels = uniq(instRows.map(function(r){ return r.entity_name; }));
  var presentMasks = uniq(entRows.filter(function(r){ return r.entity_kind === 'mask'; })
                                  .map(function(r){ return r.entity_name; }));

  // One structure at a time: rendering every structure's charts is the main cost on a real
  // batch, and the question is always about one of them.
  var allEnts = presentLabels.concat(presentMasks);
  if (st.statsEntity === null
      || (st.statsEntity !== '__all__' && allEnts.indexOf(st.statsEntity) === -1)) {
    st.statsEntity = allEnts.length ? allEnts[0] : '__all__';
  }
  var sel = document.getElementById('stats-entity-select');
  sel.innerHTML = '<option value="__all__"' + (st.statsEntity === '__all__' ? ' selected' : '')
      + '>All structures</option>'
    + allEnts.map(function(e) {
        return '<option value="' + escHtml(e) + '"' + (st.statsEntity === e ? ' selected' : '')
          + '>' + escHtml(e) + '</option>';
      }).join('');
  sel.onchange = function() {
    st.statsEntity = sel.value;
    renderEntityStats();
  };
  // Structures as pills in the colours the rest of the page gives them, not a dropdown that
  // reads as a filter on the whole page: this is a choice of which structure the charts below
  // are about, and its answer should be visible without opening anything.
  var pills = document.getElementById('entity-pills');
  if (pills && pills.replaceChildren) {
    pills.replaceChildren();
    var label = document.createElement('span');
    label.className = 'pill-label';
    label.textContent = 'Structure';
    pills.appendChild(label);
    [['__all__', 'All structures', null, null]].concat(allEnts.map(function(e) {
      return [e, e, entColor(e), presentMasks.indexOf(e) >= 0 ? 'mask' : null];
    })).forEach(function(entry) {
      var pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'pill' + (st.statsEntity === entry[0] ? ' active' : '');
      if (entry[2]) pill.style.setProperty('--c', entry[2]);
      pill.innerHTML = (entry[2] ? '<i style="background:' + entry[2] + '"></i>' : '')
        + escHtml(entry[1]) + (entry[3] ? ' <small>' + entry[3] + '</small>' : '');
      pill.addEventListener('click', function() {
        st.statsEntity = entry[0];
        sel.value = entry[0];
        renderEntityStats();
      });
      pills.appendChild(pill);
    });
  }
  // The viewer's instance gallery goes where the charts go, once: after that it is the
  // reader's, and a filter changing under them does not take their choice away.
  if (st.statsEntity !== '__all__' && st.statsEntity !== dockState.followed) {
    dockState.followed = st.statsEntity;
    dockState.entity = st.statsEntity;
  }
  var showLabels = st.statsEntity === '__all__' ? presentLabels
    : presentLabels.filter(function(e){ return e === st.statsEntity; });
  var showMasks = st.statsEntity === '__all__' ? presentMasks
    : presentMasks.filter(function(e){ return e === st.statsEntity; });

  showLabels.forEach(function(ent) {
    var rows = instRows.filter(function(r){ return r.entity_name === ent; });
    var color = entColor(ent);
    var named = dataName(ent);
    var fallback = { name: ent, color: color };
    var block = document.createElement('div');
    block.className = 'entity-block';
    block.dataset.entity = ent;
    block.style.borderLeftColor = color;
    block.innerHTML = '<div class="entity-title"><span>' + named + '</span>'
        + '<span class="cs-chip cs-chip-label">label</span></div>'
      + '<div class="subsection-label">Inspecting individual ' + named + ' instances</div>'
      + '<div data-slot="gallery"></div>'
      + '<div class="subsection-label">How big and what shape is each ' + named + '?</div>'
      + '<p class="subsection-desc">' + panelStyleSentence(ent) + '</p>'
      + '<div class="charts-row mb-3" data-slot="metrics"></div>'
      + '<div class="subsection-label" data-slot="dist-label" style="display:none">How far is'
        + ' each ' + named + ' from other structures?</div>'
      + '<p class="subsection-desc" data-slot="dist-desc" style="display:none">Distance from'
        + ' the closest point of each ' + named + ' to the nearest instance of each structure.'
        + ' 0 means they overlap.</p>'
      + '<div class="charts-row" data-slot="dist"></div>'
      + '<div class="subsection-label mt-3" data-slot="end-label" style="display:none">How far'
        + ' is each ' + named + '\u2019s <b>end</b> from other structures?</div>'
      + '<p class="subsection-desc" data-slot="end-desc" style="display:none">The distance of'
        + ' the nearest <b>tip</b> of each ' + named + ' - where its skeleton stops.</p>'
      + '<div class="charts-row" data-slot="end"></div>'
      + '<div class="subsection-label mt-3" data-slot="both-label" style="display:none">Is each'
        + ' ' + named + ' close to two structures at once?</div>'
      + '<p class="subsection-desc" data-slot="both-desc" style="display:none">One panel per'
        + ' pair of structures. For each ' + named + ' we take the <b>larger</b> of its two'
        + ' distances - the distance at which it is close to <b>both</b> - and the curve shows'
        + ' the share of them at or below it.</p>'
      + '<div data-slot="both"></div>'
      + '<div class="subsection-label mt-3" data-slot="hist-label" style="display:none">How much'
        + ' of the body of each ' + named + ' lies near a structure?</div>'
      + '<p class="subsection-desc" data-slot="hist-desc" style="display:none">Every voxel'
        + ' making up the ' + named + ' population, binned by its distance to a structure. A'
        + ' tall peak near 0 means they hug that structure along much of their body - not just'
        + ' touch at a single point. The dotted line is the same distances taken over every'
        + ' voxel of the ' + noun() + ', not only over the ' + named + '.</p>'
      + '<div data-slot="hist-legend"></div>'
      + '<div class="charts-row" data-slot="hist"></div>'
      + '<div class="subsection-label mt-3" data-slot="corr-label" style="display:none">Free'
        + ' exploration: compare any two measurements of ' + named + '</div>'
      + '<p class="subsection-desc" data-slot="corr-desc" style="display:none">One point per ' + named + ' instance, with any two of'
        + ' its own measurements on the two axes. With enough instances the line is the median'
        + ' y per equal-count bin of x and the band the middle 50%; with only a few, the'
        + ' instances are drawn with a dotted least-squares line through them. R² below'
        + ' is the share of the variation in y that a straight line through x accounts for,'
        + ' across all instances. 0 means the line explains none of it, and 1 means every'
        + ' point lies on the line. With few instances, treat any trend with caution.</p>'
      + '<div class="charts-row" data-slot="corr"></div>';
    container.appendChild(block);
    var slot = function(name){ return block.querySelector('[data-slot="' + name + '"]'); };
    var reveal = function(name) {
      var el = slot(name);
      if (el) el.style.display = '';
    };

    // ── how big and what shape ────────────────────────────────────────────────
    var mcEl = slot('metrics');
    nonDistMetricCols(rows).forEach(function(metric) {
      distributionPanel(mcEl, {
        series: facets.valuesOf(rows, metric, fallback),
        title: panelTitle(ent, axisColLabel(metric)),
        unit: axisColUnit(metric) || null,
        help: metricHelp(metric, 'instance_'),
        noun: ent,
        explain: metricSpec(ent, metric),
        showFacetTicks: !facets.many
      });
    });

    // ── the instances themselves ──────────────────────────────────────────────
    galleryOrPointer(slot('gallery'), ent);

    // Which objects each facet is made of: a chance distribution is per object, and a
    // facet is read against its own objects pooled, not against the batch.
    var allObjects = uniq(rows.map(function(r){ return r.object_id; }));
    var objectsOf = {}, rowsOf = {};
    facets.split(rows, fallback).forEach(function(f) {
      objectsOf[f.name] = uniq(f.rows.map(function(r){ return r.object_id; }));
      rowsOf[f.name] = f.rows;
    });
    // ── how far from other structures ─────────────────────────────────────────
    var dCols = distCols(rows);
    if (dCols.length) {
      reveal('dist-label'); reveal('dist-desc');
      var dcEl = slot('dist');
      orderDistDims(dCols).forEach(function(col) {
        var title = distColLabel(col);
        distributionPanel(dcEl, {
          series: facets.valuesOf(rows, col, fallback),
          title: panelTitle(ent, 'to ' + title), unit: 'µm',
          noun: ent, showFacetTicks: !facets.many,
          explain: distanceSpec(ent, col)
        });
      });
    }

    // ── how far the ends sit, for a structure that has ends ───────────────────
    var eCols = endDistCols(rows);
    if (eCols.length) {
      reveal('end-label'); reveal('end-desc');
      var ecEl = slot('end');
      orderDistDims(eCols).forEach(function(col) {
        var endTarget = col.replace(/^distance_to_/, '').replace(/_end_um$/, '');
        distributionPanel(ecEl, {
          series: facets.valuesOf(rows, col, fallback),
          title: panelTitle(ent, 'end to ' + endTarget.replace(/_/g, ' ')), unit: 'µm',
          noun: ent, showFacetTicks: !facets.many,
          explain: distanceSpec(ent, col)
        });
      });
    }

    // ── close to two structures at once ───────────────────────────────────────
    var d2cols = scatterDistCols(rows);
    if (d2cols.length >= 2) {
      reveal('both-label'); reveal('both-desc');
      var dims = orderDistDims(d2cols);
      pairPanels(slot('both'), {
        series: facets.split(rows, fallback), dims: dims, noun: ent, entity: ent,
        allMean: dims.every(function(c){ return /_mean_um$/.test(c); })
      });
    }

    // ── how much of the body lies near a structure ────────────────────────────
    var hTargets = histTargets(rows);
    if (!hTargets.length && dCols.length) {
      // The measurement exists; this run just did not ask for it. Silence here reads as
      // "the report cannot answer this", which is the wrong thing to conclude.
      reveal('hist-label'); reveal('hist-desc');
      slot('hist-desc').innerHTML =
        'Not measured in this run. Every voxel of every instance has its distance to each'
        + ' structure, and the distribution of those is what says whether a ' + named
        + ' <em>hugs</em> a structure along its length or only touches it at one point - the'
        + ' closest-point charts above see only the single nearest point. Re-run with'
        + ' <code>--distance-histograms</code> to record it.';
    }
    if (hTargets.length) {
      reveal('hist-label'); reveal('hist-desc');
      var dhEl = slot('hist');
      var unbinnable = [];
      var drewChance = false;
      hTargets.forEach(function(target) {
        var el = document.createElement('div');
        el.className = 'chart-cell';
        var histEl = document.createElement('div');
        el.appendChild(histEl);
        dhEl.appendChild(el);
        // Shared grid across all facets so the density curves line up (no bar-width gaps),
        // and wide enough to hold the chance distribution too: the two are only comparable
        // drawn on one axis, and the object reaches further from a structure than the
        // instances of any one population do.
        var measuredSpan = histSpan(rows, target);
        var span = spanUnion(measuredSpan,
                             histSpan(chanceRows(allObjects, target), target));
        var traces = [];
        // The same distances taken over the whole object, dotted: the shape a population
        // that cared nothing for this structure would have, in the same object.
        var addChance = function(objects, name, colour) {
          var curve = chanceCurve(objects, target, span);
          if (!curve) return;
          drewChance = true;
          traces.push({ type:'scatter', mode:'lines', name:name + ' (chance)',
            x:curve.centers, y:curve.density,
            line:{color:colour, width:1.5, dash:'dot', shape:'hv'},
            hovertemplate:'%{x:.2f} µm: %{y:.4f}<extra>anywhere in ' + name + '</extra>' });
        };
        if (facets.many) {
          facets.values.forEach(function(v) {
            var agg = aggregateHistograms(
              rows.filter(function(r){ return r[facets.key] === v; }), target, 30, span);
            if (!agg) return;
            traces.push({ type:'scatter', mode:'lines', name:facets.labelOf(v),
              x:agg.centers, y:agg.density, line:{color:gColor(v), width:2, shape:'hv'},
              hovertemplate:'%{x:.2f} µm: %{y:.4f}<extra>' + v + '</extra>' });
            addChance(objectsOf[String(v)], String(v), gColor(v));
          });
        } else {
          var agg = aggregateHistograms(rows, target, 30, span);
          if (agg) {
            traces.push({ type:'scatter', mode:'lines', name:ent, fill:'tozeroy',
              x:agg.centers, y:agg.density, line:{color:color, width:2, shape:'hv'},
              fillcolor: withAlpha(color, 0.22),
              hovertemplate:'%{x:.2f} µm: %{y:.4f}<extra></extra>' });
            addChance(allObjects, noun(), CS.ink3);
          }
        }
        if (!traces.length) {
          // The column is there and the panel is not: without saying so, a reader is left
          // to conclude the report cannot answer this, which is not what happened.
          unbinnable.push(target);
          el.remove();
          return;
        }
        drawPlot(histEl, traces, {
          margin:{l:54,r:6,t:36,b:50}, height:270,
          title:{text:ent + ' body to ' + target + ', every voxel'},
          xaxis:{title:'distance to ' + target + ' (µm)', rangemode:'tozero'},
          yaxis:{title:'voxel density', rangemode:'tozero'},
          showlegend:false   // facets are named by the block's shared legend
        }, {responsive:true, displayModeBar:false});
      });
      if (drewChance) {
        // Ground the run was told no instance could occupy (--baseline-exclude) is not in the
        // dotted distribution, so the page says which structures those are.
        var leftOut = {};
        hTargets.forEach(function(t) {
          chanceRows(allObjects, t).forEach(function(r) {
            String(r.chance_excluded || '').split(',').forEach(function(n) {
              if (n.trim()) leftOut[n.trim()] = true;
            });
          });
        });
        var leftOutNames = Object.keys(leftOut);
        var withoutWords = leftOutNames.length ? ', without the ' + escHtml(leftOutNames.join(', ')) : '';
        if (leftOutNames.length) {
          slot('hist-desc').insertAdjacentHTML('beforeend',
            ' That reference leaves out the ' + escHtml(leftOutNames.join(', '))
            + ', where no ' + named + ' can be.');
        }
        var key = function(swatch, words) {
          return '<span class="d-inline-flex align-items-center gap-1">' + swatch + words + '</span>';
        };
        slot('hist-legend').innerHTML = '<div class="chart-legend">'
          + (facets.many ? facets.values.map(function(v) {
              return key('<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:'
                + gColor(v) + '"></span>', escHtml(v));
            }).join('') : '')
          + key('<span style="display:inline-block;width:20px;border-top:2px dotted ' + CS.ink3 + '"></span>',
                'every voxel of the ' + escHtml(noun()) + withoutWords) + '</div>';
      }
      if (unbinnable.length) {
        var trouble = document.createElement('div');
        trouble.className = 'chart-note';
        trouble.innerHTML = 'No distribution could be drawn to '
          + unbinnable.map(dataName).join(', ')
          + ': the recorded histograms for it are all at one distance, so there is no range '
          + 'to bin over.';
        dhEl.appendChild(trouble);
      }
    }

    // ── free exploration ──────────────────────────────────────────────────────
    var axisCols = correlationAxisCols(rows);
    if (axisCols.length >= 2) {
      reveal('corr-label'); reveal('corr-desc');
      renderCorrelation(slot('corr'), rows, ent, color, {
        axisCols: axisCols, facets: facets
      });
    }
  });

  // ── whole-structure masks: one shape, measured once per object ─────────────
  showMasks.forEach(function(ent) {
    var mRows = entRows.filter(function(r) {
      return r.entity_name === ent && r.entity_kind === 'mask';
    });
    var color = entColor(ent);
    var named = dataName(ent);
    var maskFacets = facetsOf(mRows);
    var block = document.createElement('div');
    block.className = 'entity-block';
    block.dataset.entity = ent;
    block.style.borderLeftColor = color;
    block.innerHTML = '<div class="entity-title"><span>' + named + '</span>'
        + '<span class="cs-chip cs-chip-mask">mask</span></div>'
      + '<div class="subsection-label">How big and what shape is the whole ' + named + '?</div>'
      + '<p class="subsection-desc">A mask has no instances to distribute, so this is one'
        + ' measurement per ' + noun() + '.</p>'
      + '<div class="charts-row" data-slot="mask"></div>'
      + '<div class="subsection-label">What does each ' + named + ' look like?</div>'
      + '<p class="subsection-desc">The shape behind each measurement above, one card per '
        + noun() + ', with the value under it. Click one to look at it properly.</p>'
      + '<div data-slot="mask-gallery"></div>';
    container.appendChild(block);
    galleryOrPointer(block.querySelector('[data-slot="mask-gallery"]'), ent, 'file');
    if (!mRows.length) return;
    var mkEl = block.querySelector('[data-slot="mask"]');

    [
      { col:terms.total, label:terms.word.charAt(0).toUpperCase() + terms.word.slice(1),
        unit:terms.unit },
      { col:terms.boundary, label:terms.boundaryLabel.replace(/ \(.*\)$/, ''),
        unit:terms.boundaryUnit },
      { col:terms.round, label:terms.roundLabel, unit:'' },
      { col:'aspect_ratio_major_minor', label:'Aspect ratio', unit:'' },
      { col:'polar_dist_um', label:'Distance from ' + noun() + ' centre', unit:'µm' }
    ].forEach(function(m) {
      if (!hasValue(mRows, m.col)) return;
      distributionPanel(mkEl, {
        series: maskFacets.valuesOf(mRows, m.col, { name: ent, color: color }),
        title: panelTitle(ent, m.label.toLowerCase()), unit: m.unit || null,
        help: metricHelp(m.col), noun: noun(), width: 280,
        explain: structureSpec(ent),
        showFacetTicks: !maskFacets.many
      });
    });
  });

  if (!showLabels.length && !showMasks.length) {
    container.innerHTML = '<div class="text-muted small">No structure matches the current'
      + ' filters.</div>';
  }
}

/** What the panels in this block are showing, given the style and the faceting. */
function panelStyleSentence(ent) {
  var by = plotGroupBy === 'group' ? 'group' : noun();
  if (plotStyle === 'histogram') {
    return 'One histogram per ' + by + ', binned on a shared grid and drawn as a share of'
      + ' that ' + by + '\u2019s own ' + dataName(ent) + ' instances - so the shapes are'
      + ' comparable however many each holds. Switch <b>Charts</b> in the bar above back to'
      + ' <b>Distributions</b> for boxes.';
  }
  return 'One box per ' + by + '. The box is the middle 50% of instances, the line the'
    + ' median, dots the individual ' + dataName(ent) + ' instances that sit outside it.';
}

/**
 * Free exploration: any two measurements against each other.
 *
 * Two free-form measurements have no "close to both" reading, so this keeps an x-versus-y
 * view — but it must still say something when an object holds a single instance. Enough
 * instances to bin: median of y per equal-count x-bin with the middle 50% as a band. Too
 * few: the instances themselves plus a least-squares line, pooled across objects when no
 * single object can carry a line — with one instance per object the objects ARE the
 * observations. Either way there is a trend to look at. R² always comes from every
 * instance, never from the bins.
 */
function renderCorrelation(host, rows, ent, color, opts) {
  var axisCols = opts.axisCols;
  var maskName = (OBJECTS[0] || {}).object_mask_name;
  var defX = axisCols.find(function(c) {
      return maskName && c.indexOf('distance_to_' + maskName) === 0;
    })
    || axisCols.find(function(c){ return c.indexOf('distance_to_') === 0; })
    || axisCols[0];
  var defY = ['branches','sphericity','circularity','aspect_ratio_major_minor','length_um',
              'tortuosity','polar_dist_um']
    .filter(function(c){ return axisCols.indexOf(c) >= 0 && c !== defX; })[0]
    || axisCols.find(function(c){ return c !== defX; })
    || axisCols[0];

  // Wide, because this one carries two selectors, a chart and a fit note per facet: at the
  // panel width the rest of the page uses, the selectors stacked and the note ran into them.
  var wrap = document.createElement('div');
  wrap.className = 'chart-cell wide';
  host.appendChild(wrap);

  var ctrl = document.createElement('div');
  ctrl.className = 'ctrl-bar';
  var ySel = document.createElement('select'), xSel = document.createElement('select');
  ySel.className = xSel.className = 'form-select form-select-sm';
  ySel.style.cssText = xSel.style.cssText = 'width:auto;font-size:.7rem';
  axisCols.forEach(function(c) {
    ySel.innerHTML += '<option value="' + escHtml(c) + '"' + (c === defY ? ' selected' : '')
      + '>' + escHtml(axisColLabel(c)) + '</option>';
    xSel.innerHTML += '<option value="' + escHtml(c) + '"' + (c === defX ? ' selected' : '')
      + '>' + escHtml(axisColLabel(c)) + '</option>';
  });
  var yLbl = document.createElement('span'); yLbl.textContent = 'y:';
  var xLbl = document.createElement('span'); xLbl.textContent = 'vs x:';
  ctrl.append(yLbl, ySel, xLbl, xSel);
  wrap.appendChild(ctrl);

  var plotEl = document.createElement('div');
  plotEl.style.cssText = 'width:620px;height:360px';
  wrap.appendChild(plotEl);
  // Per-facet fit quality goes here and not into the chart legend: as legend entries
  // these lines wrap one-per-row over the x-axis title; as text they stay readable.
  var fitEl = document.createElement('div');
  fitEl.className = 'fit-note';
  wrap.appendChild(fitEl);

  function draw() {
    var colX = xSel.value, colY = ySel.value;
    var lblX = axisColTitle(colX), lblY = axisColTitle(colY);
    var traces = [], fits = [], subtitle = '';
    var facets = opts.facets;
    var band = (facets.many ? facets.values.length : 1) <= TREND_MAX_BANDS;
    var series = facets.split(rows, { name: ent, color: color }).map(function(f) {
      return { name: f.label, full: f.name, color: f.color, rows: f.rows };
    });
    var pooled = [], anyTrend = false, anyFitted = false;
    series.forEach(function(sr) {
      var pts = finitePairs(sr.rows.map(function(r){ return r[colX]; }),
                            sr.rows.map(function(r){ return r[colY]; }));
      if (!pts.length) return;
      pooled = pooled.concat(pts);
      var rr = pearsonR(pts.map(function(q){ return q[0]; }), pts.map(function(q){ return q[1]; }));
      fits.push({ facet:sr.full, r2: rr != null ? rr * rr : null, n: pts.length });
      var traceOpts = { band:band, xlabel:lblX, ylabel:lblY,
                        width: series.length > TREND_MAX_BANDS ? 1.5 : 2 };
      var t = binnedTrend(pts, 12);
      if (t) { anyTrend = true; traces.push.apply(traces, trendTraces(t, sr.name, sr.color, traceOpts)); }
      else {
        traces.push(pointTrace(pts, sr.name, sr.color, traceOpts));
        var fit = fitLine(pts);
        if (fit) { anyFitted = true; traces.push(fitTrace(fit, sr.color, traceOpts)); }
      }
    });
    // No single series could be summarised: fit the pooled instances instead, so the chart
    // still shows the relationship and not a handful of loose dots.
    if (!anyTrend && !anyFitted) {
      var pfit = fitLine(pooled);
      if (pfit) {
        traces.push(fitTrace(pfit, CS.grey, { xlabel:lblX, ylabel:lblY, pooled:true }));
        // The drawn line gets its own number: the per-object rows all read n=1 here.
        var prr = pearsonR(pooled.map(function(q){ return q[0]; }),
                           pooled.map(function(q){ return q[1]; }));
        fits.push({ facet:'all ' + facets.word + 's pooled',
                    r2: prr != null ? prr * prr : null, n: pooled.length, pooled:true });
      }
    }
    if (!facets.many && fits.length) {
      subtitle = fits[0].r2 != null
        ? '  —  R² = ' + fits[0].r2.toFixed(3) + ' (n=' + fits[0].n + ')'
        : '  —  n=' + fits[0].n;
    }
    fitEl.innerHTML = fits.length
      ? '<span class="fit-note-lbl">correlation per ' + facets.word + '</span>'
        + fits.map(function(f) {
            return '<span class="fit-note-item"><span class="fit-dot" style="background:'
              + (f.pooled ? CS.grey : gColor(f.facet)) + '"></span>'
              + escHtml(f.pooled ? f.facet : facets.labelOf(f.facet)) + ' <b>R²&nbsp;'
              + (f.r2 != null ? f.r2.toFixed(2) : '–') + '</b> <span class="text-muted">n='
              + f.n + '</span></span>';
          }).join('')
      : '';
    if (!traces.length) { Plotly.purge(plotEl); return; }
    drawPlot(plotEl, traces, {
      margin:{l:62, r:12, t:32, b:52}, height:360,
      title:{text:lblY + ' vs ' + lblX + subtitle},
      xaxis:{title:lblX}, yaxis:{title:lblY},
      showlegend:false   // facets are named by the block's shared legend
    }, {responsive:true, displayModeBar:false});
  }

  xSel.onchange = draw;
  ySel.onchange = draw;
  draw();
}
