<template>
  <!-- No fixed height: frappe-ui charts need at least 300px and size themselves -->
  <div class="w-full overflow-hidden rounded-lg border border-outline-gray-1 bg-surface-white">
    <DonutChart v-if="chart.chart_type === 'donut'" :config="donutConfig" />
    <AxisChart v-else :config="axisConfig" />
  </div>
</template>

<script setup lang="ts">
import { AxisChart, DonutChart } from 'frappe-ui'
import { computed } from 'vue'

export interface ChartSpec {
  title: string
  chart_type: 'bar' | 'line' | 'donut'
  data: Record<string, any>[]
  x: string
  y: string[]
}

const props = defineProps<{ chart: ChartSpec }>()

// "total_sales" -> "Total sales"
function humanize(key: string) {
  const text = key.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

// frappe-ui reads each value as row[series.name], so the value keys are renamed to the labels
// shown in the legend and tooltip ("total_sales" -> "Total sales"). The model sometimes sends
// numbers as strings; charts need real numbers.
const rows = computed(() =>
  props.chart.data.map((row) => {
    const clean: Record<string, any> = { [props.chart.x]: String(row[props.chart.x]) }
    for (const key of props.chart.y) clean[humanize(key)] = Number(row[key]) || 0
    return clean
  }),
)

const donutConfig = computed(() => ({
  title: props.chart.title,
  data: rows.value,
  categoryColumn: props.chart.x,
  valueColumn: humanize(props.chart.y[0]),
}))

const axisConfig = computed(() => ({
  title: props.chart.title,
  data: rows.value,
  xAxis: { key: props.chart.x, type: 'category' as const },
  // frappe-ui prints the y-axis title above the axis; it shows "undefined" if left out
  yAxis: { title: props.chart.y.map(humanize).join(', ') },
  series: props.chart.y.map((key) => ({
    name: humanize(key),
    type: props.chart.chart_type === 'line' ? ('line' as const) : ('bar' as const),
  })),
}))
</script>
