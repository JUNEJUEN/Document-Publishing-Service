<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchDocuments, type SourceDocument } from '../api'
import { errorMessage } from '../http'

const documents = ref<SourceDocument[]>([])
const loaded = ref(false)
const busy = ref(false)
const error = ref('')
let latest = 0 // only the newest refresh may update the table

async function refresh() {
  const request = ++latest
  busy.value = true
  error.value = ''
  try {
    const result = await fetchDocuments()
    if (request === latest) documents.value = result
  } catch (e) {
    // Keep the last list; only show what went wrong.
    if (request === latest) error.value = errorMessage(e)
  } finally {
    if (request === latest) {
      busy.value = false
      loaded.value = true
    }
  }
}

onMounted(refresh)
</script>

<template>
  <section class="card">
    <div class="card-head">
      <h2>Documents in Source</h2>
      <button class="secondary" :disabled="busy" @click="refresh">{{ busy ? 'Refreshing…' : 'Refresh' }}</button>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="!loaded" class="muted">Loading documents…</p>
    <p v-else-if="documents.length === 0 && !error" class="muted">No documents in Source</p>
    <div v-else-if="documents.length" class="table-scroll">
      <table>
        <thead>
          <tr><th>Id</th><th>Filename</th><th>Size</th><th>Source status</th></tr>
        </thead>
        <tbody>
          <tr v-for="doc in documents" :key="doc.id">
            <td>{{ doc.id }}</td>
            <td>{{ doc.filename }}</td>
            <td>{{ doc.size === null ? '—' : `${doc.size} B` }}</td>
            <td>{{ doc.status }}<span v-if="doc.deleted" class="muted"> · deleted</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
