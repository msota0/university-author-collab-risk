import React, { useEffect, useMemo, useRef, useState } from 'react'
import axios from 'axios'
import CytoscapeComponent from 'react-cytoscapejs'

const API_BASE = 'http://localhost:8000'

const COLOR_SEED = '#c0392b'
const COLOR_DIRECT = '#2980b9'
const COLOR_FLAGGED = '#e67e22'

// Accept either a bare OpenAlex ID ("A5012345") or a full URL
// ("https://openalex.org/A5012345"). Backend expects the URL form.
function normalizeOpenAlexId(input) {
  if (!input) return ''
  const trimmed = input.trim()
  if (!trimmed) return ''
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  if (/^A\d+$/i.test(trimmed)) return `https://openalex.org/${trimmed.toUpperCase()}`
  return trimmed
}

export default function App() {
  const [umAuthors, setUmAuthors] = useState([])
  const [seedIdInput, setSeedIdInput] = useState('')
  const [graphData, setGraphData] = useState({ nodes: [], edges: [], seed: null })
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(false)
  const [hasRun, setHasRun] = useState(false)
  const [error, setError] = useState(null)
  const [selectedItem, setSelectedItem] = useState(null)
  const [sharedWorks, setSharedWorks] = useState(null)
  const [panelOpen, setPanelOpen] = useState(true)

  // Set of direct collaborator IDs the user has expanded. Each expanded direct
  // grows its full next-depth petal off itself; non-expanded directs stay
  // visible but show no further depth.
  const [expandedDirectIds, setExpandedDirectIds] = useState(() => new Set())
  // Per-direct neighbourhood data fetched from /review/expand. Keyed by direct ID.
  const [expansionData, setExpansionData] = useState(() => new Map())
  const [expansionLoading, setExpansionLoading] = useState(() => new Set())
  // Convenience override: expand every direct at once.
  const [showAllPaths, setShowAllPaths] = useState(false)
  // Cap on neighbours fetched per expansion.
  const [neighborLimit, setNeighborLimit] = useState(75)
  // When on, the depth-1 ring shows the seed's *full* neighbourhood (via
  // /review/expand) instead of the risk-only slice. Risky directs still get
  // their badge + orange border so the review signal isn't lost.
  const [showAllDirects, setShowAllDirects] = useState(false)
  const [allDirectsData, setAllDirectsData] = useState(null)
  const [allDirectsLoading, setAllDirectsLoading] = useState(false)
  // Resolved seed ID for the most recent Build Network click. We track this
  // separately from graphData.seed because /review/risk-paths can return
  // an empty seed when an author has no risky collaborators.
  const [seedId, setSeedId] = useState(null)

  // Filters
  const [minWeight, setMinWeight] = useState(1)
  const [countryFilter, setCountryFilter] = useState('')
  const [showDirectLabels, setShowDirectLabels] = useState(false)

  const cyRef = useRef(null)
  // Tracks which direct was JUST expanded so the layout effect can pan/zoom
  // the camera onto its newly-grown petal.
  const lastExpandedRef = useRef(null)

  useEffect(() => {
    axios
      .get(`${API_BASE}/authors/um`)
      .then((r) => setUmAuthors(r.data || []))
      .catch(() => {})
  }, [])

  const loadRiskPaths = async () => {
    const id = normalizeOpenAlexId(seedIdInput)
    if (!id) {
      setError('Enter an OpenAlex author ID (e.g. A5012345 or the full URL).')
      return
    }
    setLoading(true)
    setError(null)
    setSelectedItem(null)
    setSharedWorks(null)
    setExpandedDirectIds(new Set())
    setExpansionData(new Map())
    setExpansionLoading(new Set())
    setAllDirectsData(null)
    setSeedId(id)
    lastExpandedRef.current = null
    try {
      const [paths, summ] = await Promise.all([
        axios.get(`${API_BASE}/review/risk-paths`, {
          params: { seed_author_id: id },
        }),
        axios.get(`${API_BASE}/review/summary`, {
          params: { seed_author_id: id },
        }),
      ])
      setGraphData(paths.data)
      setSummary(summ.data)
      setHasRun(true)
      // If the precomputed graph has no risk paths for this seed (either the
      // author has zero flagged collaborators OR the ID isn't in the graph at
      // all), auto-flip "Show all directs". The /review/expand fallback will
      // then either pull from precomputed or live OpenAlex, so the user
      // always sees something useful instead of an empty card.
      if (!paths.data?.seed) {
        setShowAllDirects(true)
      }
    } catch (e) {
      setError(`Failed to load risk paths: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  // Fetch the seed's full neighbourhood the first time the user flips on
  // "Show all directs". Cached for the rest of the session unless they Build
  // Network again with a different seed.
  useEffect(() => {
    if (!showAllDirects || !seedId || allDirectsData || allDirectsLoading) return
    setAllDirectsLoading(true)
    axios
      .get(`${API_BASE}/review/expand`, {
        params: { author_id: seedId, limit: 500 },
      })
      .then((res) => setAllDirectsData(res.data))
      .catch((e) =>
        setError(`Couldn't load all directs: ${e.message}`)
      )
      .finally(() => setAllDirectsLoading(false))
  }, [showAllDirects, seedId, allDirectsData, allDirectsLoading])

  const fetchExpansion = async (id) => {
    setExpansionLoading((prev) => {
      const next = new Set(prev)
      next.add(id)
      return next
    })
    try {
      const res = await axios.get(`${API_BASE}/review/expand`, {
        params: { author_id: id, limit: neighborLimit },
      })
      setExpansionData((prev) => {
        const next = new Map(prev)
        next.set(id, res.data)
        return next
      })
    } catch (e) {
      // Surface a soft warning but keep the direct expanded so the user knows
      // the click registered.
      setError(`Couldn't expand ${id}: ${e.message}`)
    } finally {
      setExpansionLoading((prev) => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    }
  }

  const toggleExpand = (id) => {
    setExpandedDirectIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
        lastExpandedRef.current = null
      } else {
        next.add(id)
        lastExpandedRef.current = id
        if (!expansionData.has(id) && !expansionLoading.has(id)) {
          // Fire-and-forget — the elements memo re-computes when state lands.
          fetchExpansion(id)
        }
      }
      return next
    })
    setShowAllPaths(false)
  }

  const collapseAll = () => {
    setExpandedDirectIds(new Set())
    lastExpandedRef.current = null
  }

  // Auto-fetch expansions when "Show all paths" is toggled on.
  useEffect(() => {
    if (!showAllPaths) return
    const directs = (graphData.nodes || []).filter((n) => n.node_type === 'direct')
    for (const d of directs) {
      if (!expansionData.has(d.id) && !expansionLoading.has(d.id)) {
        fetchExpansion(d.id)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showAllPaths, graphData])

  // Sorted list of direct collaborators (by indirect-risk count, desc).
  // When "Show all directs" is on, the list comes from the seed's full
  // neighbourhood; risk counts from the risk-paths slice still annotate
  // matching rows so the review signal isn't lost.
  const directList = useMemo(() => {
    const riskByDirect = new Map(
      (graphData.nodes || [])
        .filter((n) => n.node_type === 'direct')
        .map((n) => [n.id, n.indirect_risk_count ?? 0])
    )
    if (showAllDirects && allDirectsData) {
      return (allDirectsData.nodes || [])
        .map((n) => ({
          ...n,
          node_type: 'direct',
          indirect_risk_count: riskByDirect.get(n.id) ?? 0,
        }))
        .sort(
          (a, b) =>
            (b.indirect_risk_count ?? 0) - (a.indirect_risk_count ?? 0) ||
            (a.label || '').localeCompare(b.label || '')
        )
    }
    const directs = (graphData.nodes || []).filter((n) => n.node_type === 'direct')
    return directs.slice().sort(
      (a, b) => (b.indirect_risk_count ?? 0) - (a.indirect_risk_count ?? 0)
    )
  }, [graphData, showAllDirects, allDirectsData])

  // Set of country codes flagged for review (comes back on the risk-paths
  // payload). Used to mark direct collaborators who are themselves in a
  // review country, even if their 2nd-hops aren't.
  const reviewCountrySet = useMemo(
    () => new Set((graphData.review_countries || []).map((c) => (c || '').toUpperCase())),
    [graphData]
  )

  // Build cytoscape elements + radial petal layout. Seed in the centre, all
  // directs on a ring around it, and each *expanded* direct grows its full
  // next-depth petal (fetched lazily from /review/expand). Flagged 2nd-hops
  // stay orange, ordinary co-authors are neutral grey.
  const elements = useMemo(() => {
    const rawNodes = graphData.nodes || []
    const rawEdges = (graphData.edges || []).filter((e) => (e.weight ?? 1) >= minWeight)
    // Seed source: prefer the risk-paths payload, but fall back to whatever
    // /review/expand returned. That way an author with no risky paths still
    // anchors the ring once "Show all directs" is on.
    const seedNode =
      rawNodes.find((n) => n.node_type === 'seed') ||
      (allDirectsData && allDirectsData.seed) ||
      null
    const cfUpper = (countryFilter || '').toUpperCase()

    // Source-swap: in "show all directs" mode, the depth-1 ring comes from
    // /review/expand on the seed instead of the risk-only slice. Risk counts
    // from /review/risk-paths still annotate the matching directs.
    const riskByDirect = new Map(
      rawNodes
        .filter((n) => n.node_type === 'direct')
        .map((n) => [n.id, n.indirect_risk_count ?? 0])
    )
    let allDirects, directBaseEdges
    if (showAllDirects && allDirectsData) {
      allDirects = (allDirectsData.nodes || []).map((n) => ({
        ...n,
        node_type: 'direct',
        indirect_risk_count: riskByDirect.get(n.id) ?? 0,
      }))
      directBaseEdges = (allDirectsData.edges || []).map((e) => ({
        ...e,
        edge_type: 'direct',
      }))
    } else {
      allDirects = rawNodes.filter((n) => n.node_type === 'direct')
      directBaseEdges = rawEdges.filter((e) => e.edge_type === 'direct')
    }
    const directIdSet = new Set(allDirects.map((d) => d.id))

    // Effective expansion set: explicit user picks, or every direct when
    // "show all paths" is on.
    const effectivelyExpanded = showAllPaths
      ? new Set(allDirects.map((n) => n.id))
      : expandedDirectIds

    // Always show all directs in the depth-1 ring.
    const visibleDirects = allDirects
    const directEdges = directBaseEdges.filter((e) => (e.weight ?? 1) >= minWeight)

    // Aggregate the next-depth petal nodes/edges from per-direct expansions.
    // Country filter applies *only* to flagged 2nd-hops — ordinary neighbours
    // are kept regardless so the petal still shows the broader network.
    const petalNodes = new Map()
    const petalEdges = []
    const petalsByDirect = new Map() // directId -> array of neighbour ids

    for (const directId of effectivelyExpanded) {
      const exp = expansionData.get(directId)
      if (!exp) continue
      const neighborIdsForDirect = []
      for (const n of exp.nodes || []) {
        if (n.id === seedNode?.id) continue
        if (directIdSet.has(n.id)) continue // already a direct, not a petal node
        if (
          n.node_type === 'flagged_second_hop' &&
          cfUpper &&
          (n.country || '').toUpperCase() !== cfUpper
        ) {
          continue
        }
        if (!petalNodes.has(n.id)) petalNodes.set(n.id, n)
        neighborIdsForDirect.push(n.id)
      }
      petalsByDirect.set(directId, neighborIdsForDirect)
      for (const e of exp.edges || []) {
        if ((e.weight ?? 1) < minWeight) continue
        if (!petalNodes.has(e.target)) continue // dropped above (country filter etc.)
        petalEdges.push(e)
      }
    }
    const visiblePetal = [...petalNodes.values()]

    // ── Layout positions ──
    const sortedDirects = [...visibleDirects].sort(
      (a, b) => (b.indirect_risk_count ?? 0) - (a.indirect_risk_count ?? 0)
    )
    const N = sortedDirects.length
    // Adaptive ring radius so dense networks don't crowd.
    const R1 = Math.max(280, Math.min(1100, 120 + N * 14))

    const positions = new Map()
    if (seedNode) positions.set(seedNode.id, { x: 0, y: 0 })

    const directAngles = new Map()
    sortedDirects.forEach((n, i) => {
      const theta = (2 * Math.PI * i) / Math.max(N, 1) - Math.PI / 2
      directAngles.set(n.id, theta)
      positions.set(n.id, { x: R1 * Math.cos(theta), y: R1 * Math.sin(theta) })
    })

    // Place each expanded direct's petal nodes in a small arc beyond it,
    // along that direct's radial. If two directs share a neighbour, first
    // expansion wins (keeps the node close to whichever petal placed it).
    for (const [directId, neighborIds] of petalsByDirect) {
      const baseAngle = directAngles.get(directId) ?? 0
      const baseX = R1 * Math.cos(baseAngle)
      const baseY = R1 * Math.sin(baseAngle)
      const dedup = [...new Set(neighborIds)].filter((id) => petalNodes.has(id))
      const M = dedup.length
      const arcSpread = Math.min(Math.PI * 0.6, 0.3 + M * 0.03)
      const r2 = Math.max(300, 120 + M * 8)
      dedup.forEach((fid, j) => {
        if (positions.has(fid)) return
        const t = M === 1 ? 0 : j / (M - 1) - 0.5
        const theta = baseAngle + t * arcSpread
        positions.set(fid, {
          x: baseX + r2 * Math.cos(theta),
          y: baseY + r2 * Math.sin(theta),
        })
      })
    }

    const visibleNodes = []
    if (seedNode) visibleNodes.push(seedNode)
    visibleNodes.push(...visibleDirects)
    visibleNodes.push(...visiblePetal)

    const cyNodes = visibleNodes.map((n) => {
      const isExpanded =
        n.node_type === 'direct' && effectivelyExpanded.has(n.id)
      const showLabel =
        n.node_type === 'seed' ||
        n.node_type === 'flagged_second_hop' ||
        n.node_type === 'neighbor' ||
        (n.node_type === 'direct' && (showDirectLabels || isExpanded))

      const classes = [n.node_type]
      if (isExpanded) classes.push('expanded')
      if (
        n.node_type === 'direct' &&
        reviewCountrySet.has((n.country || '').toUpperCase())
      ) {
        classes.push('review_country')
      }

      const riskCount = n.indirect_risk_count ?? 0
      const riskScale = 18 + Math.min(50, Math.sqrt(riskCount) * 6)

      return {
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
          scopus: n.scopus || null,
          dimensions: n.dimensions || null,
          affiliation_mismatch: n.affiliation_mismatch || null,
          funding_risk: !!n.funding_risk,
        },
        position: positions.get(n.id) || { x: 0, y: 0 },
        classes: classes.join(' '),
      }
    })

    const cyEdges = [...directEdges, ...petalEdges].map((e) => ({
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
  }, [
    graphData,
    minWeight,
    countryFilter,
    showDirectLabels,
    expandedDirectIds,
    expansionData,
    showAllPaths,
    showAllDirects,
    allDirectsData,
    reviewCountrySet,
  ])

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
          'transition-property': 'background-color, border-color, border-width, width, height',
          'transition-duration': '180ms',
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
          width: 'data(riskScale)',
          height: 'data(riskScale)',
          'text-valign': 'center',
          'text-halign': 'right',
          'font-size': 11,
        },
      },
      {
        selector: 'node.direct.expanded',
        style: {
          'border-width': 4,
          'border-color': '#0b3d61',
          'font-weight': 'bold',
        },
      },
      {
        selector: 'node.direct.review_country',
        style: {
          'border-color': '#e67e22',
          'border-width': 2.5,
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
        selector: 'node.neighbor',
        style: {
          'background-color': '#9ca3af',
          width: 14,
          height: 14,
          'font-size': 10,
          color: '#4b5563',
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
      {
        selector: 'edge.neighbor',
        style: {
          'line-color': '#d1d5db',
          'target-arrow-color': '#d1d5db',
          width: 1,
        },
      },
      { selector: ':selected', style: { 'border-width': 3, 'border-color': '#000' } },
    ],
    []
  )

  // Always preset — we compute every node's position above.
  const layoutOptions = useMemo(
    () => ({ name: 'preset', fit: false, padding: 60, animate: false }),
    []
  )

  // Re-run layout + smart camera move whenever visible elements change.
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    if (elements.length === 0) return
    cy.resize()
    const lay = cy.layout(layoutOptions)
    lay.one('layoutstop', () => {
      const focusId = lastExpandedRef.current
      if (focusId && cy.getElementById(focusId).length > 0) {
        // Animate the camera to seed + this direct + every node in its petal.
        const eles = cy.collection()
        eles.merge(cy.getElementById(focusId))
        eles.merge(cy.nodes('.seed'))
        const exp = expansionData.get(focusId)
        const petalTargets = exp ? (exp.nodes || []).map((n) => n.id) : []
        petalTargets.forEach((fid) => {
          const n = cy.getElementById(fid)
          if (n.length) eles.merge(n)
        })
        try {
          cy.animate({
            fit: { eles, padding: 90 },
            duration: 650,
            easing: 'ease-in-out-cubic',
          })
        } catch (_) {
          cy.fit(eles, 90)
        }
      } else {
        try {
          cy.animate({
            fit: { eles: cy.elements(), padding: 50 },
            duration: 450,
            easing: 'ease-in-out-cubic',
          })
        } catch (_) {
          cy.fit(undefined, 40)
        }
      }
    })
    lay.run()
  }, [elements, layoutOptions, graphData])

  // Re-fit when the side panel toggles so the network reclaims/yields space.
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    const t = setTimeout(() => {
      try {
        cy.resize()
        cy.fit(undefined, 40)
      } catch (_) {}
    }, 220)
    return () => clearTimeout(t)
  }, [panelOpen])

  const handleCyInit = (cy) => {
    cyRef.current = cy
    cy.removeListener('tap')
    cy.removeListener('dbltap')
    cy.on('tap', 'node', (evt) => {
      const data = evt.target.data()
      setSelectedItem({ kind: 'node', data })
      setSharedWorks(null)
      // Clicking a direct collaborator expands (or collapses) its next depth.
      if (data.node_type === 'direct') {
        toggleExpand(data.id)
      }
    })
    // Double-click any node to open its OpenAlex profile.
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

  const expandedCount = showAllPaths
    ? directList.length
    : expandedDirectIds.size
  // Visible petal counts, broken out into flagged vs neutral so the toolbar
  // can show both. Driven by what's actually been fetched into expansionData.
  const petalCounts = useMemo(() => {
    if (!hasRun) return { flagged: 0, other: 0 }
    const cf = (countryFilter || '').toUpperCase()
    const expSet = showAllPaths
      ? new Set(directList.map((d) => d.id))
      : expandedDirectIds
    const flagged = new Set()
    const other = new Set()
    for (const id of expSet) {
      const exp = expansionData.get(id)
      if (!exp) continue
      for (const n of exp.nodes || []) {
        if (n.node_type === 'flagged_second_hop') {
          if (!cf || (n.country || '').toUpperCase() === cf) flagged.add(n.id)
        } else if (n.node_type === 'neighbor') {
          other.add(n.id)
        }
      }
    }
    return { flagged: flagged.size, other: other.size }
  }, [
    hasRun,
    expandedDirectIds,
    showAllPaths,
    countryFilter,
    directList,
    expansionData,
  ])

  const overviewModeLabel = showAllPaths
    ? 'All paths'
    : expandedCount === 0
    ? 'Depth 1 — overview'
    : `Expanded × ${expandedCount}`

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-row">
          <div className="brand">
            <h1>UM Collaboration Risk</h1>
            <span className="subtitle">Indirect Risk Path Explorer</span>
          </div>

          <div className="group seed-input">
            <label htmlFor="seed-id">Seed OpenAlex ID</label>
            <div className="input-row">
              <input
                id="seed-id"
                type="text"
                placeholder="A5012345 or https://openalex.org/A5012345"
                value={seedIdInput}
                onChange={(e) => setSeedIdInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') loadRiskPaths()
                }}
                list="um-authors"
              />
              <datalist id="um-authors">
                {umAuthors.map((a) => (
                  <option key={a.author_id} value={a.author_id}>
                    {a.display_name}
                    {a.institution_name ? ` — ${a.institution_name}` : ''}
                  </option>
                ))}
              </datalist>
              <button className="primary" onClick={loadRiskPaths} disabled={loading}>
                {loading ? 'Loading…' : 'Build Network'}
              </button>
            </div>
          </div>

          <div className="group filters">
            <div className="filter">
              <label>Min weight: <strong>{minWeight}</strong></label>
              <input
                type="range"
                min="1"
                max="10"
                value={minWeight}
                onChange={(e) => setMinWeight(Number(e.target.value))}
              />
            </div>
            <div className="filter">
              <label>Country</label>
              <input
                type="text"
                placeholder="e.g. CN"
                value={countryFilter}
                onChange={(e) => setCountryFilter(e.target.value)}
              />
            </div>
            <div className="filter">
              <label>Neighbours / expand: <strong>{neighborLimit}</strong></label>
              <input
                type="range"
                min="10"
                max="200"
                step="5"
                value={neighborLimit}
                onChange={(e) => setNeighborLimit(Number(e.target.value))}
              />
            </div>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={showDirectLabels}
                onChange={(e) => setShowDirectLabels(e.target.checked)}
              />
              All direct labels
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={showAllDirects}
                onChange={(e) => setShowAllDirects(e.target.checked)}
              />
              Show all directs
              {allDirectsLoading && <span className="hint"> (loading…)</span>}
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={showAllPaths}
                onChange={(e) => {
                  setShowAllPaths(e.target.checked)
                  lastExpandedRef.current = null
                }}
              />
              Show all paths
            </label>
          </div>

          <div className="group legend">
            <span className="legend-item"><span className="dot seed" /> Seed</span>
            <span className="legend-item"><span className="dot direct" /> Direct (size = risk)</span>
            <span className="legend-item"><span className="dot flagged" /> Flagged 2nd-hop</span>
            <span className="legend-item"><span className="dot neighbor" /> Other 2nd-hop</span>
            <span className="legend-item"><span className="line direct" /> Co-authorship</span>
            <span className="legend-item"><span className="line indirect" /> Risk path</span>
          </div>
        </div>
        {error && <div className="error">{error}</div>}
      </header>

      <div className="content">
        <main className="graph">
          {hasRun && (
            <div className="graph-toolbar">
              <span
                className={`mode-badge mode-${
                  showAllPaths ? 'all' : expandedCount > 0 ? 'focus' : 'overview'
                }`}
              >
                {overviewModeLabel}
              </span>
              {allDirectsData?.live && (
                <span className="mode-badge mode-live" title="Network fetched live from OpenAlex (not in precomputed graph)">
                  Live
                </span>
              )}
              {expandedCount > 0 && !showAllPaths && (
                <button className="ghost" onClick={collapseAll}>
                  Collapse all
                </button>
              )}
              {expandedCount === 0 && !showAllPaths && (
                <span className="hint">
                  Click any direct collaborator (blue) to grow its depth-2 petal. Double-click a node to open its OpenAlex profile.
                </span>
              )}
              {expandedCount > 0 && (
                <span className="hint">
                  Showing {petalCounts.flagged} flagged + {petalCounts.other} other 2nd-hop
                  {petalCounts.flagged + petalCounts.other === 1 ? '' : 's'} across{' '}
                  {expandedCount} expanded direct{expandedCount === 1 ? '' : 's'}
                  {expansionLoading.size > 0 ? ` · loading ${expansionLoading.size}…` : ''}.
                </span>
              )}
              <button
                className="ghost panel-toggle"
                onClick={() => setPanelOpen((v) => !v)}
                title={panelOpen ? 'Hide details panel' : 'Show details panel'}
              >
                {panelOpen ? 'Hide panel ›' : '‹ Show panel'}
              </button>
            </div>
          )}

          {elements.length === 0 ? (
            <div className="empty-graph">
              {loading ? (
                'Loading risk paths…'
              ) : allDirectsLoading ? (
                <>
                  <div>Fetching network from OpenAlex…</div>
                  <div className="hint">
                    First lookup for this author. Subsequent expansions will be cached.
                  </div>
                </>
              ) : !hasRun ? (
                'Enter an OpenAlex author ID above and click Build Network.'
              ) : showAllDirects && allDirectsData && !allDirectsData.seed ? (
                <>
                  <div>Author not found.</div>
                  <div className="hint">
                    {seedId} couldn't be resolved against OpenAlex. Double-check the ID.
                  </div>
                </>
              ) : (
                'No collaborators to display in this view.'
              )}
            </div>
          ) : (
            <CytoscapeComponent
              elements={elements}
              stylesheet={stylesheet}
              layout={layoutOptions}
              style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
              cy={handleCyInit}
              wheelSensitivity={0.2}
              minZoom={0.05}
              maxZoom={3}
            />
          )}
        </main>

        {panelOpen && (
          <aside className="side-panel">
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
                <p className="hint">
                  Sorted by indirect risk count. Click to expand that collaborator's
                  next depth.
                </p>
                <div className="direct-list">
                  {directList.map((d) => {
                    const isExp =
                      showAllPaths || expandedDirectIds.has(d.id)
                    return (
                      <div
                        key={d.id}
                        className={`direct-item${isExp ? ' focused' : ''}`}
                        onClick={() => toggleExpand(d.id)}
                      >
                        <div className="row">
                          <div className="name">
                            <ProfileLink id={d.id}>{d.label}</ProfileLink>
                          </div>
                          <div className="badge">{d.indirect_risk_count ?? 0}</div>
                        </div>
                        <div className="meta">
                          {d.country || 'Unknown'} · {d.institution || ''}
                          {isExp && <span className="exp-tag"> · expanded</span>}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

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
                    {selectedItem.data.affiliation_mismatch && (
                      <div className="enrichment warn">
                        <div className="lbl-small">Affiliation mismatch (Scopus)</div>
                        <div className="meta">
                          Graph: {selectedItem.data.affiliation_mismatch.graph_country} ·
                          {' '}Scopus: {selectedItem.data.affiliation_mismatch.scopus_country}
                          {' — '}{selectedItem.data.affiliation_mismatch.scopus_affiliation}
                        </div>
                      </div>
                    )}
                    {selectedItem.data.funding_risk && (
                      <div className="enrichment warn">
                        <div className="lbl-small">Funding risk (Dimensions)</div>
                        <div className="meta">
                          At least one grant funded by a review-list country.
                        </div>
                      </div>
                    )}
                    {selectedItem.data.scopus && (
                      <div className="enrichment">
                        <div className="lbl-small">Scopus</div>
                        {selectedItem.data.scopus.current_affiliation && (
                          <div className="meta">
                            Current: {selectedItem.data.scopus.current_affiliation}
                            {selectedItem.data.scopus.current_affiliation_country
                              ? ` (${selectedItem.data.scopus.current_affiliation_country})`
                              : ''}
                          </div>
                        )}
                        {selectedItem.data.scopus.h_index && (
                          <div className="meta">
                            h-index: {selectedItem.data.scopus.h_index}
                            {selectedItem.data.scopus.document_count
                              ? ` · ${selectedItem.data.scopus.document_count} docs`
                              : ''}
                          </div>
                        )}
                        {selectedItem.data.scopus.affiliation_history && (
                          <div className="meta history">
                            History: {selectedItem.data.scopus.affiliation_history}
                          </div>
                        )}
                      </div>
                    )}
                    {selectedItem.data.dimensions && (
                      <div className="enrichment">
                        <div className="lbl-small">Dimensions</div>
                        <div className="meta">
                          {selectedItem.data.dimensions.grant_count ?? 0} grants ·
                          {' '}{selectedItem.data.dimensions.patent_count ?? 0} patents
                        </div>
                        {selectedItem.data.dimensions.funder_countries && (
                          <div className="meta">
                            Funders: {selectedItem.data.dimensions.funder_countries}
                          </div>
                        )}
                        {selectedItem.data.dimensions.grants && (
                          <div className="meta history">
                            {selectedItem.data.dimensions.grants}
                          </div>
                        )}
                      </div>
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
                      <div className="meta">Loading shared works…</div>
                    )}
                  </div>
                )}
              </div>
            )}

            {!hasRun && !summary && !selectedItem && (
              <div className="section">
                <h3>Getting started</h3>
                <p className="hint">
                  Paste an OpenAlex author ID at the top and click <strong>Build Network</strong>.
                  The seed appears in the centre and direct collaborators (sized by indirect risk
                  count) ring it. Click any direct to expand its next depth — its flagged
                  second-hops grow off it as a petal and the camera glides to that region.
                </p>
              </div>
            )}
          </aside>
        )}
      </div>
    </div>
  )
}

// OpenAlex author IDs are full URLs (https://openalex.org/A5012345). Use
// stopPropagation so clicking the link doesn't also trigger the row's
// select / expand handler.
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
