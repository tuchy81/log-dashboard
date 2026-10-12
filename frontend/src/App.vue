<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import FilterBar from './components/FilterBar.vue'
import { useFilters } from './stores/filters'
const f = useFilters()
onMounted(() => f.load())
const tabs = [['/assets', '자산 현황'], ['/quality', '운영 품질'], ['/dependency', '연계·의존성'], ['/governance', '보안·거버넌스']]
</script>
<template>
  <header class="top">
    <h1>API Intelligence</h1>
    <nav><RouterLink v-for="t in tabs" :key="t[0]" :to="t[0]">{{ t[1] }}</RouterLink></nav>
    <span class="asof">기준일 {{ f.asOf }}</span>
  </header>
  <div v-if="f.stale" class="banner">⚠ 집계 데이터가 최신이 아닙니다 (최종 집계일 {{ f.lastStatDate }}).</div>
  <FilterBar v-if="f.loaded && !$route.path.startsWith('/api/')" />
  <main v-if="f.loaded"><RouterView :key="$route.fullPath" /></main>
</template>
