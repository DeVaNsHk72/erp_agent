import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Chat',
    component: () => import('@/pages/Chat.vue'),
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

let router = createRouter({
  history: createWebHistory('/agent'),
  routes,
})

export default router
