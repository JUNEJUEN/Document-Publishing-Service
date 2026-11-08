<script setup lang="ts">
import { computed, nextTick, ref, useId, useTemplateRef } from 'vue'
import type { SourceDocument } from '../api'

const props = defineProps<{
  options: SourceDocument[]
  id: string
  labelId: string
  placeholder?: string
  required?: boolean
}>()
const emit = defineEmits<{ focus: [] }>()
const model = defineModel<string>({ required: true })

const baseId = useId()
const list = useTemplateRef<HTMLElement>('list')
const open = ref(false)
// Tracked by id: the 2 s job poll rebuilds `options`.
const highlightedId = ref<string | null>(null)

const filtered = computed(() => {
  const query = model.value.trim().toLowerCase()
  return props.options.filter(
    (doc) => doc.id.toLowerCase().includes(query) || doc.filename.toLowerCase().includes(query),
  )
})
const expanded = computed(() => open.value && filtered.value.length > 0)
const highlightedIndex = computed(() => filtered.value.findIndex((doc) => doc.id === highlightedId.value))

function close() {
  open.value = false
  highlightedId.value = null
}

function pick(doc: SourceDocument) {
  model.value = doc.id
  close()
}

function onInput() {
  // Typing hands control back to the text, so Enter submits what was typed.
  highlightedId.value = null
  open.value = true
}

function highlight(index: number) {
  highlightedId.value = filtered.value[index].id
  nextTick(() => list.value?.children[index]?.scrollIntoView?.({ block: 'nearest' }))
}

function onKeydown(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return // IME, incl. Safari
  if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return // keep text selection shortcuts
  const last = filtered.value.length - 1
  switch (e.key) {
    case 'ArrowDown':
      e.preventDefault()
      if (!expanded.value) {
        open.value = true
        if (last >= 0) highlight(0)
      } else {
        highlight(Math.min(highlightedIndex.value + 1, last))
      }
      break
    case 'ArrowUp':
      e.preventDefault()
      if (highlightedIndex.value > 0) highlight(highlightedIndex.value - 1)
      break
    case 'Enter':
      if (highlightedIndex.value >= 0) {
        e.preventDefault()
        pick(filtered.value[highlightedIndex.value])
      } else {
        close()
      }
      break
    case 'Escape':
      close()
      break
  }
}
</script>

<template>
  <div class="picker">
    <input
      :id="id"
      v-model="model"
      role="combobox"
      aria-autocomplete="list"
      :aria-controls="`${baseId}-listbox`"
      :aria-expanded="expanded"
      :aria-activedescendant="highlightedIndex >= 0 ? `${baseId}-option-${highlightedIndex}` : undefined"
      :placeholder="placeholder"
      :required="required"
      autocomplete="off"
      spellcheck="false"
      @focus="open = true; emit('focus')"
      @click="open = true"
      @input="onInput"
      @blur="close"
      @keydown="onKeydown"
    />
    <!-- mousedown.prevent keeps focus in the input, so blur doesn't close the list first. -->
    <ul
      v-show="expanded"
      :id="`${baseId}-listbox`"
      ref="list"
      class="options"
      role="listbox"
      :aria-labelledby="labelId"
      @mousedown.prevent
    >
      <li
        v-for="(doc, i) in filtered"
        :id="`${baseId}-option-${i}`"
        :key="doc.id"
        class="option"
        role="option"
        :aria-selected="i === highlightedIndex"
        @mousemove="highlightedId = doc.id"
        @click="pick(doc)"
      >
        <strong>{{ doc.id }}</strong>
        <span class="muted">{{ doc.filename }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.picker { position: relative; }
.picker input { width: 100%; max-width: none; box-sizing: border-box; }
.options {
  position: absolute; top: calc(100% + 4px); left: 0; right: 0; z-index: 10;
  box-sizing: border-box; max-height: 15rem; overflow-y: auto;
  margin: 0; padding: 4px 0; list-style: none;
  background: var(--card); border: 1px solid var(--border); border-radius: 6px;
  box-shadow: 0 4px 12px rgb(0 0 0 / 0.08);
}
.option { display: flex; flex-direction: column; padding: 6px 10px; cursor: pointer; overflow-wrap: anywhere; }
.option span { font-size: 0.85em; }
.option[aria-selected="true"] { background: color-mix(in srgb, var(--accent) 12%, transparent); }
</style>
