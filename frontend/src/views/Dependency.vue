<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import EChart from '../components/EChart.vue'
import Panel from '../components/Panel.vue'
import { COLOR, get, n, pct } from '../api'
import { useFilters } from '../stores/filters'

const f = useFilters()
const graph = ref<{ nodes: any[]; edges: any[] }>({ nodes: [], edges: [] })
const selected = ref<string>('')
const impact = ref<any[]>([])
const limit = ref(40)

watch(() => [f.days, f.domain, f.gateway, f.consumer, limit.value], async () => {
  graph.value = (await get('/dependency/graph', { ...f.params, limit_apis: limit.value })).data
}, { immediate: true })

async function pick(id: string) {
  selected.value = id
  const node = graph.value.nodes.find((x) => x.id === id)
  impact.value = node?.type === 'API' ? (await get('/dependency/impact/' + id)).data : []
}

const opt = computed(() => {
  const cols: Record<string, any[]> = { CONSUMER: [], API: [], PROVIDER: [] }
  graph.value.nodes.forEach((x) => cols[x.type].push(x))
  const X: any = { CONSUMER: 80, API: 480, PROVIDER: 880 }
  const colour: any = { CONSUMER: '#5b7a99', API: '#8fa8c2', PROVIDER: '#2f6d62' }
  const H = 640
  const nodes = graph.value.nodes.map((x) => {
    const list = cols[x.type]; const i = list.indexOf(x)
    return { id: x.id, name: x.id, x: X[x.type], y: 20 + ((i + 0.5) * (H - 40)) / list.length, symbolSize: x.type === 'API' ? 8 + (4 - (x.criticality || 3)) * 3 : 18,
      itemStyle: { color: colour[x.type] }, label: { show: x.type !== 'API' || list.length < 45, position: x.type === 'CONSUMER' ? 'left' : x.type === 'PROVIDER' ? 'right' : 'right', formatter: x.type === 'API' ? x.id.slice(-6) : x.label, fontSize: 10 },
      tip: x.label + (x.criticality ? ` (중요도 ${x.criticality})` : '') }
  })
  const max = Math.max(1, ...graph.value.edges.map((e) => e.calls || 0))
  const edges = graph.value.edges.map((e) => ({ source: e.from, target: e.to,
    lineStyle: { width: e.calls ? 0.5 + (e.calls / max) * 6 : 1, opacity: 0.45, color: e.errorRate > 0.03 ? COLOR.bad : '#9fb0c3' }, calls: e.calls }))
  return { tooltip: { formatter: (p: any) => p.dataType === 'edge' ? `${p.data.source} → ${p.data.target}` + (p.data.calls ? `<br/>${n(p.data.calls)}건` : '') : p.data.tip },
    series: [{ type: 'graph', layout: 'none', roam: true, data: nodes, edges, edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 5, emphasis: { focus: 'adjacency', lineStyle: { opacity: 1 } }, left: 0, right: 0, top: 0, bottom: 0 }] }
})
const consumers = computed(() => graph.value.nodes.filter((x) => x.type === 'CONSUMER'))
</script>

<template>
  <div class="grid g21">
    <Panel title="소비 시스템 → API → 제공 시스템" range note="엣지 두께=호출량, 붉은색=오류율 3% 초과">
      <div class="toolbar">표시 API 수(호출량 상위)
        <select v-model.number="limit"><option :value="20">20</option><option :value="40">40</option><option :value="80">80</option><option :value="140">전체</option></select>
        <small>노드 클릭 시 연결 경로 강조 / API 클릭 시 영향도 표시 · 노드 {{ graph.nodes.length }}</small></div>
      <EChart :option="opt" :height="640" @click="(p: any) => p.dataType === 'node' && pick(p.data.id)" />
    </Panel>
    <div>
      <Panel title="영향도 패널">
        <p v-if="!impact.length">그래프에서 API 노드를 선택하세요.</p>
        <template v-else>
          <p><router-link :to="'/api/' + selected">{{ selected }}</router-link> 변경 시 영향 시스템 {{ impact.length }}개</p>
          <table><thead><tr><th>시스템</th><th class="num">30일 호출</th><th class="num">오류율</th></tr></thead>
            <tbody><tr v-for="r in impact" :key="r.consumer_system"><td>{{ r.consumer_name }} <small>({{ r.consumer_system }})</small></td><td class="num">{{ n(r.calls30d) }}</td><td class="num">{{ pct(r.error_rate || 0) }}</td></tr></tbody></table>
        </template>
      </Panel>
      <Panel title="소비 시스템 목록">
        <div v-for="c in consumers" :key="c.id"><a href="#" @click.prevent="f.consumer = c.id">{{ c.label }} ({{ c.id }})</a></div>
        <small>클릭 시 해당 시스템으로 필터링</small>
      </Panel>
    </div>
  </div>
</template>
