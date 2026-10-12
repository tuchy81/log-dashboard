<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Card from '../components/Card.vue'
import EChart from '../components/EChart.vue'
import Panel from '../components/Panel.vue'
import { COLOR, csvUrl, get, n, pct } from '../api'
import { useFilters } from '../stores/filters'

const f = useFilters(); const router = useRouter()
const sum = ref<any>(null), dist = ref<any>(null), trend = ref<any[]>([])
const rows = ref<any[]>([]), total = ref(0)
const q = reactive({ keyword: '', status: '', criticality: '', owner_missing: false, no_calls: false, sort: 'api_id', desc: false, page: 1 })
const size = 50

const tableParams = computed(() => ({ domain: f.domain, gateway: f.gateway, ...q, criticality: q.criticality || undefined, owner_missing: q.owner_missing || undefined, no_calls: q.no_calls || undefined, size }))
async function loadTable() { const r = await get('/assets/apis', tableParams.value); rows.value = r.data; total.value = r.meta.rowCount }
async function loadAll() {
  sum.value = (await get('/assets/summary')).data
  dist.value = (await get('/assets/distribution')).data
  trend.value = (await get('/assets/trend')).data
}
loadAll(); loadTable()
watch(() => [f.domain, f.gateway], () => { q.page = 1; loadTable() })
watch(q, loadTable, { deep: false })
const sortBy = (c: string) => { q.desc = q.sort === c ? !q.desc : false; q.sort = c; q.page = 1; loadTable() }
const pages = computed(() => Math.max(1, Math.ceil(total.value / size)))
const toGov = (rule: string) => router.push({ path: '/governance', query: { rule } })
const showOwnerMissing = () => { q.owner_missing = true; q.no_calls = false; q.page = 1 }

const domainOpt = computed(() => !dist.value ? {} : {
  grid: { left: 90, right: 20, top: 30, bottom: 20 }, tooltip: { trigger: 'axis' }, legend: { top: 0 },
  xAxis: { type: 'value' }, yAxis: { type: 'category', data: [...dist.value.domains].reverse().map((d: any) => d.name) },
  series: [
    { name: '등록', type: 'bar', data: [...dist.value.domains].reverse().map((d: any) => d.registered), itemStyle: { color: '#c3cfdc' } },
    { name: '실사용', type: 'bar', data: [...dist.value.domains].reverse().map((d: any) => d.active), itemStyle: { color: COLOR.neutral } },
  ],
})
const trendOpt = computed(() => ({
  grid: { left: 40, right: 20, top: 30, bottom: 25 }, tooltip: { trigger: 'axis' }, legend: { top: 0 },
  xAxis: { type: 'category', data: trend.value.map((t) => t.month) }, yAxis: { type: 'value' },
  series: [
    { name: '등록 누계', type: 'line', data: trend.value.map((t) => t.registered), itemStyle: { color: COLOR.neutral } },
    { name: '실사용 API', type: 'line', connectNulls: false, data: trend.value.map((t) => t.active), itemStyle: { color: COLOR.ok } },
  ],
}))
</script>

