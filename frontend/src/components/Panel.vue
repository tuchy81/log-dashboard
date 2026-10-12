<script setup lang="ts">
import { useFilters } from '../stores/filters'
defineProps<{ title: string; range?: boolean; note?: string }>()
const f = useFilters()
</script>
<template>
  <section class="card panel">
    <header>
      <h3>{{ title }}</h3>
      <span class="asof">as-of {{ f.asOf }}<template v-if="range"> · {{ f.from }} ~ {{ f.to }}</template><template v-if="note"> · {{ note }}</template></span>
    </header>
    <slot />
  </section>
</template>
