<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Card from '../components/Card.vue'
import EChart from '../components/EChart.vue'
import Panel from '../components/Panel.vue'
import { COLOR, csvUrl, get, n, patch, pct } from '../api'
import { useFilters } from '../stores/filters'

const f = useFilters(); const route = useRoute()
const comp = ref<any>(null), byRule = ref<any[]>([]), rows = ref<any[]>([]), total = ref(0), anom = ref<any>({ data: [], meta: {} })
const q = reactive({ rule: (route.query.rule as string) || '', status: '', severity: '', page: 1 })
const anomConsumer = ref('')
const RULES = ['UNREGISTERED', 'DEPRECATED_CALLED', 'AUTHZ_FAILURE', 'RESOURCE_ENUM', 'POLICY_MISMATCH', 'SLO_BREACH', 'CONSUMER_SPIKE', 'OWNER_MISSING', 'UNUSED']
const STATUSES = ['OPEN', 'ACK', 'RESOLVED', 'EXCEPTION']
const STATUS_LABEL: any = { OPEN: 'OPEN', ACK: '조치중', RESOLVED: '조치완료', EXCEPTION: '예외승인' }

async function loadList() { const r = await get('/governance/detections', { ...q, size: 50 }); rows.value = r.data; total.value = r.meta.rowCount }
async function loadTop() { comp.value = (await get('/governance/compliance')).data; byRule.value = (await get('/governance/by-rule')).data }
loadTop(); loadList()
watch(q, loadList, { deep: true })
watch(() => [f.days, anomConsumer.value], async () => { anom.value = await get('/governance/anomalies', { from: f.from, to: f.to, consumer: anomConsumer.value }) }, { immediate: true })

