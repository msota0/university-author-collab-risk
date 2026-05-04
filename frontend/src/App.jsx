import React, { useEffect, useMemo, useRef, useState } from 'react'
import axios from 'axios'
import CytoscapeComponent from 'react-cytoscapejs'

const API_BASE = 'http://localhost:8000'

const COLOR_SEED = '#c0392b'
const COLOR_DIRECT = '#2980b9'
const COLOR_FLAGGED = '#e67e22'

export default function App() {
  const [umAuthors, setUmAuthors] = useState([])
  const [authorQuery, setAuthorQuery] = useState('')
  const [selectedAuthor, setSelectedAuthor] = useState(null)
  const [graphData, setGraphData] = useState({ nodes: [], edges: [], seed: null })
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(false)
  const [hasRun, setHasRun] = useState(false)
  const [error, setError] = useState(null)
  const [selectedItem, setSelectedItem] = useState(null)
  const [sharedWorks, setSharedWorks] = useState(null)

  // Focus / drill-down: when set, show only this direct collaborator's flagged hops.
  const [focusedDirectId, setFocusedDirectId] = useState(null)
  // Power-user override: show every path simultaneously.
  const [showAllPaths, setShowAllPaths] = useState(false)

  // Filters
  const [minWeight, setMinWeight] = useState(1)
  const [countryFilter, setCountryFilter] = useState('')
  const [showDirectLabels, setShowDirectLabels] = useState(false)

  const cyRef = useRef(null)

  useEffect(() => {
    axios
      .get(`${API_BASE}/authors/um`)
      .then((r) => setUmAuthors(r.data || []))
      .catch((err) => setError(`Failed to load UM authors: ${err.message}`))
  }, [])

  const filteredAuthors = useMemo(() => {
    if (!authorQuery) return umAuthors.slice(0, 50)
    const q = authorQuery.toLowerCase()
    return umAuthors
      .filter((a) => (a.display_name || '').toLowerCase().includes(q))
      .slice(0, 50)
  }, [authorQuery, umAuthors])

  const loadRiskPaths = async () => {
    if (!selectedAuthor) return
    setLoading(true)
    setError(null)
    setSelectedItem(null)
    setSharedWorks(null)
    setFocusedDirectId(null)
    try {
      const [paths, summ] = await Promise.all([
        axios.get(`${API_BASE}/review/risk-paths`, {
          params: { seed_author_id: selectedAuthor.author_id },
        }),
        axios.get(`${API_BASE}/review/summary`, {
          params: { seed_author_id: selectedAuthor.author_id },
        }),
      ])
      setGraphData(paths.data)
      setSummary(summ.data)
      setHasRun(true)
    } catch (e) {
      setError(`Failed to load risk paths: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  // Sorted list of direct collaborators (by indirect-risk count, desc).
  const directList = useMemo(() => {
    const directs = (graphData.nodes || []).filter((n) => n.node_type === 'direct')
    return directs.slice().sort(
      (a, b) => (b.indirect_risk_count ?? 0) - (a.indirect_risk_count ?? 0)
    )
  }, [graphData])

  // Flagged hops associated with the currently focused direct (for the right-side detail list).
  const focusedFlagged = useMemo(() => {
    if (!focusedDirectId) return []
    const flaggedById = new Map(
      (graphData.nodes || [])
        .filter((n) => n.node_type === 'flagged_second_hop')
        .map((n) => [n.id, n])
    )
    return (graphData.edges || [])
      .filter((e) => e.edge_type === 'indirect_risk_path' && e.source === focusedDirectId)
      .map((e) => ({ ...flaggedById.get(e.target), edgeWeight: e.weight }))
      .filter((n) => n && n.id)
      .filter(
        (n) =>
          !countryFilter ||
          (n.country || '').toUpperCase() === countryFilter.toUpperCase()
      )
      .sort((a, b) => {
        const c = (a.country || '').localeCompare(b.country || '')
        if (c !== 0) return c
        return (a.label || '').localeCompare(b.label || '')
      })
  }, [graphData, focusedDirectId, countryFilter])

  const elements = useMemo(() => {
    const rawNodes = graphData.nodes || []
    const rawEdges = (graphData.edges || []).filter((e) => (e.weight ?? 1) >= minWeight)
    const seedNode = rawNodes.find((n) => n.node_type === 'seed') || null
    const cfUpper = (countryFilter || '').toUpperCase()

    // Decide which nodes are visible based on mode.
    //   showAllPaths=true → everything (firehose)
    //   focusedDirectId   → seed + that direct + its flagged hops
    //   else (overview)   → seed + all directs (flagged hops hidden until drill-down)
    const allDirects = rawNodes.filter((n) => n.node_type === 'direct')
    let visibleDirects = allDirects
    let visibleFlagged = []
    let directEdges = []
    let indirectEdges = []

    if (showAllPaths) {
      const keptFlagged = new Set(
        rawNodes
          .filter((n) => n.node_type === 'flagged_second_hop')
          .filter((n) => !cfUpper || (n.country || '').toUpperCase() === cfUpper)
          .map((n) => n.id)
      )
      indirectEdges = rawEdges.filter(
        (e) => e.edge_type === 'indirect_risk_path' && keptFlagged.has(e.target)
      )
      const keptDirects = new Set(indirectEdges.map((e) => e.source))
      visibleDirects = allDirects.filter((n) => keptDirects.has(n.id))
      visibleFlagged = rawNodes.filter(
        (n) => n.node_type === 'flagged_second_hop' && keptFlagged.has(n.id)
      )
      directEdges = rawEdges.filter(
        (e) => e.edge_type === 'direct' && keptDirects.has(e.target)
      )
    } else if (focusedDirectId) {
      // Drill-down: only the focused direct + its flagged hops, optionally country-filtered.
      const flaggedIds = new Set(
        rawEdges
          .filter((e) => e.edge_type === 'indirect_risk_path' && e.source === focusedDirectId)
          .map((e) => e.target)
      )
      const flaggedById = new Map(
        rawNodes
          .filter((n) => n.node_type === 'flagged_second_hop' && flaggedIds.has(n.id))
          .filter((n) => !cfUpper || (n.country || '').toUpperCase() === cfUpper)
          .map((n) => [n.id, n])
      )
      indirectEdges = rawEdges.filter(
        (e) =>
          e.edge_type === 'indirect_risk_path' &&
          e.source === focusedDirectId &&
          flaggedById.has(e.target)
      )
      visibleFlagged = Array.from(flaggedById.values())
      visibleDirects = allDirects.filter((n) => n.id === focusedDirectId)
      directEdges = rawEdges.filter(
        (e) => e.edge_type === 'direct' && e.target === focusedDirectId
      )
    } else {
      // Overview mode — seed + all directs. Show seed→direct edges so the
      // relationship is visible (concentric layout makes them rays from the
      // center).
      directEdges = rawEdges.filter((e) => e.edge_type === 'direct')
    }

    const visibleNodes = []
    if (seedNode) visibleNodes.push(seedNode)
    visibleNodes.push(...visibleDirects)
    visibleNodes.push(...visibleFlagged)

    // Sort directs by indirect_risk_count (desc) for the drill-down column.
    const sortedDirects = visibleDirects
      .slice()
      .sort((a, b) => (b.indirect_risk_count ?? 0) - (a.indirect_risk_count ?? 0))
    const sortedFlagged = visibleFlagged.slice().sort((a, b) => {
      const c = (a.country || '').localeCompare(b.country || '')
      if (c !== 0) return c
      return (a.label || '').localeCompare(b.label || '')
    })

    // Preset positions are only used in drill-down / show-all modes.
    // Overview mode lets cytoscape's `concentric` layout place nodes.
    const positions = new Map()
    const usePreset = !!focusedDirectId || showAllPaths
    if (usePreset) {
      const Y_SPACING_DIRECT = 30
      const Y_SPACING_FLAGGED = 26
      const X_SEED = 0
      const X_DIRECT = 600
      const X_FLAGGED = 1200

      const directHeight = Math.max(0, (sortedDirects.length - 1) * Y_SPACING_DIRECT)
      const flaggedHeight = Math.max(0, (sortedFlagged.length - 1) * Y_SPACING_FLAGGED)
      const totalHeight = Math.max(directHeight, flaggedHeight, 200)

      if (seedNode) positions.set(seedNode.id, { x: X_SEED, y: totalHeight / 2 })
      const directOffset = (totalHeight - directHeight) / 2
      sortedDirects.forEach((n, i) => {
        positions.set(n.id, { x: X_DIRECT, y: directOffset + i * Y_SPACING_DIRECT })
      })
      const flaggedOffset = (totalHeight - flaggedHeight) / 2
      sortedFlagged.forEach((n, i) => {
        positions.set(n.id, { x: X_FLAGGED, y: flaggedOffset + i * Y_SPACING_FLAGGED })
      })
    }

    const cyNodes = visibleNodes.map((n) => {
      const showLabel =
        n.node_type === 'seed' ||
        n.node_type === 'flagged_second_hop' ||
        (n.node_type === 'direct' &&
          (showDirectLabels || focusedDirectId === n.id || !showAllPaths))

      const isFocused = focusedDirectId && n.id === focusedDirectId
      const classes = [n.node_type]
      if (isFocused) classes.push('focused')

      // Encode risk count for stylesheet sizing. Scale up so the difference
      // between low- and high-risk directs is clearly visible.
      const riskCount = n.indirect_risk_count ?? 0
      const riskScale = 18 + Math.min(50, Math.sqrt(riskCount) * 6)

      const node = {
        data: {
          id: n.id,
          label: n.label,
          displayLabel: showLabel
            ? n.node_type === 'direct' && riskCount
              ? `${n.label}  (${riskCount})`
              : n.label
            : '',
          country: n.country,
          institution: n.institution,
          node_type: n.node_type,
          indirect_risk_count: riskCount,
          riskScale,
          is_um_author: n.is_um_author,
          flag_reason: n.flag_reason || '',
          risk_level: n.risk_level || '',
        },
        classes: classes.join(' '),
      }
      // Only attach a position when we're using preset layout. Concentric
      // layout assigns positions itself.
      if (usePreset) node.position = positions.get(n.id) || { x: 0, y: 0 }
      return node
    })

    const cyEdges = [...directEdges, ...indirectEdges].map((e) => ({
      data: {
        id: `${e.source}__${e.target}__${e.edge_type}`,
        source: e.source,
        target: e.target,
        weight: e.weight,
        edge_type: e.edge_type,
      },
      classes: e.edge_type,
    }))

    return [...cyNodes, ...cyEdges]
  }, [graphData, minWeight, countryFilter, showDirectLabels, focusedDirectId, showAllPaths])

  const stylesheet = useMemo(
    () => [
      {
        selector: 'node',
        style: {
          'background-color': '#aaa',
          label: 'data(displayLabel)',
          color: '#222',
          'font-size': 11,
          'text-valign': 'center',
          'text-halign': 'right',
          'text-margin-x': 6,
          'text-wrap': 'wrap',
          'text-max-width': 200,
          width: 18,
          height: 18,
          'border-width': 1,
          'border-color': '#444',
        },
      },
      {
        selector: 'node.seed',
        style: {
          'background-color': COLOR_SEED,
          width: 42,
          height: 42,
          'font-weight': 'bold',
          'font-size': 14,
          'text-halign': 'left',
          'text-margin-x': -10,
        },
      },
      {
        selector: 'node.direct',
        style: {
          'background-color': COLOR_DIRECT,
          // Size scales with how many flagged second-hops this direct has.
          width: 'data(riskScale)',
          height: 'data(riskScale)',
          'text-valign': 'center',
          'text-halign': 'right',
          'font-size': 11,
        },
      },
      {
        selector: 'node.direct.focused',
        style: {
          'border-width': 4,
          'border-color': '#111',
          'font-weight': 'bold',
        },
      },
      {
        selector: 'node.flagged_second_hop',
        style: {
          'background-color': COLOR_FLAGGED,
          width: 18,
          height: 18,
        },
      },
      {
        selector: 'edge',
        style: {
          'curve-style': 'bezier',
          'line-color': '#bbb',
          width: 1.5,
          'target-arrow-shape': 'triangle',
          'target-arrow-color': '#bbb',
          'arrow-scale': 0.7,
        },
      },
      {
        selector: 'edge.direct',
        style: { 'line-color': '#7faed3', 'target-arrow-color': '#7faed3', width: 1.5 },
      },
      {
        selector: 'edge.indirect_risk_path',
        style: {
          'line-color': '#e67e22',
          'target-arrow-color': '#e67e22',
          'line-style': 'dashed',
          width: 2,
        },
      },
      { selector: ':selected', style: { 'border-width': 3, 'border-color': '#000' } },
    ],
    []
  )

  // Overview uses cytoscape's concentric layout (seed in the middle, directs
  // arranged by indirect-risk count). Drill-down / "show all" use the preset
  // 3-column positions computed above.
  const layoutOptions = useMemo(() => {
    if (focusedDirectId || showAllPaths) {
      return { name: 'preset', fit: true, padding: 60, animate: false }
    }
    return {
      name: 'concentric',
      fit: true,
      padding: 60,
      animate: false,
      // Higher value = closer to the center. Seed pinned innermost; directs
      // ranked by their risk count.
      concentric: (node) => {
        if (node.hasClass('seed')) return 1e9
        return node.data('indirect_risk_count') || 0
      },
      // Each distinct concentric value gets its own ring; this groups directs
      // with identical risk counts together but spreads the rest out.
      levelWidth: () => 1,
      minNodeSpacing: 24,
      spacingFactor: 1.1,
      avoidOverlap: true,
    }
  }, [focusedDirectId, showAllPaths])

  // Re-run layout + fit whenever the visible elements change.
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    if (elements.length === 0) return
    cy.resize()
    const lay = cy.layout(layoutOptions)
    lay.one('layoutstop', () => {
      try {
        cy.fit(undefined, 40)
      } catch (_) {}
    })
    lay.run()
  }, [elements, layoutOptions])

  const handleCyInit = (cy) => {
    cyRef.current = cy
    cy.removeListener('tap')
    cy.removeListener('dbltap')
    cy.on('tap', 'node', (evt) => {
      const data = evt.target.data()
      setSelectedItem({ kind: 'node', data })
      setSharedWorks(null)
      // Drill-down: clicking a direct enters focus mode for that author.
      if (data.node_type === 'direct') {
        setFocusedDirectId(data.id)
      }
    })
    // Double-click a node to open its OpenAlex profile in a new tab.
    // (Single click is reserved for select / drill-down.)
    cy.on('dbltap', 'node', (evt) => {
      const id = evt.target.data('id')
      if (id && /^https?:\/\//.test(id)) {
        window.open(id, '_blank', 'noopener,noreferrer')
      }
    })
    cy.on('tap', 'edge', async (evt) => {
      const data = evt.target.data()
      setSelectedItem({ kind: 'edge', data })
      setSharedWorks(null)
      try {
        const res = await axios.get(`${API_BASE}/review/shared-works`, {
          params: { source: data.source, target: data.target },
        })
        setSharedWorks(res.data)
      } catch (e) {
        setSharedWorks({ works: [], error: e.message })
      }
    })
    cy.on('tap', (evt) => {
      if (evt.target === cy) {
        setSelectedItem(null)
        setSharedWorks(null)
      }
    })
  }

  const focusedDirect = useMemo(
    () => directList.find((d) => d.id === focusedDirectId) || null,
    [directList, focusedDirectId]
  )

  const overviewModeLabel = showAllPaths
    ? 'All paths'
    : focusedDirectId
    ? `Drill-down: ${focusedDirect?.label ?? '...'}`
    : 'Overview'

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>UM Collaboration Risk</h1>
        <p className="subtitle">Indirect Risk Path Explorer</p>

        <div className="section">
          <label>Seed UM Author</label>
          <input
            type="text"
            placeholder="Search UM authors by name..."
            value={authorQuery}
            onChange={(e) => setAuthorQuery(e.target.value)}
          />
          <div className="author-list">
            {filteredAuthors.length === 0 && (
              <div className="empty">
                {umAuthors.length === 0
                  ? 'No UM authors loaded. Run ingest + precompute scripts first.'
                  : 'No matches.'}
              </div>
            )}
            {filteredAuthors.map((a) => (
              <div
                key={a.author_id}
                className={`author-item${
                  selectedAuthor?.author_id === a.author_id ? ' selected' : ''
                }`}
                onClick={() => setSelectedAuthor(a)}
              >
                <div className="name">
                  <ProfileLink id={a.author_id}>{a.display_name}</ProfileLink>
                </div>
                <div className="meta">{a.institution_name}</div>
              </div>
            ))}
          </div>
          <button
            className="primary"
            onClick={loadRiskPaths}
            disabled={!selectedAuthor || loading}
          >
            {loading ? 'Loading...' : 'Load Risk Paths'}
          </button>
          {selectedAuthor && (
            <div className="selected-meta">
              Selected: <strong>{selectedAuthor.display_name}</strong>
            </div>
          )}
        </div>

        {error && <div className="error">{error}</div>}

        {summary && (
          <div className="section">
            <h3>Risk Summary</h3>
            <div className="summary-grid">
              <div className="card">
                <div className="num">{summary.total_risk_paths}</div>
                <div className="lbl">Risk Paths</div>
              </div>
              <div className="card">
                <div className="num">{summary.direct_collaborators_with_indirect_risk}</div>
                <div className="lbl">Direct w/ Risk</div>
              </div>
              <div className="card">
                <div className="num">{summary.flagged_second_hop_authors}</div>
                <div className="lbl">Flagged 2nd-Hop</div>
              </div>
            </div>
            {Object.keys(summary.country_breakdown || {}).length > 0 && (
              <div className="country-breakdown">
                <div className="lbl-small">By country</div>
                {Object.entries(summary.country_breakdown).map(([c, n]) => (
                  <div key={c} className="row">
                    <span>{c}</span>
                    <span>{n}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {hasRun && directList.length > 0 && (
          <div className="section">
            <h3>Direct Collaborators ({directList.length})</h3>
            <p className="hint">Sorted by indirect risk count. Click to drill in.</p>
            <div className="direct-list">
              {directList.map((d) => (
                <div
                  key={d.id}
                  className={`direct-item${focusedDirectId === d.id ? ' focused' : ''}`}
                  onClick={() => {
                    setShowAllPaths(false)
                    setFocusedDirectId(d.id === focusedDirectId ? null : d.id)
                  }}
                >
                  <div className="row">
                    <div className="name">
                      <ProfileLink id={d.id}>{d.label}</ProfileLink>
                    </div>
                    <div className="badge">{d.indirect_risk_count ?? 0}</div>
                  </div>
                  <div className="meta">
                    {d.country || 'Unknown'} · {d.institution || ''}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="section">
          <h3>Filters</h3>
          <label>Min edge weight: {minWeight}</label>
          <input
            type="range"
            min="1"
            max="10"
            value={minWeight}
            onChange={(e) => setMinWeight(Number(e.target.value))}
          />
          <label>Country filter (flagged hop)</label>
          <input
            type="text"
            placeholder="e.g. CN"
            value={countryFilter}
            onChange={(e) => setCountryFilter(e.target.value)}
          />
          <label className="checkbox">
            <input
              type="checkbox"
              checked={showDirectLabels}
              onChange={(e) => setShowDirectLabels(e.target.checked)}
            />
            Always show direct labels
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={showAllPaths}
              onChange={(e) => {
                setShowAllPaths(e.target.checked)
                if (e.target.checked) setFocusedDirectId(null)
              }}
            />
            Show all paths at once (advanced)
          </label>
        </div>

        <div className="section">
          <h3>Legend</h3>
          <div className="legend">
            <span className="dot seed" /> Seed UM author
          </div>
          <div className="legend">
            <span className="dot direct" /> Direct collaborator (size = risk count)
          </div>
          <div className="legend">
            <span className="dot flagged" /> Flagged second-hop (Needs Review)
          </div>
          <div className="legend">
            <span className="line direct" /> Direct co-authorship
          </div>
          <div className="legend">
            <span className="line indirect" /> Indirect Risk Path
          </div>
        </div>

        {selectedItem && (
          <div className="section details">
            <h3>Selection</h3>
            {selectedItem.kind === 'node' ? (
              <div>
                <div>
                  <strong>
                    <ProfileLink id={selectedItem.data.id}>
                      {selectedItem.data.label}
                    </ProfileLink>
                  </strong>
                </div>
                <div className="meta">Type: {selectedItem.data.node_type}</div>
                <div className="meta">Country: {selectedItem.data.country}</div>
                <div className="meta">Institution: {selectedItem.data.institution}</div>
                {selectedItem.data.indirect_risk_count != null &&
                  selectedItem.data.indirect_risk_count > 0 && (
                    <div className="meta">
                      Indirect risk count: {selectedItem.data.indirect_risk_count}
                    </div>
                  )}
                {selectedItem.data.flag_reason && (
                  <div className="meta">Flag reason: {selectedItem.data.flag_reason}</div>
                )}
                {selectedItem.data.risk_level && (
                  <div className="meta">Risk level: {selectedItem.data.risk_level}</div>
                )}
                <div className="meta">
                  <ProfileLink id={selectedItem.data.id} className="profile-cta">
                    Open OpenAlex profile ↗
                  </ProfileLink>
                </div>
              </div>
            ) : (
              <div>
                <div className="meta">Edge: {selectedItem.data.edge_type}</div>
                <div className="meta">Weight: {selectedItem.data.weight}</div>
                {sharedWorks ? (
                  <div className="works">
                    <div className="lbl-small">
                      Shared works ({sharedWorks.works?.length ?? 0})
                    </div>
                    {(sharedWorks.works || []).map((w) => (
                      <div key={w.work_id} className="work-row">
                        <div className="title">{w.title || w.work_id}</div>
                        {w.publication_year && <div className="year">{w.publication_year}</div>}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="meta">Loading shared works...</div>
                )}
              </div>
            )}
          </div>
        )}
      </aside>

      <main className="graph">
        {hasRun && (
          <div className="graph-toolbar">
            <span className={`mode-badge mode-${showAllPaths ? 'all' : focusedDirectId ? 'focus' : 'overview'}`}>
              {overviewModeLabel}
            </span>
            {focusedDirectId && !showAllPaths && (
              <button className="ghost" onClick={() => setFocusedDirectId(null)}>
                Clear drill-down
              </button>
            )}
            {!focusedDirectId && !showAllPaths && (
              <span className="hint">
                Click any direct collaborator (blue) to drill in. Double-click any node to open its OpenAlex profile.
              </span>
            )}
            {focusedDirectId && (
              <span className="hint">
                Showing {focusedFlagged.length} flagged second-hop
                {focusedFlagged.length === 1 ? '' : 's'} for {focusedDirect?.label}. Double-click any node to open its profile.
              </span>
            )}
          </div>
        )}

        {elements.length === 0 ? (
          <div className="empty-graph">
            {loading
              ? 'Loading...'
              : hasRun
              ? 'No risk paths to display in this view.'
              : 'Select a UM author and load risk paths to view the graph.'}
          </div>
        ) : (
          <CytoscapeComponent
            elements={elements}
            stylesheet={stylesheet}
            layout={layoutOptions}
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
            cy={handleCyInit}
            wheelSensitivity={0.2}
            minZoom={0.1}
            maxZoom={3}
          />
        )}
      </main>
    </div>
  )
}

// OpenAlex author IDs are full URLs (https://openalex.org/A5012345). Use
// stopPropagation so clicking the link doesn't also trigger the row's
// select / drill-down handler.
function ProfileLink({ id, children, className }) {
  if (!id) return <span>{children}</span>
  return (
    <a
      href={id}
      target="_blank"
      rel="noopener noreferrer"
      className={`profile-link${className ? ` ${className}` : ''}`}
      onClick={(e) => e.stopPropagation()}
    >
      {children}
    </a>
  )
}
