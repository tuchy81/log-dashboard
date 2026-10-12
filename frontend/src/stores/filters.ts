import { defineStore } from 'pinia'
import { get } from '../api'

export const useFilters = defineStore('filters', {
  state: () => ({
    days: 30, domain: '', gateway: '', consumer: '',
    domains: [] as any[], gateways: [] as string[], consumers: [] as any[],
    asOf: '', lastStatDate: '', stale: false, loaded: false,
  }),
  getters: {
    to: (s) => s.lastStatDate,
    from(s) {
      if (!s.lastStatDate) return ''
      const d = new Date(s.lastStatDate + 'T00:00:00Z')
      d.setUTCDate(d.getUTCDate() - (s.days - 1))
      return d.toISOString().slice(0, 10)
    },
    params(): Record<string, any> {
      return { from: this.from, to: this.to, domain: this.domain, gateway: this.gateway, consumer: this.consumer }
    },
  },
  actions: {
    async load() {
      if (this.loaded) return
      const r = await get('/meta/filters')
      this.domains = r.data.domains; this.gateways = r.data.gateways; this.consumers = r.data.consumers
      this.lastStatDate = r.data.lastStatDate; this.stale = r.data.dataStale; this.asOf = r.meta.asOf
      this.loaded = true
    },
  },
})
