<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { fetchDocuments, type SourceDocument } from '../api'
import { errorMessage } from '../http'
import { jobList, publishDocument } from '../jobs'

const documents = ref<SourceDocument[]>([])
const loaded = ref(false)
const busy = ref(false)
const error = ref('')
const publishError = ref('')
const publishing = reactive(new Set<string>())
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

async function publishRow(documentId: string) {
  publishing.add(documentId)
  publishError.value = ''
  try {
    await publishDocument(documentId)
  } catch (e) {
    publishError.value = errorMessage(e)
  } finally {
    publishing.delete(documentId)
  }
}

// A finished job has changed the document in Source.
const finishedJobs = computed(() =>
  jobList.jobs
    .filter((job) => job.state === 'done' || job.state === 'failed')
    .map((job) => `${job.id}:${job.state}`)
    .join(),
)
watch(finishedJobs, refresh)

onMounted(refresh)
</script>

<template>
  <section class="card">
    <div class="card-head">
      <h2>Documents in Source</h2>
      <button class="secondary" :disabled="busy" @click="refresh">{{ busy ? 'Refreshing…' : 'Refresh' }}</button>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="publishError" class="error">{{ publishError }}</p>
    <p v-if="!loaded" class="muted">Loading documents…</p>
    <p v-else-if="documents.length === 0 && !error" class="muted">No documents in Source</p>
    <div v-else-if="documents.length" class="table-scroll">
      <table>
        <thead>
          <tr><th>Id</th><th>Filename</th><th>Size</th><th>Source status</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="doc in documents" :key="doc.id">
            <td>{{ doc.id }}</td>
            <td>{{ doc.filename }}</td>
            <td>{{ doc.size === null ? '—' : `${doc.size} B` }}</td>
            <td>{{ doc.status }}<span v-if="doc.deleted" class="muted"> · deleted</span></td>
            <td>
              <button :disabled="doc.deleted || publishing.has(doc.id)" @click="publishRow(doc.id)">
                {{ publishing.has(doc.id) ? 'Publishing…' : 'Publish' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
