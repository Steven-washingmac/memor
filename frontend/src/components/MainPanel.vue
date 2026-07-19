<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import {
  logout as apiLogout,
  getRunPaper,
  getCalendar,
  getTasks,
  generateRoute,
  submitRun,
} from '../api'

const props = defineProps<{ user: any }>()
const emit = defineEmits<{ logout: [] }>()

// 状态
const activeTab = ref<'run' | 'calendar' | 'history'>('run')
const loading = ref(false)
const submitting = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error' | 'info'>('info')

// 跑步表单
const runPaper = ref<any>(null)
const routes = ref<any[]>([])
const routeId = ref('')
const runDate = ref('')
const startTime = ref('06:00')
const durationMin = ref(15)
const distance = ref(3.2)

// 日历
const calendarRecords = ref<Record<string, any>>({})
const calendarTerm = ref('')
const calendarMonth = ref(0)
const calendarYear = ref(2026)

// 任务历史
const tasks = ref<any[]>([])

onMounted(async () => {
  // 默认日期今天
  const today = new Date()
  runDate.value = today.toISOString().slice(0, 10)
  calendarYear.value = today.getFullYear()
  calendarMonth.value = today.getMonth()

  await loadRunPaper()
  await loadCalendarData()
})

function show(msg: string, type: 'success' | 'error' | 'info' = 'info') {
  message.value = msg
  messageType.value = type
  setTimeout(() => { message.value = '' }, 5000)
}

async function loadRunPaper() {
  try {
    const res = await getRunPaper()
    if (res.success && res.data) {
      runPaper.value = res.data
      routes.value = res.data.runPointList || []
      if (routes.value.length > 0) {
        routeId.value = routes.value[0].pointId
      }
      if (res.data.mileage) {
        distance.value = parseFloat(res.data.mileage)
      }
    }
  } catch (e: any) {
    show('获取跑步任务失败: ' + e.message, 'error')
  }
}

async function loadCalendarData() {
  try {
    const res = await getCalendar()
    if (res.success && res.data) {
      calendarRecords.value = res.data.records || {}
      calendarTerm.value = res.data.termName || ''
    }
  } catch {}
}

async function loadTasks() {
  try {
    const res = await getTasks()
    if (res.success && res.data) {
      tasks.value = res.data
    }
  } catch {}
}

const selectedRoute = computed(() => {
  return routes.value.find(r => r.pointId === routeId.value)
})

const endTimePreview = computed(() => {
  const [h, m] = startTime.value.split(':').map(Number)
  const total = h * 60 + m + durationMin.value
  const eh = Math.floor(total / 60) % 24
  const em = total % 60
  return `${String(eh).padStart(2, '0')}:${String(em).padStart(2, '0')}`
})

async function doRun() {
  if (!routeId.value) {
    show('请选择跑步路线', 'error')
    return
  }

  submitting.value = true
  show('正在生成 GPS 轨迹...', 'info')

  try {
    // Step 1: 生成轨迹
    const genRes = await generateRoute({
      routeId: routeId.value,
      distance: distance.value,
      runDate: runDate.value,
      startTime: startTime.value,
      durationMin: durationMin.value,
    })

    if (!genRes.success) {
      show('生成轨迹失败: ' + genRes.message, 'error')
      return
    }

    show(
      `轨迹生成成功！${genRes.data.pointCount} 个GPS点，预计距离 ${genRes.data.distance} km，正在提交...`,
      'info'
    )

    // Step 2: 提交跑步
    const subRes = await submitRun({
      routeId: routeId.value,
      runDate: runDate.value,
    })

    if (subRes.success) {
      show(
        `✅ 跑步提交成功！距离: ${subRes.data.distance}km，用时: ${subRes.data.usedTime}，配速: ${subRes.data.avgSpeed}km/h`,
        'success'
      )
      await loadCalendarData()
    } else {
      show('提交失败: ' + subRes.message, 'error')
    }
  } catch (e: any) {
    show('跑步异常: ' + e.message, 'error')
  } finally {
    submitting.value = false
  }
}

async function handleLogout() {
  try {
    await apiLogout()
  } catch {}
  emit('logout')
}

// 日历渲染
const weekdays = ['日', '一', '二', '三', '四', '五', '六']

const calendarDays = computed(() => {
  const firstDay = new Date(calendarYear.value, calendarMonth.value, 1)
  const lastDay = new Date(calendarYear.value, calendarMonth.value + 1, 0)
  const daysInMonth = lastDay.getDate()
  const startWeekday = firstDay.getDay()

  const today = new Date().toISOString().slice(0, 10)
  const days = []

  for (let i = 0; i < startWeekday; i++) {
    days.push(null)
  }

  for (let d = 1; d <= daysInMonth; d++) {
    const ds = `${calendarYear.value}-${String(calendarMonth.value + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`
    days.push({
      day: d,
      date: ds,
      isToday: ds === today,
      record: calendarRecords.value[ds] || null,
    })
  }

  return days
})