<template>
  <div v-if="sum">
    <div class="grid g6">
      <Card label="등록 API" :value="n(sum.registered)" clickable @click="q.owner_missing = false; q.no_calls = false" />
      <Card label="실사용 API (90일)" :value="n(sum.activelyUsed)" tone="ok" />
      <Card label="미등록 API" :value="n(sum.unregistered)" :tone="sum.unregistered ? 'bad' : ''" clickable @click="toGov('UNREGISTERED')" />
      <Card label="폐기 후보" :value="n(sum.retireCandidates)" clickable @click="toGov('UNUSED')" />
      <Card label="소유자 미지정" :value="n(sum.ownerMissing)" :tone="sum.ownerMissing ? 'warn' : ''" clickable @click="showOwnerMissing" />
      <Card label="폐기 API 호출" :value="n(sum.deprecatedButCalled)" :tone="sum.deprecatedButCalled ? 'bad' : ''" clickable @click="toGov('DEPRECATED_CALLED')" />
    </div>
    <div class="grid g2">
      <Panel title="자산 4분면 (등록 × 사용)" note="최근 90일">
        <div class="quad">
          <div class="q-ok" @click="q.no_calls = false; q.owner_missing = false"><small>등록 · 사용</small><b>{{ n(sum.quadrant.registeredUsed) }}</b>{{ pct(sum.quadrant.registeredUsed / sum.registered) }}</div>
          <div class="q-warn" @click="q.no_calls = true; q.owner_missing = false"><small>등록 · 미사용(폐기/신규/배치 포함)</small><b>{{ n(sum.quadrant.registeredUnused) }}</b>{{ pct(sum.quadrant.registeredUnused / sum.registered) }}</div>
          <div class="q-bad" @click="toGov('UNREGISTERED')"><small>미등록 · 사용</small><b>{{ n(sum.quadrant.unregisteredUsed) }}</b>Registry 외 호출</div>
          <div class="q-na"><small>미등록 · 미사용</small><b>-</b>관측 불가</div>
        </div>
      </Panel>
      <Panel title="도메인별 분포"><EChart :option="domainOpt" :height="230" /></Panel>
    </div>
    <Panel title="월별 등록 API 누계 및 실사용 API 수" note="실사용: 집계 보존 180일 구간만 표시"><EChart :option="trendOpt" :height="220" /></Panel>
    <Panel title="API 목록" class="" >
      <div class="toolbar">
        <input v-model="q.keyword" placeholder="api_id / 경로 검색" />
        <select v-model="q.status"><option value="">상태 전체</option><option>ACTIVE</option><option>DEPRECATED</option><option>DESIGN</option><option>RETIRED</option></select>
        <select v-model="q.criticality"><option value="">중요도 전체</option><option value="1">1</option><option value="2">2</option><option value="3">3</option></select>
        <label><input type="checkbox" v-model="q.owner_missing" /> 소유자 미지정</label>
        <label><input type="checkbox" v-model="q.no_calls" /> 30일 무호출</label>
        <span class="sp" />
        <a class="btn" :href="csvUrl('/assets/apis', tableParams)">CSV 내보내기</a>
      </div>
      <div class="scroll"><table>
        <thead><tr>
          <th class="sortable" @click="sortBy('api_id')">api_id</th><th class="sortable" @click="sortBy('path_template')">경로</th>
          <th class="sortable" @click="sortBy('domain_code')">도메인</th><th class="sortable" @click="sortBy('owner_org')">소유자</th>
          <th class="sortable" @click="sortBy('lifecycle_status')">상태</th><th class="sortable num" @click="sortBy('criticality')">중요도</th>
          <th class="sortable num" @click="sortBy('calls30d')">30일 호출량</th><th class="sortable" @click="sortBy('last_call_date')">최근 호출일</th>
        </tr></thead>
        <tbody><tr v-for="r in rows" :key="r.api_id">
          <td><router-link :to="'/api/' + r.api_id">{{ r.api_id }}</router-link></td>
          <td>{{ r.http_method }} {{ r.path_template }}</td><td>{{ r.domain_name }}</td>
          <td><span v-if="r.owner_org">{{ r.owner_org }} / {{ r.owner_id }}</span><span v-else class="red">미지정</span></td>
          <td><span class="pill" :class="r.lifecycle_status">{{ r.lifecycle_status }}</span></td>
          <td class="num">{{ r.criticality }}</td><td class="num">{{ n(r.calls30d) }}</td><td>{{ r.last_call_date || '-' }}</td>
        </tr></tbody>
      </table></div>
      <div class="toolbar" style="margin-top:8px"><span>총 {{ n(total) }}건</span><span class="sp" />
        <button :disabled="q.page <= 1" @click="q.page--">이전</button> {{ q.page }} / {{ pages }} <button :disabled="q.page >= pages" @click="q.page++">다음</button></div>
    </Panel>
  </div>
</template>
