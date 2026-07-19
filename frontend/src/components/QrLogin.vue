<script setup lang="ts">
import { ref, onUnmounted } from 'vue'
import { getQrCode, login, sseUrl } from '../api'

const emit = defineEmits<{ login: [user: any] }>()

const qrImgUrl = ref('')
const qrUuid = ref('')
const loading = ref(false)
const errorMsg = ref('')
const statusMsg = ref('')
const loginLoading = ref(false)

let eventSource: EventSource | null = null

function closeSse() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

onUnmounted(closeSse)

function startSse(uuid: string) {
  closeSse()
  eventSource = new EventSource(sseUrl(uuid))

  eventSource.addEventListener('scanned', (e) => {
    const data = JSON.parse(e.data)
    statusMsg.value = '微信扫码成功，正在登录...'
    closeSse()
    doLogin(data.code)
  })

  eventSource.addEventListener('timeout', () => {
    statusMsg.value = '扫码超时，请刷新二维码重试'
    closeSse()
  })

  eventSource.addEventListener('error', () => {
    // SSE 连接断开会自动重连，不需要特殊处理
  })
}

async function fetchQr() {
  loading.value = true
  errorMsg.value = ''
  statusMsg.value = ''
  qrImgUrl.value = ''
  closeSse()

  try {
    const res = await getQrCode()
    qrImgUrl.value = res.imgUrl
    qrUuid.value = res.uuid
    statusMsg.value = '请使用微信扫描二维码'
    startSse(res.uuid)
  } catch (e: any) {
    errorMsg.value = '获取二维码失败: ' + (e.message || '网络错误')
  } finally {
    loading.value = false
  }
}

async function doLogin(code: string) {
  loginLoading.value = true
  statusMsg.value = '正在登录龙猫校园...'

  try {
    const res = await login(code)
    if (res.success && res.session) {
      statusMsg.value = '登录成功！'
      emit('login', res.session)
    } else {
      errorMsg.value = res.message || '登录失败'
      statusMsg.value = ''
    }
  } catch (e: any) {
    errorMsg.value = '登录异常: ' + (e.message || '网络错误')
    statusMsg.value = ''
  } finally {
    loginLoading.value = false
  }
}

// 初始化时自动加载二维码
fetchQr()
</script>

<template>
  <div class="qr-login">
    <div class="card">
      <h2>微信扫码登录</h2>
      <p class="desc">请使用微信扫描下方二维码，授权"龙猫校园"登录</p>

      <!-- 二维码展示区 -->
      <div class="qr-container">
        <div v-if="loading" class="qr-loading">
          <div class="spinner"></div>
          <p>加载二维码中...</p>
        </div>

        <img
          v-else-if="qrImgUrl"
          :src="qrImgUrl"
          alt="微信二维码"
          class="qr-image"
        />

        <div v-if="errorMsg" class="error">{{ errorMsg }}</div>
      </div>

      <!-- 状态提示 -->
      <div v-if="statusMsg && !errorMsg" class="status">
        <span v-if="loginLoading" class="spinner-small"></span>
        {{ statusMsg }}
      </div>

      <!-- 刷新按钮 -->
      <button
        class="btn btn-secondary"
        :disabled="loading"
        @click="fetchQr"
      >
        {{ loading ? '加载中...' : '刷新二维码' }}
      </button>

      <div class="tips">
        <p><strong>使用说明：</strong></p>
        <ol>
          <li>打开手机微信，扫描上方二维码</li>
          <li>在微信中确认授权登录"龙猫校园"</li>
          <li>授权成功后页面自动跳转</li>
          <li>选择跑步日期、路线、时间，点击提交</li>
          <li>跑步记录立即生效（可提交任意日期）</li>
        </ol>
        <p class="warn">⚠️ 请勿在微信中直接打开此页面，需要用浏览器打开</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.qr-login {
  display: flex;
  justify-content: center;
  padding: 20px;
}

.card {
  background: var(--card-bg);
  border-radius: 16px;
  padding: 32px;
  max-width: 460px;
  width: 100%;
  box-shadow: 0 4px 24px rgba(0,0,0,0.08);
}

.card h2 {
  margin: 0 0 8px;
  font-size: 1.5rem;
}

.desc {
  color: var(--text-secondary);
  margin: 0 0 24px;
}

.qr-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 280px;
}

.qr-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 60px 0;
  color: var(--text-secondary);
}

.qr-image {
  width: 280px;
  height: 280px;
  border-radius: 8px;
  border: 1px solid var(--border);
}

.error {
  margin-top: 16px;
  padding: 12px 16px;
  background: #fee2e2;
  color: #dc2626;
  border-radius: 8px;
}

.status {
  margin-top: 16px;
  padding: 12px 16px;
  background: #dbeafe;
  color: #2563eb;
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.btn {
  display: block;
  width: 100%;
  margin-top: 16px;
  padding: 12px;
  border: none;
  border-radius: 8px;
  font-size: 1rem;
  cursor: pointer;
  transition: all 0.2s;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-secondary {
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--border);
}

.btn-secondary:hover:not(:disabled) {
  background: var(--border);
}

.tips {
  margin-top: 24px;
  padding: 16px;
  background: var(--bg);
  border-radius: 8px;
  font-size: 0.9rem;
}

.tips ol {
  margin: 8px 0;
  padding-left: 20px;
}

.tips li {
  margin: 4px 0;
  color: var(--text-secondary);
}

.warn {
  margin-top: 8px;
  color: #f59e0b;
}
</style>
