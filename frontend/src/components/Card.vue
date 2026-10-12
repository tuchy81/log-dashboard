<script setup lang="ts">
defineProps<{ label: string; value: string | number; tone?: 'ok' | 'warn' | 'bad' | ''; sub?: string; clickable?: boolean }>()
</script>
<template>
  <div class="card kpi" :class="[tone, { clickable }]">
    <div class="kpi-label">{{ label }}</div>
    <div class="kpi-value">{{ value }}</div>
    <div v-if="sub" class="kpi-sub">{{ sub }}</div>
  </div>
</template>
