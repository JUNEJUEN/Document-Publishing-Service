import { reactive } from 'vue'
import { fetchJobs, publish, type Job } from './api'
import { errorMessage } from './http'

const POLL_MS = 2000

/** The job list shared by the page; polled while the job list is shown. */
export const jobList = reactive({ jobs: [] as Job[], loaded: false, error: '' })
let latest = 0 // only the newest refresh may update the list
let generation = 0 // a new start or a stop ends the previous polling loop
let timer: ReturnType<typeof setTimeout> | undefined

export async function refreshJobs(): Promise<void> {
  const request = ++latest
  try {
    const jobs = await fetchJobs()
    if (request === latest) Object.assign(jobList, { jobs, error: '' })
  } catch (e) {
    // Keep the last list; only show what went wrong.
    if (request === latest) jobList.error = errorMessage(e)
  } finally {
    if (request === latest) jobList.loaded = true
  }
}

export function startPolling(): void {
  // Also clears anything a late answer wrote after the last stop.
  Object.assign(jobList, { jobs: [], loaded: false, error: '' })
  const current = ++generation
  const poll = async () => {
    await refreshJobs()
    if (current === generation) timer = setTimeout(poll, POLL_MS)
  }
  void poll()
}

export function stopPolling(): void {
  generation++
  latest++ // ignore answers still on their way
  clearTimeout(timer)
}

/** Shared by the document-id field and the row buttons. */
export async function publishDocument(documentId: string): Promise<void> {
  await publish(documentId)
  await refreshJobs()
}