function prevMonth() {
  if (calendarMonth.value === 0) {
    calendarYear.value--
    calendarMonth.value = 11
  } else {
    calendarMonth.value--
  }
}

function nextMonth() {
  if (calendarMonth.value === 11) {
    calendarYear.value++
    calendarMonth.value = 0
  } else {
    calendarMonth.value++
  }
}

function onDayClick(day: any) {
  if (!day) return
  runDate.value = day.date
  activeTab.value = 'run'
}

function switchTab(tab: 'run' | 'calendar' | 'history') {
  activeTab.value = tab
  if (tab === 'history') loadTasks()
}
</script>

<template>
  <div class="main-panel">
    <!-- 用户信息栏 -->
    <div class="user-bar">
      <div class="user-info">
        <span class="user-name">{{ user?.stuName || '-' }}</span>
        <span class="user-number">{{ user?.stuNumber || '-' }}</span>
        <span class="user-school">{{ user?.schoolName || '-' }}</span>
      </div>
      <button class="btn-logout" @click="handleLogout">退出</button>
    </div>

    <!-- Tab 切换 -->
    <div class="tabs">
      <button
        :class="['tab', { active: activeTab === 'run' }]"
        @click="switchTab('run')"
      >🏃 跑步</button>
      <button
        :class="['tab', { active: activeTab === 'calendar' }]"
        @click="switchTab('calendar')"
      >📅 日历</button>
      <button
        :class="['tab', { active: activeTab === 'history' }]"
        @click="switchTab('history')"
      >📋 历史</button>
    </div>

    <!-- 消息提示 -->
    <div v-if="message" :class="['message', `message-${messageType}`]">
      {{ message }}
    </div>

    <!-- ====== 跑步面板 ====== -->
    <div v-if="activeTab === 'run'" class="card">
      <h3>跑步设置</h3>

      <div class="form">
        <!-- 日期 -->
        <div class="form-row">
          <label>跑步日期</label>
          <input type="date" v-model="runDate" class="input" />
        </div>

        <!-- 路线 -->
        <div class="form-row">
          <label>跑步路线</label>
          <select v-model="routeId" class="input">
            <option v-for="r in routes" :key="r.pointId" :value="r.pointId">
              {{ r.pointName || r.pointId }}
            </option>
          </select>
        </div>

        <!-- 路线信息 -->
        <div v-if="selectedRoute" class="route-info">
          距离要求: {{ distance }} km | 路线: {{ selectedRoute.pointName }}
        </div>

        <!-- 开始时间 -->
        <div class="form-row">
          <label>开始时间</label>
          <input type="time" v-model="startTime" class="input" />
        </div>

        <!-- 跑步时长 -->
        <div class="form-row">
          <label>
            跑步时长: <strong>{{ durationMin }} 分钟</strong>
          </label>
          <input
            type="range"
            v-model.number="durationMin"
            min="10"
            max="25"
            class="range"
          />
        </div>

        <!-- 预计结束时间 -->
        <div class="form-row">
          <label>预计结束</label>
          <span class="end-time">{{ endTimePreview }} (含随机波动)</span>
        </div>

        <!-- 提交按钮 -->
        <button
          class="btn btn-primary"
          :disabled="submitting || !routeId"
          @click="doRun"
        >
          {{ submitting ? '提交中...' : `🚀 提交跑步 — ${runDate}` }}
        </button>
      </div>

      <div class="tips-box">
        <p><strong>提示：</strong></p>
        <ul>
          <li>可选择<strong>任意日期</strong>（过去/未来均可正常计入）</li>
          <li>跑步时长含随机波动（±30秒），模拟真实跑步</li>
          <li>提交成功后立即生效，可在日历和历史中查看</li>
        </ul>
      </div>
    </div>

    <!-- ====== 日历面板 ====== -->
    <div v-if="activeTab === 'calendar'" class="card">
      <div class="calendar-header">
        <button class="btn btn-small" @click="prevMonth">◀</button>
        <h3>
          {{ calendarYear }}年{{ calendarMonth + 1 }}月
          <span class="term-name" v-if="calendarTerm">({{ calendarTerm }})</span>
        </h3>
        <button class="btn btn-small" @click="nextMonth">▶</button>
      </div>

      <div class="calendar-legend">
        <span><span class="dot green"></span> 已跑步</span>
        <span><span class="dot red"></span> 未跑步</span>
        <span><span class="dot blue"></span> 今天</span>
      </div>

      <div class="calendar-grid">
        <div v-for="w in weekdays" :key="w" class="cal-cell cal-header">{{ w }}</div>
        <div
          v-for="(day, i) in calendarDays"
          :key="i"
          :class="[
            'cal-cell',
            'cal-day',
            {
              'cal-empty': !day,
              'has-run': day?.record,
              'no-run': day && !day.record && day.date < new Date().toISOString().slice(0, 10),
              'today': day?.isToday,
            }
          ]"
          @click="onDayClick(day)"
        >
          <template v-if="day">
            <span class="day-num">{{ day.day }}</span>
            <span v-if="day.record" class="day-dot green-dot"></span>
            <span v-else-if="day.date < new Date().toISOString().slice(0, 10)" class="day-dot red-dot"></span>
          </template>
        </div>
      </div>

      <p class="calendar-hint">点击日期可选择该天进行跑步</p>
    </div>

    <!-- ====== 历史面板 ====== -->
    <div v-if="activeTab === 'history'" class="card">
      <h3>本服务提交的跑步记录</h3>
      <div v-if="tasks.length === 0" class="empty">暂无记录</div>
      <table v-else class="task-table">
        <thead>
          <tr>
            <th>日期</th>
            <th>距离</th>
            <th>用时</th>
            <th>配速</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.id">
            <td>{{ t.run_date }}</td>
            <td>{{ t.distance }} km</td>
            <td>{{ t.used_time }}</td>
            <td>{{ t.avg_speed }} km/h</td>
            <td :class="{ 'text-success': t.status === 'success' }">
              {{ t.status === 'success' ? '✅ 成功' : t.status }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.main-panel {
  max-width: 640px;
  margin: 0 auto;
  padding: 16px;
}

/* 用户信息栏 */
.user-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: var(--card-bg);
  border-radius: 12px;
  margin-bottom: 16px;
}

