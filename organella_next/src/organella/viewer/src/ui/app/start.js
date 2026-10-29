if (typeof document !== 'undefined' && document.getElementById
    && document.getElementById('load-btn')) {
  boot();
}

// Everything with a contract worth pinning, for tests/report_page_check.mjs: the shape the
// report is read into (adapt), the maths the panels draw (contactClusters, ecdfCurve,
// aggregateHistograms, binnedTrend, pearsonR), the binary container mesh.py writes
// (decodePayload), index arithmetic (mergeInstances), and the SQL the geometry sections
// build.
if (typeof globalThis !== 'undefined') {
  globalThis.OrganellaReport = {
    adapt: adapt, checkReadable: checkReadable,
    // What a deep row is asked for, so a rename in the writer is a failing test
    // and not an empty section.
    DEEP_COLUMNS: DEEP_COLUMNS,
    // The surface kinds this page can draw, so the writer's list can be checked against it.
    surfaceKinds: Object.keys(SURFACE_BUILDERS),
    ellipsoidOf: ellipsoidOf,
    MAX_ELLIPSOID_STRETCH: MAX_ELLIPSOID_STRETCH,
    setSurfaceDetail: setSurfaceDetail,
    decodeDrawable: decodeDrawable,
    state: function(){ return { OBJECTS:OBJECTS, ENTS:ENTS, INSTANCES:INSTANCES,
                                CONTACTS:CONTACTS, COLS:COLS, DIST_TARGETS:DIST_TARGETS,
                                PLANAR:PLANAR, MIXED_DIMS:MIXED_DIMS, CHANCE:CHANCE }; },
    showMakeAReport: showMakeAReport, showLandingForm: showLandingForm,
    distColumnsFor: distColumnsFor,
    distCols: distCols, scatterDistCols: scatterDistCols, histTargets: histTargets,
    endDistCols: endDistCols, extentGapUm: extentGapUm,
    reportOnlyMetrics: reportOnlyMetrics, reportMetricValues: reportMetricValues,
    metricSpan: metricSpan, viridisHexAt: viridisHexAt, labelFor: labelFor,
    boundingMaskName: boundingMaskName,
    polarUnit: polarUnit, polarOffset: polarOffset, polarReferences: polarReferences,
    polarFrame: polarFrame, polarAngleDeg: polarAngleDeg, polarConeDeg: polarConeDeg,
    polarCentreAngleDeg: polarCentreAngleDeg,
    polarityIndices: polarityIndices,
    setPolarReference: function(name){ st.polarRef = name; },
    chanceRows: chanceRows, chanceStats: chanceStats, chanceCurve: chanceCurve,
    medianOfCounts: medianOfCounts, spanUnion: spanUnion,
    nonDistMetricCols: nonDistMetricCols, correlationAxisCols: correlationAxisCols,
    orderDistDims: orderDistDims,
    contactClusters: contactClusters, contactStructures: contactStructures,
    contactMaxGap: contactMaxGap,
    ecdfCurve: ecdfCurve, binnedTrend: binnedTrend, pearsonR: pearsonR, fitLine: fitLine,
    mannWhitney: mannWhitney, significanceStars: significanceStars,
    pairwiseSignificance: pairwiseSignificance, significanceLabel: significanceLabel,
    untestedLines: untestedLines, bracketRungs: bracketRungs,
    exactUCounts: exactUCounts, normalCdf: normalCdf, smallestPossibleP: smallestPossibleP,
    effectOf: effectOf, testLimit: testLimit,
    logChoose: logChoose, baselineScore: baselineScore, histogramOf: histogramOf,
    histSpan: histSpan, aggregateHistograms: aggregateHistograms,
    quantileSorted: quantileSorted, finitePairs: finitePairs,
    decodePayload: decodePayload, mergeInstances: mergeInstances,
    contactGroups: contactGroups, explodeOffset: explodeOffset,
    // Explaining a chart in the viewer: the specs a chart hands over, and the maths the
    // viewer draws them with.
    explainKey: explainKey, explainEntities: explainEntities, explainShown: explainShown,
    explainedValue: explainedValue, targetOfColumn: targetOfColumn, fmtValue: fmtValue,
    metricSpec: metricSpec, distanceSpec: distanceSpec, bothSpec: bothSpec,
    groupSpec: groupSpec, polaritySpec: polaritySpec,
    structureSpec: structureSpec, polarityStructures: polarityStructures,
    polarityFrame: polarityFrame,
    polarMorphPositions: polarMorphPositions, polarBinOf: polarBinOf,
    polarSubjects: polarSubjects, polarCellPoint: polarCellPoint,
    polarWeight: polarWeight, polarWeights: polarWeights, weightedQuantile: weightedQuantile,
    clearPolarOffsets: function(){ _POLAR_OFFSETS = {}; },
    toRgb: toRgb, viridisAt: viridisAt,
    pluralise: pluralise,
    toHsl: toHsl, hslToHex: hslToHex, litColour: litColour,
    toLinear: toLinear, toLinearRgb: toLinearRgb,
    thumbsWanted: thumbsWanted, GALLERY_ROWS: GALLERY_ROWS, MAX_THUMBS: MAX_THUMBS,
    shortFacetLabels: shortFacetLabels, extentTerms: extentTerms, geometryMetrics: geometryMetrics,
    overviewSummary: overviewSummary, structureColumns: structureColumns,
    coverageNote: coverageNote, seriesColour: seriesColour,
    entColor: entColor, renderAll: renderAll, dataName: dataName,
    groupRows: groupRows, groupBaselineRows: groupBaselineRows,
    dimPairs: dimPairs, facetsOf: facetsOf,
    setColumnHelp: function(map){ COLUMN_HELP = map || {}; },
    setReportNoun: function(word){ REPORT_NOUN = word || null; },
    nounState: function(){ return { one: NOUN.one, many: NOUN.many,
                                    bounded: NOUN.bounded, a: aNoun() }; },
    metricHelp: metricHelp,
    setPlotStyle: function(style){ plotStyle = style; },
    initFilters: initFilters,
    groupBy: function(){ return plotGroupBy; },
    setGroupBy: function(by){ plotGroupBy = by; },
    setSignificance: function(on){ showSignificance = on; },
    sourceOf: sourceOf, geometryFilesIn: geometryFilesIn,
    geometryProblem: geometryProblem,
    geometryOwnersSql: geometryOwnersSql, sceneSummarySql: sceneSummarySql,
    sceneGapSql: sceneGapSql, sceneGeometrySql: sceneGeometrySql,
    sceneEdgesSql: sceneEdgesSql, galleryCountSql: galleryCountSql, gallerySql: gallerySql,
    HAS_GEOMETRY_SQL: HAS_GEOMETRY_SQL, GEOMETRY_SIZE_SQL: GEOMETRY_SIZE_SQL,
    esc: esc
  };
}
