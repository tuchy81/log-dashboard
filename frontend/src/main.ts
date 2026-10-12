import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './style.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/assets' },
    { path: '/assets', component: () => import('./views/Assets.vue') },
    { path: '/quality', component: () => import('./views/Quality.vue') },
    { path: '/dependency', component: () => import('./views/Dependency.vue') },
    { path: '/governance', component: () => import('./views/Governance.vue') },
    { path: '/api/:apiId', component: () => import('./views/ApiDetail.vue'), props: true },
  ],
})
createApp(App).use(createPinia()).use(router).mount('#app')