.user-info {
  display: flex;
  gap: 12px;
  align-items: baseline;
}

.user-name {
  font-weight: 700;
  font-size: 1.1rem;
}

.user-number {
  color: var(--text-secondary);
}

.user-school {
  color: var(--text-secondary);
  font-size: 0.85rem;
}

.btn-logout {
  padding: 6px 16px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.btn-logout:hover {
  background: #fee2e2;
  color: #dc2626;
  border-color: #dc2626;
}

/* Tabs */
.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.tab {
  flex: 1;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--card-bg);
  cursor: pointer;
  font-size: 0.95rem;
  transition: all 0.2s;
}

.tab.active {
  background: #3b82f6;
  color: white;
  border-color: #3b82f6;
}

/* 消息 */
.message {
  padding: 12px 16px;
  border-radius: 8px;
  margin-bottom: 16px;
  font-size: 0.9rem;
}

.message-info { background: #dbeafe; color: #2563eb; }
.message-success { background: #dcfce7; color: #16a34a; }
.message-error { background: #fee2e2; color: #dc2626; }

/* 卡片 */
.card {
  background: var(--card-bg);
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}

.card h3 { margin: 0 0 16px; }

/* 表单 */
.form { display: flex; flex-direction: column; gap: 14px; }

.form-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.form-row label {
  min-width: 80px;
  color: var(--text-secondary);
  font-size: 0.9rem;
}

.input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 0.95rem;
  background: var(--bg);
  color: var(--text);
}

.range { flex: 1; }

.end-time {
  color: var(--text-secondary);
  font-size: 0.85rem;
}

.route-info {
  padding: 8px 12px;
  background: var(--bg);
  border-radius: 6px;
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.btn-primary {
  margin-top: 8px;
  padding: 14px;
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 1.05rem;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-primary:hover:not(:disabled) { background: #2563eb; }
.btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }

.tips-box {
  margin-top: 20px;
  padding: 14px;
  background: var(--bg);
  border-radius: 8px;
  font-size: 0.85rem;
}

.tips-box ul {
  margin: 6px 0 0;
  padding-left: 18px;
}

.tips-box li { margin: 3px 0; color: var(--text-secondary); }

/* 日历 */
.calendar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.term-name { font-size: 0.8rem; color: var(--text-secondary); }

.btn-small {
  padding: 4px 12px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  font-size: 0.9rem;
}

.calendar-legend {
  display: flex;
  gap: 16px;
  margin-bottom: 12px;
  font-size: 0.8rem;
}

.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; }
.dot.green { background: #22c55e; }
.dot.red { background: #ef4444; }
.dot.blue { background: #3b82f6; }

.calendar-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 4px;
}

.cal-cell {
  aspect-ratio: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  font-size: 0.85rem;
}

.cal-header { color: var(--text-secondary); font-weight: 600; }

.cal-day {
  cursor: pointer;
  position: relative;
}

.cal-day:hover { background: var(--bg); }

.has-run { background: #dcfce7; }
.no-run { background: #fee2e2; }
.today { border: 2px solid #3b82f6; }

.day-dot { width: 5px; height: 5px; border-radius: 50%; margin-top: 2px; }
.green-dot { background: #22c55e; }
.red-dot { background: #ef4444; }

.calendar-hint {
  margin-top: 12px;
  font-size: 0.8rem;
  color: var(--text-secondary);
}

/* 历史表格 */
.task-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}

.task-table th, .task-table td {
  padding: 10px 8px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}

.task-table th { color: var(--text-secondary); font-weight: 600; }

.empty { color: var(--text-secondary); padding: 24px; text-align: center; }

.text-success { color: #16a34a; font-weight: 600; }
</style>
