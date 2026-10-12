<script setup lang="ts">
import * as echarts from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{ option: any; height?: number }>()
const emit = defineEmits<{ (e: 'click', p: any): void }>()
const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null
let ro: ResizeObserver | null = null

onMounted(() => {
  chart = echarts.init(el.value!)
  chart.on('click', (p) => emit('click', p))
  chart.setOption(props.option)
  ro = new ResizeObserver(() => chart?.resize())
  ro.observe(el.value!)
})
watch(() => props.option, (o) => chart?.setOption(o, true), { deep: true })
onBeforeUnmount(() => { ro?.disconnect(); chart?.dispose() })
</script>

<template><div ref="el" :style="{ height: (height || 280) + 'px', width: '100%' }" /></template>
