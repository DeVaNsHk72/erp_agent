import DOMPurify from 'dompurify'
import { marked } from 'marked'

const inr = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 })

// The agent writes money as ₹ + raw number (₹34423819.35) because models group
// Indian digits unreliably; format it here as ₹3,44,23,819.35.
function formatRupees(text: string): string {
  return text.replace(/₹\s?(-?\d+(?:\.\d+)?)(?![\d,])/g, (_, num) => `₹${inr.format(Number(num))}`)
}

// Agent answers are Markdown (tables, lists, bold). Sanitize because answers can
// echo text stored in ERPNext records, e.g. customer names.
export function renderMarkdown(text: string): string {
  const html = marked.parse(formatRupees(text || ''), { gfm: true, breaks: true, async: false }) as string
  return DOMPurify.sanitize(html)
}