async function setStatus(r: any, status: string) {
  await patch('/governance/detections/' + r.id, { status }); await Promise.all([loadList(), loadTop()])
}
// 경과일 SLA: 보안(HIGH) 24시간, 일반 10일 (영업일 근사: 달력일 14일)
const overSla = (r: any) => r.status !== 'RESOLVED' && r.status !== 'EXCEPTION' && (r.severity === 'HIGH' ? r.elapsedDays >= 1 : r.elapsedDays > 14)
const ev = (e: any) => Object.entries(e || {}).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join('/') : v}`).join(', ')

const ruleOpt = computed(() => ({
  grid: { left: 150, right: 20, top: 30, bottom: 20 }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } }, legend: { top: 0 },
  xAxis: { type: 'value' }, yAxis: { type: 'category', data: [...RULES].reverse() },
  series: [['OPEN', COLOR.bad], ['ACK', COLOR.warn], ['RESOLVED', COLOR.ok], ['EXCEPTION', '#b7c0cb']].map(([s, c]) => ({
    name: STATUS_LABEL[s], type: 'bar', stack: 't', itemStyle: { color: c },
    data: [...RULES].reverse().map((r) => byRule.value.find((b) => b.rule === r && b.status === s)?.n || 0) })),
}))
const anomOpt = computed(() => {
  const agg: Record<string, any> = {}
  for (const r of anom.value.data) { const a = (agg[r.date] ||= { authz: 0, c404: 0, c429: 0 }); a.authz += r.c401 + r.c403; a.c404 += r.c404; a.c429 += r.c429 }
  const d = Object.keys(agg).sort(); const th = anom.value.meta.thresholds || { authz: 50, enum: 100 }
  const mk = (name: string, key: string, color: string, line?: number) => ({ name, type: 'line', showSymbol: false, data: d.map((x) => agg[x][key]), itemStyle: { color },
    markLine: line ? { symbol: 'none', lineStyle: { type: 'dashed', color }, label: { formatter: `${name} 임계 ${line}` }, data: [{ yAxis: line }] } : undefined })
  return { grid: { left: 50, right: 90, top: 30, bottom: 25 }, tooltip: { trigger: 'axis' }, legend: { top: 0 }, xAxis: { type: 'category', data: d }, yAxis: { type: 'value' },
    series: [mk('401+403', 'authz', COLOR.bad, th.authz), mk('404', 'c404', COLOR.warn, th.enum), mk('429', 'c429', COLOR.neutral)] }
})
const pages = computed(() => Math.max(1, Math.ceil(total.value / 50)))
</script>

<template>
  <div v-if="comp">
    <div class="grid g4">
      <Card label="Registry 등록률" :value="pct(comp.registrationRate)" :tone="comp.registrationRate < 1 ? 'bad' : 'ok'" sub="목표 100%" />
      <Card label="소유자 지정률" :value="pct(comp.ownerRate)" :tone="comp.ownerRate < 1 ? 'warn' : 'ok'" sub="목표 100%" />
      <Card label="정책 일치율" :value="pct(comp.policyRate)" :tone="comp.policyRate < 1 ? 'warn' : 'ok'" sub="목표 100%" />
      <Card label="SLO 정의율" :value="pct(comp.sloDefinedRate)" :tone="comp.sloDefinedRate < 1 ? 'warn' : 'ok'" sub="목표 100%" />
    </div>
    <div class="grid g2">
      <Panel title="탐지 현황 (규칙 × 상태)"><EChart :option="ruleOpt" :height="320" /></Panel>
      <Panel title="이상 호출 (401·403·404·429)" range>
        <div class="toolbar">소비 시스템 <select v-model="anomConsumer"><option value="">전체</option><option v-for="c in f.consumers" :key="c.code" :value="c.code">{{ c.code }}</option></select></div>
        <EChart :option="anomOpt" :height="270" />
      </Panel>
    </div>
    <Panel title="탐지 목록" note="OPEN 상태 HIGH는 24시간 초과 시 적색">
      <div class="toolbar">
        <select v-model="q.rule"><option value="">규칙 전체</option><option v-for="r in RULES" :key="r">{{ r }}</option></select>
        <select v-model="q.severity"><option value="">심각도 전체</option><option>HIGH</option><option>MEDIUM</option><option>LOW</option></select>
        <select v-model="q.status"><option value="">상태 전체(예외 제외)</option><option v-for="s in STATUSES" :key="s" :value="s">{{ STATUS_LABEL[s] }}</option></select>
        <span class="sp" /><span>{{ n(total) }}건</span><a class="btn" :href="csvUrl('/governance/detections', q)">CSV 내보내기</a>
      </div>
      <div class="scroll"><table>
        <thead><tr><th>규칙</th><th>심각도</th><th>API</th><th>소유 조직</th><th>탐지일</th><th class="num">경과일</th><th>근거</th><th>상태</th></tr></thead>
        <tbody><tr v-for="r in rows" :key="r.id">
          <td>{{ r.ruleCode }}</td><td><span class="pill" :class="r.severity">{{ r.severity }}</span></td>
          <td><router-link :to="'/api/' + r.apiId">{{ r.apiId }}</router-link></td><td>{{ r.ownerOrg || '미지정' }}</td>
          <td>{{ r.firstDetectedAt.slice(0, 10) }}</td><td class="num" :class="{ red: overSla(r) }">{{ r.elapsedDays }}일</td>
          <td><small>{{ ev(r.evidence) }}</small></td>
          <td><select :value="r.status" @change="setStatus(r, ($event.target as HTMLSelectElement).value)"><option v-for="s in STATUSES" :key="s" :value="s">{{ STATUS_LABEL[s] }}</option></select></td>
        </tr></tbody>
      </table></div>
      <div class="toolbar" style="margin-top:8px"><span class="sp" /><button :disabled="q.page <= 1" @click="q.page--">이전</button> {{ q.page }} / {{ pages }} <button :disabled="q.page >= pages" @click="q.page++">다음</button></div>
    </Panel>
  </div>
</template>
