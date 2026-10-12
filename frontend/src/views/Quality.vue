<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Card from '../components/Card.vue'
import EChart from '../components/EChart.vue'
import Panel from '../components/Panel.vue'
import { COLOR, csvUrl, get, n, pct } from '../api'
import { useFilters } from '../stores/filters'
import { useRouter } from 'vue-router'

const f = useFilters(); const router = useRouter()
const sum = ref<any>(null), trend = ref<any[]>([]), lat = ref<any[]>([]), br = ref<any[]>([])
const split = ref(false), crit = ref(1)
const ERR_THRESHOLD = 0.02

async function load() {
  const p = f.params
  sum.value = (await get('/quality/summary', p)).data
  trend.value = (await get('/quality/trend', { ...p, from: f.days >= 90 ? p.from : undefined })).data
  lat.value = (await get('/quality/latency', { ...p, criticality: crit.value })).data
  br.value = (await get('/quality/slo-breaches')).data
}
watch(() => [f.days, f.domain, f.gateway, f.consumer, crit.value], load, { immediate: true })

const dates = computed(() => [...new Set(trend.value.map((t) => t.date))])
const callsOpt = computed(() => {
  const gws = [...new Set(trend.value.map((t) => t.gateway))]
  const by = (gw?: string) => dates.value.map((d) => trend.value.filter((t) => t.date === d && (!gw || t.gateway === gw)).reduce((a, t) => a + t.calls, 0))
  const colors: any = { APISIX: '#5b7a99', KONG: '#8fa8c2', WEBMETHODS: '#c3cfdc' }
  return {
    grid: { left: 55, right: 20, top: 30, bottom: 25 }, tooltip: { trigger: 'axis' }, legend: { top: 0 },
    xAxis: { type: 'category', data: dates.value }, yAxis: { type: 'value' },
    series: split.value
      ? gws.map((g: any) => ({ name: g, type: 'bar', stack: 'a', data: by(g), itemStyle: { color: colors[g] } }))
      : [{ name: '총 호출량', type: 'line', showSymbol: false, data: by(), itemStyle: { color: COLOR.neutral } }],
  }
})
const errOpt = computed(() => ({
  grid: { left: 55, right: 60, top: 30, bottom: 25 }, tooltip: { trigger: 'axis', valueFormatter: (v: number) => (v * 100).toFixed(1) + '%' },
  xAxis: { type: 'category', data: dates.value }, yAxis: { type: 'value', axisLabel: { formatter: (v: number) => (v * 100).toFixed(1) + '%' } },
  series: [{ name: '오류율', type: 'bar', data: dates.value.map((d) => {
    const rs = trend.value.filter((t) => t.date === d); const c = rs.reduce((a, t) => a + t.calls, 0)
    const e = rs.reduce((a, t) => a + t.errors, 0); const v = c ? e / c : 0
    return { value: v, itemStyle: { color: v > ERR_THRESHOLD ? COLOR.bad : COLOR.neutral } }
  }), markLine: { symbol: 'none', lineStyle: { type: 'dashed', color: COLOR.bad }, label: { formatter: '임계 2.0%' }, data: [{ yAxis: ERR_THRESHOLD }] } }],
}))
const latOpt = computed(() => ({
  grid: { left: 55, right: 20, top: 30, bottom: 90 }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } }, legend: { top: 0 },
  xAxis: { type: 'category', data: lat.value.map((l) => l.api_id.slice(-6)), axisLabel: { rotate: 60 } }, yAxis: { type: 'value', name: 'ms' },
  series: [
    { name: 'P95~P99', type: 'custom', data: lat.value.map((l, i) => [i, l.p95, l.p99]),
      renderItem: (_: any, api: any) => { const x = api.coord([api.value(0), 0])[0]; const a = api.coord([0, api.value(1)])[1]; const b = api.coord([0, api.value(2)])[1]
        return { type: 'group', children: [{ type: 'line', shape: { x1: x, y1: a, x2: x, y2: b }, style: { stroke: '#8fa8c2', lineWidth: 2 } }] } } },
    { name: 'P50', type: 'scatter', data: lat.value.map((l) => l.p50), itemStyle: { color: COLOR.neutral } },
    { name: 'P95', type: 'scatter', symbolSize: 6, data: lat.value.map((l) => ({ value: l.p95, itemStyle: { color: l.p95 > l.slo_p95_ms ? COLOR.bad : l.p95 >= l.slo_p95_ms * 0.8 ? COLOR.warn : COLOR.ok } })) },
    { name: 'SLO 목표', type: 'scatter', symbol: 'rect', symbolSize: [14, 3], data: lat.value.map((l) => l.slo_p95_ms), itemStyle: { color: '#222' } },
  ],
}))
</script>

<template>
  <div v-if="sum">
    <div class="grid g4">
      <Card :label="`총 호출량 (${f.days}일)`" :value="n(sum.totalCalls)" />
      <Card label="평균 오류율" :value="pct(sum.errorRate)" :tone="sum.errorRate > ERR_THRESHOLD ? 'bad' : ''" />
      <Card label="SLO 위반 API" :value="n(sum.sloBreachApis)" :tone="sum.sloBreachApis ? 'bad' : 'ok'" />
      <Card label="P95 중앙값" :value="n(sum.p95Median) + ' ms'" />
    </div>
    <div class="grid g2">
      <Panel title="일별 호출량" range>
        <div class="toolbar toggle"><button :class="{ on: !split }" @click="split = false">합계</button><button :class="{ on: split }" @click="split = true">게이트웨이별</button></div>
        <EChart :option="callsOpt" :height="240" />
      </Panel>
      <Panel title="일별 오류율" range><EChart :option="errOpt" :height="276" /></Panel>
    </div>
    <Panel title="API별 응답시간 (점: P50, 수염: P95~P99, 검정: SLO)" range>
      <div class="toolbar">중요도 <select v-model.number="crit"><option :value="1">1등급 (기본)</option><option :value="2">2등급</option><option :value="3">3등급</option></select>
        <small>색: <b style="color:#2a9d6f">충족</b> / <b style="color:#e0a100">목표의 80~100%</b> / <b style="color:#d64545">위반</b></small></div>
      <EChart :option="latOpt" :height="340" @click="(p: any) => lat[p.dataIndex] && router.push('/api/' + lat[p.dataIndex].api_id)" />
    </Panel>
    <Panel title="SLO 위반 목록" note="P95 > SLO 2일 연속">
      <div class="toolbar"><span class="sp" /><a class="btn" :href="csvUrl('/quality/slo-breaches')">CSV 내보내기</a></div>
      <table><thead><tr><th>API</th><th>경로</th><th>소유 조직</th><th class="num">목표 P95</th><th class="num">실측 P95</th><th class="num">초과율</th><th class="num">연속 위반</th></tr></thead>
        <tbody><tr v-for="r in br" :key="r.api_id">
          <td><router-link :to="'/api/' + r.api_id">{{ r.api_id }}</router-link></td><td>{{ r.path_template }}</td><td>{{ r.owner_org || '미지정' }}</td>
          <td class="num">{{ n(r.slo_p95_ms) }} ms</td><td class="num red">{{ n(r.p95_ms) }} ms</td><td class="num red">+{{ r.over_pct }}%</td><td class="num">{{ r.consecutive_days }}일</td>
        </tr></tbody></table>
    </Panel>
  </div>
</template>
