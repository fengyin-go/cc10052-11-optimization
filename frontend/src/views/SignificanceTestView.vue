<template>
  <section class="page" data-module="significance_test">
    <header class="page-head">
      <div>
        <h2>显著性检验</h2>
        <p class="page-desc">按实验与指标锁定检索，比较同一指标的多个检验窗口与版次，选中后登记切片取证单。</p>
      </div>
      <div class="page-actions">
        <span v-if="signature" class="signature-chip" data-testid="query-signature">
          查询签名 {{ signature }}
        </span>
        <button class="btn" data-testid="reload" type="button" @click="runSearch()">重新查询</button>
      </div>
    </header>

    <form class="filter-bar" data-testid="search-form" @submit.prevent="runSearch(1)">
      <label class="filter-item">
        <span>实验编码</span>
        <input v-model="conditions.experiment" data-testid="search-experiment" placeholder="如 E1" />
      </label>
      <label class="filter-item">
        <span>指标编码</span>
        <input v-model="conditions.metric" data-testid="search-metric" placeholder="如 M" />
      </label>
      <label class="filter-item">
        <span>检验窗口</span>
        <input v-model="conditions.windows" data-testid="search-windows" placeholder="W1,W2（留空为全部）" />
      </label>
      <label class="filter-item">
        <span>日期起</span>
        <input v-model="conditions.date_from" data-testid="search-date-from" type="date" />
      </label>
      <label class="filter-item">
        <span>日期止</span>
        <input v-model="conditions.date_to" data-testid="search-date-to" type="date" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="conditions.status" data-testid="search-status">
          <option value="">全部</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>排序</span>
        <select v-model="conditions.sort" data-testid="search-sort" @change="runSearch(1)">
          <option v-for="mode in sortModes" :key="mode.value" :value="mode.value">{{ mode.label }}</option>
        </select>
      </label>
      <button class="btn primary" data-testid="search-submit" type="submit">检索</button>
      <button class="btn ghost" type="button" @click="resetConditions">重置条件</button>
    </form>

    <p v-if="searchMessage" class="error-text" data-testid="search-message">{{ searchMessage }}</p>

    <div class="selection-bar">
      <span data-testid="selection-summary">
        已选 {{ selected.size }} 个窗口：{{ selectedWindows.join('、') || '—' }}
        <template v-if="selected.size"> / {{ selectedBatches.join('、') }}</template>
      </span>
      <span class="muted">切换排序或翻页只改变展示顺序，选中身份按检验批次保留。</span>
      <button class="btn primary" data-testid="open-ticket" type="button" :disabled="!selected.size" @click="openTicket">
        登记切片取证单
      </button>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th class="pick-col">选</th>
          <th>检验批次</th>
          <th>检验窗口</th>
          <th>窗口范围</th>
          <th>轮次 / 版次</th>
          <th>P值</th>
          <th>置信区间</th>
          <th>差异方向</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.stable_key"
          :data-testid="`sig-row-${row.id}`"
          :data-status="row.status"
          :data-selected="selected.has(Number(row.id)) ? 'true' : 'false'"
          :class="{ 'row-revised': row['修订状态'] !== '原结果' }"
        >
          <td class="pick-col">
            <input
              type="checkbox"
              :data-testid="`pick-${row.id}`"
              :checked="selected.has(Number(row.id))"
              @change="toggleSelect(row)"
            />
          </td>
          <td>{{ row['检验批次'] }}</td>
          <td>{{ row['检验窗口'] }}</td>
          <td>{{ row['窗口起点'] }} ~ {{ row['窗口终点'] }}</td>
          <td>
            <span :class="['badge', row['修订状态'] === '修订后' ? 'badge-new' : row['修订状态'] === '修订前' ? 'badge-old' : 'badge-plain']">
              R{{ row['轮次'] }} · {{ row['修订状态'] }}
            </span>
          </td>
          <td>{{ row['P值'] }}</td>
          <td>{{ row['置信区间'] }}</td>
          <td :data-testid="`direction-${row.id}`">{{ row['差异方向'] }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-if="row.status === '已检验'"
              class="link"
              type="button"
              :data-testid="`confirm-${row.id}`"
              @click="confirmRow(row)"
            >确认检验</button>
            <button
              v-if="row.status === '已确认'"
              class="link"
              type="button"
              :data-testid="`revise-${row.id}`"
              @click="startRevise(row)"
            >更正区间</button>
            <button
              v-if="row.status === '已确认'"
              class="link danger"
              type="button"
              :data-testid="`revoke-${row.id}`"
              @click="revokeRow(row)"
            >撤销检验</button>
            <button
              class="link"
              type="button"
              :data-testid="`trace-${row.id}`"
              @click="openTrace(row)"
            >追查</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="10" class="empty-state">没有符合实验、指标与窗口条件的检验结果</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span data-testid="page-summary">共 {{ total }} 条，第 {{ page }} 页</span>
      <div class="page-actions">
        <button class="btn" data-testid="prev-page" type="button" :disabled="page <= 1" @click="runSearch(page - 1)">上一页</button>
        <button class="btn" data-testid="next-page" type="button" :disabled="page * pageSize >= total" @click="runSearch(page + 1)">下一页</button>
      </div>
    </footer>

    <section class="inline-panel" data-testid="query-history">
      <header class="board-head">
        <h3>已确认结果的检索记录</h3>
        <button class="btn ghost" type="button" data-testid="query-history-refresh" @click="loadQueries">刷新</button>
      </header>
      <p class="muted">按当时的排序与筛选条件重查；之后的重检会以新的可辨认批次出现在结果里。</p>
      <table class="data-table">
        <thead>
          <tr><th>查询签名</th><th>实验 / 指标</th><th>窗口</th><th>排序</th><th>创建时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="saved in queries" :key="String(saved['查询签名'])" :data-testid="`query-${saved['查询签名']}`">
            <td>{{ saved['查询签名'] }}</td>
            <td>{{ saved['实验编码'] }} / {{ saved['指标编码'] }}</td>
            <td>{{ saved['窗口集合'] || '全部' }}</td>
            <td>{{ saved['排序说明'] }}</td>
            <td>{{ saved['创建时间'] }}</td>
            <td class="row-actions">
              <button
                class="link"
                type="button"
                :data-testid="`rerun-${saved['查询签名']}`"
                @click="rerunQuery(String(saved['查询签名']))"
              >按原条件重查</button>
            </td>
          </tr>
          <tr v-if="!queries.length">
            <td colspan="6" class="empty-state">还没有检索记录</td>
          </tr>
        </tbody>
      </table>
      <p v-if="rerunMessage" class="inline-message ok-text" data-testid="rerun-message">{{ rerunMessage }}</p>
      <div v-if="rerunAdded.length" data-testid="rerun-added">
        <h4>重查新增的可辨认结果</h4>
        <ul>
          <li v-for="row in rerunAdded" :key="row.stable_key">
            {{ row['检验窗口'] }} · {{ row['检验批次'] }} · R{{ row['轮次'] }} {{ row['修订状态'] }} ·
            {{ row['置信区间'] }}（{{ row['差异方向'] }}）
          </li>
        </ul>
      </div>
    </section>

    <form v-if="revising" class="inline-panel" data-testid="revise-panel" @submit.prevent="submitRevise">      <h3>更正 {{ revising['检验批次'] }}（{{ revising['检验窗口'] }}）</h3>
      <p class="muted">原结果保持已确认，本更正登记为新的可辨认轮次。</p>
      <label class="create-field">
        <span>更正后置信区间</span>
        <input v-model="reviseForm.interval" data-testid="revise-interval" placeholder="如 [-0.01, 0.04]" required />
      </label>
      <label class="create-field">
        <span>更正原因</span>
        <input v-model="reviseForm.reason" data-testid="revise-reason" />
      </label>
      <div class="page-actions">
        <button class="btn" type="button" @click="revising = null">取消</button>
        <button class="btn primary" data-testid="revise-submit" type="submit">确认更正</button>
      </div>
    </form>

    <div v-if="trace" class="inline-panel" data-testid="trace-panel">
      <h3>追查 {{ trace.entry['检验窗口'] }} 的检验批次</h3>
      <table class="data-table">
        <thead>
          <tr><th>检验批次</th><th>轮次</th><th>修订状态</th><th>置信区间</th><th>区间方向</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in trace.chain" :key="row.stable_key" :data-testid="`trace-row-${row.id}`">
            <td>{{ row['检验批次'] }}</td>
            <td>R{{ row['轮次'] }}</td>
            <td>{{ row['修订状态'] }}</td>
            <td>{{ row['置信区间'] }}</td>
            <td>{{ row['差异方向'] }}</td>
            <td>{{ row.status }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="trace.tickets.length" data-testid="trace-tickets">
        引用取证单：
        <span v-for="ticket in trace.tickets" :key="String(ticket.id)" class="ticket-ref">
          {{ ticket['取证单号'] }}（{{ ticket['状态'] }}）
        </span>
      </p>
      <p v-else class="muted">该窗口还没有被下钻取证单引用。</p>
      <button class="btn" type="button" @click="trace = null">收起</button>
    </div>

    <form v-if="ticketDrafting" class="inline-panel" data-testid="ticket-panel" @submit.prevent="submitTicket">
      <h3>登记切片取证单</h3>
      <p class="muted">
        实验 {{ conditions.experiment }} · 指标 {{ conditions.metric }} · 排序 {{ sortLabel }}
        <template v-if="signature"> · 查询签名 {{ signature }}</template>
      </p>
      <table class="data-table">
        <thead>
          <tr><th>窗口</th><th>检验批次</th><th>版次</th><th>置信区间</th><th>差异方向</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in ticketBasis" :key="item.stable_key" :data-testid="`ticket-basis-${item.id}`">
            <td>{{ item['检验窗口'] }}</td>
            <td>{{ item['检验批次'] }}</td>
            <td>R{{ item['轮次'] }} · {{ item['修订状态'] }}</td>
            <td>{{ item['置信区间'] }}</td>
            <td>{{ item['差异方向'] }}</td>
          </tr>
        </tbody>
      </table>
      <label class="create-field">
        <span>申请人</span>
        <input v-model="ticketForm.applicant" data-testid="ticket-applicant" />
      </label>
      <p v-if="ticketMessage" :class="['inline-message', ticketOk ? 'ok-text' : 'error-text']" data-testid="ticket-message">
        {{ ticketMessage }}
      </p>
      <div class="page-actions">
        <button class="btn" type="button" @click="ticketDrafting = false">取消</button>
        <button class="btn primary" data-testid="ticket-submit" type="submit">确认登记</button>
      </div>
    </form>

    <section class="inline-panel" data-testid="ticket-board">
      <header class="board-head">
        <h3>切片取证单</h3>
        <button class="btn ghost" type="button" data-testid="ticket-refresh" @click="loadTickets">刷新</button>
      </header>
      <table class="data-table">
        <thead>
          <tr><th>取证单号</th><th>实验 / 指标</th><th>窗口</th><th>状态</th><th>采用依据</th><th>切片批次</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="ticket in tickets" :key="ticket.id" :data-testid="`ticket-${ticket.id}`">
            <td>
              {{ ticket['取证单号'] }}
              <span v-if="ticket['重发来源']" class="muted">（重发于 {{ ticket['重发来源'] }}）</span>
              <div v-if="ticket['区间变化']?.length" class="muted" :data-testid="`ticket-changes-${ticket.id}`">
                <div v-for="change in ticket['区间变化']" :key="change['检验窗口']">
                  {{ change['检验窗口'] }}：{{ change['登记批次'] }} {{ change['登记区间'] }} →
                  {{ change['本次批次'] }} {{ change['本次区间'] }}
                </div>
              </div>
            </td>
            <td>{{ ticket['实验编码'] }} / {{ ticket['指标编码'] }}</td>
            <td>{{ ticket['窗口集合'] }}</td>
            <td>{{ ticket['状态'] }}</td>
            <td>
              {{ ticket['采用依据'] || '—' }}
              <div v-if="ticket['实际引用']?.length" class="muted" :data-testid="`ticket-refs-${ticket.id}`">
                <div v-for="ref in ticket['实际引用']" :key="ref['检验批次']">
                  {{ ref['检验窗口'] }} → {{ ref['检验批次'] }} {{ ref['置信区间'] }}（{{ ref['依据来源'] }}）
                </div>
              </div>
            </td>
            <td>{{ ticket['切片批次'] || '—' }}</td>
            <td class="row-actions">
              <button
                v-if="ticket['状态'] === '待领取'"
                class="link"
                type="button"
                :data-testid="`claim-pinned-${ticket.id}`"
                @click="claim(ticket, 'pinned')"
              >按登记依据领取</button>
              <button
                v-if="ticket['状态'] === '待领取'"
                class="link"
                type="button"
                :data-testid="`claim-latest-${ticket.id}`"
                @click="claim(ticket, 'latest')"
              >按最新确认领取</button>
              <button
                class="link"
                type="button"
                :data-testid="`reissue-${ticket.id}`"
                @click="reissue(ticket)"
              >重新发起</button>
            </td>
          </tr>
          <tr v-if="!tickets.length">
            <td colspan="7" class="empty-state">还没有取证单，勾选窗口后可登记</td>
          </tr>
        </tbody>
      </table>
      <p v-if="ticketMessage" :class="['inline-message', ticketOk ? 'ok-text' : 'error-text']" data-testid="ticket-board-message">
        {{ ticketMessage }}
      </p>
    </section>

    <p v-if="errorMessage" class="error-text" data-testid="error-message">{{ errorMessage }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import {
  claimEvidenceTicket,
  createEvidenceTicket,
  listEvidenceTickets,
  listSignificanceQueries,
  reissueEvidenceTicket,
  rerunSignificanceQuery,
  reviseSignificance,
  revokeSignificance,
  runAction,
  searchSignificance,
  traceSignificance,
  type EvidenceTicket,
  type SignificanceConditions,
  type SignificanceItem,
} from '@/api/backend-service'

const statuses = ['待检验', '已检验', '已确认', '检验异常', '已撤销']
const sortModes = [
  { value: 'start_asc', label: '窗口起点升序' },
  { value: 'start_desc', label: '窗口起点降序' },
  { value: 'end_asc', label: '窗口终点升序' },
  { value: 'end_desc', label: '窗口终点降序' },
  { value: 'batch_asc', label: '检验批次升序' },
  { value: 'confirm_desc', label: '确认时间倒序' },
]
const pageSize = 20

const conditions = reactive<SignificanceConditions>({
  experiment: 'E1',
  metric: 'M',
  windows: '',
  date_from: '',
  date_to: '',
  status: '',
  sort: 'start_asc',
})
const rows = ref<SignificanceItem[]>([])
const snapshot = ref<SignificanceItem[]>([])
const total = ref(0)
const page = ref(1)
const signature = ref('')
const sortLabel = ref('窗口起点升序')
const searchMessage = ref('')
const errorMessage = ref('')
const ticketMessage = ref('')
const ticketOk = ref(false)
const selected = ref<Set<number>>(new Set())
const selectedWindows = ref<string[]>([])
const selectedBatches = ref<string[]>([])
const ticketDrafting = ref(false)
const ticketBasis = ref<SignificanceItem[]>([])
const revising = ref<SignificanceItem | null>(null)
const reviseForm = reactive({ interval: '', reason: '' })
const trace = ref<{ entry: SignificanceItem; chain: SignificanceItem[]; tickets: Record<string, unknown>[] } | null>(null)
const tickets = ref<EvidenceTicket[]>([])
const ticketForm = reactive({ applicant: '数据分析员' })
const lastFilterKey = ref('')
const queries = ref<Record<string, unknown>[]>([])
const rerunMessage = ref('')
const rerunAdded = ref<SignificanceItem[]>([])

async function runSearch(targetPage = 1) {
  errorMessage.value = ''
  searchMessage.value = ''
  // 条件变化是一次新检索，旧选区与旧快照都作废；仅翻页/换排序时保留选中身份。
  const key = filterKey()
  if (key !== lastFilterKey.value) {
    lastFilterKey.value = key
    selected.value = new Set()
    snapshot.value = []
  }
  try {
    const payload = await searchSignificance(conditions, targetPage, pageSize)
    if (!payload.ok) {
      searchMessage.value = payload.message
      snapshot.value = []
      rows.value = []
      total.value = 0
      return
    }
    page.value = payload.page
    total.value = payload.total
    signature.value = payload.query_signature
    sortLabel.value = payload.sort_label
    rows.value = payload.items
    // 快照按 id 累积已见批次，选中只认 id，排序/翻页都不会把窗口换成另一条。
    const merged = new Map(snapshot.value.map((row) => [Number(row.id), row]))
    for (const row of payload.items) merged.set(Number(row.id), row)
    snapshot.value = [...merged.values()]
    syncSelection()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检索失败'
  }
}

function filterKey(): string {
  return [
    conditions.experiment,
    conditions.metric,
    conditions.windows,
    conditions.date_from,
    conditions.date_to,
    conditions.status,
  ].join('|')
}

function syncSelection() {
  const order = snapshot.value
  selectedWindows.value = order.filter((row) => selected.value.has(Number(row.id))).map((row) => row['检验窗口'])
  selectedBatches.value = order.filter((row) => selected.value.has(Number(row.id))).map((row) => row['检验批次'])
}

function toggleSelect(row: SignificanceItem) {
  const id = Number(row.id)
  const window = row['检验窗口']
  if (selected.value.has(id)) {
    selected.value.delete(id)
  } else {
    for (const other of snapshot.value) {
      if (other['检验窗口'] === window) selected.value.delete(Number(other.id))
    }
    selected.value.add(id)
  }
  selected.value = new Set(selected.value)
  syncSelection()
}

function resetConditions() {
  conditions.windows = ''
  conditions.date_from = ''
  conditions.date_to = ''
  conditions.status = ''
  conditions.sort = 'start_asc'
  selected.value = new Set()
  snapshot.value = []
  syncSelection()
  void runSearch(1)
}

async function confirmRow(row: SignificanceItem) {
  await act(() => runAction('significance_test', Number(row.id), '确认检验'))
}

async function revokeRow(row: SignificanceItem) {
  await act(async () => revokeSignificance(Number(row.id), { 原因: '人工撤销：确认结果作废' }))
}

function startRevise(row: SignificanceItem) {
  revising.value = row
  reviseForm.interval = String(row['置信区间'] ?? '')
  reviseForm.reason = ''
}

async function submitRevise() {
  if (!revising.value) return
  const target = revising.value
  const result = await reviseSignificance(Number(target.id), {
    置信区间: reviseForm.interval,
    原因: reviseForm.reason,
  })
  revising.value = null
  await act(async () => result)
}

async function openTrace(row: SignificanceItem) {
  try {
    trace.value = await traceSignificance(Number(row.id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '追查失败'
  }
}

async function act(run: () => Promise<{ ok: boolean; message: string }>) {
  errorMessage.value = ''
  try {
    const result = await run()
    if (!result.ok) throw new Error(result.message)
    await runSearch(page.value)
    await loadTickets()
    await loadQueries()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function openTicket() {
  if (!conditions.experiment || !conditions.metric) {
    searchMessage.value = '登记取证单前必须锁定实验编码与指标编码'
    return
  }
  if (!selected.value.size) return
  ticketMessage.value = ''
  ticketDrafting.value = true
  ticketBasis.value = [...selected.value]
    .map((id) => snapshot.value.find((row) => Number(row.id) === id))
    .filter((row): row is SignificanceItem => Boolean(row))
    .sort((a, b) => a['窗口起点'].localeCompare(b['窗口起点']) || Number(a.id) - Number(b.id))
}

async function submitTicket() {
  ticketMessage.value = ''
  try {
    const result = await createEvidenceTicket({
      experiment: conditions.experiment,
      metric: conditions.metric,
      windows: [...new Set(selectedWindows.value)].join(','),
      selected_ids: [...selected.value],
      sort: conditions.sort,
      query_signature: signature.value,
      applicant: ticketForm.applicant,
    })
    ticketOk.value = result.ok
    ticketMessage.value = result.message
    if (result.ok) {
      selected.value = new Set()
      syncSelection()
      ticketDrafting.value = false
      await loadTickets()
    }
  } catch (error) {
    ticketOk.value = false
    ticketMessage.value = error instanceof Error ? error.message : '取证单登记失败'
  }
}

async function claim(ticket: EvidenceTicket, basis: 'pinned' | 'latest') {
  try {
    const result = await claimEvidenceTicket(Number(ticket.id), { basis, claimed_by: '切片分析器' })
    ticketOk.value = result.ok
    ticketMessage.value = result.message
    await loadTickets()
  } catch (error) {
    ticketOk.value = false
    ticketMessage.value = error instanceof Error ? error.message : '领取失败'
  }
}

async function reissue(ticket: EvidenceTicket) {
  try {
    const result = await reissueEvidenceTicket(Number(ticket.id), { applicant: ticketForm.applicant })
    ticketOk.value = result.ok
    ticketMessage.value = result.message
    await loadTickets()
  } catch (error) {
    ticketOk.value = false
    ticketMessage.value = error instanceof Error ? error.message : '重新发起失败'
  }
}

async function loadQueries() {
  try {
    queries.value = await listSignificanceQueries()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检索记录读取失败'
  }
}

async function rerunQuery(signatureText: string) {
  rerunMessage.value = ''
  rerunAdded.value = []
  try {
    const result = await rerunSignificanceQuery(signatureText)
    if (!result.ok) throw new Error(result.message)
    rerunMessage.value = result.message
    rerunAdded.value = result.added
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重查失败'
  }
}

async function loadTickets() {
  try {
    tickets.value = await listEvidenceTickets(conditions.experiment, conditions.metric)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '取证单读取失败'
  }
}

onMounted(async () => {
  await runSearch(1)
  await loadTickets()
  await loadQueries()
})
</script>
