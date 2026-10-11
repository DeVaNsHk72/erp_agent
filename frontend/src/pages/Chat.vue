<template>
  <div class="flex h-screen flex-col bg-surface-white">
    <header class="flex h-12 shrink-0 items-center gap-2.5 border-b border-outline-gray-1 pl-4 pr-2">
      <div class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-gray-7 text-ink-white">
        <LucideSparkles class="h-4 w-4" />
      </div>
      <div class="min-w-0 flex-1 leading-tight">
        <div class="text-base font-semibold text-ink-gray-9">ERP Agent</div>
        <div v-if="contextLabel" class="truncate text-xs text-ink-gray-5" :title="contextLabel">
          {{ contextLabel }}
        </div>
      </div>
      <Button
        variant="ghost"
        :icon="LucideHistory"
        tooltip="Recent chats"
        :class="{ 'bg-surface-gray-3': showHistory }"
        :disabled="busy"
        @click="toggleHistory"
      />
      <Button
        v-if="messages.length || showHistory"
        variant="ghost"
        :icon="LucideSquarePen"
        tooltip="New chat"
        :disabled="busy"
        @click="newChat"
      />
      <Button v-if="embedded" variant="ghost" :icon="LucideX" tooltip="Close (Ctrl/⌘ I)" @click="close" />
    </header>

    <main ref="scroller" class="flex-1 overflow-y-auto">
      <ChatHistory
        v-if="showHistory"
        :chats="chats"
        :current="chatId"
        :loading="loadingChats"
        @open="openChat"
        @delete="deleteChat"
      />

      <div v-else-if="loadingChat" class="flex h-full items-center justify-center">
        <LoadingIndicator class="h-5 w-5 text-ink-gray-5" />
      </div>

      <div v-else-if="!messages.length" class="flex min-h-full flex-col justify-center px-5 py-8">
        <div class="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-surface-gray-2 text-ink-gray-7">
          <LucideSparkles class="h-5 w-5" />
        </div>
        <h1 class="text-xl font-semibold text-ink-gray-9">How can I help?</h1>
        <p class="mt-1 text-p-sm text-ink-gray-5">
          Ask about sales, customers, stock or payments. Answers come from live ERPNext data you can access.
        </p>
        <div class="mt-6 grid gap-2">
          <button
            v-for="s in suggestions"
            :key="s.text"
            class="group flex items-center gap-3 rounded-lg border border-outline-gray-2 px-3 py-2.5 text-left text-sm text-ink-gray-7 transition-colors hover:border-outline-gray-3 hover:bg-surface-gray-1"
            @click="send(s.text)"
          >
            <component :is="s.icon" class="h-4 w-4 shrink-0 text-ink-gray-5 group-hover:text-ink-gray-7" />
            <span>{{ s.text }}</span>
          </button>
        </div>
      </div>

      <div v-else class="space-y-6 px-4 py-5">
        <ChatMessage v-for="(m, i) in messages" :key="i" :message="m" :chat="chatId" @open="openDoc" />
      </div>
    </main>

    <footer class="shrink-0 px-3 pb-3">
      <div
        class="rounded-xl border border-outline-gray-2 bg-surface-white shadow-sm transition-colors focus-within:border-outline-gray-4"
      >
        <textarea
          ref="inputEl"
          v-model="input"
          rows="1"
          placeholder="Ask about your data…"
          class="block max-h-40 w-full resize-none border-0 bg-transparent px-3 pt-2.5 text-base text-ink-gray-9 placeholder-ink-gray-4 focus:ring-0"
          @input="autoGrow"
          @keydown.enter.exact.prevent="send(input)"
        />
        <div class="flex items-center justify-between px-2 pb-2">
          <span class="pl-1 text-xs text-ink-gray-4">Shift + Enter for a new line</span>
          <button
            class="flex h-7 w-7 items-center justify-center rounded-full bg-surface-gray-7 text-ink-white transition-opacity disabled:opacity-30"
            :disabled="!input.trim() || busy"
            aria-label="Send"
            @click="send(input)"
          >
            <LoadingIndicator v-if="busy" class="h-3.5 w-3.5" />
            <LucideArrowUp v-else class="h-4 w-4" />
          </button>
        </div>
      </div>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { Button, LoadingIndicator, call } from 'frappe-ui'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import LucideArrowUp from '~icons/lucide/arrow-up'
