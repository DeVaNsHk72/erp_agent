<template>
  <div v-if="message.role === 'user'" class="flex justify-end">
    <div
      class="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-surface-gray-2 px-3.5 py-2 text-base text-ink-gray-9"
    >
      {{ message.content }}
    </div>
  </div>

  <div v-else class="group space-y-2.5">
    <div v-if="message.steps.length">
      <button
        v-if="message.status !== 'pending'"
        class="inline-flex items-center gap-1 rounded-md py-0.5 text-sm text-ink-gray-5 hover:text-ink-gray-7"
        @click="showSteps = !showSteps"
      >
        <LucideChevronRight class="h-3.5 w-3.5 transition-transform" :class="{ 'rotate-90': showSteps }" />
        Used {{ message.steps.length }} {{ message.steps.length === 1 ? 'step' : 'steps' }}
      </button>
      <ul
        v-if="message.status === 'pending' || showSteps"
        class="mt-1 space-y-1.5 border-l-2 border-outline-gray-1 py-0.5 pl-3"
      >
        <li v-for="(step, i) in message.steps" :key="i" class="flex items-center gap-2 text-sm text-ink-gray-6">
          <LoadingIndicator v-if="step.status === 'running'" class="h-3.5 w-3.5 shrink-0 text-ink-gray-5" />
          <LucideCircleAlert v-else-if="step.status === 'error'" class="h-3.5 w-3.5 shrink-0 text-ink-red-4" />
          <LucideCheck v-else class="h-3.5 w-3.5 shrink-0 text-ink-green-3" />
          <span class="truncate">{{ step.label }}</span>
        </li>
      </ul>
    </div>

    <div
      v-if="message.status === 'pending' && message.content"
      class="answer streaming prose prose-sm max-w-none text-ink-gray-8"
      v-html="html"
    />
    <div v-else-if="message.status === 'pending'" class="flex items-center gap-2 text-sm text-ink-gray-5">
      <span class="thinking-dot" />
      {{ message.steps.length ? 'Working…' : 'Thinking…' }}
    </div>

    <div
      v-else-if="message.status === 'error'"
      class="flex gap-2 rounded-lg bg-surface-red-1 px-3 py-2 text-sm text-ink-red-4"
    >
      <LucideCircleAlert class="mt-0.5 h-4 w-4 shrink-0" />
      <span>{{ message.content }}</span>
    </div>

    <template v-else>
      <div class="answer prose prose-sm max-w-none text-ink-gray-8" v-html="html" />
      <ChartView v-for="(chart, i) in message.charts" :key="i" :chart="chart" />
      <div class="opacity-0 transition-opacity group-hover:opacity-100">
        <button
          class="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-7"
          @click="copy"
        >
          <LucideCheck v-if="copied" class="h-3.5 w-3.5" />
          <LucideCopy v-else class="h-3.5 w-3.5" />
          {{ copied ? 'Copied' : 'Copy' }}
        </button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { LoadingIndicator } from 'frappe-ui'
import { computed, ref } from 'vue'
import LucideCheck from '~icons/lucide/check'
import LucideChevronRight from '~icons/lucide/chevron-right'
import LucideCircleAlert from '~icons/lucide/circle-alert'
import LucideCopy from '~icons/lucide/copy'
import ChartView, { type ChartSpec } from './ChartView.vue'
import { renderMarkdown } from '@/lib/markdown'

export interface Step {
  label: string
  status: 'running' | 'done' | 'error'
}

export interface Message {
  role: 'user' | 'assistant'
  content: string
  status?: 'pending' | 'done' | 'error'
  runId?: string
  steps: Step[]
  charts: ChartSpec[]
}

const props = defineProps<{ message: Message }>()
const showSteps = ref(false)
const copied = ref(false)
const html = computed(() => renderMarkdown(props.message.content))

async function copy() {
  try {
    await navigator.clipboard.writeText(props.message.content)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch (e) {
    // clipboard blocked (e.g. insecure context): nothing to do
  }
}
</script>

<style scoped>
.answer :deep(table) {
  display: block;
  overflow-x: auto;
  margin: 0.5em 0;
  font-size: 0.8125rem;
}
.answer :deep(th),
.answer :deep(td) {
  padding: 0.3rem 0.6rem;
  white-space: nowrap;
}
.answer :deep(p) {
  margin: 0.4em 0;
}
.answer :deep(:first-child) {
  margin-top: 0;
}

/* Blinking caret after the last streamed word */
.streaming :deep(> :last-child)::after {
  content: '';
  display: inline-block;
  width: 0.45em;
  height: 1em;
  margin-left: 2px;
  vertical-align: text-bottom;
  background: currentColor;
  animation: blink 1s steps(2, start) infinite;
}

@keyframes blink {
  to {
    visibility: hidden;
  }
}

.thinking-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: currentColor;
  animation: pulse 1s ease-in-out infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 0.25;
    transform: scale(0.85);
  }
  50% {
    opacity: 1;
    transform: scale(1);
  }
}
</style>
