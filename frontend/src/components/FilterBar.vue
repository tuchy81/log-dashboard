<script setup lang="ts">
import { useFilters } from '../stores/filters'
const f = useFilters()
</script>
<template>
  <div class="filterbar">
    <label>기간
      <select v-model.number="f.days"><option :value="7">7일</option><option :value="30">30일</option><option :value="90">90일</option></select>
    </label>
    <label>업무 도메인
      <select v-model="f.domain"><option value="">전체</option><option v-for="d in f.domains" :key="d.code" :value="d.code">{{ d.name }}</option></select>
    </label>
    <label>게이트웨이
      <select v-model="f.gateway"><option value="">전체</option><option v-for="g in f.gateways" :key="g" :value="g">{{ g }}</option></select>
    </label>
    <label>소비 시스템
      <select v-model="f.consumer"><option value="">전체</option><option v-for="c in f.consumers" :key="c.code" :value="c.code">{{ c.name }} ({{ c.code }})</option></select>
    </label>
  </div>
</template>
