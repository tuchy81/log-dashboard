<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import EChart from '../components/EChart.vue'
import Panel from '../components/Panel.vue'
import { COLOR, get, n } from '../api'

const props = defineProps<{ apiId: string }>()
const router = useRouter()
const d = ref<any>(null), calls = ref<any[]>([]), impact = ref<any[]>([]), status = ref('')
const err = ref('')

get('/apis/' + props.apiId).then((r) => (d.value = r.data)).catch(() => (err.value = '해당 API를 찾을 수 없습니다.'))
get('/dependency/impact/' + props.apiId).then((r) => (impact.value = r.data))
watch(status, async () => { calls.value = (await get(`/apis/${props.apiId}/calls`, { status: status.value, limit: 200 })).data }, { immediate: true })

const opt = computed(() => d.value && ({
  grid: { left: 50, right: 50, top: 30, bottom: 25 }, tooltip: { trigger: 'axis' }, legend: { top: 0 },
  xAxis: { type: 'category', data: d.value.daily.map((x: any) => x.date) }, yAxis: [{ type: 'value' }, { type: 'value', name: 'P95 ms' }],
  series: [
    { name: '호출량', type: 'bar', data: d.value.daily.map((x: any) => x.calls), itemStyle: { color: '#b9c7d6' } },
    { name: '오류', type: 'bar', data: d.value.daily.map((x: any) => x.errors), itemStyle: { color: COLOR.bad } },
    { name: 'P95', type: 'line', yAxisIndex: 1, showSymbol: false, data: d.value.daily.map((x: any) => x.p95), itemStyle: { color: COLOR.neutral },
      markLine: d.value.registry?.slo_p95_ms ? { symbol: 'none', lineStyle: { type: 'dashed', color: COLOR.bad }, label: { formatter: 'SLO' }, data: [{ yAxis: d.value.registry.slo_p95_ms }] } : undefined },
  ],
}))
const fields = ['path_template', 'http_method', 'version', 'domain_name', 'provider_system', 'owner_org', 'owner_id', 'criticality', 'lifecycle_status', 'auth_type', 'authz_policy_id',
  'rate_limit_rpm', 'gw_rate_limit_rpm', 'slo_p95_ms', 'slo_availability', 'gateway', 'registered_at', 'deprecate_date', 'openapi_url']
</script>

<template>
  <div>
    <p><a href="#" @click.prevent="router.back()">← 뒤로</a></p>
    <h2>{{ apiId }}</h2>
    <p v-if="err" class="red">{{ err }}</p>
    <div v-if="d">
      <p v-if="d.unregistered" class="red">⚠ Registry에 등록되지 않은 API입니다 (UNREGISTERED).</p>
      <p><span v-for="x in d.detections" :key="x.id" class="pill" :class="x.severity" style="margin-right:6px">{{ x.ruleCode }} · {{ x.status }}</span></p>
      <div class="grid g2">
        <Panel title="Registry 정보"><dl v-if="d.registry"><template v-for="k in fields" :key="k"><dt>{{ k }}</dt><dd>{{ d.registry[k] ?? '-' }}</dd></template></dl><p v-else>-</p></Panel>
        <div>
          <Panel title="일별 호출·오류·P95 (90일)" note="최근 90일"><EChart :option="opt" :height="260" /></Panel>
          <Panel title="영향 소비 시스템 (30일)"><table><thead><tr><th>시스템</th><th class="num">호출</th><th>최근 호출</th></tr></thead>
            <tbody><tr v-for="r in impact" :key="r.consumer_system"><td>{{ r.consumer_name || r.consumer_system }}</td><td class="num">{{ n(r.calls30d) }}</td><td>{{ r.last_call }}</td></tr></tbody></table></Panel>
        </div>
      </div>
      <Panel title="원천 호출 로그 (최근 200건, 조회는 감사 로그에 기록됨)">
        <div class="toolbar">상태코드 <select v-model="status"><option value="">전체</option><option v-for="s in [200, 201, 400, 401, 403, 404, 429, 500]" :key="s">{{ s }}</option></select></div>
        <div class="scroll" style="max-height:420px;overflow-y:auto"><table>
          <thead><tr><th>시각(UTC)</th><th>소비자</th><th>상태</th><th>인가</th><th class="num">총 ms</th><th class="num">upstream</th><th class="num">GW 오버헤드</th><th>GW 노드</th><th>trace_id</th></tr></thead>
          <tbody><tr v-for="c in calls" :key="c.trace_id"><td>{{ c.timestamp }}</td><td>{{ c.consumer_system }}</td><td :class="{ red: c.status_code >= 500 }">{{ c.status_code }}</td><td>{{ c.auth_result }}</td>
            <td class="num">{{ c.latency_total_ms }}</td><td class="num">{{ c.latency_upstream_ms }}</td><td class="num">{{ c.gw_overhead_ms }}</td><td>{{ c.gateway }}/{{ c.gateway_node }}</td><td><small>{{ c.trace_id }}</small></td></tr></tbody>
        </table></div>
      </Panel>
    </div>
  </div>
</template>
