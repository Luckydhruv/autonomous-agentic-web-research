import { useState, useEffect, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../api.js'
import StatusBadge from '../components/StatusBadge.jsx'
import ProgressBar from '../components/ProgressBar.jsx'
import { SpinnerIcon, ExternalLinkIcon, ChevronDownIcon } from '../components/Icons.jsx'

const ACTIVE = ['pending', 'planning', 'running']

// ── Small reusable pieces ──────────────────────────────────────────────────

function Section({ title, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="card space-y-3">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center justify-between w-full text-left"
      >
        <h3 className="font-semibold text-white">{title}</h3>
        <ChevronDownIcon className={`w-4 h-4 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && <div>{children}</div>}
    </div>
  )
}

function Stat({ label, value }) {
  return (
    <div className="bg-slate-800/60 rounded-lg px-4 py-3">
      <p className="text-xs text-slate-500 mb-0.5">{label}</p>
      <p className="text-lg font-bold text-white">{value}</p>
    </div>
  )
}

function SourceLink({ url }) {
  return (
    <a
      href={url} target="_blank" rel="noopener noreferrer"
      className="flex items-center gap-1 text-brand-400 hover:text-brand-300 text-sm truncate max-w-full"
    >
      <ExternalLinkIcon className="w-3 h-3 shrink-0" />
      <span className="truncate">{url}</span>
    </a>
  )
}

// ── Research Plan panel ────────────────────────────────────────────────────

function PlanPanel({ plan }) {
  if (!plan) return <p className="text-slate-500 text-sm">Plan not yet generated.</p>
  return (
    <div className="space-y-3 text-sm">
      <div><span className="text-slate-400">Topic: </span><span className="text-white font-medium">{plan.topic}</span></div>
      {plan.constraints?.length > 0 && (
        <div>
          <p className="text-slate-400 mb-1">Constraints</p>
          <ul className="space-y-0.5">{plan.constraints.map((c, i) => <li key={i} className="text-slate-200">• {c}</li>)}</ul>
        </div>
      )}
      <div>
        <p className="text-slate-400 mb-1">Required fields</p>
        <div className="flex flex-wrap gap-1.5">
          {plan.required_fields?.map(f => (
            <span key={f} className="badge bg-slate-800 text-slate-300">{f}</span>
          ))}
        </div>
      </div>
      <div>
        <p className="text-slate-400 mb-1">Search queries generated</p>
        <ul className="space-y-0.5">{plan.search_queries?.map((q, i) => <li key={i} className="text-slate-200 font-mono text-xs">"{q}"</li>)}</ul>
      </div>
      <div>
        <p className="text-slate-400 mb-1">Stopping criteria</p>
        <ul className="space-y-0.5">{plan.stopping_criteria?.map((s, i) => <li key={i} className="text-slate-300 text-xs">• {s}</li>)}</ul>
      </div>
    </div>
  )
}

// ── Progress panel ─────────────────────────────────────────────────────────

function ProgressPanel({ progress }) {
  if (!progress) return null
  return (
    <div className="space-y-4">
      <ProgressBar value={progress.progress_percent} label="Overall progress" />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Pages crawled" value={progress.pages_crawled} />
        <Stat label="Results found" value={progress.records_count} />
        <Stat label="URLs queued" value={progress.candidate_urls_count} />
        <Stat label="Missing fields" value={progress.missing_fields?.length ?? 0} />
      </div>
      {progress.missing_fields?.length > 0 && (
        <div>
          <p className="text-xs text-slate-400 mb-1">Still looking for:</p>
          <div className="flex flex-wrap gap-1.5">
            {progress.missing_fields.map(f => (
              <span key={f} className="badge bg-yellow-900/60 text-yellow-300">{f}</span>
            ))}
          </div>
        </div>
      )}
      {progress.recent_urls?.length > 0 && (
        <div>
          <p className="text-xs text-slate-400 mb-1">Recently crawled</p>
          <div className="space-y-1">
            {progress.recent_urls.map((u, i) => <SourceLink key={i} url={u} />)}
          </div>
        </div>
      )}
      {progress.stopping_reason && (
        <p className="text-xs text-slate-400 italic">Stopped: {progress.stopping_reason}</p>
      )}
    </div>
  )
}

// ── Sources panel ──────────────────────────────────────────────────────────

function SourcesPanel({ sources }) {
  if (!sources) return <p className="text-slate-500 text-sm">No source data yet.</p>
  return (
    <div className="space-y-4 text-sm">
      <div className="grid grid-cols-3 gap-3">
        <Stat label="Visited" value={sources.visited_urls?.length ?? 0} />
        <Stat label="Failed" value={sources.failed_urls?.length ?? 0} />
        <Stat label="Extracted" value={sources.records?.length ?? 0} />
      </div>

      {sources.records?.length > 0 && (
        <div>
          <p className="text-slate-400 mb-2">Extracted records ({sources.records.length})</p>
          <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
            {sources.records.map((rec, i) => (
              <div key={i} className="bg-slate-800/50 rounded-lg p-3 space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-white text-sm font-medium line-clamp-1">{rec.title || 'Untitled'}</p>
                  <span className="badge bg-brand-900/60 text-brand-300 shrink-0">
                    {(rec.relevance_score * 100).toFixed(0)}% relevant
                  </span>
                </div>
                <SourceLink url={rec.url} />
                {(rec.page_summary || rec.relevant_text) && (
                  <p className="text-slate-300 text-xs leading-relaxed mt-2">{rec.page_summary || rec.relevant_text}</p>
                )}
                {rec.main_text && (
                  <details className="text-xs mt-2">
                    <summary className="text-slate-500 cursor-pointer hover:text-slate-300">Extracted page text</summary>
                    <p className="text-slate-400 leading-relaxed mt-2 whitespace-pre-wrap">{rec.main_text}</p>
                  </details>
                )}
                {Object.keys(rec.dynamic_fields ?? {}).length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-1">
                    {Object.entries(rec.dynamic_fields).slice(0, 6).map(([k, v]) => (
                      <span key={k} className="badge bg-slate-700 text-slate-300 text-xs">
                        {k}: {String(v).slice(0, 40)}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {sources.failed_urls?.length > 0 && (
        <details className="text-xs">
          <summary className="text-slate-500 cursor-pointer hover:text-slate-300">
            Failed URLs ({sources.failed_urls.length})
          </summary>
          <div className="mt-2 space-y-1">
            {sources.failed_urls.slice(0, 20).map((u, i) => (
              <p key={i} className="text-slate-600 font-mono truncate">{u}</p>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

// ── Results / Ranked panel ─────────────────────────────────────────────────

function ResultsPanel({ report, sources }) {
  const fallbackResults = (sources?.records ?? []).map((record, i) => ({
    rank: i + 1,
    url: record.url,
    title: record.title || 'Untitled page',
    domain: record.domain || new URL(record.url).hostname,
    final_score: record.relevance_score ?? 0,
    score_explanation: 'Relevant text extracted from this page; full ranking is not available yet.',
    score_breakdown: {},
    dynamic_fields: record.dynamic_fields ?? {},
  }))
  const results = report?.ranked_results?.length ? report.ranked_results : fallbackResults
  if (!results.length) {
    return <p className="text-slate-500 text-sm">No ranked results yet.</p>
  }
  return (
    <div className="space-y-3">
      {results.map((r) => (
        <div key={r.rank} className="bg-slate-800/50 rounded-lg p-4 space-y-2">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-xs font-bold text-slate-500 bg-slate-800 rounded-full w-6 h-6 flex items-center justify-center shrink-0">
                {r.rank}
              </span>
              <p className="text-white text-sm font-medium line-clamp-1">{r.title}</p>
            </div>
            <span className="badge bg-brand-900/60 text-brand-300 shrink-0">
              {(r.final_score * 100).toFixed(0)}
            </span>
          </div>

          <SourceLink url={r.url} />

          {Object.keys(r.dynamic_fields ?? {}).length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(r.dynamic_fields).slice(0, 8).map(([k, v]) => (
                <span key={k} className="badge bg-slate-700 text-slate-300 text-xs">
                  {k}: {String(v).slice(0, 50)}
                </span>
              ))}
            </div>
          )}

          <details className="text-xs">
            <summary className="text-slate-500 cursor-pointer hover:text-slate-300">
              Score breakdown
            </summary>
            <p className="text-slate-500 mt-1 font-mono text-xs leading-relaxed">
              {r.score_explanation}
            </p>
          </details>
        </div>
      ))}
    </div>
  )
}

// ── Report panel ───────────────────────────────────────────────────────────

function ReportPanel({ report }) {
  if (!report?.report) {
    return <p className="text-slate-500 text-sm">Report not yet generated.</p>
  }
  const r = report.report
  return (
    <div className="space-y-5 text-sm">
      {/* Executive summary */}
      <div>
        <h4 className="text-slate-400 text-xs uppercase tracking-wider mb-2">Executive Summary</h4>
        <p className="text-slate-200 leading-relaxed">{r.executive_summary}</p>
      </div>

      {r.top_source_url && (
        <div className="bg-slate-800/40 rounded-lg p-4 space-y-2">
          <h4 className="text-slate-400 text-xs uppercase tracking-wider">Top Retrieved Result</h4>
          <p className="text-white font-medium">{r.top_source_title}</p>
          <SourceLink url={r.top_source_url} />
          {r.relevant_source_text && (
            <details className="text-xs">
              <summary className="text-slate-500 cursor-pointer hover:text-slate-300">Relevant text extracted for BART</summary>
              <p className="text-slate-300 leading-relaxed mt-2 whitespace-pre-wrap">{r.relevant_source_text}</p>
            </details>
          )}
        </div>
      )}

      {r.report_narrative && (
        <div>
          <h4 className="text-slate-400 text-xs uppercase tracking-wider mb-2">Evidence-Based Research Report</h4>
          <p className="text-slate-200 leading-relaxed whitespace-pre-wrap">{r.report_narrative}</p>
          <p className="text-slate-500 text-xs mt-1">{r.report_narrative.trim().split(/\s+/).length} words</p>
        </div>
      )}

      {/* Key findings */}
      {r.key_findings?.length > 0 && (
        <div>
          <h4 className="text-slate-400 text-xs uppercase tracking-wider mb-2">Key Findings</h4>
          <ul className="space-y-1">
            {r.key_findings.map((f, i) => (
              <li key={i} className="text-slate-200 flex gap-2">
                <span className="text-brand-500 shrink-0">→</span>
                <span>{f}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Comparison table */}
      {r.comparison_table?.length > 0 && (
        <div>
          <h4 className="text-slate-400 text-xs uppercase tracking-wider mb-2">Comparison Table</h4>
          <div className="overflow-x-auto rounded-lg border border-slate-800">
            <table className="w-full text-xs">
              <thead className="bg-slate-800">
                <tr>
                  {Object.keys(r.comparison_table[0]).map(k => (
                    <th key={k} className="px-3 py-2 text-left text-slate-400 font-medium capitalize whitespace-nowrap">
                      {k}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {r.comparison_table.map((row, i) => (
                  <tr key={i} className="border-t border-slate-800 hover:bg-slate-800/40">
                    {Object.entries(row).map(([k, v]) => (
                      <td key={k} className="px-3 py-2 text-slate-300 whitespace-nowrap max-w-[200px] truncate">
                        {k === 'url'
                          ? <a href={String(v)} target="_blank" rel="noopener noreferrer" className="text-brand-400 hover:underline truncate block max-w-[160px]">link</a>
                          : String(v ?? '—')
                        }
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Methodology */}
      <div>
        <h4 className="text-slate-400 text-xs uppercase tracking-wider mb-2">Methodology</h4>
        <p className="text-slate-400 leading-relaxed">{r.methodology}</p>
      </div>

      {/* Limitations */}
      {r.limitations?.length > 0 && (
        <div>
          <h4 className="text-slate-400 text-xs uppercase tracking-wider mb-2">Limitations</h4>
          <ul className="space-y-1">
            {r.limitations.map((l, i) => (
              <li key={i} className="text-slate-400">• {l}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Conflicting info */}
      {r.conflicting_information?.length > 0 && (
        <div>
          <h4 className="text-yellow-500 text-xs uppercase tracking-wider mb-2">⚠ Conflicting Information</h4>
          <ul className="space-y-1">
            {r.conflicting_information.map((c, i) => (
              <li key={i} className="text-yellow-300 text-xs">• {c}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Crawl stats */}
      {r.crawl_stats && (
        <div>
          <h4 className="text-slate-400 text-xs uppercase tracking-wider mb-2">Crawl Statistics</h4>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {Object.entries(r.crawl_stats)
              .filter(([, v]) => typeof v !== 'object' || v === null)
              .map(([k, v]) => (
                <div key={k} className="bg-slate-800/60 rounded px-3 py-2">
                  <p className="text-slate-500 text-xs capitalize">{k.replace(/_/g, ' ')}</p>
                  <p className="text-slate-200 font-medium text-sm">{String(v ?? '—')}</p>
                </div>
              ))
            }
          </div>
        </div>
      )}

      {/* Confidence */}
      <div className="flex items-center gap-3">
        <span className="text-slate-400 text-xs">Confidence score:</span>
        <div className="flex-1 max-w-xs">
          <ProgressBar value={(r.confidence_score ?? 0) * 100} />
        </div>
        <span className="text-white font-bold text-sm">{((r.confidence_score ?? 0) * 100).toFixed(0)}%</span>
      </div>
    </div>
  )
}

// ── Main TaskDetail page ───────────────────────────────────────────────────

export default function TaskDetail() {
  const { taskId } = useParams()
  const [task, setTask] = useState(null)
  const [progress, setProgress] = useState(null)
  const [sources, setSources] = useState(null)
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [cancelling, setCancelling] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const [activeTab, setActiveTab] = useState('progress')

  const fetchAll = useCallback(async () => {
    try {
      const [taskData, progressData] = await Promise.all([
        api.getTask(taskId),
        api.getProgress(taskId),
      ])
      setTask(taskData)
      setProgress(progressData)
      setError(null)

      // Fetch sources and report regardless of status
      const [srcData, rptData] = await Promise.all([
        api.getSources(taskId).catch(() => null),
        api.getReport(taskId).catch(() => null),
      ])
      setSources(srcData)
      setReport(rptData)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [taskId])

  useEffect(() => { fetchAll() }, [fetchAll])

  // Poll while active
  useEffect(() => {
    if (!task || !ACTIVE.includes(task.status)) return
    const id = setInterval(fetchAll, 4000)
    return () => clearInterval(id)
  }, [task, fetchAll])

  const handleCancel = async () => {
    if (!window.confirm('Cancel this research task?')) return
    setCancelling(true)
    try {
      await api.cancelTask(taskId)
      await fetchAll()
    } catch (err) {
      alert(err.message)
    } finally {
      setCancelling(false)
    }
  }

  const handleRegenerateReport = async () => {
    setRegenerating(true)
    try {
      await api.regenerateReport(taskId)
      await fetchAll()
      setActiveTab('report')
    } catch (err) {
      setError(err.message)
    } finally {
      setRegenerating(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-slate-400 py-20 justify-center">
        <SpinnerIcon className="w-5 h-5" /> Loading task…
      </div>
    )
  }

  if (error) {
    return (
      <div className="text-red-400 text-sm py-20 text-center">{error}</div>
    )
  }

  const isActive = task && ACTIVE.includes(task.status)
  const TABS = [
    { id: 'progress', label: 'Progress' },
    { id: 'plan',     label: 'Plan' },
    { id: 'sources',  label: `Sources (${sources?.records?.length ?? 0})` },
    { id: 'results',  label: `Results (${report?.ranked_results?.length || sources?.records?.length || 0})` },
    { id: 'report',   label: 'Report' },
  ]

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1 min-w-0">
          <div className="flex items-center gap-3">
            <Link to="/" className="text-slate-500 hover:text-slate-300 text-sm">← Dashboard</Link>
            {task && <StatusBadge status={task.status} />}
          </div>
          <h1 className="text-lg font-bold text-white leading-snug line-clamp-2">
            {task?.objective}
          </h1>
          {task?.stopping_reason && (
            <p className="text-xs text-slate-500 italic">{task.stopping_reason}</p>
          )}
        </div>

        <div className="flex gap-2 shrink-0">
          <button onClick={fetchAll} className="btn-secondary text-xs">Refresh</button>
          {isActive && (
            <button onClick={handleCancel} disabled={cancelling} className="btn-danger text-xs">
              {cancelling ? 'Cancelling…' : 'Cancel'}
            </button>
          )}
          {!isActive && task?.status === 'completed' && (
            <button onClick={handleRegenerateReport} disabled={regenerating} className="btn-secondary text-xs">
              {regenerating ? 'Building BART report…' : 'Rebuild report from top source'}
            </button>
          )}
        </div>
      </div>

      {/* Quick stats */}
      {progress && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Stat label="Pages crawled" value={progress.pages_crawled} />
          <Stat label="Results" value={progress.records_count} />
          <Stat label="Queued URLs" value={progress.candidate_urls_count} />
          <Stat label="Progress" value={`${(progress.progress_percent ?? 0).toFixed(1)}%`} />
        </div>
      )}

      {isActive && progress && (
        <ProgressBar value={progress.progress_percent} label="Research progress" />
      )}

      {/* Tabs */}
      <div className="border-b border-slate-800 flex gap-1 overflow-x-auto">
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
              activeTab === tab.id
                ? 'border-brand-500 text-white'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div>
        {activeTab === 'progress' && (
          <div className="card">
            <h3 className="font-semibold text-white mb-4">Live Progress</h3>
            <ProgressPanel progress={progress} />
          </div>
        )}
        {activeTab === 'plan' && (
          <div className="card">
            <h3 className="font-semibold text-white mb-4">Research Plan</h3>
            <PlanPanel plan={task?.plan} />
          </div>
        )}
        {activeTab === 'sources' && (
          <div className="card">
            <h3 className="font-semibold text-white mb-4">Discovered Sources & Evidence</h3>
            <SourcesPanel sources={sources} />
          </div>
        )}
        {activeTab === 'results' && (
          <div className="card">
            <h3 className="font-semibold text-white mb-4">Ranked Results</h3>
          <ResultsPanel report={report} sources={sources} />
          </div>
        )}
        {activeTab === 'report' && (
          <div className="card">
            <h3 className="font-semibold text-white mb-4">Research Report</h3>
            <ReportPanel report={report} />
          </div>
        )}
      </div>
    </div>
  )
}