import LucideHistory from '~icons/lucide/history'
import LucidePackage from '~icons/lucide/package'
import LucideReceipt from '~icons/lucide/receipt'
import LucideSparkles from '~icons/lucide/sparkles'
import LucideSquarePen from '~icons/lucide/square-pen'
import LucideTrendingUp from '~icons/lucide/trending-up'
import LucideUsers from '~icons/lucide/users'
import LucideX from '~icons/lucide/x'
import ChatHistory, { type ChatSummary } from '@/components/ChatHistory.vue'
import ChatMessage, { type Message } from '@/components/ChatMessage.vue'
import { getSocket } from '@/lib/socket'

const STEP_EVENT = 'erp_agent_step'
const TEXT_EVENT = 'erp_agent_text'
// Remembers the open chat so reopening the panel or reloading desk continues it
const CHAT_KEY = 'erp_agent_chat'
const suggestions = [
  { icon: LucideUsers, text: 'Who are my top 5 customers by revenue?' },
  { icon: LucideTrendingUp, text: 'Show monthly sales for this fiscal year as a chart' },
  { icon: LucideReceipt, text: 'Which invoices are overdue and how much is owed?' },
  { icon: LucidePackage, text: 'Which items sold the most units last quarter?' },
]

// Inside the desk side panel (an iframe) we show a close button; standalone /agent does not
const embedded = window.parent !== window

const messages = ref<Message[]>([])
const input = ref('')
const inputEl = ref<HTMLTextAreaElement | null>(null)
const scroller = ref<HTMLElement | null>(null)
const context = ref<{ doctype?: string; docname?: string } | null>(null)
const chatId = ref<string | null>(null)
const showHistory = ref(false)
const chats = ref<ChatSummary[]>([])
const loadingChats = ref(false)
const loadingChat = ref(false)

const busy = computed(() => messages.value.some((m) => m.status === 'pending'))
const contextLabel = computed(() => {
  if (!context.value?.doctype) return ''
  return context.value.docname
    ? `Viewing ${context.value.doctype} · ${context.value.docname}`
    : `Viewing ${context.value.doctype} list`
})

async function send(text: string) {
  const question = text.trim()
  if (!question || busy.value) return

  messages.value.push({ role: 'user', content: question, steps: [], charts: [] })
  const reply = reactive<Message>({
    role: 'assistant',
    content: '',
    status: 'pending',
    runId: newRunId(),
    steps: [],
    charts: [],
  })
  messages.value.push(reply)
  input.value = ''
  autoGrow()
  scrollToBottom()

  try {
    const result = await call('erp_agent.api.ask', {
      question,
      chat: chatId.value,
      context: context.value ? JSON.stringify(context.value) : null,
      run_id: reply.runId,
    })
    reply.content = result.answer || 'No answer.'
    reply.charts = result.charts || []
    reply.actions = result.actions || []
    // The server's list is authoritative, in case a live event was missed
    reply.steps = (result.steps || []).map((s: any) => ({ label: s.label, status: s.ok ? 'done' : 'error' }))
    reply.status = 'done'
    setChatId(result.chat)
  } catch (e: any) {
    reply.content = e?.messages?.join('\n') || e?.message || 'Something went wrong.'
    reply.status = 'error'
  }
  scrollToBottom()
}

function onStep(data: { run_id: string; label: string; status: 'running' | 'done' | 'error' }) {
  const reply = messages.value.find((m) => m.runId === data.run_id && m.status === 'pending')
  if (!reply) return
  if (data.status === 'running') {
    // Text streamed before a tool call was the model thinking aloud, not the answer
    reply.content = ''
    reply.steps.push({ label: data.label, status: 'running' })
  } else {
    const step = [...reply.steps].reverse().find((s) => s.label === data.label && s.status === 'running')
    if (step) step.status = data.status
  }
  scrollToBottom()
}

