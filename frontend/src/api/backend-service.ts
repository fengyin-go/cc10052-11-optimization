import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// --- 显著性检验检索 / 查询签名 / 取证单 ---

export type SignificanceConditions = {
  experiment: string
  metric: string
  windows: string
  date_from: string
  date_to: string
  status: string
  sort: string
}

export type SignificanceItem = EntryRow & {
  stable_key: string
  '差异方向': string
  '有效依据': boolean
  '检验批次': string
  '检验窗口': string
  '窗口起点': string
  '窗口终点': string
  '轮次': string
  '修订状态': string
}

export type SignificanceSearch = {
  ok: boolean
  message: string
  query_signature: string
  conditions: SignificanceConditions
  sort_label: string
  items: SignificanceItem[]
  total: number
  page: number
  size: number
}

export type TicketBasis = {
  id: number
  '检验批次': string
  '检验窗口': string
  '窗口起点': string
  '窗口终点': string
  '轮次': string
  '修订状态': string
  'P值': string
  '置信区间': string
  '差异方向': string
  '状态': string
  '依据来源'?: string
  '现行批次'?: string
}

export type EvidenceTicket = {
  id: number
  '取证单号': string
  '状态': string
  '查询签名': string
  '实验编码': string
  '指标编码': string
  '窗口集合': string
  '排序说明': string
  '筛选条件': string
  '依据': TicketBasis[]
  '采用依据': string
  '实际引用': TicketBasis[]
  '缺失依据': string[]
  '切片批次': string
  '核对提示': string
  '重发来源'?: string
  '区间变化'?: {
    '检验窗口': string
    '登记批次': string
    '登记区间': string
    '本次批次': string
    '本次区间': string
    '登记批次状态': string
    '区间变化': boolean
  }[]
}

function queryString(params: Record<string, string | number>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== '' && value !== undefined && value !== null) search.set(key, String(value))
  }
  return search.toString()
}

export async function searchSignificance(
  conditions: SignificanceConditions,
  page = 1,
  size = 20,
): Promise<SignificanceSearch> {
  const response = await request(`/api/significance_test/search?${queryString({ ...conditions, page, size })}`)
  if (!response.ok) throw new Error('检验检索失败')
  return await response.json()
}

export async function listSignificanceQueries(): Promise<Record<string, unknown>[]> {
  const response = await request('/api/significance_test/queries')
  if (!response.ok) throw new Error('查询签名读取失败')
  return await response.json()
}

export async function rerunSignificanceQuery(signature: string): Promise<SignificanceSearch & {
  added: SignificanceItem[]
  missing: SignificanceItem[]
  snapshot: number[]
}> {
  const response = await request(`/api/significance_test/queries/${signature}/rerun`)
  if (!response.ok) throw new Error('按原条件重查失败')
  return await response.json()
}

export async function listEvidenceTickets(experiment = '', metric = ''): Promise<EvidenceTicket[]> {
  const response = await request(`/api/significance_test/evidence-tickets?${queryString({ experiment, metric })}`)
  if (!response.ok) throw new Error('取证单读取失败')
  return await response.json()
}

export async function createEvidenceTicket(values: Record<string, unknown>): Promise<ActionResult> {
  const response = await request('/api/significance_test/evidence-tickets', {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function claimEvidenceTicket(
  id: number,
  values: Record<string, unknown>,
): Promise<ActionResult> {
  const response = await request(`/api/significance_test/evidence-tickets/${id}/claim`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function reissueEvidenceTicket(
  id: number,
  values: Record<string, unknown> = {},
): Promise<ActionResult> {
  const response = await request(`/api/significance_test/evidence-tickets/${id}/reissue`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function reviseSignificance(
  id: number,
  values: Record<string, unknown>,
): Promise<ActionResult> {
  const response = await request(`/api/significance_test/${id}/revise`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function revokeSignificance(id: number, values: Record<string, unknown> = {}): Promise<ActionResult> {
  const response = await request(`/api/significance_test/${id}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function traceSignificance(id: number): Promise<{
  entry: SignificanceItem
  chain: SignificanceItem[]
  tickets: Record<string, unknown>[]
}> {
  const response = await request(`/api/significance_test/${id}/trace`)
  if (!response.ok) throw new Error('检验追查失败')
  return await response.json()
}

export async function runSlice(id: number): Promise<ActionResult> {
  const response = await request(`/api/segment_slice/${id}/run`, { method: 'POST' })
  return await response.json()
}
