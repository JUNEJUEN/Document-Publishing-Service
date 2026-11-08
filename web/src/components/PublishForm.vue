<script setup lang="ts">
import { ref } from 'vue'
import { errorMessage } from '../http'
import { publishDocument } from '../jobs'

const documentId = ref('')
const busy = ref(false)
const error = ref('')

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await publishDocument(documentId.value.trim())
    documentId.value = ''
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <form class="publish-form" @submit.prevent="submit">
    <label>Document id <input v-model="documentId" placeholder="doc-1" required /></label>
    <button :disabled="busy">{{ busy ? 'Publishing…' : 'Publish' }}</button>
  </form>
  <p v-if="error" class="error">{{ error }}</p>
</template>