// The answer arrives in small pieces while the model writes it
function onText(data: { run_id: string; delta: string }) {
  const reply = messages.value.find((m) => m.runId === data.run_id && m.status === 'pending')
  if (!reply) return
  reply.content += data.delta
  scrollToBottom()
}

// Messages from the desk side panel (agent_panel.bundle.js)
function onParentMessage(event: MessageEvent) {
  if (event.origin !== window.location.origin) return
  if (event.data?.type === 'erp-agent-context') context.value = event.data.context
  if (event.data?.type === 'erp-agent-focus') inputEl.value?.focus()
  if (event.data?.type === 'erp-agent-open-chat' && event.data.chat) {
    window.parent.postMessage({ type: 'erp-agent-chat-opened' }, window.location.origin)
    if (event.data.chat !== chatId.value && !busy.value) openChat(event.data.chat)
  }
}

// Show a document the agent created: in desk when docked, in a new tab when standalone
function openDoc(doc: { doctype: string; name: string }) {
  if (embedded) {
    window.parent.postMessage({ type: 'erp-agent-open-doc', ...doc }, window.location.origin)
  } else {
    const slug = doc.doctype.toLowerCase().replace(/ /g, '-')
    window.open(`/app/${slug}/${encodeURIComponent(doc.name)}`, '_blank')
  }
}

function close() {
  window.parent.postMessage({ type: 'erp-agent-close' }, window.location.origin)
}

function onKeydown(event: KeyboardEvent) {
  // Same shortcut as desk, so it also works while typing in the chat
  if (embedded && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'i') {
    event.preventDefault()
    close()
  }
}

function newChat() {
  messages.value = []
  showHistory.value = false
  setChatId(null)
  inputEl.value?.focus()
}

function setChatId(name: string | null) {
  chatId.value = name
  try {
    if (name) localStorage.setItem(CHAT_KEY, name)
    else localStorage.removeItem(CHAT_KEY)
  } catch (e) {
    // storage blocked: chats are still saved on the server, just not auto-reopened
  }
}

async function toggleHistory() {
  showHistory.value = !showHistory.value
  if (!showHistory.value) return
  loadingChats.value = true
  try {
    chats.value = await call('erp_agent.api.list_chats')
  } finally {
    loadingChats.value = false
  }
}

async function openChat(name: string) {
  showHistory.value = false
  loadingChat.value = true
  try {
    const chat = await call('erp_agent.api.get_chat', { chat: name })
    messages.value = chat.messages.map((m: any) => ({
      role: m.role,
      content: m.content || '',
      status: m.role === 'assistant' ? 'done' : undefined,
      steps: (m.steps || []).map((s: any) => ({ label: s.label, status: s.ok ? 'done' : 'error' })),
      charts: m.charts || [],
      actions: m.actions || [],
    }))
    setChatId(name)
  } catch (e) {
    // deleted or not ours any more: start fresh
    messages.value = []
    setChatId(null)
  } finally {
    loadingChat.value = false
  }
  scrollToBottom()
}

async function deleteChat(name: string) {
  if (!window.confirm('Delete this chat?')) return
  await call('erp_agent.api.delete_chat', { chat: name })
  chats.value = chats.value.filter((c) => c.name !== name)
  if (chatId.value === name) {
    messages.value = []
    setChatId(null)
  }
}

function savedChatId() {
  try {
    return localStorage.getItem(CHAT_KEY)
  } catch (e) {
    return null
  }
}

function newRunId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 10)
}

function autoGrow() {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

function scrollToBottom() {
  nextTick(() => {
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  })
}

onMounted(() => {
  getSocket().on(STEP_EVENT, onStep)
  getSocket().on(TEXT_EVENT, onText)
  window.addEventListener('message', onParentMessage)
  window.addEventListener('keydown', onKeydown)
  if (embedded) window.parent.postMessage({ type: 'erp-agent-ready' }, window.location.origin)
  const saved = savedChatId()
  if (saved) openChat(saved)
  inputEl.value?.focus()
})

onBeforeUnmount(() => {
  getSocket().off(STEP_EVENT, onStep)
  getSocket().off(TEXT_EVENT, onText)
  window.removeEventListener('message', onParentMessage)
  window.removeEventListener('keydown', onKeydown)
})
</script>
