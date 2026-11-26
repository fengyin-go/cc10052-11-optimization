import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import SegmentSliceView from '@/views/SegmentSliceView.vue'
import SignificanceTestView from '@/views/SignificanceTestView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 显著性检验与切片分析器有独立的检索/取证流程，必须排在动态模块路由之前。
    { path: '/significance_test', name: 'significance_test', component: SignificanceTestView },
    { path: '/segment_slice', name: 'segment_slice', component: SegmentSliceView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
