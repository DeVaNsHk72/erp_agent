<template>
  <div class="overflow-hidden rounded-lg border border-outline-gray-2 bg-surface-white">
    <div class="flex items-center gap-2 border-b border-outline-gray-1 bg-surface-gray-1 px-3 py-2">
      <LucideFilePlus class="h-4 w-4 text-ink-gray-6" />
      <span class="text-sm font-medium text-ink-gray-8">Draft {{ action.preview.doctype }}</span>
      <span class="ml-auto rounded-full px-2 py-0.5 text-xs" :class="badge.class">{{ badge.label }}</span>
    </div>

    <div class="space-y-2 px-3 py-2.5 text-sm">
      <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <template v-for="(value, label) in action.preview.fields" :key="label">
          <dt class="text-ink-gray-5">{{ label }}</dt>
          <dd class="truncate text-ink-gray-8">{{ value }}</dd>
        </template>
      </dl>

      <table v-if="action.preview.items?.length" class="w-full text-xs">
        <thead>
          <tr class="text-left text-ink-gray-5">
            <th class="py-1 font-normal">Item</th>
            <th class="py-1 text-right font-normal">Qty</th>
            <th class="py-1 text-right font-normal">Rate</th>
            <th class="py-1 text-right font-normal">Amount</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in action.preview.items" :key="i" class="border-t border-outline-gray-1 text-ink-gray-8">
            <td class="py-1 pr-2">{{ row.item }}</td>
            <td class="py-1 text-right">{{ row.qty }}</td>
            <td class="py-1 text-right">{{ money(row.rate) }}</td>
            <td class="py-1 text-right">{{ money(row.amount) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="action.preview.total != null" class="flex justify-between border-t border-outline-gray-1 pt-1.5 font-medium text-ink-gray-9">
        <span>Total</span>
        <span>{{ money(action.preview.total) }}</span>
      </div>
    </div>

    <div class="flex items-center gap-2 border-t border-outline-gray-1 px-3 py-2">
      <template v-if="action.status === 'pending'">
        <Button variant="solid" size="sm" :loading="working === 'confirm'" :disabled="!!working" @click="run('confirm')">
          Create draft
        </Button>
        <Button variant="ghost" size="sm" :disabled="!!working" @click="run('cancel')">Cancel</Button>
        <span class="ml-auto text-xs text-ink-gray-5">Saved as a draft, not submitted</span>
      </template>
      <template v-else-if="action.status === 'created' && action.created">
        <LucideCheck class="h-4 w-4 text-ink-green-3" />
        <span class="text-sm text-ink-gray-7">Created {{ action.created.name }}</span>
        <Button variant="subtle" size="sm" class="ml-auto" @click="emit('open', action.created)">Open</Button>
      </template>
      <span v-else class="text-sm text-ink-gray-5">Cancelled, nothing was created</span>
    </div>
    <div v-if="error" class="border-t border-outline-gray-1 px-3 py-2 text-xs text-ink-red-4">{{ error }}</div>
  </div>
</template>

<script setup lang="ts">
import { Button, call } from 'frappe-ui'
import { computed, ref } from 'vue'
import LucideCheck from '~icons/lucide/check'
import LucideFilePlus from '~icons/lucide/file-plus'

export interface ActionSpec {
  id: string
  status: 'pending' | 'created' | 'cancelled'
  preview: {
    doctype: string
    fields: Record<string, string>
    items?: { item: string; qty: number; rate: number; amount: number }[]
    total?: number | null
  }
  created?: { doctype: string; name: string }
}

const props = defineProps<{ action: ActionSpec; chat: string | null }>()
const emit = defineEmits<{ open: [doc: { doctype: string; name: string }] }>()

const working = ref<'confirm' | 'cancel' | null>(null)
const error = ref('')
const inr = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 })

const badge = computed(() => {
  if (props.action.status === 'created') return { label: 'Created', class: 'bg-surface-green-2 text-ink-green-3' }
  if (props.action.status === 'cancelled') return { label: 'Cancelled', class: 'bg-surface-gray-2 text-ink-gray-5' }
  return { label: 'Needs your OK', class: 'bg-surface-amber-2 text-ink-amber-3' }
})

function money(value: number | null | undefined) {
  return value == null ? '' : inr.format(value)
}

async function run(kind: 'confirm' | 'cancel') {
  working.value = kind
  error.value = ''
  try {
    if (kind === 'confirm') {
      const created = await call('erp_agent.api.confirm_action', { action_id: props.action.id, chat: props.chat })
      props.action.created = created
      props.action.status = 'created'
    } else {
      await call('erp_agent.api.cancel_action', { action_id: props.action.id, chat: props.chat })
      props.action.status = 'cancelled'
    }
  } catch (e: any) {
    error.value = e?.messages?.join(' ') || e?.message || 'Something went wrong.'
  } finally {
    working.value = null
  }
}
</script>
