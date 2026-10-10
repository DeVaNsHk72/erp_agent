<template>
  <div class="flex h-full flex-col">
    <div class="flex items-center justify-between px-4 pb-2 pt-4">
      <div class="text-sm font-semibold text-ink-gray-9">Recent chats</div>
      <LoadingIndicator v-if="loading" class="h-3.5 w-3.5 text-ink-gray-5" />
    </div>

    <div v-if="!loading && !chats.length" class="px-4 py-6 text-sm text-ink-gray-5">
      No saved chats yet. Ask a question and it will appear here.
    </div>

    <ul class="flex-1 overflow-y-auto px-2 pb-3">
      <li v-for="chat in chats" :key="chat.name">
        <div
          class="group flex w-full items-center gap-2 rounded-lg px-2.5 py-2 hover:bg-surface-gray-2"
          :class="{ 'bg-surface-gray-2': chat.name === current }"
        >
          <button class="min-w-0 flex-1 text-left" @click="emit('open', chat.name)">
            <div class="truncate text-sm text-ink-gray-8">{{ chat.title }}</div>
            <div class="text-xs text-ink-gray-5">{{ timeAgo(chat.modified) }}</div>
          </button>
          <button
            class="shrink-0 rounded p-1 text-ink-gray-4 opacity-0 hover:bg-surface-gray-3 hover:text-ink-red-4 group-hover:opacity-100"
            aria-label="Delete chat"
            @click="emit('delete', chat.name)"
          >
            <LucideTrash2 class="h-3.5 w-3.5" />
          </button>
        </div>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { LoadingIndicator } from 'frappe-ui'
import LucideTrash2 from '~icons/lucide/trash-2'

export interface ChatSummary {
  name: string
  title: string
  modified: string
}

defineProps<{ chats: ChatSummary[]; current: string | null; loading: boolean }>()
const emit = defineEmits<{ open: [name: string]; delete: [name: string] }>()

// Frappe returns "2026-10-10 14:03:12.123456" in the site's time zone
function timeAgo(value: string) {
  const then = new Date(value.replace(' ', 'T')).getTime()
  const minutes = Math.round((Date.now() - then) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.round(hours / 24)
  return days === 1 ? 'yesterday' : `${days} days ago`
}
</script>
