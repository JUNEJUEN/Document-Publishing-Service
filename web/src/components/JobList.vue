<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import type { Job, JobState } from '../api'
import { errorMessage } from '../http'
import { jobList, retryAndRefresh, startPolling, stopPolling } from '../jobs'

const LABELS: Record<JobState, string> = { queued: 'Waiting', running: 'Running', done: 'Done', failed: 'Failed' }
const retrying = reactive(new Set<string>())
const retryError = ref('')

function details(job: Job): string {
  // Jobs finished before vendor delivery existed have no receipt.
  if (job.state === 'done') return job.vendorReceipt ? `Vendor receipt ${job.vendorReceipt}` : ''
  if (job.state === 'failed') return job.error ?? ''
  return job.error ?? `Vendor attempts so far: ${job.vendorAttempts}`
}

async function retry(jobId: string) {
  retrying.add(jobId)
  retryError.value = ''
  try {
    await retryAndRefresh(jobId)
  } catch (e) {
    retryError.value = errorMessage(e)
  } finally {
    retrying.delete(jobId)
  }
}

onMounted(startPolling)
onUnmounted(stopPolling)
</script>

<template>
  <section class="card">
    <h2>Jobs</h2>
    <p v-if="jobList.error" class="error">{{ jobList.error }}</p>
    <p v-if="retryError" class="error">{{ retryError }}</p>
    <p v-if="!jobList.loaded" class="muted">Loading jobs…</p>
    <p v-else-if="jobList.jobs.length === 0 && !jobList.error" class="muted">No jobs yet</p>
    <div v-else-if="jobList.jobs.length" class="table-scroll">
      <table>
        <thead>
          <tr><th>Started</th><th>Document</th><th>State</th><th>Details</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="job in jobList.jobs" :key="job.id">
            <td>{{ new Date(job.createdAt).toLocaleString() }}</td>
            <td>{{ job.documentId }}</td>
            <td><span :class="['state', job.state]">{{ LABELS[job.state] }}</span></td>
            <td>{{ details(job) }}</td>
            <td>
              <button v-if="job.canRetry" :disabled="retrying.has(job.id)" @click="retry(job.id)">
                {{ retrying.has(job.id) ? 'Retrying…' : 'Retry' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
