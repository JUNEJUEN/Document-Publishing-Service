<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchDocuments, type SourceDocument } from '../api'
import { errorMessage } from '../http'
import { jobList, publishAll, publishDocument } from '../jobs'
import DocumentPicker from './DocumentPicker.vue'

const documents = ref<SourceDocument[]>([])
const documentId = ref('')
const publishing = ref(false)
const publishingAll = ref(false)
const loadError = ref('')
const publishError = ref('')
let latest = 0 // only the newest load may update the list

// Offered in the dropdown: not deleted, and no job yet. Any other id can still be typed.
const withJob = computed(() => new Set(jobList.jobs.map((job) => job.documentId)))
const unpublished = computed(() => documents.value.filter((doc) => !doc.deleted && !withJob.value.has(doc.id)))
const busy = computed(() => publishing.value || publishingAll.value)

async function loadDocuments() {
  const request = ++latest
  try {
    const result = await fetchDocuments()
    if (request === latest) {
      documents.value = result
      loadError.value = ''
    }
  } catch (e) {
    // Keep the last list; typing an id still works.
    if (request === latest) loadError.value = `Could not load the document list: ${errorMessage(e)}`
  }
}

async function publishOne() {
  publishing.value = true
  publishError.value = ''
  try {
    await publishDocument(documentId.value.trim())
    documentId.value = ''
  } catch (e) {
    publishError.value = errorMessage(e)
  } finally {
    publishing.value = false
  }
}

async function publishEverything() {
  const ids = unpublished.value.map((doc) => doc.id)
  const count = `${ids.length} document${ids.length === 1 ? '' : 's'}`
  // Publishing deletes the originals in Source, so ask first.
  if (!window.confirm(`Publish ${count}? The originals will be deleted from Source.`)) return
  publishingAll.value = true
  publishError.value = ''
  try {
    const failures = await publishAll(ids)
    if (failures.length) {
      publishError.value = `${failures.length} of ${ids.length} could not be published: ${failures.join('; ')}`
    }
  } finally {
    publishingAll.value = false
  }
}

onMounted(loadDocuments)
</script>

<template>
  <section class="card">
    <h2>Publish a document</h2>
    <form class="publish-form" @submit.prevent="publishOne">
      <div class="field">
        <label id="document-id-label" for="document-id">Document id</label>
        <!-- Reloads on focus, so new documents in Source appear without a Refresh button. -->
        <DocumentPicker
          id="document-id"
          v-model="documentId"
          label-id="document-id-label"
          :options="unpublished"
          placeholder="Pick or type, e.g. doc-1"
          required
          @focus="loadDocuments"
        />
      </div>
      <button :disabled="busy">{{ publishing ? 'Publishing…' : 'Publish' }}</button>
      <button
        type="button"
        class="secondary"
        :disabled="busy || !jobList.loaded || !!jobList.error || unpublished.length === 0"
        @click="publishEverything"
      >
        {{ publishingAll ? 'Publishing…' : `Publish all (${unpublished.length})` }}
      </button>
    </form>
    <p v-if="loadError" class="error">{{ loadError }}</p>
    <p v-if="publishError" class="error">{{ publishError }}</p>
  </section>
</template>
