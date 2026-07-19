<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { checkSession } from './api'
import QrLogin from './components/QrLogin.vue'
import MainPanel from './components/MainPanel.vue'

const loggedIn = ref(false)
const userInfo = ref<any>(null)
const loading = ref(true)

onMounted(async () => {
  try {
    const res = await checkSession()
    if (res.success && res.data) {
      userInfo.value = res.data
      loggedIn.value = true
    }
  } catch {}
  loading.value = false
})

function onLogin(user: any) {
  userInfo.value = user
  loggedIn.value = true
}

function onLogout() {
  userInfo.value = null
  loggedIn.value = false
}
</script>

<template>
  <div class="app">
    <header class="app-header">
      <h1>🐱 龙猫校园刷跑</h1>
      <span class="subtitle">微信扫码登录 · 自动轨迹生成 · 跑步记录提交</span>
    </header>

    <div v-if="loading" class="loading">加载中...</div>

    <QrLogin v-else-if="!loggedIn" @login="onLogin" />

    <MainPanel
      v-else
      :user="userInfo"
      @logout="onLogout"
    />
  </div>
</template>
