/**
 * api.js – All backend API calls in one place.
 */

const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  getConfig: () => request('/config'),
  getHealth: () => fetch('/health').then(r => r.json()),

  createTask: (objective, maxPages, crawlDepth) =>
    request('/tasks', {
      method: 'POST',
      body: JSON.stringify({ objective, max_pages: maxPages, crawl_depth: crawlDepth }),
    }),

  listTasks: () => request('/tasks'),
  getTask:   (id) => request(`/tasks/${id}`),
  cancelTask:(id) => request(`/tasks/${id}/cancel`, { method: 'POST' }),
  getProgress:(id) => request(`/tasks/${id}/progress`),
  getSources: (id) => request(`/tasks/${id}/sources`),
  getReport:  (id) => request(`/tasks/${id}/report`),
  regenerateReport: (id) => request(`/tasks/${id}/report/regenerate`, { method: 'POST' }),
}
