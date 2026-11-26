<template>
  <section class="page" data-module="segment_slice">
    <header class="page-head">
      <div>
        <h2>切片分析器</h2>
        <p class="page-desc">按取证单领取检验批次，按实际确认区间与窗口范围取数计算，跨零只记无法判定差异方向。</p>
      </div>
      <div class="page-actions">
        <label class="filter-item">
          <span>实验编码</span>
          <input v-model="experiment" data-testid="slice-experiment" @change="reload" />
        </label>
        <button class="btn" data-testid="slice-refresh" type="button" @click="reload">刷新</button>
      </div>
    </header>

    <p v-if="message" :class="['inline-message', ok ? 'ok-text' : 'error-text']" data-testid="slice-message">
      {{ message }}
    </p>

    <table class="data-table">
      <thead>
        <tr>
          <th>切片批次</th>
          <th>检验窗口</th>
          <th>检验批次</th>
          <th>窗口范围</th>
          <th>置信区间</th>
          <th>差异方向</th>
          <th>分组差异</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`slice-row-${row.id}`" :data-status="String(row.status)">
          <td>{{ row['切片批次'] }}</td>
          <td>{{ row['切片值'] }}</td>
          <td>{{ row['检验批次'] ?? '—' }}</td>
          <td>{{ row['窗口范围'] ?? '—' }}</td>
          <td>{{ row['置信区间'] ?? '—' }}</td>
          <td>{{ row['差异方向'] ?? '—' }}</td>
          <td>{{ row['分组差异'] ?? '—' }}</td>
          <td>
            {{ row.status }}
            <div v-if="row['缺失依据']" class="error-text small" :data-testid="`slice-missing-${row.id}`">
              {{ row['缺失依据'] }}
            </div>
          </td>
          <td class="row-actions">
            <button
              v-if="row.status === '待计算'"
              class="link"
              type="button"
              :data-testid="`slice-run-${row.id}`"
              @click="run(row)"
            >开始计算</button>
            <button
              v-if="row.status === '计算失败'"
              class="link"
              type="button"
              :data-testid="`slice-retry-${row.id}`"
              @click="retry(row)"
            >重试计算</button>
            <button
              class="link"
              type="button"
              :data-testid="`slice-evidence-${row.id}`"
              @click="showBasis(row)"
            >取数依据</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="9" class="empty-state">该实验没有切片批次，请先从显著性检验登记取证单</td>
        </tr>
      </tbody>
    </table>

    <div v-if="basis" class="inline-panel" data-testid="slice-basis">
      <h3>{{ basis['切片批次'] }} 的取数依据</h3>
      <dl class="detail-grid">
        <div><dt>检验批次</dt><dd>{{ basis['检验批次'] }}</dd></div>
        <div><dt>窗口范围</dt><dd>{{ basis['窗口范围'] }}</dd></div>
        <div><dt>置信区间</dt><dd>{{ basis['置信区间'] }}</dd></div>
        <div><dt>差异方向</dt><dd>{{ basis['差异方向'] }}</dd></div>
        <div><dt>取证单号</dt><dd>{{ basis['取证单号'] ?? '—' }}</dd></div>
        <div><dt>计算时间</dt><dd>{{ basis['计算时间'] ?? '—' }}</dd></div>
      </dl>
      <p v-if="basisMeta" class="muted">
        实际引用批次 {{ basisMeta['检验批次'] }}，
        修订状态 {{ basisMeta['修订状态'] }}，
        区间是否跨零 {{ basisMeta['区间是否跨零'] ? '是' : '否' }}。
      </p>
      <button class="btn" type="button" @click="basis = null">收起</button>
    </div>

    <p v-if="errorMessage" class="error-text" data-testid="slice-error">{{ errorMessage }}</p>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { listEntries, runAction, runSlice } from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const experiment = ref('E1')
const rows = ref<EntryRow[]>([])
const message = ref('')
const ok = ref(true)
const errorMessage = ref('')
const basis = ref<EntryRow | null>(null)
const basisMeta = computed(() => {
  const raw = basis.value?.['取数依据']
  return raw && typeof raw === 'object' ? raw : null
})

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries('segment_slice', { 实验编码: experiment.value })
    rows.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '切片读取失败'
  }
}

async function run(row: EntryRow) {
  message.value = ''
  try {
    const result = await runSlice(Number(row.id))
    ok.value = result.ok
    message.value = result.message
    await reload()
  } catch (error) {
    ok.value = false
    message.value = error instanceof Error ? error.message : '切片计算失败'
  }
}

async function retry(row: EntryRow) {
  message.value = ''
  try {
    const result = await runAction('segment_slice', Number(row.id), '重试计算')
    ok.value = result.ok
    message.value = result.message
    await reload()
  } catch (error) {
    ok.value = false
    message.value = error instanceof Error ? error.message : '重试失败'
  }
}

function showBasis(row: EntryRow) {
  basis.value = row
}

onMounted(reload)
</script>
